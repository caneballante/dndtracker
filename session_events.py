"""Persistent structured session events, kept separate from legacy notes.jsonl."""

import copy
import json
import os
import shutil
import tempfile
import threading
import time
import uuid


SCHEMA_VERSION = 1
EVENTS_FILENAME = "session_events.json"
OPERATIONS_FILENAME = "session_event_operations.jsonl"

_STORE_LOCK = threading.RLock()
_EVENT_STATUSES = {"active", "unresolved", "resolved", "superseded"}
_IMPORTANCE_LEVELS = {"low", "medium", "high", "critical"}
_CONFIDENCE_LEVELS = {"unknown", "low", "medium", "high"}
_REVIEW_STATUSES = {"pending", "kept", "rejected"}
_EDITABLE_FIELDS = {
    "type", "status", "importance", "confidence", "summary", "facts", "entities", "sourceChunks",
    "reviewStatus",
}


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


def _facts(value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("facts must be a list of strings.")
    result = []
    for item in value:
        fact = str(item or "").strip()
        if fact and fact not in result:
            result.append(fact)
    return result


def _entities(value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("entities must be a list.")
    result = []
    seen = set()
    for item in value:
        if isinstance(item, str):
            entity = item.strip()
            key = ("name", entity.lower())
        elif isinstance(item, dict):
            entity = {str(k): v for k, v in item.items() if str(k).strip() and v not in (None, "")}
            if not entity:
                continue
            key = ("object", json.dumps(entity, sort_keys=True, separators=(",", ":")))
        else:
            raise ValueError("entities entries must be names or objects.")
        if entity and key not in seen:
            seen.add(key)
            result.append(entity)
    return result


def _combined_unique(left, right):
    result = copy.deepcopy(left)
    seen = {json.dumps(item, sort_keys=True, separators=(",", ":")) for item in result}
    for item in right:
        key = json.dumps(item, sort_keys=True, separators=(",", ":"))
        if key not in seen:
            seen.add(key)
            result.append(copy.deepcopy(item))
    return result


def _empty_store(session_id):
    return {
        "schemaVersion": SCHEMA_VERSION,
        "sessionId": str(session_id),
        "lastSequence": 0,
        "updatedAt": None,
        "events": {},
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


def read_event_operations(session_dir):
    """Return the immutable operation history in append order."""
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
                    raise ValueError(f"Invalid event history at line {line_number}: {exc.msg}.") from None
                if not isinstance(operation, dict):
                    raise ValueError(f"Invalid event history at line {line_number}: expected an object.")
                operations.append(operation)
    except FileNotFoundError:
        pass
    return operations


def _replay_operations(session_id, operations):
    store = _empty_store(session_id)
    expected_sequence = 1
    for operation in operations:
        sequence = operation.get("sequence")
        if sequence != expected_sequence:
            raise ValueError(f"Invalid event history sequence: expected {expected_sequence}, got {sequence}.")
        if str(operation.get("sessionId") or "") != str(session_id):
            raise ValueError("Event history belongs to a different session.")
        after_events = operation.get("afterEvents")
        if not isinstance(after_events, dict):
            raise ValueError(f"Invalid event history at sequence {sequence}: afterEvents is required.")
        for event_id, event in after_events.items():
            store["events"][event_id] = copy.deepcopy(event)
        store["lastSequence"] = sequence
        store["updatedAt"] = operation.get("occurredAt")
        expected_sequence += 1
    return store


def read_event_store(session_dir, session_id):
    """Read current state, rebuilding it from history after an interrupted write."""
    with _STORE_LOCK:
        operations = read_event_operations(session_dir)
        path = os.path.join(session_dir, EVENTS_FILENAME)
        stored = _read_json(path)
        rebuilt = _replay_operations(session_id, operations)
        if stored == rebuilt:
            return copy.deepcopy(stored)
        if operations or os.path.exists(path):
            _write_json_atomic(path, rebuilt)
        return copy.deepcopy(rebuilt)


def _append_operation(session_dir, session_id, operation, payload, before_events, after_events, actor, reason):
    with _STORE_LOCK:
        store = read_event_store(session_dir, session_id)
        occurred_at = _now()
        record = {
            "schemaVersion": SCHEMA_VERSION,
            "operationId": _identifier("op"),
            "sessionId": str(session_id),
            "sequence": int(store["lastSequence"]) + 1,
            "operation": operation,
            "occurredAt": occurred_at,
            "actor": str(actor or "system").strip()[:80] or "system",
            "reason": str(reason or "").strip()[:500],
            "payload": copy.deepcopy(payload),
            "beforeEvents": copy.deepcopy(before_events),
            "afterEvents": copy.deepcopy(after_events),
        }
        os.makedirs(session_dir, exist_ok=True)
        history_path = os.path.join(session_dir, OPERATIONS_FILENAME)
        with open(history_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        for event_id, event in after_events.items():
            store["events"][event_id] = copy.deepcopy(event)
        store["lastSequence"] = record["sequence"]
        store["updatedAt"] = occurred_at
        _write_json_atomic(os.path.join(session_dir, EVENTS_FILENAME), store)
        return copy.deepcopy(record)


def _event_or_error(store, event_id):
    event_id = str(event_id or "").strip()
    event = store["events"].get(event_id)
    if not event:
        raise ValueError(f"Unknown eventId: {event_id or '(empty)'}.")
    return event_id, copy.deepcopy(event)


def _apply_changes(event, changes, occurred_at):
    if not isinstance(changes, dict) or not changes:
        raise ValueError("changes must be a non-empty object.")
    unknown = set(changes) - _EDITABLE_FIELDS
    if unknown:
        raise ValueError(f"Unsupported event fields: {', '.join(sorted(unknown))}.")
    updated = copy.deepcopy(event)
    if "type" in changes:
        updated["type"] = _text(changes["type"], "type", 80)
    if "status" in changes:
        updated["status"] = _choice(changes["status"], "status", _EVENT_STATUSES, "unresolved")
    if "importance" in changes:
        updated["importance"] = _choice(changes["importance"], "importance", _IMPORTANCE_LEVELS, "medium")
    if "confidence" in changes:
        updated["confidence"] = _choice(changes["confidence"], "confidence", _CONFIDENCE_LEVELS, "unknown")
    if "reviewStatus" in changes:
        updated["reviewStatus"] = _choice(
            changes["reviewStatus"], "reviewStatus", _REVIEW_STATUSES, "pending"
        )
    if "summary" in changes:
        updated["summary"] = _text(changes["summary"], "summary", 2000)
    if "facts" in changes:
        updated["facts"] = _facts(changes["facts"])
    if "entities" in changes:
        updated["entities"] = _entities(changes["entities"])
    if "sourceChunks" in changes:
        updated["sourceChunks"] = sorted(set(updated["sourceChunks"] + _source_chunks(changes["sourceChunks"])))
    updated["firstChunk"] = min(updated["sourceChunks"])
    updated["lastChunk"] = max(updated["sourceChunks"])
    updated["revision"] = int(updated.get("revision") or 0) + 1
    updated["updatedAt"] = occurred_at
    return updated


def create_event(session_dir, session_id, event, actor="system", reason=""):
    if not isinstance(event, dict):
        raise ValueError("event must be an object.")
    source_chunks = _source_chunks(event.get("sourceChunks"), required=True)
    occurred_at = _now()
    event_id = _identifier("evt")
    current = {
        "eventId": event_id,
        "type": _text(event.get("type") or "other", "type", 80),
        "status": _choice(event.get("status"), "status", _EVENT_STATUSES, "unresolved"),
        "importance": _choice(event.get("importance"), "importance", _IMPORTANCE_LEVELS, "medium"),
        "confidence": _choice(event.get("confidence"), "confidence", _CONFIDENCE_LEVELS, "unknown"),
        "reviewStatus": _choice(
            event.get("reviewStatus"), "reviewStatus", _REVIEW_STATUSES, "pending"
        ),
        "firstChunk": min(source_chunks),
        "lastChunk": max(source_chunks),
        "sourceChunks": source_chunks,
        "summary": _text(event.get("summary"), "summary", 2000),
        "facts": _facts(event.get("facts")),
        "entities": _entities(event.get("entities")),
        "mergedFrom": [],
        "supersededBy": [],
        "revision": 1,
        "createdAt": occurred_at,
        "updatedAt": occurred_at,
    }
    record = _append_operation(
        session_dir, session_id, "CREATE_EVENT", {"eventId": event_id}, {}, {event_id: current}, actor, reason
    )
    return record


def update_event(session_dir, session_id, event_id, changes, actor="system", reason=""):
    with _STORE_LOCK:
        store = read_event_store(session_dir, session_id)
        event_id, before = _event_or_error(store, event_id)
        after = _apply_changes(before, changes, _now())
        return _append_operation(
            session_dir, session_id, "UPDATE_EVENT", {"eventId": event_id, "changes": changes},
            {event_id: before}, {event_id: after}, actor, reason
        )


def merge_events(session_dir, session_id, target_event_id, source_event_ids, changes=None, actor="system", reason=""):
    if not isinstance(source_event_ids, list) or not source_event_ids:
        raise ValueError("sourceEventIds must contain at least one eventId.")
    with _STORE_LOCK:
        store = read_event_store(session_dir, session_id)
        target_event_id, target = _event_or_error(store, target_event_id)
        source_ids = []
        sources = []
        for raw_id in source_event_ids:
            source_id, source = _event_or_error(store, raw_id)
            if source_id == target_event_id:
                raise ValueError("A merged source cannot also be the target event.")
            if source_id not in source_ids:
                source_ids.append(source_id)
                sources.append(source)

        occurred_at = _now()
        before = {target_event_id: copy.deepcopy(target)}
        after = {}
        merged_target = copy.deepcopy(target)
        for source_id, source in zip(source_ids, sources):
            before[source_id] = copy.deepcopy(source)
            merged_target["sourceChunks"] = sorted(set(merged_target["sourceChunks"] + source["sourceChunks"]))
            merged_target["facts"] = _combined_unique(merged_target["facts"], source["facts"])
            merged_target["entities"] = _combined_unique(merged_target["entities"], source["entities"])
            merged_target["mergedFrom"] = _combined_unique(
                merged_target.get("mergedFrom") or [], [source_id] + (source.get("mergedFrom") or [])
            )
            superseded = copy.deepcopy(source)
            superseded["status"] = "superseded"
            superseded["supersededBy"] = _combined_unique(superseded.get("supersededBy") or [], [target_event_id])
            superseded["revision"] = int(superseded.get("revision") or 0) + 1
            superseded["updatedAt"] = occurred_at
            after[source_id] = superseded
        merged_target["firstChunk"] = min(merged_target["sourceChunks"])
        merged_target["lastChunk"] = max(merged_target["sourceChunks"])
        if changes:
            merged_target = _apply_changes(merged_target, changes, occurred_at)
        else:
            merged_target["revision"] = int(merged_target.get("revision") or 0) + 1
            merged_target["updatedAt"] = occurred_at
        after[target_event_id] = merged_target
        return _append_operation(
            session_dir, session_id, "MERGE_EVENTS",
            {"targetEventId": target_event_id, "sourceEventIds": source_ids, "changes": changes or {}},
            before, after, actor, reason
        )


def mark_resolved(session_dir, session_id, event_id, actor="system", reason=""):
    return _set_status(session_dir, session_id, event_id, "resolved", "MARK_RESOLVED", None, actor, reason)


def mark_superseded(session_dir, session_id, event_id, superseded_by=None, actor="system", reason=""):
    return _set_status(
        session_dir, session_id, event_id, "superseded", "MARK_SUPERSEDED", superseded_by, actor, reason
    )


def _set_review_status(session_dir, session_id, event_id, review_status, operation, actor, reason):
    with _STORE_LOCK:
        store = read_event_store(session_dir, session_id)
        event_id, before = _event_or_error(store, event_id)
        after = _apply_changes(before, {"reviewStatus": review_status}, _now())
        return _append_operation(
            session_dir,
            session_id,
            operation,
            {"eventId": event_id},
            {event_id: before},
            {event_id: after},
            actor,
            reason,
        )


def _set_status(session_dir, session_id, event_id, status, operation, superseded_by, actor, reason):
    with _STORE_LOCK:
        store = read_event_store(session_dir, session_id)
        event_id, before = _event_or_error(store, event_id)
        after = _apply_changes(before, {"status": status}, _now())
        payload = {"eventId": event_id}
        if superseded_by:
            replacement_id, _ = _event_or_error(store, superseded_by)
            after["supersededBy"] = _combined_unique(after.get("supersededBy") or [], [replacement_id])
            payload["supersededBy"] = replacement_id
        return _append_operation(
            session_dir, session_id, operation, payload, {event_id: before}, {event_id: after}, actor, reason
        )


def apply_event_operation(session_dir, session_id, request):
    """Dispatch the operation shape that later reconciliation and review paths can share."""
    if not isinstance(request, dict):
        raise ValueError("Request must be an object.")
    operation = str(request.get("operation") or "").strip().upper()
    actor = request.get("actor") or "manual"
    reason = request.get("reason") or ""
    if operation == "CREATE_EVENT":
        record = create_event(session_dir, session_id, request.get("event"), actor, reason)
    elif operation == "UPDATE_EVENT":
        record = update_event(session_dir, session_id, request.get("eventId"), request.get("changes"), actor, reason)
    elif operation == "MERGE_EVENTS":
        record = merge_events(
            session_dir, session_id, request.get("targetEventId"), request.get("sourceEventIds"),
            request.get("changes"), actor, reason
        )
    elif operation == "MARK_RESOLVED":
        record = mark_resolved(session_dir, session_id, request.get("eventId"), actor, reason)
    elif operation == "MARK_SUPERSEDED":
        record = mark_superseded(
            session_dir, session_id, request.get("eventId"), request.get("supersededBy"), actor, reason
        )
    elif operation == "KEEP_EVENT":
        record = _set_review_status(
            session_dir, session_id, request.get("eventId"), "kept", operation, actor, reason
        )
    elif operation == "REJECT_EVENT":
        record = _set_review_status(
            session_dir, session_id, request.get("eventId"), "rejected", operation, actor, reason
        )
    else:
        raise ValueError(
            "operation must be CREATE_EVENT, UPDATE_EVENT, MERGE_EVENTS, MARK_RESOLVED, "
            "MARK_SUPERSEDED, KEEP_EVENT, or REJECT_EVENT."
        )
    return {"operation": record, "eventStore": read_event_store(session_dir, session_id)}


def apply_event_operations_batch(session_dir, session_id, operations, batch_metadata=None, actor="reconciliation"):
    """Validate every operation in isolation, then commit one replayable history record."""
    if not isinstance(operations, list):
        raise ValueError("operations must be a list.")
    if len(operations) > 200:
        raise ValueError("A reconciliation batch may contain at most 200 operations.")
    if not isinstance(batch_metadata, dict):
        batch_metadata = {}

    with _STORE_LOCK:
        before_store = read_event_store(session_dir, session_id)
        history_path = os.path.join(session_dir, OPERATIONS_FILENAME)
        staged_records = []
        with tempfile.TemporaryDirectory(prefix="dnd-event-batch-") as staging_dir:
            if os.path.isfile(history_path):
                shutil.copyfile(history_path, os.path.join(staging_dir, OPERATIONS_FILENAME))
            for requested in operations:
                if not isinstance(requested, dict):
                    raise ValueError("Every reconciliation operation must be an object.")
                staged_request = copy.deepcopy(requested)
                staged_request["actor"] = actor
                result = apply_event_operation(staging_dir, session_id, staged_request)
                record = result["operation"]
                staged_records.append({
                    "operation": record["operation"],
                    "payload": record["payload"],
                    "beforeEvents": record["beforeEvents"],
                    "afterEvents": record["afterEvents"],
                })
            after_store = read_event_store(staging_dir, session_id)

        before_events = before_store["events"]
        after_events = after_store["events"]
        changed_ids = sorted(
            event_id for event_id in set(before_events) | set(after_events)
            if before_events.get(event_id) != after_events.get(event_id)
        )
        record = _append_operation(
            session_dir=session_dir,
            session_id=session_id,
            operation="RECONCILIATION_BATCH",
            payload={
                "batchMetadata": copy.deepcopy(batch_metadata),
                "requestedOperations": copy.deepcopy(operations),
                "appliedOperations": staged_records,
            },
            before_events={event_id: copy.deepcopy(before_events[event_id]) for event_id in changed_ids if event_id in before_events},
            after_events={event_id: copy.deepcopy(after_events[event_id]) for event_id in changed_ids if event_id in after_events},
            actor=actor,
            reason="Validated structured reconciliation batch.",
        )
        return {
            "operation": record,
            "appliedOperations": staged_records,
            "eventStore": read_event_store(session_dir, session_id),
        }


def find_reconciliation_batch(session_dir, finalization_id):
    wanted = str(finalization_id or "").strip()
    if not wanted:
        return None
    for record in reversed(read_event_operations(session_dir)):
        if record.get("operation") != "RECONCILIATION_BATCH":
            continue
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        metadata = payload.get("batchMetadata") if isinstance(payload.get("batchMetadata"), dict) else {}
        if str(metadata.get("finalizationId") or "") == wanted:
            return copy.deepcopy(record)
    return None
