"""Durable session highlights, kept separate from events and legacy notes."""

import copy
import json
import os
import shutil
import tempfile
import threading
import time
import uuid


SCHEMA_VERSION = 1
HIGHLIGHTS_FILENAME = "session_highlights.json"
OPERATIONS_FILENAME = "session_highlight_operations.jsonl"

HIGHLIGHT_CATEGORIES = {
    "humor",
    "combat",
    "dramatic_roll",
    "clever_solution",
    "character_moment",
    "social_moment",
    "failure",
    "triumph",
    "memorable_action",
    "other",
}

_CONFIDENCE_LEVELS = {"unknown", "low", "medium", "high"}
_REVIEW_STATUSES = {"pending", "kept", "rejected"}
_EDITABLE_FIELDS = {
    "categories",
    "confidence",
    "summary",
    "participants",
    "sourceChunks",
    "relatedEventIds",
    "reviewStatus",
}
_STORE_LOCK = threading.RLock()


def _now():
    return int(time.time())


def _identifier(prefix):
    return f"{prefix}_{uuid.uuid4().hex}"


def _text(value, field, max_length):
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} is required.")
    if len(result) > max_length:
        raise ValueError(f"{field} must be at most {max_length} characters.")
    return result


def _choice(value, field, allowed, default):
    result = str(value or default).strip().lower()
    if result not in allowed:
        raise ValueError(f"{field} must be one of: {', '.join(sorted(allowed))}.")
    return result


def _source_chunks(value, required=False):
    if value is None:
        value = []
    if not isinstance(value, list):
        raise ValueError("sourceChunks must be a list of non-negative chunk numbers.")
    result = []
    for item in value:
        if isinstance(item, bool):
            raise ValueError("sourceChunks must contain non-negative integers.")
        try:
            chunk = int(item)
        except (TypeError, ValueError):
            raise ValueError("sourceChunks must contain non-negative integers.") from None
        if chunk < 0 or str(item).strip() != str(chunk):
            raise ValueError("sourceChunks must contain non-negative integers.")
        result.append(chunk)
    result = sorted(set(result))
    if required and not result:
        raise ValueError("At least one source transcript chunk is required.")
    return result


def _string_list(value, field, max_items):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list of strings.")
    result = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{field} must contain strings.")
        text = item.strip()
        if text and text not in result:
            result.append(text)
    if len(result) > max_items:
        raise ValueError(f"{field} may contain at most {max_items} entries.")
    return result


def _categories(value):
    result = [item.lower() for item in _string_list(value or ["other"], "categories", 4)]
    if not result:
        result = ["other"]
    invalid = sorted(set(result) - HIGHLIGHT_CATEGORIES)
    if invalid:
        raise ValueError(f"Unsupported highlight categories: {', '.join(invalid)}.")
    return result


def _related_event_ids(value):
    result = _string_list(value, "relatedEventIds", 20)
    for event_id in result:
        if len(event_id) != 36 or not event_id.startswith("evt_"):
            raise ValueError(f"Invalid related event ID: {event_id}.")
        try:
            uuid.UUID(hex=event_id[4:])
        except ValueError:
            raise ValueError(f"Invalid related event ID: {event_id}.") from None
    return result


def _empty_store(session_id):
    return {
        "schemaVersion": SCHEMA_VERSION,
        "sessionId": str(session_id),
        "lastSequence": 0,
        "updatedAt": None,
        "highlights": {},
    }


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _write_json_atomic(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = f"{path}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def read_highlight_operations(session_dir):
    path = os.path.join(session_dir, OPERATIONS_FILENAME)
    operations = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    operation = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"Invalid highlight history at line {line_number}: {exc.msg}."
                    ) from None
                if not isinstance(operation, dict):
                    raise ValueError(
                        f"Invalid highlight history at line {line_number}: expected an object."
                    )
                operations.append(operation)
    except FileNotFoundError:
        pass
    return operations


def _replay_operations(session_id, operations):
    store = _empty_store(session_id)
    for expected_sequence, operation in enumerate(operations, start=1):
        sequence = operation.get("sequence")
        if sequence != expected_sequence:
            raise ValueError(
                f"Invalid highlight history sequence: expected {expected_sequence}, got {sequence}."
            )
        if str(operation.get("sessionId") or "") != str(session_id):
            raise ValueError("Highlight history belongs to a different session.")
        after = operation.get("afterHighlights")
        if not isinstance(after, dict):
            raise ValueError(
                f"Invalid highlight history at sequence {sequence}: afterHighlights is required."
            )
        for highlight_id, highlight in after.items():
            store["highlights"][highlight_id] = copy.deepcopy(highlight)
        store["lastSequence"] = sequence
        store["updatedAt"] = operation.get("occurredAt")
    return store


def read_highlight_store(session_dir, session_id):
    """Read current state, rebuilding it from append-only history if needed."""
    with _STORE_LOCK:
        operations = read_highlight_operations(session_dir)
        path = os.path.join(session_dir, HIGHLIGHTS_FILENAME)
        stored = _read_json(path)
        rebuilt = _replay_operations(session_id, operations)
        if stored == rebuilt:
            return copy.deepcopy(stored)
        if operations or os.path.exists(path):
            _write_json_atomic(path, rebuilt)
        return copy.deepcopy(rebuilt)


def _append_operation(
    session_dir,
    session_id,
    operation,
    payload,
    before_highlights,
    after_highlights,
    actor,
    reason,
):
    with _STORE_LOCK:
        store = read_highlight_store(session_dir, session_id)
        occurred_at = _now()
        record = {
            "schemaVersion": SCHEMA_VERSION,
            "operationId": _identifier("hop"),
            "sessionId": str(session_id),
            "sequence": int(store["lastSequence"]) + 1,
            "operation": operation,
            "occurredAt": occurred_at,
            "actor": str(actor or "system").strip()[:80] or "system",
            "reason": str(reason or "").strip()[:500],
            "payload": copy.deepcopy(payload),
            "beforeHighlights": copy.deepcopy(before_highlights),
            "afterHighlights": copy.deepcopy(after_highlights),
        }
        os.makedirs(session_dir, exist_ok=True)
        history_path = os.path.join(session_dir, OPERATIONS_FILENAME)
        with open(history_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        for highlight_id, highlight in after_highlights.items():
            store["highlights"][highlight_id] = copy.deepcopy(highlight)
        store["lastSequence"] = record["sequence"]
        store["updatedAt"] = occurred_at
        _write_json_atomic(os.path.join(session_dir, HIGHLIGHTS_FILENAME), store)
        return copy.deepcopy(record)


def _highlight_or_error(store, highlight_id):
    highlight_id = str(highlight_id or "").strip()
    highlight = store["highlights"].get(highlight_id)
    if not highlight:
        raise ValueError(f"Unknown highlightId: {highlight_id or '(empty)' }.")
    return highlight_id, copy.deepcopy(highlight)


def _apply_changes(highlight, changes, occurred_at):
    if not isinstance(changes, dict) or not changes:
        raise ValueError("changes must be a non-empty object.")
    unknown = set(changes) - _EDITABLE_FIELDS
    if unknown:
        raise ValueError(f"Unsupported highlight fields: {', '.join(sorted(unknown))}.")
    updated = copy.deepcopy(highlight)
    if "categories" in changes:
        updated["categories"] = _categories(changes["categories"])
    if "confidence" in changes:
        updated["confidence"] = _choice(
            changes["confidence"], "confidence", _CONFIDENCE_LEVELS, "unknown"
        )
    if "summary" in changes:
        updated["summary"] = _text(changes["summary"], "summary", 1200)
    if "participants" in changes:
        updated["participants"] = _string_list(changes["participants"], "participants", 30)
    if "sourceChunks" in changes:
        updated["sourceChunks"] = sorted(
            set(updated["sourceChunks"] + _source_chunks(changes["sourceChunks"]))
        )
    if "relatedEventIds" in changes:
        updated["relatedEventIds"] = _related_event_ids(changes["relatedEventIds"])
    if "reviewStatus" in changes:
        updated["reviewStatus"] = _choice(
            changes["reviewStatus"], "reviewStatus", _REVIEW_STATUSES, "pending"
        )
    updated["firstChunk"] = min(updated["sourceChunks"])
    updated["lastChunk"] = max(updated["sourceChunks"])
    updated["revision"] = int(updated.get("revision") or 0) + 1
    updated["updatedAt"] = occurred_at
    return updated


def create_highlight(session_dir, session_id, highlight, actor="system", reason=""):
    if not isinstance(highlight, dict):
        raise ValueError("highlight must be an object.")
    source_chunks = _source_chunks(highlight.get("sourceChunks"), required=True)
    occurred_at = _now()
    highlight_id = _identifier("hlt")
    current = {
        "highlightId": highlight_id,
        "categories": _categories(highlight.get("categories")),
        "confidence": _choice(
            highlight.get("confidence"), "confidence", _CONFIDENCE_LEVELS, "unknown"
        ),
        "firstChunk": min(source_chunks),
        "lastChunk": max(source_chunks),
        "sourceChunks": source_chunks,
        "summary": _text(highlight.get("summary"), "summary", 1200),
        "participants": _string_list(highlight.get("participants"), "participants", 30),
        "relatedEventIds": _related_event_ids(highlight.get("relatedEventIds")),
        "reviewStatus": _choice(
            highlight.get("reviewStatus"), "reviewStatus", _REVIEW_STATUSES, "pending"
        ),
        "revision": 1,
        "createdAt": occurred_at,
        "updatedAt": occurred_at,
    }
    return _append_operation(
        session_dir,
        session_id,
        "CREATE_HIGHLIGHT",
        {"highlightId": highlight_id},
        {},
        {highlight_id: current},
        actor,
        reason,
    )


def update_highlight(session_dir, session_id, highlight_id, changes, actor="system", reason=""):
    with _STORE_LOCK:
        store = read_highlight_store(session_dir, session_id)
        highlight_id, before = _highlight_or_error(store, highlight_id)
        after = _apply_changes(before, changes, _now())
        return _append_operation(
            session_dir,
            session_id,
            "UPDATE_HIGHLIGHT",
            {"highlightId": highlight_id, "changes": copy.deepcopy(changes)},
            {highlight_id: before},
            {highlight_id: after},
            actor,
            reason,
        )


def _set_review_status(session_dir, session_id, highlight_id, review_status, operation, actor, reason):
    with _STORE_LOCK:
        store = read_highlight_store(session_dir, session_id)
        highlight_id, before = _highlight_or_error(store, highlight_id)
        after = _apply_changes(before, {"reviewStatus": review_status}, _now())
        return _append_operation(
            session_dir,
            session_id,
            operation,
            {"highlightId": highlight_id},
            {highlight_id: before},
            {highlight_id: after},
            actor,
            reason,
        )


def apply_highlight_operation(session_dir, session_id, request):
    """Apply a model or future human-review operation to the highlight store."""
    if not isinstance(request, dict):
        raise ValueError("Request must be an object.")
    operation = str(request.get("operation") or "").strip().upper()
    actor = request.get("actor") or "manual"
    reason = request.get("reason") or ""
    if operation == "CREATE_HIGHLIGHT":
        record = create_highlight(
            session_dir, session_id, request.get("highlight"), actor, reason
        )
    elif operation == "UPDATE_HIGHLIGHT":
        record = update_highlight(
            session_dir,
            session_id,
            request.get("highlightId"),
            request.get("changes"),
            actor,
            reason,
        )
    elif operation == "KEEP_HIGHLIGHT":
        record = _set_review_status(
            session_dir,
            session_id,
            request.get("highlightId"),
            "kept",
            operation,
            actor,
            reason,
        )
    elif operation == "REJECT_HIGHLIGHT":
        record = _set_review_status(
            session_dir,
            session_id,
            request.get("highlightId"),
            "rejected",
            operation,
            actor,
            reason,
        )
    else:
        raise ValueError(
            "operation must be CREATE_HIGHLIGHT, UPDATE_HIGHLIGHT, KEEP_HIGHLIGHT, or REJECT_HIGHLIGHT."
        )
    return {"operation": record, "highlightStore": read_highlight_store(session_dir, session_id)}


def apply_highlight_operations_batch(
    session_dir, session_id, operations, batch_metadata=None, actor="reconciliation"
):
    """Validate every highlight operation, then commit one replayable history record."""
    if not isinstance(operations, list):
        raise ValueError("highlightOperations must be a list.")
    if len(operations) > 100:
        raise ValueError("A reconciliation batch may contain at most 100 highlight operations.")
    if not isinstance(batch_metadata, dict):
        batch_metadata = {}

    with _STORE_LOCK:
        before_store = read_highlight_store(session_dir, session_id)
        history_path = os.path.join(session_dir, OPERATIONS_FILENAME)
        staged_records = []
        with tempfile.TemporaryDirectory(prefix="dnd-highlight-batch-") as staging_dir:
            if os.path.isfile(history_path):
                shutil.copyfile(history_path, os.path.join(staging_dir, OPERATIONS_FILENAME))
            for requested in operations:
                if not isinstance(requested, dict):
                    raise ValueError("Every highlight operation must be an object.")
                staged_request = copy.deepcopy(requested)
                staged_request["actor"] = actor
                result = apply_highlight_operation(staging_dir, session_id, staged_request)
                record = result["operation"]
                staged_records.append({
                    "operation": record["operation"],
                    "payload": record["payload"],
                    "beforeHighlights": record["beforeHighlights"],
                    "afterHighlights": record["afterHighlights"],
                })
            after_store = read_highlight_store(staging_dir, session_id)

        before = before_store["highlights"]
        after = after_store["highlights"]
        changed_ids = sorted(
            highlight_id
            for highlight_id in set(before) | set(after)
            if before.get(highlight_id) != after.get(highlight_id)
        )
        record = _append_operation(
            session_dir,
            session_id,
            "RECONCILIATION_HIGHLIGHT_BATCH",
            {
                "batchMetadata": copy.deepcopy(batch_metadata),
                "requestedOperations": copy.deepcopy(operations),
                "appliedOperations": staged_records,
            },
            {
                highlight_id: copy.deepcopy(before[highlight_id])
                for highlight_id in changed_ids
                if highlight_id in before
            },
            {
                highlight_id: copy.deepcopy(after[highlight_id])
                for highlight_id in changed_ids
                if highlight_id in after
            },
            actor,
            "Validated structured reconciliation highlight batch.",
        )
        return {
            "operation": record,
            "appliedOperations": staged_records,
            "highlightStore": read_highlight_store(session_dir, session_id),
        }


def find_reconciliation_highlight_batch(session_dir, finalization_id):
    wanted = str(finalization_id or "").strip()
    if not wanted:
        return None
    for record in reversed(read_highlight_operations(session_dir)):
        if record.get("operation") != "RECONCILIATION_HIGHLIGHT_BATCH":
            continue
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        metadata = (
            payload.get("batchMetadata")
            if isinstance(payload.get("batchMetadata"), dict)
            else {}
        )
        if str(metadata.get("finalizationId") or "") == wanted:
            return copy.deepcopy(record)
    return None
