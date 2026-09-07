"""Canonical Session Memory edits and DungeonShare publication records.

The reconciliation event/highlight stores remain generated source material.  This
module applies append-only, exact-ID human overrides when producing the effective
canonical Session Memory.
"""

import copy
from datetime import date, datetime
import hashlib
import json
import os
import re
import threading
import time
import uuid

from session_reconciliation import (
    CONFIDENCE_LEVELS,
    EVENT_TYPES,
    HIGHLIGHT_CATEGORIES,
    IMPORTANCE_LEVELS,
)


SCHEMA_VERSION = 1
EDITS_FILENAME = "session_memory_edits.jsonl"
PUBLICATIONS_FILENAME = "session_memory_publications.jsonl"
PUBLISH_SCHEMA_VERSION = 1
PUBLISH_KIND = "dungeontracker_session_memory"

_LOCK = threading.RLock()
_EVENT_FIELDS = {
    "summary",
    "facts",
    "entities",
    "type",
    "status",
    "importance",
    "confidence",
}
_HIGHLIGHT_FIELDS = {
    "summary",
    "categories",
    "participants",
    "confidence",
}
_EVENT_STATUSES = {"active", "unresolved", "resolved"}
_EDIT_OPERATIONS = {
    "UPDATE_EVENT": ("event", "update"),
    "REMOVE_EVENT": ("event", "remove"),
    "RESTORE_EVENT": ("event", "restore"),
    "UPDATE_HIGHLIGHT": ("highlight", "update"),
    "REMOVE_HIGHLIGHT": ("highlight", "remove"),
    "RESTORE_HIGHLIGHT": ("highlight", "restore"),
}
_ID_PATTERNS = {
    "event": re.compile(r"^evt_[0-9a-f]{32}$"),
    "highlight": re.compile(r"^hlt_[0-9a-f]{32}$"),
}


def _identifier(prefix):
    return f"{prefix}_{uuid.uuid4().hex}"


def _stable_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value):
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _append_jsonl(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _read_jsonl(path, label):
    records = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"Invalid {label} at line {line_number}: {exc.msg}."
                    ) from None
                if not isinstance(record, dict):
                    raise ValueError(
                        f"Invalid {label} at line {line_number}: expected an object."
                    )
                records.append(record)
    except FileNotFoundError:
        pass
    return records


def read_edit_operations(session_dir):
    """Return canonical-memory edit/rebuild records in append order."""
    return _read_jsonl(os.path.join(session_dir, EDITS_FILENAME), "Session Memory edit history")


def read_publication_operations(session_dir):
    """Return DungeonShare publication attempts in append order."""
    return _read_jsonl(
        os.path.join(session_dir, PUBLICATIONS_FILENAME),
        "Session Memory publication history",
    )


def _built(event_store, highlight_store, built):
    if built is not None:
        return bool(built)
    return bool(
        (event_store.get("events") if isinstance(event_store, dict) else {})
        or (highlight_store.get("highlights") if isinstance(highlight_store, dict) else {})
    )


def _validate_history(session_id, records, baseline_revision):
    expected_sequence = 1
    previous_revision = baseline_revision
    for record in records:
        if record.get("sequence") != expected_sequence:
            raise ValueError(
                "Invalid Session Memory edit history sequence: "
                f"expected {expected_sequence}, got {record.get('sequence')}."
            )
        if str(record.get("sessionId") or "") != str(session_id):
            raise ValueError("Session Memory edit history belongs to a different session.")
        revision = record.get("memoryRevision")
        if revision != previous_revision + 1:
            raise ValueError(
                "Invalid Session Memory revision sequence: "
                f"expected {previous_revision + 1}, got {revision}."
            )
        actions = record.get("actions")
        if not isinstance(actions, list):
            raise ValueError(
                f"Invalid Session Memory edit history at sequence {expected_sequence}: actions are required."
            )
        expected_sequence += 1
        previous_revision = revision
    return previous_revision


def _override_state(records):
    state = {"events": {}, "highlights": {}}
    for record in records:
        if record.get("kind") != "edit":
            continue
        for action in record.get("actions") or []:
            if not isinstance(action, dict):
                continue
            target = action.get("target")
            target_id = str(action.get("targetId") or "")
            bucket = "events" if target == "event" else "highlights" if target == "highlight" else ""
            if not bucket or not target_id:
                continue
            after = action.get("overrideAfter")
            if isinstance(after, dict):
                state[bucket][target_id] = copy.deepcopy(after)
            else:
                state[bucket].pop(target_id, None)
    return state


def _generated_digest(event_store, highlight_store):
    return _digest({
        "events": (event_store or {}).get("events") or {},
        "highlights": (highlight_store or {}).get("highlights") or {},
    })


def _public_event_content(event):
    return {
        "id": _identifier_text(event.get("eventId"), "memory.events.id"),
        "type": _choice(event.get("type") or "other", "memory.events.type", set(EVENT_TYPES)),
        "status": _choice(event.get("status") or "unresolved", "memory.events.status", _EVENT_STATUSES),
        "importance": _choice(event.get("importance") or "medium", "memory.events.importance", set(IMPORTANCE_LEVELS)),
        "confidence": _choice(event.get("confidence") or "unknown", "memory.events.confidence", set(CONFIDENCE_LEVELS)),
        "summary": _required_text(event.get("summary"), "memory.events.summary", 2000),
        "facts": _string_list(_display_strings(event.get("facts") or []), "memory.events.facts", 50, 1000),
        "entities": _string_list(_entity_display_strings(event.get("entities") or []), "memory.events.entities", 50, 240),
    }


def _public_highlight_content(highlight):
    categories = [
        _choice(item, "memory.highlights.categories", set(HIGHLIGHT_CATEGORIES))
        for item in _string_list(
            _display_strings(highlight.get("categories") or []),
            "memory.highlights.categories",
            4,
            80,
        )
    ]
    if not categories:
        raise ValueError("memory.highlights.categories must contain at least one entry.")
    return {
        "id": _identifier_text(highlight.get("highlightId"), "memory.highlights.id"),
        "categories": categories,
        "confidence": _choice(highlight.get("confidence") or "unknown", "memory.highlights.confidence", set(CONFIDENCE_LEVELS)),
        "summary": _required_text(highlight.get("summary"), "memory.highlights.summary", 1200),
        "participants": _string_list(_display_strings(highlight.get("participants") or []), "memory.highlights.participants", 30, 240),
        "relatedEventIds": [
            _identifier_text(item, "memory.highlights.relatedEventIds")
            for item in _string_list(
                _display_strings(highlight.get("relatedEventIds") or []),
                "memory.highlights.relatedEventIds",
                20,
                100,
            )
        ],
    }


def _audit_content(target, item):
    if not isinstance(item, dict):
        return None
    if target == "event":
        return {
            "id": str(item.get("eventId") or ""),
            "type": str(item.get("type") or "other"),
            "status": str(item.get("status") or "unresolved"),
            "importance": str(item.get("importance") or "medium"),
            "confidence": str(item.get("confidence") or "unknown"),
            "summary": str(item.get("summary") or ""),
            "facts": _display_strings(item.get("facts") or []),
            "entities": _entity_display_strings(item.get("entities") or []),
        }
    return {
        "id": str(item.get("highlightId") or ""),
        "categories": _display_strings(item.get("categories") or []),
        "confidence": str(item.get("confidence") or "unknown"),
        "summary": str(item.get("summary") or ""),
        "participants": _display_strings(item.get("participants") or []),
        "relatedEventIds": _display_strings(item.get("relatedEventIds") or []),
    }


def _apply_override(item, override):
    effective = copy.deepcopy(item)
    fields = override.get("fields") if isinstance(override, dict) else {}
    if isinstance(fields, dict):
        for key, value in fields.items():
            effective[key] = copy.deepcopy(value)
    return effective


def _removed_entry(target, target_id, item, reason, override=None):
    return {
        "target": target,
        "targetId": target_id,
        "reason": reason,
        "item": copy.deepcopy(item) if isinstance(item, dict) else None,
        "canRestore": reason in {"human_removed", "generated_rejected"},
        "baseGeneratedRevision": (
            override.get("baseGeneratedRevision") if isinstance(override, dict) else None
        ),
    }


def read_canonical_memory(
    session_dir,
    session_id,
    event_store,
    highlight_store,
    *,
    built=None,
    publication=True,
):
    """Merge generated stores with durable human patches without changing either store."""
    with _LOCK:
        event_store = event_store if isinstance(event_store, dict) else {}
        highlight_store = highlight_store if isinstance(highlight_store, dict) else {}
        generated_events = event_store.get("events") or {}
        generated_highlights = highlight_store.get("highlights") or {}
        is_built = _built(event_store, highlight_store, built)
        baseline_revision = 1 if is_built else 0
        records = read_edit_operations(session_dir)
        revision = _validate_history(session_id, records, baseline_revision)
        overrides = _override_state(records)
        events = {}
        highlights = {}
        removed_events = []
        removed_highlights = []
        conflicts = []

        for event_id in sorted(set(generated_events) | set(overrides["events"])):
            generated = generated_events.get(event_id)
            override = overrides["events"].get(event_id)
            if not isinstance(generated, dict):
                conflicts.append({
                    "target": "event",
                    "targetId": event_id,
                    "code": "missing_generated_item",
                    "baseGeneratedRevision": (
                        override.get("baseGeneratedRevision") if isinstance(override, dict) else None
                    ),
                    "currentGeneratedRevision": None,
                })
                fallback = (override or {}).get("baseItem") if isinstance(override, dict) else None
                removed_events.append(
                    _removed_entry("event", event_id, fallback, "missing_generated_item", override)
                )
                continue
            effective = _apply_override(generated, override or {})
            if isinstance(override, dict):
                base_revision = override.get("baseGeneratedRevision")
                current_revision = int(generated.get("revision") or 0)
                if base_revision is not None and int(base_revision) != current_revision:
                    conflicts.append({
                        "target": "event",
                        "targetId": event_id,
                        "code": "generated_revision_changed",
                        "baseGeneratedRevision": int(base_revision),
                        "currentGeneratedRevision": current_revision,
                    })
            if str(generated.get("status") or "").lower() == "superseded":
                removed_events.append(
                    _removed_entry("event", event_id, effective, "generated_superseded", override)
                )
            elif isinstance(override, dict) and override.get("removed") is True:
                removed_events.append(
                    _removed_entry("event", event_id, effective, "human_removed", override)
                )
            elif (
                str(generated.get("reviewStatus") or "").lower() == "rejected"
                and not (isinstance(override, dict) and override.get("removed") is False)
            ):
                removed_events.append(
                    _removed_entry("event", event_id, effective, "generated_rejected", override)
                )
            else:
                events[event_id] = effective

        for highlight_id in sorted(set(generated_highlights) | set(overrides["highlights"])):
            generated = generated_highlights.get(highlight_id)
            override = overrides["highlights"].get(highlight_id)
            if not isinstance(generated, dict):
                conflicts.append({
                    "target": "highlight",
                    "targetId": highlight_id,
                    "code": "missing_generated_item",
                    "baseGeneratedRevision": (
                        override.get("baseGeneratedRevision") if isinstance(override, dict) else None
                    ),
                    "currentGeneratedRevision": None,
                })
                fallback = (override or {}).get("baseItem") if isinstance(override, dict) else None
                removed_highlights.append(
                    _removed_entry(
                        "highlight", highlight_id, fallback, "missing_generated_item", override
                    )
                )
                continue
            effective = _apply_override(generated, override or {})
            if isinstance(override, dict):
                base_revision = override.get("baseGeneratedRevision")
                current_revision = int(generated.get("revision") or 0)
                if base_revision is not None and int(base_revision) != current_revision:
                    conflicts.append({
                        "target": "highlight",
                        "targetId": highlight_id,
                        "code": "generated_revision_changed",
                        "baseGeneratedRevision": int(base_revision),
                        "currentGeneratedRevision": current_revision,
                    })
            if isinstance(override, dict) and override.get("removed") is True:
                removed_highlights.append(
                    _removed_entry(
                        "highlight", highlight_id, effective, "human_removed", override
                    )
                )
            elif (
                str(generated.get("reviewStatus") or "").lower() == "rejected"
                and not (isinstance(override, dict) and override.get("removed") is False)
            ):
                removed_highlights.append(
                    _removed_entry(
                        "highlight", highlight_id, effective, "generated_rejected", override
                    )
                )
            else:
                highlights[highlight_id] = effective

        generated_digest = _generated_digest(event_store, highlight_store)
        last_edit = next(
            (record for record in reversed(records) if record.get("kind") == "edit"),
            None,
        )
        memory_digest = _digest({
            "sessionId": str(session_id),
            "revision": revision,
            "generatedDigest": generated_digest,
            "overrides": overrides,
        })
        result = {
            "schemaVersion": SCHEMA_VERSION,
            "sessionId": str(session_id),
            "built": is_built,
            "revision": revision,
            "digest": memory_digest,
            "generatedDigest": generated_digest,
            "hasHumanEdits": any(record.get("kind") == "edit" for record in records),
            "updatedAt": records[-1].get("occurredAt") if records else None,
            "lastEditedAt": last_edit.get("occurredAt") if last_edit else None,
            "events": events,
            "highlights": highlights,
            "removed": {
                "events": removed_events,
                "highlights": removed_highlights,
            },
            "conflicts": conflicts,
        }
        if publication:
            result["publication"] = publication_status(
                session_dir, revision, memory_digest
            )
        return result


def _required_text(value, field, max_length):
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required.")
    if len(text) > max_length:
        raise ValueError(f"{field} must be at most {max_length} characters.")
    return text


def _identifier_text(value, field):
    text = _required_text(value, field, 100)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", text):
        raise ValueError(
            f"{field} may contain only letters, numbers, dots, underscores, and hyphens."
        )
    return text


def _iso_date_text(value, field):
    text = _required_text(value, field, 10)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise ValueError(f"{field} must be an ISO date (YYYY-MM-DD).")
    try:
        date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"{field} must be an ISO date (YYYY-MM-DD).") from None
    return text


def _iso_datetime_text(value, field):
    text = _required_text(value, field, 80)
    if not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})",
        text,
    ):
        raise ValueError(f"{field} must be an ISO datetime with an offset.")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{field} must be an ISO datetime with an offset.") from None
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must be an ISO datetime with an offset.")
    return text


def _string_list(value, field, max_items, max_length=2000):
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list of strings.")
    result = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{field} must contain strings.")
        text = item.strip()
        if len(text) > max_length:
            raise ValueError(f"{field} entries must be at most {max_length} characters.")
        if text and text not in result:
            result.append(text)
    if len(result) > max_items:
        raise ValueError(f"{field} may contain at most {max_items} entries.")
    return result


def _choice(value, field, choices):
    text = str(value or "").strip().lower()
    if text not in choices:
        raise ValueError(f"{field} must be one of: {', '.join(sorted(choices))}.")
    return text


def _normalize_changes(target, changes):
    if not isinstance(changes, dict) or not changes:
        raise ValueError("changes must be a non-empty object.")
    allowed = _EVENT_FIELDS if target == "event" else _HIGHLIGHT_FIELDS
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError(
            f"Unsupported {target} fields: {', '.join(sorted(unknown))}."
        )
    normalized = {}
    for field, value in changes.items():
        if field == "summary":
            normalized[field] = _required_text(
                value, "summary", 2000 if target == "event" else 1200
            )
        elif field == "facts":
            normalized[field] = _string_list(value, "facts", 30, 1000)
        elif field == "entities":
            normalized[field] = _string_list(value, "entities", 30, 240)
        elif field == "type":
            normalized[field] = _choice(value, "type", set(EVENT_TYPES))
        elif field == "status":
            normalized[field] = _choice(value, "status", _EVENT_STATUSES)
        elif field == "importance":
            normalized[field] = _choice(value, "importance", set(IMPORTANCE_LEVELS))
        elif field == "confidence":
            normalized[field] = _choice(value, "confidence", set(CONFIDENCE_LEVELS))
        elif field == "categories":
            values = _string_list(value, "categories", 4, 80)
            if not values:
                raise ValueError("categories must contain at least one entry.")
            normalized[field] = [
                _choice(item, "categories", set(HIGHLIGHT_CATEGORIES)) for item in values
            ]
        elif field == "participants":
            normalized[field] = _string_list(value, "participants", 30, 240)
    return normalized


def _normalize_requested_operation(requested):
    if not isinstance(requested, dict):
        raise ValueError("Every Session Memory edit operation must be an object.")
    operation = str(requested.get("operation") or "").strip().upper()
    if operation not in _EDIT_OPERATIONS:
        raise ValueError(
            "operation must be UPDATE_EVENT, REMOVE_EVENT, RESTORE_EVENT, "
            "UPDATE_HIGHLIGHT, REMOVE_HIGHLIGHT, or RESTORE_HIGHLIGHT."
        )
    target, action = _EDIT_OPERATIONS[operation]
    id_field = "eventId" if target == "event" else "highlightId"
    expected = {"operation", id_field, "changes"} if action == "update" else {"operation", id_field}
    extra = set(requested) - expected
    missing = expected - set(requested)
    if missing:
        raise ValueError(
            f"{operation} is missing fields: {', '.join(sorted(missing))}."
        )
    if extra:
        raise ValueError(
            f"{operation} has unsupported fields: {', '.join(sorted(extra))}."
        )
    target_id = str(requested.get(id_field) or "").strip()
    if not _ID_PATTERNS[target].match(target_id):
        raise ValueError(f"Invalid {id_field}: {target_id or '(empty)' }.")
    result = {
        "operation": operation,
        "target": target,
        "action": action,
        "targetId": target_id,
    }
    if action == "update":
        result["changes"] = _normalize_changes(target, requested.get("changes"))
    return result


def apply_edit_transaction(
    session_dir,
    session_id,
    event_store,
    highlight_store,
    *,
    base_revision,
    base_digest,
    operations,
    built=None,
    reason="",
):
    """Validate and append one all-or-nothing human edit transaction."""
    with _LOCK:
        current = read_canonical_memory(
            session_dir,
            session_id,
            event_store,
            highlight_store,
            built=built,
            publication=False,
        )
        if not current["built"]:
            raise ValueError("Session Memory has not been built.")
        if isinstance(base_revision, bool):
            raise ValueError("baseRevision must be an integer.")
        try:
            supplied_revision = int(base_revision)
        except (TypeError, ValueError):
            raise ValueError("baseRevision must be an integer.") from None
        if supplied_revision != current["revision"]:
            raise ValueError(
                f"Session Memory revision is stale; expected {current['revision']}."
            )
        if not isinstance(base_digest, str) or base_digest != current["digest"]:
            raise ValueError("Session Memory digest is stale; reload before saving.")
        if not isinstance(operations, list) or not operations:
            raise ValueError("operations must contain at least one edit.")
        if len(operations) > 200:
            raise ValueError("A Session Memory save may contain at most 200 edits.")

        normalized = [_normalize_requested_operation(item) for item in operations]
        records = read_edit_operations(session_dir)
        baseline_revision = 1
        _validate_history(session_id, records, baseline_revision)
        overrides = _override_state(records)
        generated = {
            "event": (event_store or {}).get("events") or {},
            "highlight": (highlight_store or {}).get("highlights") or {},
        }
        working_items = {
            "event": copy.deepcopy(current["events"]),
            "highlight": copy.deepcopy(current["highlights"]),
        }
        removed_by_target = {
            "event": {
                item["targetId"]: item for item in current["removed"]["events"]
            },
            "highlight": {
                item["targetId"]: item for item in current["removed"]["highlights"]
            },
        }
        actions = []
        for item in normalized:
            target = item["target"]
            bucket = "events" if target == "event" else "highlights"
            target_id = item["targetId"]
            generated_item = generated[target].get(target_id)
            if not isinstance(generated_item, dict):
                raise ValueError(f"Unknown {target} ID: {target_id}.")
            if target == "event" and str(generated_item.get("status") or "").lower() == "superseded":
                raise ValueError("Superseded Events cannot be edited or restored.")

            before_override = copy.deepcopy(overrides[bucket].get(target_id))
            override = copy.deepcopy(before_override) if isinstance(before_override, dict) else {
                "fields": {},
                "baseGeneratedRevision": int(generated_item.get("revision") or 0),
                "baseItem": copy.deepcopy(generated_item),
            }
            override["baseGeneratedRevision"] = int(generated_item.get("revision") or 0)
            override["baseItem"] = copy.deepcopy(generated_item)
            effective_before = working_items[target].get(target_id)
            if effective_before is None:
                removed = removed_by_target[target].get(target_id) or {}
                effective_before = copy.deepcopy(removed.get("item") or generated_item)

            action = item["action"]
            if action == "update":
                fields = override.get("fields") if isinstance(override.get("fields"), dict) else {}
                fields = copy.deepcopy(fields)
                for field, value in item["changes"].items():
                    if generated_item.get(field) == value:
                        fields.pop(field, None)
                    else:
                        fields[field] = copy.deepcopy(value)
                override["fields"] = fields
            elif action == "remove":
                if override.get("removed") is True:
                    raise ValueError(f"{target.title()} is already removed from Session Memory.")
                override["removed"] = True
            elif action == "restore":
                was_removed = target_id in removed_by_target[target]
                if not was_removed:
                    raise ValueError(f"{target.title()} is not removed from Session Memory.")
                override["removed"] = False

            if not override.get("fields") and "removed" not in override:
                after_override = None
                overrides[bucket].pop(target_id, None)
            else:
                after_override = override
                overrides[bucket][target_id] = copy.deepcopy(override)

            effective_after = _apply_override(generated_item, override)
            if override.get("removed") is True:
                working_items[target].pop(target_id, None)
            else:
                working_items[target][target_id] = copy.deepcopy(effective_after)
            actions.append({
                "action": item["operation"],
                "target": target,
                "targetId": target_id,
                "before": _audit_content(target, effective_before),
                "after": _audit_content(target, effective_after),
                "changes": copy.deepcopy(item.get("changes") or {}),
                "overrideBefore": before_override,
                "overrideAfter": copy.deepcopy(after_override),
                "source": "human",
            })

        if all(action["overrideBefore"] == action["overrideAfter"] for action in actions):
            raise ValueError("Session Memory edits do not change the canonical record.")
        occurred_at = int(time.time())
        record = {
            "schemaVersion": SCHEMA_VERSION,
            "operationId": _identifier("memop"),
            "sessionId": str(session_id),
            "sequence": len(records) + 1,
            "memoryRevision": current["revision"] + 1,
            "occurredAt": occurred_at,
            "source": "human",
            "kind": "edit",
            "reason": str(reason or "").strip()[:500],
            "baseRevision": current["revision"],
            "baseDigest": current["digest"],
            "actions": actions,
        }
        _append_jsonl(os.path.join(session_dir, EDITS_FILENAME), record)
        return {
            "operation": copy.deepcopy(record),
            "memory": read_canonical_memory(
                session_dir,
                session_id,
                event_store,
                highlight_store,
                built=built,
            ),
        }


def record_rebuild(
    session_dir,
    session_id,
    event_store,
    highlight_store,
    *,
    finalization_id,
    rebuild_of_finalization_id="",
    before_generated_digest="",
    built=True,
):
    """Idempotently increment canonical revision after a successful generated rebuild."""
    with _LOCK:
        finalization_id = str(finalization_id or "").strip()
        if not finalization_id:
            raise ValueError("finalizationId is required for a rebuild marker.")
        records = read_edit_operations(session_dir)
        for record in records:
            if (
                record.get("kind") == "rebuild"
                and str(record.get("finalizationId") or "") == finalization_id
            ):
                return copy.deepcopy(record)
        current = read_canonical_memory(
            session_dir,
            session_id,
            event_store,
            highlight_store,
            built=built,
            publication=False,
        )
        after_digest = current["generatedDigest"]
        record = {
            "schemaVersion": SCHEMA_VERSION,
            "operationId": _identifier("memop"),
            "sessionId": str(session_id),
            "sequence": len(records) + 1,
            "memoryRevision": current["revision"] + 1,
            "occurredAt": int(time.time()),
            "source": "reconciliation",
            "kind": "rebuild",
            "reason": "Generated Session Memory was explicitly rebuilt.",
            "baseRevision": current["revision"],
            "baseDigest": current["digest"],
            "finalizationId": finalization_id,
            "rebuildOfFinalizationId": str(rebuild_of_finalization_id or ""),
            "actions": [{
                "action": "REBUILD_SESSION_MEMORY",
                "target": "session_memory",
                "targetId": str(session_id),
                "before": {"generatedDigest": str(before_generated_digest or "")},
                "after": {"generatedDigest": after_digest},
                "source": "reconciliation",
            }],
        }
        _append_jsonl(os.path.join(session_dir, EDITS_FILENAME), record)
        return copy.deepcopy(record)


def _display_strings(value):
    result = []
    for item in value if isinstance(value, list) else []:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _entity_display_strings(value):
    result = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = next(
                (
                    str(item.get(key) or "").strip()
                    for key in ("canonicalName", "displayName", "name", "label")
                    if str(item.get(key) or "").strip()
                ),
                "",
            )
        else:
            text = ""
        if text and text not in result:
            result.append(text)
    return result


def build_publish_payload(
    memory,
    *,
    campaign_id,
    campaign_name,
    dungeon_share_slug,
    world,
    session_id,
    session_title,
    session_date,
    published_at,
):
    """Build the deterministic, display-only DungeonShare v1 contract."""
    if not isinstance(memory, dict) or not memory.get("built"):
        raise ValueError("Session Memory has not been built.")
    if isinstance(memory.get("revision"), bool) or not isinstance(memory.get("revision"), int):
        raise ValueError("memory.revision must be a positive integer.")
    memory_revision = memory["revision"]
    if memory_revision <= 0:
        raise ValueError("memory.revision must be a positive integer.")
    slug = str(dungeon_share_slug or "").strip().lower()
    if not re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", slug) or len(slug) > 120:
        raise ValueError("Choose a valid DungeonShare campaign.")

    events = []
    source_events = memory.get("events") if isinstance(memory.get("events"), dict) else {}
    if len(source_events) > 200:
        raise ValueError("memory.events may contain at most 200 entries.")
    ordered_events = sorted(
        source_events.values(),
        key=lambda item: (int(item.get("firstChunk") or 0), str(item.get("eventId") or "")),
    )
    for event in ordered_events:
        if not isinstance(event, dict):
            continue
        status = str(event.get("status") or "unresolved").strip().lower()
        if status not in _EVENT_STATUSES:
            continue
        events.append(_public_event_content(event))
    if len({item["id"] for item in events}) != len(events):
        raise ValueError("memory.events contains duplicate Event IDs.")
    included_event_ids = {item["id"] for item in events}

    highlights = []
    source_highlights = (
        memory.get("highlights") if isinstance(memory.get("highlights"), dict) else {}
    )
    if len(source_highlights) > 100:
        raise ValueError("memory.highlights may contain at most 100 entries.")
    ordered_highlights = sorted(
        source_highlights.values(),
        key=lambda item: (
            int(item.get("firstChunk") or 0),
            str(item.get("highlightId") or ""),
        ),
    )
    for highlight in ordered_highlights:
        if not isinstance(highlight, dict):
            continue
        item = _public_highlight_content(highlight)
        item["relatedEventIds"] = [
            event_id for event_id in item["relatedEventIds"] if event_id in included_event_ids
        ]
        highlights.append(item)
    if len({item["id"] for item in highlights}) != len(highlights):
        raise ValueError("memory.highlights contains duplicate Highlight IDs.")

    world_payload = None
    if isinstance(world, dict) and str(world.get("id") or "").strip():
        world_payload = {
            "id": _identifier_text(world.get("id"), "world.id"),
            "name": _required_text(world.get("name") or world.get("id"), "world.name", 160),
        }
    return {
        "schemaVersion": PUBLISH_SCHEMA_VERSION,
        "kind": PUBLISH_KIND,
        "campaign": {
            "id": _identifier_text(campaign_id, "campaign.id"),
            "name": _required_text(campaign_name, "campaign.name", 160),
            "dungeonShareSlug": slug,
        },
        "world": world_payload,
        "session": {
            "id": _identifier_text(session_id, "session.id"),
            "title": _required_text(session_title, "session.title", 240),
            "date": _iso_date_text(session_date, "session.date"),
        },
        "memory": {
            "revision": memory_revision,
            "events": events,
            "highlights": highlights,
        },
        "publication": {
            "publishedAt": _iso_datetime_text(published_at, "publication.publishedAt"),
            "source": "DungeonTracker",
        },
    }


def record_publication(
    session_dir,
    session_id,
    *,
    succeeded,
    memory_revision,
    memory_digest,
    dungeon_share_slug,
    occurred_at=None,
    destination_session_id="",
    action="",
    state="",
    error="",
):
    """Append one non-secret publication outcome; prior successes remain intact."""
    with _LOCK:
        records = read_publication_operations(session_dir)
        record = {
            "schemaVersion": SCHEMA_VERSION,
            "publicationId": _identifier("pub"),
            "sessionId": str(session_id),
            "sequence": len(records) + 1,
            "occurredAt": int(time.time()) if occurred_at is None else int(occurred_at),
            "status": "success" if succeeded else "failure",
            "memoryRevision": int(memory_revision),
            "memoryDigest": str(memory_digest or ""),
            "payloadSchemaVersion": PUBLISH_SCHEMA_VERSION,
            "kind": PUBLISH_KIND,
            "dungeonShareSlug": str(dungeon_share_slug or "").strip().lower(),
            "destinationPath": "/api/session-memory",
            "destinationSessionId": str(destination_session_id or ""),
            "action": str(action or "").strip()[:80],
            "state": str(state or "").strip()[:80],
            "error": str(error or "").strip()[:500] if not succeeded else "",
        }
        _append_jsonl(os.path.join(session_dir, PUBLICATIONS_FILENAME), record)
        return copy.deepcopy(record)


def _safe_publication_record(record):
    allowed = {
        "publicationId",
        "occurredAt",
        "status",
        "memoryRevision",
        "memoryDigest",
        "payloadSchemaVersion",
        "kind",
        "dungeonShareSlug",
        "destinationPath",
        "destinationSessionId",
        "action",
        "state",
        "error",
    }
    return {key: copy.deepcopy(value) for key, value in record.items() if key in allowed}


def publication_status(session_dir, current_revision, current_digest):
    records = read_publication_operations(session_dir)
    last_attempt = records[-1] if records else None
    last_success = next(
        (record for record in reversed(records) if record.get("status") == "success"),
        None,
    )
    stale = bool(
        last_success
        and (
            int(last_success.get("memoryRevision") or -1) != int(current_revision)
            or str(last_success.get("memoryDigest") or "") != str(current_digest or "")
        )
    )
    failed = bool(last_attempt and last_attempt.get("status") == "failure")
    if failed:
        state = "needs_attention"
    elif not last_success:
        state = "not_published"
    elif stale:
        state = "stale"
    else:
        state = "current"
    return {
        "state": state,
        "currentRevision": int(current_revision),
        "publishedRevision": (
            int(last_success.get("memoryRevision")) if last_success else None
        ),
        "stale": stale,
        "updateAvailable": stale,
        "lastSuccessful": _safe_publication_record(last_success) if last_success else None,
        "lastAttempt": _safe_publication_record(last_attempt) if last_attempt else None,
    }
