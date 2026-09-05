"""Ordered transcript evidence and the session-finalization barrier."""

import copy
import json
import os
import re
import time
import uuid


SCHEMA_VERSION = 1
_AUDIO_CHUNK_RE = re.compile(r"^chunk_(\d+)\.(wav|webm|ogg|mp4|m4a|mp3)$", re.IGNORECASE)


def _final_chunk_index(value):
    if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
        raise ValueError("finalExpectedChunkIndex must be an integer.")
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValueError("finalExpectedChunkIndex must be an integer.") from None
    if result < -1:
        raise ValueError("finalExpectedChunkIndex must be -1 or greater.")
    return result


def read_ordered_transcript_entries(session_dir):
    """Return one current transcript entry per chunk, always ordered by chunkIndex."""
    path = os.path.join(session_dir, "transcripts.jsonl")
    by_chunk = {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                    chunk_index = int(entry.get("chunkIndex", -1))
                except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                if chunk_index < 0:
                    continue
                entry = dict(entry)
                entry["chunkIndex"] = chunk_index
                by_chunk[chunk_index] = entry
    except FileNotFoundError:
        pass
    return [by_chunk[index] for index in sorted(by_chunk)]


def _uploaded_chunk_indexes(session_dir, status):
    indexes = set()
    for item in (status or {}).get("chunks") or []:
        try:
            chunk_index = int(item.get("chunkIndex", -1))
        except (AttributeError, TypeError, ValueError):
            continue
        if chunk_index >= 0:
            indexes.add(chunk_index)
    try:
        for name in os.listdir(session_dir):
            match = _AUDIO_CHUNK_RE.match(name)
            if match:
                indexes.add(int(match.group(1)))
    except FileNotFoundError:
        pass
    return sorted(indexes)


def _failed_chunk_indexes(status, transcribed):
    failed = set()
    for item in (status or {}).get("chunks") or []:
        try:
            chunk_index = int(item.get("chunkIndex", -1))
        except (AttributeError, TypeError, ValueError):
            continue
        if chunk_index >= 0 and str(item.get("transcriptionStatus") or "").lower() == "failed":
            failed.add(chunk_index)
    failures = (status or {}).get("transcriptionFailures") or {}
    if isinstance(failures, dict):
        for raw_index in failures:
            try:
                chunk_index = int(raw_index)
            except (TypeError, ValueError):
                continue
            if chunk_index >= 0:
                failed.add(chunk_index)
    legacy_error = (status or {}).get("transcriptError") or {}
    if isinstance(legacy_error, dict):
        try:
            chunk_index = int(legacy_error.get("chunkIndex", -1))
        except (TypeError, ValueError):
            chunk_index = -1
        if chunk_index >= 0:
            failed.add(chunk_index)
    return sorted(failed - set(transcribed))


def build_ordered_evidence(session_dir, status=None, final_expected_chunk_index=None):
    """Build the evidence contract consumed by future reconciliation code."""
    status = status if isinstance(status, dict) else {}
    entries = read_ordered_transcript_entries(session_dir)
    transcribed = [int(entry["chunkIndex"]) for entry in entries]
    uploaded = _uploaded_chunk_indexes(session_dir, status)
    failed = _failed_chunk_indexes(status, transcribed)

    if final_expected_chunk_index is None:
        observed = uploaded + transcribed + failed
        boundary = max(observed) if observed else None
    else:
        boundary = _final_chunk_index(final_expected_chunk_index)

    expected = list(range(boundary + 1)) if boundary is not None and boundary >= 0 else []
    uploaded_set = set(uploaded)
    transcribed_set = set(transcribed)
    failed_set = set(failed)
    missing = [index for index in expected if index not in uploaded_set]
    pending = [
        index for index in expected
        if index in uploaded_set and index not in transcribed_set and index not in failed_set
    ]
    failed_expected = [index for index in expected if index in failed_set]
    unexpected = [] if boundary is None else sorted(
        index for index in uploaded_set | transcribed_set | failed_set if index > boundary
    )
    reconciliation_entries = entries if boundary is None else [
        entry for entry in entries if int(entry["chunkIndex"]) <= boundary
    ]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "entries": entries,
        "reconciliationEntries": reconciliation_entries,
        "finalExpectedChunkIndex": boundary,
        "highestUploadedChunk": max(uploaded) if uploaded else None,
        "highestTranscribedChunk": max(transcribed) if transcribed else None,
        "uploadedChunks": uploaded,
        "transcribedChunks": transcribed,
        "missingChunks": missing,
        "pendingChunks": pending,
        "failedChunks": failed_expected,
        "unexpectedChunks": unexpected,
    }


def _finalization_candidate(current, evidence, final_expected_chunk_index, now):
    current = copy.deepcopy(current) if isinstance(current, dict) else {}
    if current.get("state") in {"reconciliation_in_progress", "reconciliation_error", "finalized"}:
        if int(current.get("finalExpectedChunkIndex", -2)) != final_expected_chunk_index:
            raise ValueError("A claimed or finalized session cannot change its final expected chunk.")
        return current

    result = current or {
        "schemaVersion": SCHEMA_VERSION,
        "finalizationId": f"fin_{uuid.uuid4().hex}",
        "requestedAt": now,
        "recordingStoppedAt": now,
        "state": "recording_stopped",
    }
    result["schemaVersion"] = SCHEMA_VERSION
    result["finalExpectedChunkIndex"] = final_expected_chunk_index
    for field in (
        "highestUploadedChunk", "highestTranscribedChunk", "uploadedChunks", "transcribedChunks",
        "missingChunks", "pendingChunks", "failedChunks", "unexpectedChunks",
    ):
        result[field] = copy.deepcopy(evidence.get(field))

    error = ""
    if final_expected_chunk_index < 0:
        state = "error"
        error = "No final audio chunk was declared."
    elif result["unexpectedChunks"]:
        state = "error"
        error = "Chunks exist beyond the declared final expected chunk."
    elif result["missingChunks"]:
        state = "waiting_for_uploads"
    elif result["failedChunks"]:
        state = "error"
        error = "One or more required transcript jobs failed."
    elif result["pendingChunks"]:
        state = "waiting_for_transcription"
    else:
        state = "ready_for_reconciliation"

    result["state"] = state
    result["error"] = error
    if state == "ready_for_reconciliation":
        result["readyAt"] = result.get("readyAt") or now
    else:
        result.pop("readyAt", None)
    return result


def request_finalization(current, evidence, final_expected_chunk_index, now=None):
    """Create or idempotently refresh a finalization request."""
    requested_boundary = _final_chunk_index(final_expected_chunk_index)

    current = current if isinstance(current, dict) else {}
    existing_boundary = current.get("finalExpectedChunkIndex")
    if existing_boundary is not None:
        existing_boundary = int(existing_boundary)
        if current.get("state") == "finalized" and existing_boundary != requested_boundary:
            raise ValueError("A finalized session cannot change its final expected chunk.")
        requested_boundary = max(existing_boundary, requested_boundary)
    if evidence.get("finalExpectedChunkIndex") != requested_boundary:
        raise ValueError("Evidence must be evaluated using the final expected chunk.")

    timestamp = int(time.time()) if now is None else int(now)
    candidate = _finalization_candidate(current, evidence, requested_boundary, timestamp)
    comparable_current = {key: value for key, value in current.items() if key != "updatedAt"}
    comparable_candidate = {key: value for key, value in candidate.items() if key != "updatedAt"}
    if current and comparable_current == comparable_candidate:
        return copy.deepcopy(current)
    candidate["updatedAt"] = timestamp
    return candidate


def refresh_finalization(current, evidence, now=None):
    """Advance an existing barrier as uploads and transcripts settle; old sessions return None."""
    if not isinstance(current, dict) or current.get("finalExpectedChunkIndex") is None:
        return None
    return request_finalization(current, evidence, current["finalExpectedChunkIndex"], now=now)


def mark_finalized(current, reconciliation_id, now=None):
    """Future reconciliation can atomically close a ready barrier exactly once."""
    current = copy.deepcopy(current) if isinstance(current, dict) else {}
    reconciliation_id = str(reconciliation_id or "").strip()
    if not reconciliation_id:
        raise ValueError("reconciliationId is required.")
    if current.get("state") == "finalized":
        if current.get("reconciliationId") != reconciliation_id:
            raise ValueError("Session was already finalized by a different reconciliation run.")
        return current
    if current.get("state") != "reconciliation_in_progress":
        raise ValueError("Session has not been claimed for reconciliation.")
    if current.get("reconciliationId") != reconciliation_id:
        raise ValueError("Session was claimed by a different reconciliation run.")
    timestamp = int(time.time()) if now is None else int(now)
    current.update({
        "state": "finalized",
        "reconciliationId": reconciliation_id,
        "finalizedAt": timestamp,
        "updatedAt": timestamp,
        "error": "",
    })
    return current


def fail_reconciliation(current, reconciliation_id, error, now=None):
    """Release a claimed barrier into a deliberate, retryable error state."""
    current = copy.deepcopy(current) if isinstance(current, dict) else {}
    reconciliation_id = str(reconciliation_id or "").strip()
    if current.get("state") != "reconciliation_in_progress":
        raise ValueError("Session has no reconciliation run in progress.")
    if not reconciliation_id or current.get("reconciliationId") != reconciliation_id:
        raise ValueError("Session was claimed by a different reconciliation run.")
    timestamp = int(time.time()) if now is None else int(now)
    current.update({
        "state": "reconciliation_error",
        "error": str(error or "Reconciliation failed.").strip()[:2000],
        "reconciliationFailedAt": timestamp,
        "updatedAt": timestamp,
    })
    return current


def claim_reconciliation(current, reconciliation_id, now=None, allow_retry=False):
    """Claim a ready or deliberately retried barrier for exactly one run."""
    current = copy.deepcopy(current) if isinstance(current, dict) else {}
    reconciliation_id = str(reconciliation_id or "").strip()
    if not reconciliation_id:
        raise ValueError("reconciliationId is required.")
    if current.get("state") in {"reconciliation_in_progress", "finalized"}:
        if current.get("reconciliationId") != reconciliation_id:
            raise ValueError("Session was already claimed by a different reconciliation run.")
        return current
    state = current.get("state")
    if state == "reconciliation_error" and not allow_retry:
        raise ValueError("Session reconciliation failed; an explicit retry is required.")
    if state not in {"ready_for_reconciliation", "reconciliation_error"}:
        raise ValueError("Session is not ready for reconciliation.")
    timestamp = int(time.time()) if now is None else int(now)
    current.update({
        "state": "reconciliation_in_progress",
        "reconciliationId": reconciliation_id,
        "reconciliationClaimedAt": timestamp,
        "reconciliationAttempt": int(current.get("reconciliationAttempt") or 0) + 1,
        "updatedAt": timestamp,
        "error": "",
    })
    return current
