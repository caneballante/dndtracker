"""Recording reliability primitives: temporary system-awake leases and capture audits.

The OS request is deliberately process-local and bounded by browser heartbeats.  This
module contains no audio, transcript, or model behavior.
"""

from __future__ import annotations

import atexit
import copy
import json
import os
import threading
import time
from dataclasses import dataclass


CAPTURE_SCHEMA_VERSION = 1
CAPTURE_AUDIT_FILENAME = "capture_events.jsonl"
HEARTBEAT_INTERVAL_SECONDS = 10
LEASE_TIMEOUT_SECONDS = 45
LEASE_REAPER_INTERVAL_SECONDS = 5

CAPTURE_HEALTH_STATES = {
    "healthy",
    "suspect",
    "interrupted",
    "recovering",
    "recovered_with_gap",
    "stopped",
    "finalizing",
    "error",
}

CAPTURE_EVENT_NAMES = {
    "recorder_started",
    "keep_awake_acquired",
    "keep_awake_released",
    "keep_awake_failed",
    "heartbeat_expired",
    "chunk_emitted",
    "chunk_upload_started",
    "chunk_acknowledged",
    "chunk_upload_failed",
    "track_ended",
    "stream_inactive",
    "recorder_error",
    "unexpected_recorder_stop",
    "device_change",
    "screen_wake_lock_acquired",
    "screen_wake_lock_lost",
    "screen_wake_lock_failed",
    "sleep_gap",
    "capture_gap",
    "capture_suspect",
    "capture_interrupted",
    "capture_recovery_started",
    "capture_recovered",
    "capture_recovery_failed",
    "alert_acknowledged",
    "stop_requested",
    "finalize_started",
    "finalize_completed",
    "finalize_failed",
    "pending_finalization_recovered",
}

_AUDIT_LOCKS: dict[str, threading.Lock] = {}
_AUDIT_LOCKS_LOCK = threading.Lock()


def _audit_lock(path: str) -> threading.Lock:
    absolute = os.path.abspath(path)
    with _AUDIT_LOCKS_LOCK:
        return _AUDIT_LOCKS.setdefault(absolute, threading.Lock())


def _safe_scalar(value):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:500]


def append_capture_event(session_dir: str, session_id: str, event: str, *, details=None, now=None):
    """Append one bounded metadata-only reliability event and fsync it."""
    name = str(event or "").strip().lower()
    if name not in CAPTURE_EVENT_NAMES:
        raise ValueError("Unsupported capture diagnostic event.")
    bounded = {}
    if isinstance(details, dict):
        for key, value in list(details.items())[:20]:
            clean_key = str(key or "").strip()[:80]
            if clean_key:
                bounded[clean_key] = _safe_scalar(value)
    record = {
        "schemaVersion": CAPTURE_SCHEMA_VERSION,
        "occurredAt": int(time.time() if now is None else now),
        "sessionId": str(session_id),
        "event": name,
        "details": bounded,
    }
    path = os.path.join(session_dir, CAPTURE_AUDIT_FILENAME)
    with _audit_lock(path):
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    return record


def normalize_capture_status(value=None):
    current = copy.deepcopy(value) if isinstance(value, dict) else {}
    gaps = current.get("gaps")
    if not isinstance(gaps, list):
        gaps = []
    state = str(current.get("state") or "stopped").strip().lower()
    if state not in CAPTURE_HEALTH_STATES:
        state = "error"
    return {
        "schemaVersion": CAPTURE_SCHEMA_VERSION,
        "state": state,
        "sessionOpen": bool(current.get("sessionOpen")),
        "interrupted": bool(current.get("interrupted")),
        "hadInterruption": bool(current.get("hadInterruption") or current.get("interrupted") or gaps),
        "recoveredWithGap": bool(current.get("recoveredWithGap") or any(
            isinstance(gap, dict) and gap.get("recovered") for gap in gaps
        )),
        "partialCapture": bool(current.get("partialCapture") or gaps),
        "recoveryRequired": bool(current.get("recoveryRequired")),
        "alertAcknowledged": bool(current.get("alertAcknowledged")),
        "startedAt": current.get("startedAt"),
        "stoppedAt": current.get("stoppedAt"),
        "lastHeartbeatAt": current.get("lastHeartbeatAt"),
        "lastClientTimestampMs": current.get("lastClientTimestampMs"),
        "lastEventTimestampMs": current.get("lastEventTimestampMs"),
        "expiredHeartbeatAt": current.get("expiredHeartbeatAt"),
        "connectionLost": bool(current.get("connectionLost")),
        "clientBuild": str(current.get("clientBuild") or "unknown")[:80],
        "recorderState": str(current.get("recorderState") or "unknown")[:40],
        "streamActive": current.get("streamActive"),
        "trackState": str(current.get("trackState") or "unknown")[:40],
        "lastChunkEmittedAt": current.get("lastChunkEmittedAt"),
        "lastChunkUploadStartedAt": current.get("lastChunkUploadStartedAt"),
        "lastChunkAcknowledgedAt": current.get("lastChunkAcknowledgedAt"),
        "lastAcknowledgedChunkIndex": current.get("lastAcknowledgedChunkIndex"),
        "chunkIntervalSeconds": current.get("chunkIntervalSeconds"),
        "watchdogGraceSeconds": current.get("watchdogGraceSeconds"),
        "keepAwake": copy.deepcopy(current.get("keepAwake") or {}),
        "screenWakeLock": copy.deepcopy(current.get("screenWakeLock") or {}),
        "pendingFinalization": copy.deepcopy(current.get("pendingFinalization") or {}),
        "gaps": gaps[-100:],
        "lastError": str(current.get("lastError") or "")[:500],
        "updatedAt": current.get("updatedAt"),
    }


class UnsupportedSystemAwakeRequest:
    def __init__(self, reason="unsupported_platform"):
        self.supported = False
        self.active = False
        self.error = str(reason)

    def acquire(self):
        return self.status()

    def release(self):
        self.active = False
        return self.status()

    def status(self):
        return {"supported": False, "active": False, "error": self.error}


class WindowsSystemAwakeRequest:
    """A process-scoped Windows Power Request for PowerRequestSystemRequired."""

    POWER_REQUEST_CONTEXT_VERSION = 0
    POWER_REQUEST_CONTEXT_SIMPLE_STRING = 0x1
    POWER_REQUEST_SYSTEM_REQUIRED = 0

    def __init__(self, reason="DungeonTracker active recording"):
        self.supported = os.name == "nt"
        self.active = False
        self.error = "" if self.supported else "unsupported_platform"
        self._handle = None
        self._reason = str(reason)

    def acquire(self):
        if not self.supported:
            return self.status()
        if self.active:
            return self.status()
        try:
            import ctypes
            from ctypes import wintypes

            class REASON_CONTEXT(ctypes.Structure):
                _fields_ = [
                    ("Version", wintypes.ULONG),
                    ("Flags", wintypes.DWORD),
                    ("SimpleReasonString", wintypes.LPWSTR),
                ]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            create = kernel32.PowerCreateRequest
            create.argtypes = [ctypes.POINTER(REASON_CONTEXT)]
            create.restype = wintypes.HANDLE
            set_request = kernel32.PowerSetRequest
            set_request.argtypes = [wintypes.HANDLE, ctypes.c_int]
            set_request.restype = wintypes.BOOL
            context = REASON_CONTEXT(
                self.POWER_REQUEST_CONTEXT_VERSION,
                self.POWER_REQUEST_CONTEXT_SIMPLE_STRING,
                self._reason,
            )
            handle = create(ctypes.byref(context))
            invalid = wintypes.HANDLE(-1).value
            if not handle or handle == invalid:
                raise OSError(ctypes.get_last_error(), "PowerCreateRequest failed")
            if not set_request(handle, self.POWER_REQUEST_SYSTEM_REQUIRED):
                error = ctypes.get_last_error()
                kernel32.CloseHandle(handle)
                raise OSError(error, "PowerSetRequest failed")
            self._handle = handle
            self.active = True
            self.error = ""
        except Exception as exc:
            self.active = False
            self.error = str(exc)[:500]
        return self.status()

    def release(self):
        if self._handle is not None:
            try:
                import ctypes
                from ctypes import wintypes
                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                clear = kernel32.PowerClearRequest
                clear.argtypes = [wintypes.HANDLE, ctypes.c_int]
                clear.restype = wintypes.BOOL
                clear(self._handle, self.POWER_REQUEST_SYSTEM_REQUIRED)
                kernel32.CloseHandle(self._handle)
            except Exception as exc:
                self.error = str(exc)[:500]
        self._handle = None
        self.active = False
        return self.status()

    def status(self):
        return {"supported": bool(self.supported), "active": bool(self.active), "error": self.error}


def default_system_awake_request():
    return WindowsSystemAwakeRequest() if os.name == "nt" else UnsupportedSystemAwakeRequest()


@dataclass(frozen=True)
class LeaseResult:
    session_id: str
    supported: bool
    active: bool
    error: str
    last_heartbeat_at: float | None
    expires_at: float | None

    def as_dict(self):
        return {
            "sessionId": self.session_id,
            "supported": self.supported,
            "active": self.active,
            "error": self.error,
            "lastHeartbeatAt": self.last_heartbeat_at,
            "leaseExpiresAt": self.expires_at,
            "timeoutSeconds": LEASE_TIMEOUT_SECONDS,
        }


class CaptureLeaseManager:
    def __init__(self, backend=None, *, timeout=LEASE_TIMEOUT_SECONDS,
                 reaper_interval=LEASE_REAPER_INTERVAL_SECONDS, time_fn=None, on_expire=None):
        self.backend = backend or default_system_awake_request()
        self.timeout = float(timeout)
        self.reaper_interval = float(reaper_interval)
        self.time_fn = time_fn or time.time
        self.on_expire = on_expire
        self._leases: dict[str, float] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        atexit.register(self.shutdown)

    def _ensure_reaper(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._reap_loop, name="capture-lease-reaper", daemon=True)
        self._thread.start()

    def _result_locked(self, session_id):
        status = self.backend.status()
        last = self._leases.get(session_id)
        return LeaseResult(
            str(session_id), bool(status.get("supported")), bool(status.get("active")),
            str(status.get("error") or ""), last, (last + self.timeout if last is not None else None),
        )

    def acquire_or_renew(self, session_id, *, now=None):
        timestamp = float(self.time_fn() if now is None else now)
        with self._lock:
            first = not self._leases
            self._leases[str(session_id)] = timestamp
            if first or not self.backend.status().get("active"):
                self.backend.acquire()
            result = self._result_locked(str(session_id)).as_dict()
        self._ensure_reaper()
        return result

    def release(self, session_id, *, reason="recording_stopped"):
        with self._lock:
            self._leases.pop(str(session_id), None)
            if not self._leases:
                self.backend.release()
            result = self._result_locked(str(session_id)).as_dict()
        result["releaseReason"] = str(reason)
        return result

    def status(self, session_id):
        self.expire_stale()
        with self._lock:
            return self._result_locked(str(session_id)).as_dict()

    def expire_stale(self, *, now=None):
        timestamp = float(self.time_fn() if now is None else now)
        expired = []
        with self._lock:
            for session_id, heartbeat in list(self._leases.items()):
                if timestamp - heartbeat > self.timeout:
                    expired.append((session_id, heartbeat))
                    self._leases.pop(session_id, None)
            if expired and not self._leases:
                self.backend.release()
        callback = self.on_expire
        if callback:
            for session_id, heartbeat in expired:
                try:
                    callback(session_id, heartbeat, timestamp)
                except Exception:
                    pass
        return [session_id for session_id, _ in expired]

    def _reap_loop(self):
        while not self._stop.wait(self.reaper_interval):
            self.expire_stale()

    def shutdown(self):
        self._stop.set()
        with self._lock:
            self._leases.clear()
            self.backend.release()
