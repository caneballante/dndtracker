#!/usr/bin/env python3
import json
import os
import re
import time
import threading
import uuid
import hashlib
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
CAMPAIGNS_DIR = os.path.join(BASE_DIR, "campaigns")
NOTES_LAB_RUNS_DIR = os.path.join(BASE_DIR, "notes-lab-runs")
ENV_PATH = os.path.join(BASE_DIR, ".env")
OPENAI_API_KEY = None
NOTES_LOCK = threading.Lock()
REPROCESS_LOCK = threading.Lock()
TRACKING_LOCK = threading.Lock()
STATUS_LOCKS_LOCK = threading.Lock()
STATUS_LOCKS = {}
REPROCESS_JOBS = {}
CLEAN_TRANSCRIPT_LOCK = threading.Lock()
CLEAN_TRANSCRIPT_JOBS = {}
TRANSCRIPT_BACKFILL_LOCK = threading.Lock()
TRANSCRIPT_BACKFILL_JOBS = {}
NOTES_MODEL_DEFAULT = "gpt-4o-mini"
NOTES_WINDOW_DEFAULT = 5
REPROCESS_STALL_SECONDS_DEFAULT = 600
SUMMARY_MODEL_DEFAULT = "gpt-4o-mini"
CLEAN_TRANSCRIPT_MODEL_DEFAULT = "gpt-4o-mini"
NARRATIVE_MODEL_DEFAULT = "gpt-4o-mini"
SERVER_STARTED_AT = int(time.time())
CHUNK_AUDIO_RE = re.compile(r"^chunk_(\d+)\.(wav|webm|ogg|mp4|m4a|mp3)$", re.IGNORECASE)

SESSION_ID_RE = re.compile(r"^[0-9]{8,20}$")  # timestamp-ish
SESSION_NAME_MAX_LEN = 120

def safe_session_id(s: str) -> str:
    s = (s or "").strip()
    if not SESSION_ID_RE.match(s):
        raise ValueError("Invalid sessionId (expected 8-20 digits).")
    return s

def ensure_dir(p: str):
    os.makedirs(p, exist_ok=True)

def sanitize_session_name(name: str) -> str:
    s = re.sub(r"\s+", " ", str(name or "").strip())
    return s[:SESSION_NAME_MAX_LEN]

def _safe_filename_part(text: str, default: str = "run", max_len: int = 60) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", str(text or "").strip())
    s = re.sub(r"-{2,}", "-", s).strip("-._")
    return (s or default)[:max_len]

def read_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

def read_text(path: str, default: str = "") -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return default

class ReprocessStopped(Exception):
    pass

def _reprocess_stall_seconds() -> int:
    load_env_file(ENV_PATH)
    raw = (os.environ.get("REPROCESS_STALL_SECONDS") or "").strip()
    try:
        return max(60, min(3600, int(raw)))
    except Exception:
        return REPROCESS_STALL_SECONDS_DEFAULT

def _openai_retry_delays():
    return (0.5, 1.0, 2.0)

def _openai_should_retry_http(code: int) -> bool:
    try:
        n = int(code)
    except Exception:
        return False
    return n == 429 or 500 <= n < 600

def _urlopen_with_retry(req: Request, timeout: int, read_error_prefix: str = "OpenAI API"):
    last_err = None
    attempts = len(_openai_retry_delays()) + 1
    for attempt in range(attempts):
        try:
            with urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8")
        except HTTPError as e:
            detail = e.read().decode("utf-8", errors="ignore")
            last_err = RuntimeError(f"{read_error_prefix} error: HTTP {e.code} {detail}")
            if attempt < attempts - 1 and _openai_should_retry_http(e.code):
                time.sleep(_openai_retry_delays()[attempt])
                continue
            raise last_err
        except URLError as e:
            last_err = RuntimeError(f"{read_error_prefix} connection error: {e}")
            if attempt < attempts - 1:
                time.sleep(_openai_retry_delays()[attempt])
                continue
            raise last_err
    if last_err is not None:
        raise last_err
    raise RuntimeError(f"{read_error_prefix} request failed.")

def _register_reprocess_job(session_id: str, job_id: str, stop_event: threading.Event, thread: threading.Thread):
    with REPROCESS_LOCK:
        REPROCESS_JOBS[session_id] = {
            "jobId": str(job_id or ""),
            "stopEvent": stop_event,
            "thread": thread,
            "registeredAt": int(time.time()),
        }

def _unregister_reprocess_job(session_id: str, job_id: str = ""):
    with REPROCESS_LOCK:
        current = REPROCESS_JOBS.get(session_id)
        if not current:
            return
        if job_id and str(current.get("jobId") or "") != str(job_id):
            return
        REPROCESS_JOBS.pop(session_id, None)

def _active_reprocess_job(session_id: str):
    with REPROCESS_LOCK:
        current = REPROCESS_JOBS.get(session_id)
        if not current:
            return None
        thread = current.get("thread")
        if isinstance(thread, threading.Thread) and not thread.is_alive():
            REPROCESS_JOBS.pop(session_id, None)
            return None
        return dict(current)

def _request_reprocess_stop(session_id: str, job_id: str = "") -> bool:
    with REPROCESS_LOCK:
        current = REPROCESS_JOBS.get(session_id)
        if not current:
            return False
        if job_id and str(current.get("jobId") or "") != str(job_id):
            return False
        stop_event = current.get("stopEvent")
        if isinstance(stop_event, threading.Event):
            stop_event.set()
            return True
        return False

def _register_clean_transcript_job(session_id: str, job_id: str, thread: threading.Thread):
    with CLEAN_TRANSCRIPT_LOCK:
        CLEAN_TRANSCRIPT_JOBS[session_id] = {
            "jobId": str(job_id or ""),
            "thread": thread,
            "registeredAt": int(time.time()),
        }

def _unregister_clean_transcript_job(session_id: str, job_id: str = ""):
    with CLEAN_TRANSCRIPT_LOCK:
        current = CLEAN_TRANSCRIPT_JOBS.get(session_id)
        if not current:
            return
        if job_id and str(current.get("jobId") or "") != str(job_id):
            return
        CLEAN_TRANSCRIPT_JOBS.pop(session_id, None)

def _active_clean_transcript_job(session_id: str):
    with CLEAN_TRANSCRIPT_LOCK:
        current = CLEAN_TRANSCRIPT_JOBS.get(session_id)
        if not current:
            return None
        thread = current.get("thread")
        if isinstance(thread, threading.Thread) and not thread.is_alive():
            CLEAN_TRANSCRIPT_JOBS.pop(session_id, None)
            return None
        return dict(current)

def _register_transcript_backfill_job(session_id: str, job_id: str, thread: threading.Thread):
    with TRANSCRIPT_BACKFILL_LOCK:
        TRANSCRIPT_BACKFILL_JOBS[session_id] = {
            "jobId": str(job_id or ""),
            "thread": thread,
            "registeredAt": int(time.time()),
        }

def _unregister_transcript_backfill_job(session_id: str, job_id: str = ""):
    with TRANSCRIPT_BACKFILL_LOCK:
        current = TRANSCRIPT_BACKFILL_JOBS.get(session_id)
        if not current:
            return
        if job_id and str(current.get("jobId") or "") != str(job_id):
            return
        TRANSCRIPT_BACKFILL_JOBS.pop(session_id, None)

def _active_transcript_backfill_job(session_id: str):
    with TRANSCRIPT_BACKFILL_LOCK:
        current = TRANSCRIPT_BACKFILL_JOBS.get(session_id)
        if not current:
            return None
        thread = current.get("thread")
        if isinstance(thread, threading.Thread) and not thread.is_alive():
            TRANSCRIPT_BACKFILL_JOBS.pop(session_id, None)
            return None
        return dict(current)

def _status_lock(path: str):
    path = os.path.abspath(path)
    with STATUS_LOCKS_LOCK:
        lock = STATUS_LOCKS.get(path)
        if lock is None:
            lock = threading.Lock()
            STATUS_LOCKS[path] = lock
        return lock

def _session_status_path(session_id: str) -> str:
    return os.path.join(init_session(session_id), "status.json")

def _update_session_status(session_id: str, mutator, default=None):
    status_path = _session_status_path(session_id)
    lock = _status_lock(status_path)
    with lock:
        status = read_json(status_path, default={} if default is None else default)
        result = mutator(status)
        write_json_atomic(status_path, status)
        return status if result is None else result

def write_json_atomic(path: str, data):
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        last_err = None
        for delay_ms in (10, 25, 50, 100, 200):
            try:
                os.replace(tmp, path)
                last_err = None
                break
            except PermissionError as e:
                last_err = e
                time.sleep(delay_ms / 1000.0)
        if last_err is not None:
            raise last_err
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass

def load_env_file(path: str):
    if not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception:
        # If .env can't be read, we just fall back to existing env.
        pass

def _campaign_dir(campaign_id: str) -> str:
    return os.path.join(CAMPAIGNS_DIR, campaign_id)

def _campaign_path(campaign_id: str) -> str:
    return os.path.join(_campaign_dir(campaign_id), "campaign.json")

def _safe_campaign_id(raw: str) -> str:
    s = re.sub(r"[^a-z0-9_-]+", "-", str(raw or "").strip().lower())
    s = re.sub(r"-+", "-", s).strip("-")
    if not s:
        raise ValueError("campaignId is required.")
    return s[:64]

def _default_campaign(campaign_id: str, name: str = ""):
    now = int(time.time())
    return {
        "campaignId": campaign_id,
        "name": str(name or campaign_id),
        "party": "",
        "campaignSummary": "",
        "dungeonMakerJson": {},
        "sessionSummaries": [],
        "createdAt": now,
        "updatedAt": now,
    }

def _normalize_session_summaries(items):
    out = []
    for item in items or []:
        if isinstance(item, str):
            text = item.strip()
            if text:
                out.append({"text": text, "updatedAt": int(time.time())})
            continue
        if isinstance(item, dict):
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            out.append({
                "text": text,
                "label": str(item.get("label") or "").strip(),
                "sessionId": str(item.get("sessionId") or "").strip(),
                "updatedAt": int(item.get("updatedAt") or time.time()),
            })
    return out

def _normalize_campaign_payload(campaign_id: str, payload):
    base = _default_campaign(campaign_id, (payload or {}).get("name") if isinstance(payload, dict) else "")
    if not isinstance(payload, dict):
        return base
    base["name"] = str(payload.get("name") or base["name"]).strip() or campaign_id
    base["party"] = str(payload.get("party") or "")
    base["campaignSummary"] = str(payload.get("campaignSummary") or "")
    dungeon = payload.get("dungeonMakerJson")
    base["dungeonMakerJson"] = dungeon if isinstance(dungeon, dict) else {}
    base["sessionSummaries"] = _normalize_session_summaries(payload.get("sessionSummaries") or [])
    base["createdAt"] = int(payload.get("createdAt") or base["createdAt"])
    base["updatedAt"] = int(time.time())
    return base

def list_campaigns(limit: int = 100):
    ensure_dir(CAMPAIGNS_DIR)
    campaigns = []
    for name in os.listdir(CAMPAIGNS_DIR):
        cdir = os.path.join(CAMPAIGNS_DIR, name)
        if not os.path.isdir(cdir):
            continue
        payload = read_json(_campaign_path(name), default=None)
        if not isinstance(payload, dict):
            continue
        payload = _normalize_campaign_payload(_safe_campaign_id(payload.get("campaignId") or name), payload)
        campaigns.append({
            "campaignId": payload["campaignId"],
            "name": payload["name"],
            "updatedAt": payload["updatedAt"],
            "sessionSummaryCount": len(payload.get("sessionSummaries") or []),
        })
    campaigns.sort(key=lambda x: x.get("updatedAt", 0), reverse=True)
    return campaigns[:limit]

def read_campaign(campaign_id: str):
    cid = _safe_campaign_id(campaign_id)
    ensure_dir(_campaign_dir(cid))
    payload = read_json(_campaign_path(cid), default=None)
    if payload is None:
        payload = _default_campaign(cid)
        write_json_atomic(_campaign_path(cid), payload)
    return _normalize_campaign_payload(cid, payload)

def write_campaign(campaign_id: str, payload):
    cid = _safe_campaign_id(campaign_id)
    ensure_dir(_campaign_dir(cid))
    normalized = _normalize_campaign_payload(cid, payload)
    write_json_atomic(_campaign_path(cid), normalized)
    return normalized

def _campaign_recent_session_summaries_text(campaign: dict, limit: int = 3) -> str:
    entries = campaign.get("sessionSummaries") or []
    if not entries:
        return ""
    lines = []
    for item in entries[-limit:]:
        label = str(item.get("label") or item.get("sessionId") or "").strip()
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        if label:
            lines.append(f"- {label}: {text}")
        else:
            lines.append(f"- {text}")
    return "\n".join(lines).strip()

def _build_context_snapshot(campaign: dict):
    dungeon_json = campaign.get("dungeonMakerJson")
    if not isinstance(dungeon_json, dict):
        dungeon_json = {}
    prep_text = _prep_context_text(dungeon_json)
    campaign_summary = str(campaign.get("campaignSummary") or "").strip()
    recent_summaries = _campaign_recent_session_summaries_text(campaign)
    sections = []
    if campaign_summary:
        sections.append("Campaign summary:\n" + campaign_summary)
    if recent_summaries:
        sections.append("Recent session summaries:\n" + recent_summaries)
    if prep_text:
        sections.append("Relevant world prep:\n" + prep_text)
    return {
        "campaignId": campaign.get("campaignId") or "",
        "campaignName": campaign.get("name") or "",
        "partyText": str(campaign.get("party") or "").strip(),
        "campaignSummaryText": campaign_summary,
        "recentSessionSummariesText": recent_summaries,
        "prepContext": dungeon_json,
        "prepContextText": prep_text,
        "contextText": "\n\n".join([s for s in sections if s]).strip(),
        "createdAt": int(time.time()),
    }

def _read_session_status(session_id: str):
    session_dir = os.path.join(UPLOADS_DIR, session_id)
    return read_json(os.path.join(session_dir, "status.json"), default={})

def _set_session_status_fields(session_id: str, patch: dict):
    def mutate(status):
        status.update(patch or {})
        return dict(status)
    return _update_session_status(session_id, mutate, default={})

def _session_context_snapshot(session_id: str):
    status = _read_session_status(session_id)
    snap = status.get("contextSnapshot")
    if isinstance(snap, dict):
        return snap
    campaign_id = str(status.get("campaignId") or "").strip()
    if campaign_id:
        campaign = read_campaign(campaign_id)
        return _build_context_snapshot(campaign)
    return {}

def _session_party_text(session_id: str) -> str:
    session_dir = os.path.join(UPLOADS_DIR, session_id)
    party_override = read_text(os.path.join(session_dir, "party.txt"), "").strip()
    if party_override:
        return party_override
    snap = _session_context_snapshot(session_id)
    if snap.get("partyText"):
        return str(snap.get("partyText") or "").strip()
    return ""

def _session_prep_context(session_id: str):
    snap = _session_context_snapshot(session_id)
    prep = snap.get("prepContext")
    if isinstance(prep, dict) and prep:
        return prep
    return _read_prep_context(session_id)

def _session_prompt_context_text(session_id: str) -> str:
    snap = _session_context_snapshot(session_id)
    context_text = str(snap.get("contextText") or "").strip()
    if context_text:
        return context_text
    return _prep_context_text(_read_prep_context(session_id))

def _assign_session_campaign(session_id: str, campaign_id: str):
    cid = _safe_campaign_id(campaign_id)
    campaign = read_campaign(cid)
    session_dir = init_session(session_id)
    status_path = os.path.join(session_dir, "status.json")
    status = read_json(status_path, default={})
    status["campaignId"] = cid
    status["contextSnapshot"] = _build_context_snapshot(campaign)
    status["updatedAt"] = int(time.time())
    write_json_atomic(status_path, status)
    return status, campaign

def init_session(session_id: str, campaign_id: str = "") -> str:
    ensure_dir(UPLOADS_DIR)
    session_dir = os.path.join(UPLOADS_DIR, session_id)
    ensure_dir(session_dir)

    status_path = os.path.join(session_dir, "status.json")
    status = read_json(status_path, default=None)
    if status is None:
        status = {
            "sessionId": session_id,
            "sessionName": "",
            "campaignId": campaign_id,
            "createdAt": int(time.time()),
            "updatedAt": int(time.time()),
            "chunks": [],
            "latestChunkIndex": -1,
            "notes": "",
            "contextSnapshot": {},
        }
        write_json_atomic(status_path, status)

    if campaign_id and not status.get("campaignId"):
        status["campaignId"] = campaign_id
        status["updatedAt"] = int(time.time())
        write_json_atomic(status_path, status)

    return session_dir

def list_sessions(limit: int = 50, campaign_id: str = ""):
    ensure_dir(UPLOADS_DIR)
    sessions = []
    wanted_campaign = str(campaign_id or "").strip()
    for name in os.listdir(UPLOADS_DIR):
        session_dir = os.path.join(UPLOADS_DIR, name)
        if not os.path.isdir(session_dir):
            continue
        if not SESSION_ID_RE.match(name):
            continue
        status_path = os.path.join(session_dir, "status.json")
        status = read_json(status_path, default={})
        session_campaign_id = str(status.get("campaignId") or "").strip()
        if wanted_campaign and session_campaign_id != wanted_campaign:
            continue
        mtime = int(os.path.getmtime(session_dir))
        snap = status.get("contextSnapshot") if isinstance(status.get("contextSnapshot"), dict) else {}
        sessions.append({
            "sessionId": name,
            "sessionName": str(status.get("sessionName") or ""),
            "campaignId": session_campaign_id,
            "campaignName": str(snap.get("campaignName") or ""),
            "updatedAt": status.get("updatedAt") or mtime,
            "createdAt": status.get("createdAt") or mtime,
            "chunkCount": len(status.get("chunks") or []),
            "statusUrl": f"/uploads/{name}/status.json",
        })
    sessions.sort(key=lambda s: s.get("updatedAt", 0), reverse=True)
    return sessions[:limit]

def _normalize_reprocess_status(session_id: str, status: dict, persist: bool = False) -> dict:
    if not isinstance(status, dict):
        status = {}
    rep = status.get("reprocessStatus") or {}
    if not isinstance(rep, dict):
        rep = {}
    if not rep:
        status["reprocessStatus"] = {}
        return {}

    now = int(time.time())
    current = dict(rep)
    running = bool(current.get("running"))
    heartbeat_at = int(current.get("heartbeatAt") or current.get("updatedAt") or current.get("startedAt") or 0)
    owner_started_at = int(current.get("ownerStartedAt") or 0)
    job_id = str(current.get("jobId") or "")
    active_job = _active_reprocess_job(session_id) if running else None
    stall_seconds = max(0, now - heartbeat_at) if heartbeat_at else 0

    if running:
        orphan_reason = ""
        if owner_started_at and owner_started_at != SERVER_STARTED_AT:
            orphan_reason = "Rebuild was started by a previous server process and is no longer running."
        else:
            if not active_job:
                orphan_reason = "Rebuild worker is no longer active in this server process."
            elif job_id and str(active_job.get("jobId") or "") != job_id:
                orphan_reason = "Rebuild worker no longer matches the persisted job state."
        if orphan_reason:
            current.update({
                "running": False,
                "phase": "stale",
                "error": orphan_reason,
                "finishedAt": now,
                "updatedAt": now,
                "staleDetectedAt": now,
                "stalled": False,
                "stallSeconds": 0,
                "stopRequested": False,
            })
            status["reprocessStatus"] = current
            status["updatedAt"] = now
            if persist:
                _set_session_status_fields(session_id, status)
            return current

        current["stalled"] = bool(heartbeat_at and stall_seconds >= _reprocess_stall_seconds())
        current["stallSeconds"] = stall_seconds
        current["ownerIsCurrentServer"] = (owner_started_at == SERVER_STARTED_AT) if owner_started_at else False
    else:
        current["stalled"] = False
        current["stallSeconds"] = 0
        current["ownerIsCurrentServer"] = (owner_started_at == SERVER_STARTED_AT) if owner_started_at else False

    status["reprocessStatus"] = current
    return current

def _normalize_clean_transcript_status(session_id: str, status: dict, persist: bool = False) -> dict:
    if not isinstance(status, dict):
        status = {}
    current = status.get("cleanTranscriptStatus") or {}
    if not isinstance(current, dict):
        current = {}
    if not current:
        status["cleanTranscriptStatus"] = {}
        return {}

    now = int(time.time())
    running = bool(current.get("running"))
    owner_started_at = int(current.get("ownerStartedAt") or 0)
    job_id = str(current.get("jobId") or "")
    active_job = _active_clean_transcript_job(session_id) if running else None

    if running:
        orphan_reason = ""
        if owner_started_at and owner_started_at != SERVER_STARTED_AT:
            orphan_reason = "Clean transcript rebuild was started by a previous server process and is no longer running."
        else:
            if not active_job:
                orphan_reason = "Clean transcript worker is no longer active in this server process."
            elif job_id and str(active_job.get("jobId") or "") != job_id:
                orphan_reason = "Clean transcript worker no longer matches the persisted job state."
        if orphan_reason:
            current.update({
                "running": False,
                "phase": "stale",
                "error": orphan_reason,
                "updatedAt": now,
                "finishedAt": now,
            })
            status["cleanTranscriptStatus"] = current
            status["updatedAt"] = now
            if persist:
                _set_session_status_fields(session_id, status)
            return current

    status["cleanTranscriptStatus"] = current
    return current

def _normalize_transcript_backfill_status(session_id: str, status: dict, persist: bool = False) -> dict:
    if not isinstance(status, dict):
        status = {}
    current = status.get("transcriptBackfillStatus") or {}
    if not isinstance(current, dict):
        current = {}
    if not current:
        status["transcriptBackfillStatus"] = {}
        return {}

    now = int(time.time())
    running = bool(current.get("running"))
    owner_started_at = int(current.get("ownerStartedAt") or 0)
    job_id = str(current.get("jobId") or "")
    active_job = _active_transcript_backfill_job(session_id) if running else None

    if running:
        orphan_reason = ""
        if owner_started_at and owner_started_at != SERVER_STARTED_AT:
            orphan_reason = "Transcript backfill was started by a previous server process and is no longer running."
        else:
            if not active_job:
                orphan_reason = "Transcript backfill worker is no longer active in this server process."
            elif job_id and str(active_job.get("jobId") or "") != job_id:
                orphan_reason = "Transcript backfill worker no longer matches the persisted job state."
        if orphan_reason:
            current.update({
                "running": False,
                "phase": "stale",
                "error": orphan_reason,
                "updatedAt": now,
                "finishedAt": now,
            })
            status["transcriptBackfillStatus"] = current
            status["updatedAt"] = now
            if persist:
                _set_session_status_fields(session_id, status)
            return current

    status["transcriptBackfillStatus"] = current
    return current

def read_session_text(session_id: str):
    session_dir = os.path.join(UPLOADS_DIR, session_id)
    transcript_path = os.path.join(session_dir, "transcript.txt")
    clean_transcript_path = os.path.join(session_dir, "clean_transcript.txt")
    notes_path = os.path.join(session_dir, "notes.txt")
    summary_path = os.path.join(session_dir, "notes_summary.txt")
    game_summary_path = os.path.join(session_dir, "game_summary.txt")
    game_narrative_path = os.path.join(session_dir, "game_narrative.txt")
    structured = _latest_notes_structured(session_id)
    status = read_json(os.path.join(session_dir, "status.json"), default={})
    _normalize_reprocess_status(session_id, status, persist=True)
    _normalize_clean_transcript_status(session_id, status, persist=True)
    _normalize_transcript_backfill_status(session_id, status, persist=True)
    tracking_state = status.get("trackingState") or _get_tracking_state(session_id)
    prep_context = _session_prep_context(session_id)
    snap = _session_context_snapshot(session_id)
    missing_chunks = _missing_transcript_audio_chunks(session_id)
    return {
        "transcript": read_text(transcript_path, ""),
        "cleanTranscript": read_text(clean_transcript_path, ""),
        "notes": structured.get("timelineText") or read_text(notes_path, ""),
        "party": _session_party_text(session_id),
        "summary": read_text(summary_path, ""),
        "gameSummary": read_text(game_summary_path, ""),
        "gameNarrative": read_text(game_narrative_path, ""),
        "sessionName": str(status.get("sessionName") or ""),
        "campaignId": str(status.get("campaignId") or ""),
        "campaignName": str(snap.get("campaignName") or ""),
        "contextSnapshot": snap,
        "reprocessStatus": status.get("reprocessStatus") or {},
        "cleanTranscriptStatus": status.get("cleanTranscriptStatus") or {},
        "transcriptBackfillStatus": status.get("transcriptBackfillStatus") or {},
        "missingTranscriptChunks": [int(item.get("chunkIndex") or -1) for item in missing_chunks if int(item.get("chunkIndex") or -1) >= 0],
        "notesState": structured.get("state") or {},
        "notesTimeline": structured.get("timelineItems") or [],
        "notesTimelineText": structured.get("timelineText") or "",
        "notesLatestStructured": structured.get("latest"),
        "trackingState": tracking_state,
        "prepContext": prep_context,
        "prepContextText": _prep_context_text(prep_context),
    }

def update_status_for_chunk(session_id: str, chunk_index: int, filename: str, nbytes: int):
    def mutate(status):
        if not status:
            status.update({
                "sessionId": session_id,
                "sessionName": "",
                "createdAt": int(time.time()),
                "updatedAt": int(time.time()),
                "chunks": [],
                "latestChunkIndex": -1,
                "notes": "",
            })
        status["chunks"] = [c for c in status.get("chunks", []) if c.get("chunkIndex") != chunk_index]
        status["chunks"].append({
            "chunkIndex": chunk_index,
            "filename": filename,
            "bytes": nbytes,
            "uploadedAt": int(time.time()),
        })
        status["chunks"].sort(key=lambda c: c.get("chunkIndex", -1))
        status["latestChunkIndex"] = max(status.get("latestChunkIndex", -1), chunk_index)
        status["updatedAt"] = int(time.time())
    _update_session_status(session_id, mutate, default={})

def _mime_for_filename(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".webm":
        return "audio/webm"
    if ext == ".ogg":
        return "audio/ogg"
    if ext == ".mp4":
        return "audio/mp4"
    if ext == ".wav":
        return "audio/wav"
    return "application/octet-stream"

def _ext_from_content_type(content_type: str) -> str:
    ct = (content_type or "").lower()
    if "audio/wav" in ct or "audio/x-wav" in ct:
        return "wav"
    if "audio/webm" in ct:
        return "webm"
    if "audio/ogg" in ct:
        return "ogg"
    if "audio/mp4" in ct:
        return "mp4"
    return "bin"

def _openai_api_key() -> str:
    global OPENAI_API_KEY
    if OPENAI_API_KEY:
        return OPENAI_API_KEY
    load_env_file(ENV_PATH)
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
    return OPENAI_API_KEY

def _notes_model() -> str:
    load_env_file(ENV_PATH)
    return (os.environ.get("NOTES_MODEL") or NOTES_MODEL_DEFAULT).strip()

def _summary_model() -> str:
    load_env_file(ENV_PATH)
    return (os.environ.get("SUMMARY_MODEL") or SUMMARY_MODEL_DEFAULT).strip()

def _clean_transcript_model() -> str:
    load_env_file(ENV_PATH)
    return (os.environ.get("CLEAN_TRANSCRIPT_MODEL") or CLEAN_TRANSCRIPT_MODEL_DEFAULT).strip()

def _narrative_model() -> str:
    load_env_file(ENV_PATH)
    return (os.environ.get("NARRATIVE_MODEL") or NARRATIVE_MODEL_DEFAULT).strip()

def _notes_window() -> int:
    load_env_file(ENV_PATH)
    raw = (os.environ.get("NOTES_WINDOW_CHUNKS") or "").strip()
    try:
        v = int(raw)
        return max(1, min(20, v))
    except Exception:
        return NOTES_WINDOW_DEFAULT

def _clean_transcript_text(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return ""

    # Normalize whitespace first.
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)

    # Remove common ASR duplicated-word artifacts: "the the", "I I", "we we"
    # (case-insensitive, repeated immediate token only).
    s = re.sub(r"\b([A-Za-z']+)\s+\1\b", r"\1", s, flags=re.IGNORECASE)

    # Collapse filler runs while preserving a single instance.
    s = re.sub(r"\b(um|uh|erm)\b(?:\s*,?\s*\b(?:um|uh|erm)\b)+", r"\1", s, flags=re.IGNORECASE)

    # Encourage sentence boundaries for common narration/dialogue transitions.
    s = re.sub(r"\s+(and then|then|so then|meanwhile)\s+", r". \1 ", s, flags=re.IGNORECASE)

    # Split into scan-friendly lines on punctuation and some discourse markers.
    s = re.sub(r"([.!?])\s+(?=[A-Z0-9\"'])", r"\1\n", s)
    s = re.sub(r"(:)\s+(?=[A-Z0-9\"'])", r"\1\n", s)

    lines = []
    for raw_line in s.splitlines():
        line = raw_line.strip(" -")
        if not line:
            continue
        # Capitalize the first character when safe to do so.
        if line and line[0].isalpha():
            line = line[0].upper() + line[1:]
        # Hard-wrap very long lines for in-session scanning.
        if len(line) > 180:
            while len(line) > 180:
                cut = line.rfind(" ", 0, 180)
                if cut <= 0:
                    break
                lines.append(line[:cut].strip())
                line = line[cut + 1 :].strip()
            if line:
                lines.append(line)
        else:
            lines.append(line)

    # Group short lines into small paragraphs to reduce visual noise.
    paragraphs = []
    current = []
    for line in lines:
        current.append(line)
        if len(current) >= 3 or line.endswith(("?", "!")):
            paragraphs.append("\n".join(current))
            current = []
    if current:
        paragraphs.append("\n".join(current))

    return "\n\n".join(paragraphs).strip()

def _append_transcript(session_id: str, chunk_index: int, text: str):
    session_dir = os.path.join(UPLOADS_DIR, session_id)
    ensure_dir(session_dir)
    transcript_path = os.path.join(session_dir, "transcript.txt")
    clean_transcript_path = os.path.join(session_dir, "clean_transcript.txt")
    jsonl_path = os.path.join(session_dir, "transcripts.jsonl")

    raw_text = (text or "").strip()
    clean_text = _clean_transcript_text(raw_text)
    line = f"[{chunk_index:04d}] {raw_text}\n"
    clean_line = f"[{chunk_index:04d}] {clean_text}\n"
    with open(transcript_path, "a", encoding="utf-8") as f:
        f.write(line)
    with open(clean_transcript_path, "a", encoding="utf-8") as f:
        f.write(clean_line)
    with open(jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "chunkIndex": chunk_index,
            "text": text,
            "cleanText": clean_text,
            "createdAt": int(time.time()),
        }) + "\n")

    _set_session_status_fields(session_id, {
        "transcriptLatest": text,
        "transcriptUpdatedAt": int(time.time()),
    })

def _rewrite_transcript_artifacts(session_id: str):
    session_dir = os.path.join(UPLOADS_DIR, session_id)
    ensure_dir(session_dir)
    transcript_path = os.path.join(session_dir, "transcript.txt")
    clean_transcript_path = os.path.join(session_dir, "clean_transcript.txt")
    transcripts = _load_all_transcripts(session_id)

    transcript_lines = []
    clean_lines = []
    for item in transcripts:
        chunk_index = int(item.get("chunkIndex", -1))
        if chunk_index < 0:
            continue
        text = str(item.get("text") or "").strip()
        clean_text = str(item.get("cleanText") or "").strip()
        if not clean_text and text:
            clean_text = _clean_transcript_text(text)
        if text:
            transcript_lines.append(f"[{chunk_index:04d}] {text}")
        if clean_text:
            clean_lines.append(f"[{chunk_index:04d}] {clean_text}")

    with open(transcript_path, "w", encoding="utf-8") as f:
        if transcript_lines:
            f.write("\n".join(transcript_lines).strip() + "\n")
        else:
            f.write("")

    with open(clean_transcript_path, "w", encoding="utf-8") as f:
        if clean_lines:
            f.write("\n".join(clean_lines).strip() + "\n")
        else:
            f.write("")

    now = int(time.time())
    _set_session_status_fields(session_id, {
        "transcriptUpdatedAt": now,
        "cleanTranscriptUpdatedAt": now,
    })

def _set_transcript_error(session_id: str, chunk_index: int, message: str):
    def mutate(status):
        status["transcriptError"] = {
            "chunkIndex": chunk_index,
            "message": message,
            "updatedAt": int(time.time()),
        }
    _update_session_status(session_id, mutate, default={})

def _read_party_meta(session_id: str) -> str:
    return _session_party_text(session_id)

def _prep_context_path(session_id: str) -> str:
    session_dir = init_session(session_id)
    return os.path.join(session_dir, "prep_context.json")

def _read_prep_context(session_id: str):
    path = _prep_context_path(session_id)
    return read_json(path, default={})

def _write_prep_context(session_id: str, payload):
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError("prepContext must be a JSON object.")
    path = _prep_context_path(session_id)
    write_json_atomic(path, payload)
    _set_session_status_fields(session_id, {
        "prepUpdatedAt": int(time.time()),
        "updatedAt": int(time.time()),
    })

def _prep_context_text(prep: dict) -> str:
    if not isinstance(prep, dict) or not prep:
        return ""
    dungeon = prep.get("dungeon") or {}
    rooms = prep.get("rooms") or []
    monsters = prep.get("monsters") or []
    npcs = prep.get("npcs") or []
    lines = []
    if isinstance(dungeon, dict):
        dname = str(dungeon.get("name") or "").strip()
        dsub = str(dungeon.get("subtitle") or "").strip()
        if dname or dsub:
            lines.append(f"Dungeon: {dname} {('- ' + dsub) if dsub else ''}".strip())
    if isinstance(rooms, list):
        lines.append(f"Rooms loaded: {len(rooms)}")
        for r in rooms[:20]:
            if not isinstance(r, dict):
                continue
            num = str(r.get("number") or "").strip()
            short_name = str(r.get("short_name") or "").strip()
            if num or short_name:
                lines.append(f"- Room {num}: {short_name}".strip())
    if isinstance(monsters, list):
        lines.append(f"Monsters loaded: {len(monsters)}")
        for m in monsters[:30]:
            if not isinstance(m, dict):
                continue
            name = str(m.get("name") or "").strip()
            cr = str(m.get("cr") or "").strip()
            if name:
                lines.append(f"- {name}{(' (CR ' + cr + ')') if cr else ''}")
    if isinstance(npcs, list):
        lines.append(f"NPCs loaded: {len(npcs)}")
        for n in npcs[:30]:
            if not isinstance(n, dict):
                continue
            name = str(n.get("name") or "").strip()
            role = str(n.get("role") or "").strip()
            if name:
                lines.append(f"- {name}{(' (' + role + ')') if role else ''}")
    return "\n".join(lines).strip()

def _read_notes_summary(session_id: str) -> str:
    session_dir = os.path.join(UPLOADS_DIR, session_id)
    summary_path = os.path.join(session_dir, "notes_summary.txt")
    try:
        with open(summary_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""

def _load_notes_entries(session_id: str):
    session_dir = os.path.join(UPLOADS_DIR, session_id)
    jsonl_path = os.path.join(session_dir, "notes.jsonl")
    if not os.path.isfile(jsonl_path):
        return []
    entries = []
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception:
        return []
    return entries

def _notes_overrides_path(session_id: str) -> str:
    session_dir = init_session(session_id)
    return os.path.join(session_dir, "notes_overrides.json")

def _read_notes_overrides(session_id: str):
    data = read_json(_notes_overrides_path(session_id), default={})
    return data if isinstance(data, dict) else {}

def _write_notes_overrides(session_id: str, overrides: dict):
    if not isinstance(overrides, dict):
        overrides = {}
    write_json_atomic(_notes_overrides_path(session_id), overrides)
    _set_session_status_fields(session_id, {
        "notesOverridesUpdatedAt": int(time.time()),
    })

def _timeline_item_id(chunk_index: int, position: int, text: str) -> str:
    key = f"{int(chunk_index)}|{int(position)}|{str(text or '').strip()}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    return f"tl_{chunk_index:04d}_{position:02d}_{digest}"

def _coerce_priority(value) -> int:
    try:
        n = int(value)
    except Exception:
        n = 0
    if n > 0:
        return 1
    if n < 0:
        return -1
    return 0

def _timeline_entries(session_id: str):
    entries = _load_notes_entries(session_id)
    overrides = _read_notes_overrides(session_id)
    items = []
    for entry in entries:
        chunk_index = int(entry.get("chunkIndex", -1))
        notes = entry.get("notes") or {}
        timeline = notes.get("timeline") or []
        if isinstance(timeline, str):
            timeline = [timeline]
        if not isinstance(timeline, list):
            timeline = []
        for idx, raw in enumerate(timeline):
            original_text = str(raw or "").strip()
            if not original_text:
                continue
            item_id = _timeline_item_id(chunk_index, idx, original_text)
            override = overrides.get(item_id) if isinstance(overrides.get(item_id), dict) else {}
            edited_text = str(override.get("editedText") or "").strip()
            display_text = edited_text or original_text
            priority = _coerce_priority(override.get("priority"))
            items.append({
                "id": item_id,
                "chunkIndex": chunk_index,
                "position": idx,
                "originalText": original_text,
                "editedText": edited_text,
                "text": display_text,
                "priority": priority,
                "isEdited": bool(edited_text),
            })
    return items

def _compiled_notes_timeline_text(session_id: str) -> str:
    items = _timeline_entries(session_id)
    lines = [f"[{int(item.get('chunkIndex', -1)):04d}] {str(item.get('text') or '').strip()}" for item in items if str(item.get("text") or "").strip()]
    return "\n".join(lines).strip()

def _timeline_priority_texts(session_id: str):
    promoted = []
    deemphasized = []
    for item in _timeline_entries(session_id):
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        line = f"[{int(item.get('chunkIndex', -1)):04d}] {text}"
        priority = _coerce_priority(item.get("priority"))
        if priority > 0:
            promoted.append(line)
        elif priority < 0:
            deemphasized.append(line)
    return {
        "promoted": "\n".join(promoted).strip(),
        "deemphasized": "\n".join(deemphasized).strip(),
    }

def _rewrite_notes_timeline_file(session_id: str):
    session_dir = init_session(session_id)
    notes_path = os.path.join(session_dir, "notes.txt")
    compiled = _compiled_notes_timeline_text(session_id)
    with open(notes_path, "w", encoding="utf-8") as f:
        if compiled:
            f.write(compiled.strip() + "\n")
        else:
            f.write("")
    _set_session_status_fields(session_id, {
        "notesUpdatedAt": int(time.time()),
    })

def _merge_notes_state(entries):
    merged = {
        "location": "",
        "npcs": [],
        "loot": [],
        "spells": [],
        "hp": [],
        "conditions": [],
        "quests": [],
    }
    for entry in entries:
        notes = entry.get("notes") or {}
        state = notes.get("state") or {}
        if not isinstance(state, dict):
            continue
        loc = str(state.get("location") or "").strip()
        if loc:
            merged["location"] = loc
        for key in ["npcs", "loot", "spells", "hp", "conditions", "quests"]:
            val = state.get(key)
            if isinstance(val, str):
                val = [val] if val.strip() else []
            if not isinstance(val, list):
                continue
            for item in val:
                item_str = str(item).strip()
                if item_str and item_str not in merged[key]:
                    merged[key].append(item_str)
    return merged

def _latest_notes_structured(session_id: str):
    entries = _load_notes_entries(session_id)
    latest = None
    if entries:
        latest = (entries[-1].get("notes") or None)
    timeline_items = _timeline_entries(session_id)
    return {
        "latest": latest,
        "state": _merge_notes_state(entries),
        "summary": _read_notes_summary(session_id),
        "timelineItems": timeline_items,
        "timelineText": "\n".join(
            [f"[{int(item.get('chunkIndex', -1)):04d}] {str(item.get('text') or '').strip()}" for item in timeline_items if str(item.get("text") or "").strip()]
        ).strip(),
    }

def _write_notes_summary(session_id: str, summary: str):
    session_dir = os.path.join(UPLOADS_DIR, session_id)
    ensure_dir(session_dir)
    summary_path = os.path.join(session_dir, "notes_summary.txt")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(summary.strip() + "\n")

def _load_recent_transcripts(session_id: str, count: int):
    session_dir = os.path.join(UPLOADS_DIR, session_id)
    jsonl_path = os.path.join(session_dir, "transcripts.jsonl")
    if not os.path.isfile(jsonl_path):
        return []
    lines = []
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    lines.append(obj)
                except json.JSONDecodeError:
                    continue
    except Exception:
        return []
    return lines[-count:]

def _load_all_transcripts(session_id: str):
    session_dir = os.path.join(UPLOADS_DIR, session_id)
    jsonl_path = os.path.join(session_dir, "transcripts.jsonl")
    if not os.path.isfile(jsonl_path):
        return []
    lines = []
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                lines.append(obj)
    except Exception:
        return []
    lines.sort(key=lambda o: int(o.get("chunkIndex", -1)))
    return lines

def _saved_audio_chunks(session_id: str):
    session_dir = init_session(session_id)
    items = {}
    try:
        for name in os.listdir(session_dir):
            match = CHUNK_AUDIO_RE.match(name)
            if not match:
                continue
            chunk_index = int(match.group(1))
            path = os.path.join(session_dir, name)
            current = items.get(chunk_index)
            if current is None or name.lower() < str(current.get("filename") or "").lower():
                items[chunk_index] = {
                    "chunkIndex": chunk_index,
                    "filename": name,
                    "path": path,
                }
    except FileNotFoundError:
        return []
    return [items[idx] for idx in sorted(items.keys())]

def _missing_transcript_audio_chunks(session_id: str):
    transcript_indexes = {
        int(item.get("chunkIndex", -1))
        for item in _load_all_transcripts(session_id)
        if int(item.get("chunkIndex", -1)) >= 0
    }
    return [item for item in _saved_audio_chunks(session_id) if int(item.get("chunkIndex", -1)) not in transcript_indexes]

def _tracking_events_path(session_id: str) -> str:
    session_dir = init_session(session_id)
    return os.path.join(session_dir, "tracking_events.jsonl")

def _slugify_name(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text or "").strip().lower())
    s = s.strip("-")
    return s or "entity"

def _parse_party_rows(party_text: str):
    rows = []
    for raw in (party_text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        row = {}
        for part in line.split("|"):
            p = part.strip()
            if "=" not in p:
                continue
            key, value = p.split("=", 1)
            row[key.strip().lower()] = value.strip()
        if row:
            rows.append(row)
    return rows

def _initial_party_entities(session_id: str):
    rows = _parse_party_rows(_read_party_meta(session_id))
    entities = []
    used = {}
    excluded_tokens = {"false", "0", "no", "off", "n", "exclude", "excluded"}
    for row in rows:
        role = str(row.get("role") or "Player").strip() or "Player"
        role_norm = role.lower()
        if role_norm not in ("player", "npc"):
            continue
        include_raw = str(row.get("include") or row.get("included") or "yes").strip().lower()
        if include_raw in excluded_tokens:
            continue
        name = str(row.get("character") or row.get("player") or "").strip()
        if not name:
            continue
        base = _slugify_name(name)
        n = used.get(base, 0)
        used[base] = n + 1
        entity_id = base if n == 0 else f"{base}-{n+1}"
        entities.append({
            "entityId": entity_id,
            "name": name,
            "role": "NPC" if role_norm == "npc" else "Player",
            "entityType": "party",
            "hp": 0,
            "note": "",
            "lastAction": "",
        })
    return entities

def _read_tracking_events(session_id: str):
    path = _tracking_events_path(session_id)
    if not os.path.isfile(path):
        return []
    events = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception:
        return []
    return events

def _append_tracking_event(session_id: str, event: dict):
    path = _tracking_events_path(session_id)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")

def _event_delta(event: dict) -> int:
    try:
        return int(event.get("delta") or 0)
    except Exception:
        return 0

def _event_hp(event: dict):
    if event.get("hp") is None:
        return None
    try:
        return int(event.get("hp"))
    except Exception:
        return None

def _apply_tracking_action(entity: dict, event_type: str, event: dict):
    if event_type == "damage":
        entity["hp"] = int(entity.get("hp") or 0) - abs(_event_delta(event))
    elif event_type == "heal":
        entity["hp"] = int(entity.get("hp") or 0) + abs(_event_delta(event))
    elif event_type == "hp_set":
        hp = _event_hp(event)
        if hp is not None:
            entity["hp"] = hp
    elif event_type == "note":
        entity["note"] = str(event.get("note") or "")

def _derive_tracking_state(session_id: str, events=None):
    if events is None:
        events = _read_tracking_events(session_id)

    party = {}
    monsters = {}
    for e in _initial_party_entities(session_id):
        party[e["entityId"]] = dict(e)

    for event in events:
        entity_type = str(event.get("entityType") or "").strip().lower()
        entity_id = str(event.get("entityId") or "").strip()
        if entity_type not in ("party", "monster") or not entity_id:
            continue
        target = party if entity_type == "party" else monsters
        if entity_id not in target:
            target[entity_id] = {
                "entityId": entity_id,
                "name": str(event.get("entityName") or entity_id),
                "entityType": entity_type,
                "hp": 0,
                "note": "",
                "lastAction": "",
                "removed": False,
            }
        entity = target[entity_id]
        event_type = str(event.get("eventType") or "").strip().lower()

        if event_type == "create":
            entity["removed"] = False
            hp = _event_hp(event)
            if hp is not None:
                entity["hp"] = hp
            name = str(event.get("entityName") or "").strip()
            if name:
                entity["name"] = name
            entity["lastAction"] = "created"
            continue
        if event_type == "remove":
            entity["removed"] = True
            entity["lastAction"] = "removed"
            continue
        if event_type == "undo":
            undo_type = str(event.get("undoEventType") or "").strip().lower()
            _apply_tracking_action(entity, undo_type, event)
            entity["lastAction"] = f"undo {undo_type}"
            continue

        _apply_tracking_action(entity, event_type, event)
        entity["lastAction"] = event_type

    party_list = sorted(party.values(), key=lambda x: str(x.get("name") or "").lower())
    monster_list = [
        m for m in sorted(monsters.values(), key=lambda x: str(x.get("name") or "").lower())
        if not m.get("removed")
    ]
    for item in party_list + monster_list:
        item.pop("removed", None)
    return {
        "updatedAt": int(time.time()),
        "party": party_list,
        "monsters": monster_list,
    }

def _store_tracking_state(session_id: str, tracking_state: dict):
    session_dir = init_session(session_id)
    status_path = os.path.join(session_dir, "status.json")
    status = read_json(status_path, default={})
    status["trackingState"] = tracking_state
    status["updatedAt"] = int(time.time())
    write_json_atomic(status_path, status)

def _get_tracking_state(session_id: str):
    state = _derive_tracking_state(session_id)
    _store_tracking_state(session_id, state)
    return state

def _new_tracking_event(session_id: str, entity_type: str, entity_id: str, entity_name: str, event_type: str, **extra):
    event = {
        "eventId": f"{int(time.time()*1000)}-{uuid.uuid4().hex[:8]}",
        "sessionId": session_id,
        "ts": int(time.time() * 1000),
        "entityType": entity_type,
        "entityId": entity_id,
        "entityName": entity_name,
        "eventType": event_type,
        "source": "tracker_ui",
    }
    event.update(extra or {})
    return event

def _validate_tracking_event_payload(data: dict):
    session_id = safe_session_id(str(data.get("sessionId") or ""))
    entity_type = str(data.get("entityType") or "").strip().lower()
    if entity_type not in ("party", "monster"):
        raise ValueError("entityType must be party or monster.")
    entity_id = str(data.get("entityId") or "").strip()
    if not entity_id:
        raise ValueError("entityId is required.")
    entity_name = str(data.get("entityName") or entity_id).strip()
    event_type = str(data.get("eventType") or "").strip().lower()
    if event_type not in ("damage", "heal", "hp_set", "note"):
        raise ValueError("eventType must be one of damage, heal, hp_set, note.")

    payload = {
        "sessionId": session_id,
        "entityType": entity_type,
        "entityId": entity_id,
        "entityName": entity_name,
        "eventType": event_type,
    }
    if event_type in ("damage", "heal"):
        delta = abs(int(data.get("delta")))
        if delta <= 0:
            raise ValueError("delta must be > 0 for damage/heal.")
        payload["delta"] = delta
    if event_type == "hp_set":
        payload["hp"] = int(data.get("hp"))
    if event_type == "note":
        payload["note"] = str(data.get("note") or "")
    return payload

def _next_monster_id(session_id: str, name: str):
    base = _slugify_name(name)
    events = _read_tracking_events(session_id)
    used = set()
    for e in events:
        if str(e.get("entityType") or "").lower() != "monster":
            continue
        eid = str(e.get("entityId") or "").strip()
        if eid:
            used.add(eid)
    if base not in used:
        return base
    i = 2
    while f"{base}-{i}" in used:
        i += 1
    return f"{base}-{i}"

def _last_mutable_event_for_entity(events, entity_id: str):
    for event in reversed(events):
        if str(event.get("entityId") or "") != entity_id:
            continue
        et = str(event.get("eventType") or "").lower()
        if et in ("damage", "heal", "hp_set", "note"):
            return event
    return None

def _entity_state_before_event(events, entity_id: str, stop_event_id: str):
    session_id = str(events[0].get("sessionId")) if events else ""
    state = _derive_tracking_state(session_id, events=[]) if session_id else {"party": [], "monsters": []}
    by_id = {}
    for e in state.get("party", []) + state.get("monsters", []):
        by_id[str(e.get("entityId"))] = e
    for event in events:
        if str(event.get("eventId") or "") == stop_event_id:
            break
        etype = str(event.get("entityType") or "").lower()
        eid = str(event.get("entityId") or "")
        target = by_id.get(eid)
        if not target:
            target = {
                "entityId": eid,
                "name": str(event.get("entityName") or eid),
                "entityType": etype,
                "hp": 0,
                "note": "",
                "removed": False,
            }
            by_id[eid] = target
        kind = str(event.get("eventType") or "").lower()
        if kind == "create":
            target["removed"] = False
            hp = _event_hp(event)
            if hp is not None:
                target["hp"] = hp
            continue
        if kind == "remove":
            target["removed"] = True
            continue
        if kind == "undo":
            _apply_tracking_action(target, str(event.get("undoEventType") or "").lower(), event)
            continue
        _apply_tracking_action(target, kind, event)
    return by_id.get(entity_id, {"hp": 0, "note": "", "name": entity_id})

def _tracking_state_text(state: dict) -> str:
    state = state or {}
    party = state.get("party") or []
    monsters = state.get("monsters") or []
    lines = []
    lines.append("Party:")
    if party:
        for p in party:
            lines.append(f"- {p.get('name')}: HP={p.get('hp')} note={p.get('note') or ''}".rstrip())
    else:
        lines.append("- (none)")
    lines.append("Monsters:")
    if monsters:
        for m in monsters:
            lines.append(f"- {m.get('name')}: HP={m.get('hp')} note={m.get('note') or ''}".rstrip())
    else:
        lines.append("- (none)")
    return "\n".join(lines)

def _recent_tracking_events_text(session_id: str, limit: int = 25) -> str:
    events = _read_tracking_events(session_id)
    if not events:
        return ""
    items = events[-max(1, min(100, int(limit))):]
    lines = []
    for e in items:
        et = str(e.get("eventType") or "")
        name = str(e.get("entityName") or e.get("entityId") or "")
        if et in ("damage", "heal"):
            lines.append(f"- {name} {et} {abs(_event_delta(e))}")
        elif et == "hp_set":
            lines.append(f"- {name} hp set {e.get('hp')}")
        elif et == "note":
            lines.append(f"- {name} note: {str(e.get('note') or '').strip()}")
        elif et == "create":
            lines.append(f"- created monster {name} hp={e.get('hp')}")
        elif et == "remove":
            lines.append(f"- removed monster {name}")
        elif et == "undo":
            lines.append(f"- undo for {name}: {e.get('undoEventType')}")
    return "\n".join(lines)

def _extract_json_object(text: str):
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    snippet = text[start:end+1]
    try:
        return json.loads(snippet)
    except json.JSONDecodeError:
        return None

def _chat_complete_text(system_prompt: str, user_prompt: str, model: str) -> str:
    api_key = _openai_api_key()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set. Add it to .env or your environment.")

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
    }

    body = json.dumps(payload).encode("utf-8")
    req = Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    raw = _urlopen_with_retry(req, timeout=180)

    data = json.loads(raw)
    content = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )
    if not content:
        raise RuntimeError("Empty chat completion response.")
    return content

def transcribe_with_openai(path: str, model: str = "gpt-4o-mini-transcribe") -> str:
    api_key = _openai_api_key()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set. Add it to .env or your environment.")

    filename = os.path.basename(path)
    content_type = _mime_for_filename(filename)
    with open(path, "rb") as f:
        file_bytes = f.read()

    boundary = "----dnr" + uuid.uuid4().hex
    parts = []

    def add_field(name: str, value: str):
        parts.append(f"--{boundary}\r\n".encode("utf-8"))
        parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        parts.append(str(value).encode("utf-8"))
        parts.append(b"\r\n")

    def add_file(name: str, fname: str, ctype: str, data: bytes):
        parts.append(f"--{boundary}\r\n".encode("utf-8"))
        parts.append(
            f'Content-Disposition: form-data; name="{name}"; filename="{fname}"\r\n'.encode("utf-8")
        )
        parts.append(f"Content-Type: {ctype}\r\n\r\n".encode("utf-8"))
        parts.append(data)
        parts.append(b"\r\n")

    add_field("model", model)
    add_field("response_format", "json")
    add_file("file", filename, content_type, file_bytes)
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))

    body = b"".join(parts)
    req = Request(
        "https://api.openai.com/v1/audio/transcriptions",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )

    payload = _urlopen_with_retry(req, timeout=120)

    data = json.loads(payload)
    text = (data.get("text") or "").strip()
    if not text:
        raise RuntimeError("Empty transcription response.")
    return text

def _default_notes_system_prompt() -> str:
    return (
        "You are a D&D session note-taker. Produce strict JSON with keys:\n"
        "timeline (array of strings, chronological new events only),\n"
        "state (object with keys: location, npcs, loot, spells, hp, conditions, quests),\n"
        "summary (string, 3-6 sentences, rolling summary of the session so far).\n"
        "Use only facts in the transcript and party data. Do not invent details.\n"
        "Use the party roster to normalize names in the transcript. Match likely variants, mis-hearings, and shortened names to the roster when the context supports it.\n"
        "Prefer character names in timeline and summary. If helpful, include the player name once in parentheses on first mention.\n"
        "If a speaker/action cannot be confidently matched to the roster, keep the transcript wording or use a neutral label instead of guessing.\n"
        "If a field has no info, use empty string or empty array.\n"
    )

def _default_game_summary_system_prompt() -> str:
    return (
        "You are a D&D campaign recapper. Produce a readable game summary in plain text Markdown.\n"
        "Prioritize chronological events, major discoveries, NPC interactions, combat outcomes, loot, and open hooks.\n"
        "If promoted DM notes events are provided, make sure they are included unless they directly conflict with stronger source evidence.\n"
        "If de-emphasized DM notes events are provided, keep them low priority and do not center them unless needed for coherence.\n"
        "Treat tracker HP/damage state as authoritative when it conflicts with transcript narration.\n"
        "Do not invent facts. Use the party roster to normalize transcript names to the correct character names whenever context supports it.\n"
        "Prefer character names consistently. If a name is unclear and cannot be matched confidently, use a neutral description.\n"
        "Format with short sections: Summary, Timeline, Key NPCs, Loot/Treasure, Outstanding Hooks.\n"
    )

def _default_game_narrative_system_prompt(target_word_count: int = 600) -> str:
    target_word_count = max(150, min(5000, int(target_word_count or 600)))
    return (
        "You are a fantasy chronicler retelling a D&D session as a humorous high-fantasy narrative.\n"
        f"Write approximately {target_word_count} words in plain text Markdown (no code fences).\n"
        "Accuracy is the top priority. Build the narrative from the DM notes timeline first, then use the transcript to add detail.\n"
        "If promoted DM notes events are provided, make sure they appear in the narrative unless they conflict with stronger source evidence.\n"
        "If de-emphasized DM notes events are provided, keep them in the background and do not let them dominate the story unless needed for coherence.\n"
        "Preserve chronological order of events. Do not reorder scenes for dramatic effect.\n"
        "Use vivid but clear prose, with light humor only where it does not distort the facts.\n"
        "Prefer character names from the party roster when possible.\n"
        "Do not invent major events, loot, outcomes, dialogue, or motivations not supported by the provided material.\n"
        "If a detail is unclear, omit it or describe it cautiously rather than guessing.\n"
        "You may include a few short direct quotes from the transcript, but treat them as unattributed unless the speaker can be identified confidently.\n"
        "Keep it readable and coherent for players recapping the session.\n"
    )

def _default_clean_transcript_system_prompt() -> str:
    return (
        "You are editing an auto-transcribed D&D session transcript for readability.\n"
        "Return plain text only for a single transcript chunk (no JSON, no markdown fences).\n"
        "Preserve facts and sequence. Do not invent content.\n"
        "You may be given adjacent chunks for context, but only rewrite the target chunk.\n"
        "Do not pull lines forward or backward from neighboring chunks.\n"
        "Do not add connective narration, inferred transitions, or information not stated in the target chunk.\n"
        "Use the provided party roster to normalize likely mis-heard player/character names when context supports it.\n"
        "Prefer character names consistently. If uncertain, keep the original wording.\n"
        "Improve punctuation, sentence breaks, and paragraphing for readability.\n"
        "Do not summarize; this is still a transcript chunk.\n"
    )

def clean_transcript_chunk_with_openai(
    chunk_text: str,
    party: str = "",
    chunk_index: int = -1,
    prev_chunk_text: str = "",
    next_chunk_text: str = "",
) -> str:
    chunk_text = (chunk_text or "").strip()
    if not chunk_text:
        return ""
    user_prompt = (
        f"Chunk index: {chunk_index}\n\n"
        "Party roster (if any):\n"
        f"{party.strip() if party else '(none)'}\n\n"
        "Previous chunk for context only:\n"
        f"{(prev_chunk_text or '').strip() or '(none)'}\n\n"
        "Transcript chunk to clean:\n"
        f"{chunk_text}\n\n"
        "Next chunk for context only:\n"
        f"{(next_chunk_text or '').strip() or '(none)'}\n\n"
        "Return only the cleaned version of the target transcript chunk.\n"
    )
    out = _chat_complete_text(
        _default_clean_transcript_system_prompt(),
        user_prompt,
        _clean_transcript_model(),
    )
    return out.strip()

def generate_game_summary_from_text(
    transcript_text: str,
    party: str = "",
    notes_text: str = "",
    notes_summary: str = "",
    promoted_notes_text: str = "",
    deemphasized_notes_text: str = "",
    tracker_state_text: str = "",
    tracker_events_text: str = "",
    prep_context_text: str = "",
) -> str:
    transcript_text = (transcript_text or "").strip()
    if not transcript_text:
        raise RuntimeError("No transcript text available for summary generation.")

    system_prompt = _default_game_summary_system_prompt()
    user_prompt = (
        "Party roster (if any):\n"
        f"{party.strip() if party else '(none)'}\n\n"
        "Existing DM notes timeline (if any):\n"
        f"{notes_text.strip() if notes_text else '(none)'}\n\n"
        "Existing rolling notes summary (if any):\n"
        f"{notes_summary.strip() if notes_summary else '(none)'}\n\n"
        "Promoted DM notes events that should be included if supported:\n"
        f"{promoted_notes_text.strip() if promoted_notes_text else '(none)'}\n\n"
        "De-emphasized DM notes events to keep low priority:\n"
        f"{deemphasized_notes_text.strip() if deemphasized_notes_text else '(none)'}\n\n"
        "Tracker state (authoritative for hp/damage when present):\n"
        f"{tracker_state_text.strip() if tracker_state_text else '(none)'}\n\n"
        "Recent tracker events (if any):\n"
        f"{tracker_events_text.strip() if tracker_events_text else '(none)'}\n\n"
        "Campaign and session context (if any):\n"
        f"{prep_context_text.strip() if prep_context_text else '(none)'}\n\n"
        "Transcript for the full game session:\n"
        f"{transcript_text}\n"
    )
    return _chat_complete_text(system_prompt, user_prompt, _summary_model())

def generate_game_narrative_from_text(
    transcript_text: str,
    party: str = "",
    notes_text: str = "",
    notes_summary: str = "",
    promoted_notes_text: str = "",
    deemphasized_notes_text: str = "",
    prep_context_text: str = "",
    narrative_guidance: str = "",
    target_word_count: int = 600,
) -> str:
    transcript_text = (transcript_text or "").strip()
    if not transcript_text:
        raise RuntimeError("No transcript text available for narrative generation.")

    target_word_count = max(150, min(5000, int(target_word_count or 600)))
    system_prompt = _default_game_narrative_system_prompt(target_word_count)
    guidance_text = (narrative_guidance or "").strip()
    user_prompt = (
        "Party roster (if any):\n"
        f"{party.strip() if party else '(none)'}\n\n"
        "DM notes timeline (if any):\n"
        f"{notes_text.strip() if notes_text else '(none)'}\n\n"
        "DM notes rolling summary (if any):\n"
        f"{notes_summary.strip() if notes_summary else '(none)'}\n\n"
        "Promoted DM notes events that should be included if supported:\n"
        f"{promoted_notes_text.strip() if promoted_notes_text else '(none)'}\n\n"
        "De-emphasized DM notes events to keep low priority:\n"
        f"{deemphasized_notes_text.strip() if deemphasized_notes_text else '(none)'}\n\n"
        "Campaign and session context (if any):\n"
        f"{prep_context_text.strip() if prep_context_text else '(none)'}\n\n"
        f"Target word count for this run: {target_word_count}\n\n"
        "One-time narrative guidance for this run (if any):\n"
        f"{guidance_text if guidance_text else '(none)'}\n\n"
        "Transcript for the full game session:\n"
        f"{transcript_text}\n"
    )
    return _chat_complete_text(system_prompt, user_prompt, _narrative_model())

def generate_game_summary_for_session(session_id: str) -> str:
    session_dir = os.path.join(UPLOADS_DIR, session_id)
    clean_transcript = read_text(os.path.join(session_dir, "clean_transcript.txt"), "").strip()
    raw_transcript = read_text(os.path.join(session_dir, "transcript.txt"), "").strip()
    transcript_text = clean_transcript or raw_transcript
    notes_text = _compiled_notes_timeline_text(session_id) or read_text(os.path.join(session_dir, "notes.txt"), "")
    notes_summary = read_text(os.path.join(session_dir, "notes_summary.txt"), "")
    party = _read_party_meta(session_id)
    timeline_priority = _timeline_priority_texts(session_id)

    tracking_state = _get_tracking_state(session_id)
    tracking_state_text = _tracking_state_text(tracking_state)
    tracking_events_text = _recent_tracking_events_text(session_id, limit=40)
    prep_context_text = _session_prompt_context_text(session_id)

    summary = generate_game_summary_from_text(
        transcript_text=transcript_text,
        party=party,
        notes_text=notes_text,
        notes_summary=notes_summary,
        promoted_notes_text=timeline_priority.get("promoted") or "",
        deemphasized_notes_text=timeline_priority.get("deemphasized") or "",
        tracker_state_text=tracking_state_text,
        tracker_events_text=tracking_events_text,
        prep_context_text=prep_context_text,
    )

    out_path = os.path.join(session_dir, "game_summary.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(summary.strip() + "\n")

    _set_session_status_fields(session_id, {
        "gameSummaryUpdatedAt": int(time.time()),
    })
    return summary

def generate_game_narrative_for_session(session_id: str, narrative_guidance: str = "", target_word_count: int = 600) -> str:
    session_dir = os.path.join(UPLOADS_DIR, session_id)
    clean_transcript = read_text(os.path.join(session_dir, "clean_transcript.txt"), "").strip()
    raw_transcript = read_text(os.path.join(session_dir, "transcript.txt"), "").strip()
    transcript_text = clean_transcript or raw_transcript
    notes_text = _compiled_notes_timeline_text(session_id) or read_text(os.path.join(session_dir, "notes.txt"), "")
    notes_summary = read_text(os.path.join(session_dir, "notes_summary.txt"), "")
    party = _read_party_meta(session_id)
    prep_context_text = _session_prompt_context_text(session_id)
    timeline_priority = _timeline_priority_texts(session_id)

    narrative = generate_game_narrative_from_text(
        transcript_text=transcript_text,
        party=party,
        notes_text=notes_text,
        notes_summary=notes_summary,
        promoted_notes_text=timeline_priority.get("promoted") or "",
        deemphasized_notes_text=timeline_priority.get("deemphasized") or "",
        prep_context_text=prep_context_text,
        narrative_guidance=narrative_guidance,
        target_word_count=target_word_count,
    )

    out_path = os.path.join(session_dir, "game_narrative.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(narrative.strip() + "\n")

    _set_session_status_fields(session_id, {
        "gameNarrativeUpdatedAt": int(time.time()),
    })
    return narrative

def _generate_notes_with_context(session_id: str, chunk_index: int, recent: list, summary_override: str = "", window_override: int = None):
    api_key = _openai_api_key()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set. Add it to .env or your environment.")

    party = _read_party_meta(session_id)
    prep_context_text = _session_prompt_context_text(session_id)
    summary = (summary_override or "").strip() or _read_notes_summary(session_id)
    window = _notes_window()
    if window_override is not None:
        try:
            window = max(1, min(20, int(window_override)))
        except Exception:
            window = _notes_window()
    transcript_block = "\n".join(
        [f"[{obj.get('chunkIndex', '?')}] {str(obj.get('text', '')).strip()}" for obj in (recent or [])]
    ).strip()

    system_prompt = _default_notes_system_prompt()
    user_prompt = (
        "Party data (if any):\n"
        f"{party if party else '(none)'}\n\n"
        "Campaign and session context (if any):\n"
        f"{prep_context_text if prep_context_text else '(none)'}\n\n"
        "Rolling summary so far:\n"
        f"{summary if summary else '(none)'}\n\n"
        f"Recent transcript window (last {window} chunks):\n"
        f"{transcript_block if transcript_block else '(none)'}\n"
    )

    raw = _chat_complete_text(system_prompt, user_prompt, _notes_model())
    obj = _extract_json_object(raw)
    if not obj:
        raise RuntimeError("Notes response was not valid JSON.")
    return obj

def generate_notes_with_openai(session_id: str, chunk_index: int):
    window = _notes_window()
    recent = _load_recent_transcripts(session_id, window)
    return _generate_notes_with_context(session_id, chunk_index, recent, window_override=window)

def _parse_chunked_transcript(transcript_text: str):
    chunks = []
    current_idx = None
    current_lines = []
    for raw in (transcript_text or "").splitlines():
        line = raw.strip()
        m = re.match(r"^\[(\d{1,6})\]\s*(.*)$", line)
        if m:
            # flush previous
            if current_idx is not None:
                chunks.append((current_idx, "\n".join(current_lines).strip()))
            current_idx = int(m.group(1))
            first_line = m.group(2).strip()
            current_lines = [first_line] if first_line else []
        else:
            if current_idx is None:
                # treat as preamble chunk -1
                current_idx = -1
                current_lines = []
            current_lines.append(line)
    if current_idx is not None:
        chunks.append((current_idx, "\n".join(current_lines).strip()))
    # normalize: remove empty chunks
    chunks = [(idx, txt) for idx, txt in chunks if txt.strip()]
    return chunks

def generate_notes_from_text(
    transcript_text: str,
    party: str = "",
    summary: str = "",
    system_prompt: str = "",
    window: int = 2,
    prep_context_text: str = "",
):
    api_key = _openai_api_key()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set. Add it to .env or your environment.")

    transcript_text = (transcript_text or "").strip()
    if not transcript_text:
        raise RuntimeError("No transcript text provided.")

    chunks = _parse_chunked_transcript(transcript_text)
    if chunks:
        window = max(1, min(20, int(window)))
        use_chunks = chunks[-window:]
        transcript_block = "\n".join([f"[{idx:04d}] {txt}" for idx, txt in use_chunks]).strip()
    else:
        transcript_block = transcript_text

    system_prompt = (system_prompt or "").strip() or _default_notes_system_prompt()

    user_prompt = (
        "Party data (if any):\n"
        f"{party.strip() if party else '(none)'}\n\n"
        "Campaign and session context (if any):\n"
        f"{prep_context_text.strip() if prep_context_text else '(none)'}\n\n"
        "Rolling summary so far:\n"
        f"{summary.strip() if summary else '(none)'}\n\n"
        "Transcript input (most recent chunk window):\n"
        f"{transcript_block}\n"
    )

    payload = {
        "model": _notes_model(),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
    }

    body = json.dumps(payload).encode("utf-8")
    req = Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    raw = _urlopen_with_retry(req, timeout=120)

    data = json.loads(raw)
    content = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )
    obj = _extract_json_object(content)
    if not obj:
        raise RuntimeError("Notes response was not valid JSON.")
    return obj

def _normalize_chunk_range(chunks, chunk_from, chunk_to):
    if not chunks:
        return []
    by_idx = {int(idx): txt for idx, txt in chunks}
    all_idxs = sorted(by_idx.keys())
    start = all_idxs[0] if chunk_from is None else int(chunk_from)
    end = all_idxs[-1] if chunk_to is None else int(chunk_to)
    if start > end:
        start, end = end, start
    return [(idx, by_idx[idx]) for idx in all_idxs if start <= idx <= end]

def _notes_test_payload(
    transcript_text: str,
    party: str = "",
    prior_summary: str = "",
    system_prompt: str = "",
    window: int = 2,
    chunk_from=None,
    chunk_to=None,
    use_context: bool = True,
    prep_context_text: str = "",
):
    transcript_text = (transcript_text or "").strip()
    if not transcript_text:
        raise RuntimeError("No transcript text provided.")

    prompt_used = (system_prompt or "").strip() or _default_notes_system_prompt()
    chunks = _parse_chunked_transcript(transcript_text)
    window = max(1, min(20, int(window)))
    use_context = bool(use_context)

    if not chunks:
        notes_obj = generate_notes_from_text(
            transcript_text=transcript_text,
            party=party,
            summary=prior_summary if use_context else "",
            system_prompt=prompt_used,
            window=window,
            prep_context_text=prep_context_text,
        )
        timeline = notes_obj.get("timeline") or []
        if isinstance(timeline, str):
            timeline = [timeline]
        compiled = "\n".join([f"[single] {str(x).strip()}" for x in timeline if str(x).strip()])
        return {
            "mode": "single",
            "promptUsed": prompt_used,
            "window": window,
            "useContext": use_context,
            "runs": [{
                "chunkIndex": None,
                "windowChunkIndexes": [],
                "notes": notes_obj,
            }],
            "compiledNotesPreview": compiled,
            "finalSummary": str(notes_obj.get("summary") or "").strip(),
            "aggregateState": _merge_notes_state([{"notes": notes_obj}]),
        }

    selected = _normalize_chunk_range(chunks, chunk_from, chunk_to)
    if not selected:
        raise RuntimeError("Chunk range selected no transcript chunks.")

    runs = []
    rolling_summary = (prior_summary or "").strip()
    compiled_lines = []
    processed = []
    entries_for_merge = []

    for idx, text in selected:
        processed.append((idx, text))
        recent = processed[-window:]
        recent_text = "\n".join([f"[{i:04d}] {t}" for i, t in recent])
        notes_obj = generate_notes_from_text(
            transcript_text=recent_text,
            party=party,
            summary=rolling_summary if use_context else "",
            system_prompt=prompt_used,
            window=window,
            prep_context_text=prep_context_text,
        )
        timeline = notes_obj.get("timeline") or []
        if isinstance(timeline, str):
            timeline = [timeline]
        timeline = [str(x).strip() for x in timeline if str(x).strip()]
        if timeline:
            for line in timeline:
                compiled_lines.append(f"[{idx:04d}] {line}")
        else:
            compiled_lines.append(f"[{idx:04d}] (no new events)")

        if use_context:
            next_summary = str(notes_obj.get("summary") or "").strip()
            if next_summary:
                rolling_summary = next_summary

        run_entry = {
            "chunkIndex": idx,
            "windowChunkIndexes": [int(i) for i, _ in recent],
            "notes": notes_obj,
        }
        runs.append(run_entry)
        entries_for_merge.append({"notes": notes_obj})

    return {
        "mode": "chunked",
        "promptUsed": prompt_used,
        "window": window,
        "useContext": use_context,
        "chunkFrom": selected[0][0],
        "chunkTo": selected[-1][0],
        "runs": runs,
        "compiledNotesPreview": "\n".join(compiled_lines).strip(),
        "finalSummary": rolling_summary,
        "aggregateState": _merge_notes_state(entries_for_merge),
    }

def _notes_lab_markdown(session_meta: dict, payload: dict, result: dict) -> str:
    session_meta = session_meta if isinstance(session_meta, dict) else {}
    payload = payload if isinstance(payload, dict) else {}
    result = result if isinstance(result, dict) else {}

    generated_at = time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime())
    session_id = str(session_meta.get("sessionId") or "").strip()
    session_name = str(session_meta.get("sessionName") or "").strip()
    campaign_id = str(session_meta.get("campaignId") or "").strip()
    campaign_name = str(session_meta.get("campaignName") or "").strip()
    transcript = str(payload.get("transcript") or "")
    prompt = str(payload.get("prompt") or "")
    party = str(payload.get("party") or "")
    prep_context = str(payload.get("prepContextText") or "")
    rolling_summary_seed = str(payload.get("summary") or "")
    chunk_from = payload.get("chunkFrom")
    chunk_to = payload.get("chunkTo")
    window = payload.get("window")
    use_context = bool(payload.get("useContext", True))
    mode = str(result.get("mode") or "")
    runs = result.get("runs") if isinstance(result.get("runs"), list) else []
    compiled_preview = str(result.get("compiledNotesPreview") or "")
    final_summary = str(result.get("finalSummary") or "")
    aggregate_state = result.get("aggregateState") if isinstance(result.get("aggregateState"), dict) else {}

    transcript_hash = hashlib.sha256(transcript.encode("utf-8")).hexdigest() if transcript else ""
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest() if prompt else ""
    prep_hash = hashlib.sha256(prep_context.encode("utf-8")).hexdigest() if prep_context else ""

    lines = [
        "# Notes Lab Run",
        "",
        "## Metadata",
        f"- Generated at: {generated_at}",
        f"- Session ID: {session_id or '(none)'}",
        f"- Session name: {session_name or '(none)'}",
        f"- Campaign ID: {campaign_id or '(none)'}",
        f"- Campaign name: {campaign_name or '(none)'}",
        f"- Mode: {mode or '(unknown)'}",
        f"- Notes window: {window}",
        f"- Chunk from: {chunk_from if chunk_from is not None else '(auto)'}",
        f"- Chunk to: {chunk_to if chunk_to is not None else '(auto)'}",
        f"- Use rolling context: {'yes' if use_context else 'no'}",
        f"- Notes calls generated: {len(runs)}",
        f"- Transcript SHA256: {transcript_hash or '(none)'}",
        f"- Prompt SHA256: {prompt_hash or '(none)'}",
        f"- Context SHA256: {prep_hash or '(none)'}",
        "",
        "## Prompt",
        "```text",
        prompt.strip() or "(none)",
        "```",
        "",
        "## Party Data",
        "```text",
        party.strip() or "(none)",
        "```",
        "",
        "## Campaign And Session Context",
        "```text",
        prep_context.strip() or "(none)",
        "```",
        "",
        "## Rolling Summary Seed",
        "```text",
        rolling_summary_seed.strip() or "(none)",
        "```",
        "",
        "## Transcript Input",
        "```text",
        transcript.strip() or "(none)",
        "```",
        "",
        "## Compiled Timeline Preview",
        "```text",
        compiled_preview.strip() or "(none)",
        "```",
        "",
        "## Final Summary",
        "```text",
        final_summary.strip() or "(none)",
        "```",
        "",
        "## Aggregate State",
        "```json",
        json.dumps(aggregate_state, indent=2),
        "```",
        "",
        "## Raw Result JSON",
        "```json",
        json.dumps(result, indent=2),
        "```",
        "",
    ]
    return "\n".join(lines)

def _save_notes_lab_run(session_meta: dict, payload: dict, result: dict):
    ensure_dir(NOTES_LAB_RUNS_DIR)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    session_id = _safe_filename_part((session_meta or {}).get("sessionId") or "", default="no-session", max_len=24)
    session_name = _safe_filename_part((session_meta or {}).get("sessionName") or "", default="unnamed", max_len=36)
    prompt_hash = hashlib.sha256(str((payload or {}).get("prompt") or "").encode("utf-8")).hexdigest()[:10]
    filename = f"{stamp}--{session_id}--{session_name}--prompt-{prompt_hash}.md"
    path = os.path.join(NOTES_LAB_RUNS_DIR, filename)
    markdown = _notes_lab_markdown(session_meta, payload, result)
    write_text(path, markdown)
    return path

def _append_notes(session_id: str, chunk_index: int, notes_obj: dict):
    session_dir = os.path.join(UPLOADS_DIR, session_id)
    ensure_dir(session_dir)

    timeline = notes_obj.get("timeline") or []
    if isinstance(timeline, str):
        timeline = [timeline]
    summary = (notes_obj.get("summary") or "").strip()

    notes_path = os.path.join(session_dir, "notes.txt")
    jsonl_path = os.path.join(session_dir, "notes.jsonl")

    with open(notes_path, "a", encoding="utf-8") as f:
        if timeline:
            for item in timeline:
                line = str(item).strip()
                if line:
                    f.write(f"[{chunk_index:04d}] {line}\n")
        else:
            f.write(f"[{chunk_index:04d}] (no new events)\n")

    with open(jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "chunkIndex": chunk_index,
            "notes": notes_obj,
            "createdAt": int(time.time()),
        }) + "\n")

    if summary:
        _write_notes_summary(session_id, summary)

    def mutate(status):
        status["notesLatest"] = timeline[-1] if timeline else ""
        status["notesUpdatedAt"] = int(time.time())
    _update_session_status(session_id, mutate, default={})

def _set_notes_error(session_id: str, chunk_index: int, message: str):
    def mutate(status):
        status["notesError"] = {
            "chunkIndex": chunk_index,
            "message": message,
            "updatedAt": int(time.time()),
        }
    _update_session_status(session_id, mutate, default={})

def _raise_if_reprocess_stopped(stop_event: threading.Event = None):
    if isinstance(stop_event, threading.Event) and stop_event.is_set():
        raise ReprocessStopped("Rebuild stopped by user.")

def rebuild_notes_for_session(session_id: str, party_override: str = "", regenerate_summary: bool = True, progress_cb=None, window_override: int = None, stop_event: threading.Event = None):
    session_dir = init_session(session_id)
    transcripts = _load_all_transcripts(session_id)
    if not transcripts:
        raise RuntimeError("No transcripts.jsonl entries found for this session.")

    if party_override.strip():
        party_path = os.path.join(session_dir, "party.txt")
        with open(party_path, "w", encoding="utf-8") as f:
            f.write(party_override.strip() + "\n")

    # Reset derived notes outputs so they can be rebuilt deterministically.
    for name in ["notes.txt", "notes.jsonl", "notes_summary.txt", "game_summary.txt"]:
        path = os.path.join(session_dir, name)
        if os.path.exists(path):
            os.remove(path)

    # Clear status note fields/errors before rebuild.
    def mutate(status):
        status.pop("notesError", None)
        status.pop("gameSummaryUpdatedAt", None)
        status["notesLatest"] = ""
        status["notesUpdatedAt"] = int(time.time())
    _update_session_status(session_id, mutate, default={})

    rebuilt = 0
    window = _notes_window()
    if window_override is not None:
        try:
            window = max(1, min(20, int(window_override)))
        except Exception:
            window = _notes_window()
    rolling_summary = ""
    total = len([t for t in transcripts if int(t.get("chunkIndex", -1)) >= 0])
    if progress_cb:
        progress_cb({"phase": "notes", "running": True, "processed": 0, "total": total, "window": window})
    processed = []
    for item in transcripts:
        _raise_if_reprocess_stopped(stop_event)
        chunk_index = int(item.get("chunkIndex", -1))
        if chunk_index < 0:
            continue
        processed.append(item)
        recent = processed[-window:]
        with NOTES_LOCK:
            notes_obj = _generate_notes_with_context(
                session_id=session_id,
                chunk_index=chunk_index,
                recent=recent,
                summary_override=rolling_summary,
                window_override=window,
            )
            _append_notes(session_id, chunk_index, notes_obj)
        rolling_summary = str(notes_obj.get("summary") or "").strip() or rolling_summary
        rebuilt += 1
        if progress_cb:
            progress_cb({"phase": "notes", "running": True, "processed": rebuilt, "total": total, "window": window})

    summary_text = ""
    if regenerate_summary:
        _raise_if_reprocess_stopped(stop_event)
        if progress_cb:
            progress_cb({"phase": "summary", "running": True, "processed": rebuilt, "total": total})
        summary_text = generate_game_summary_for_session(session_id)

    _rewrite_notes_timeline_file(session_id)

    if progress_cb:
        progress_cb({"phase": "done", "running": False, "processed": rebuilt, "total": total})

    return {
        "rebuiltChunks": rebuilt,
        "gameSummary": summary_text,
    }

def _set_reprocess_status(session_id: str, patch: dict):
    def mutate(status):
        current = status.get("reprocessStatus") or {}
        current.update(patch or {})
        status["reprocessStatus"] = current
        status["updatedAt"] = int(time.time())
        return dict(current)
    return _update_session_status(session_id, mutate, default={})

def _set_clean_transcript_status(session_id: str, patch: dict):
    def mutate(status):
        current = status.get("cleanTranscriptStatus") or {}
        current.update(patch or {})
        status["cleanTranscriptStatus"] = current
        status["updatedAt"] = int(time.time())
        return dict(current)
    return _update_session_status(session_id, mutate, default={})

def _set_transcript_backfill_status(session_id: str, patch: dict):
    def mutate(status):
        current = status.get("transcriptBackfillStatus") or {}
        current.update(patch or {})
        status["transcriptBackfillStatus"] = current
        status["updatedAt"] = int(time.time())
        return dict(current)
    return _update_session_status(session_id, mutate, default={})

def _run_reprocess_job(session_id: str, party_override: str, regenerate_summary: bool, window_override: int = None, job_id: str = "", stop_event: threading.Event = None):
    def progress(p):
        payload = {
            "running": bool(p.get("running", True)),
            "phase": str(p.get("phase") or "notes"),
            "processed": int(p.get("processed") or 0),
            "total": int(p.get("total") or 0),
            "error": "",
            "updatedAt": int(time.time()),
            "heartbeatAt": int(time.time()),
            "window": int(p.get("window") or 0),
            "jobId": str(job_id or ""),
            "ownerStartedAt": SERVER_STARTED_AT,
            "stopRequested": bool(isinstance(stop_event, threading.Event) and stop_event.is_set()),
        }
        _set_reprocess_status(session_id, payload)

    try:
        _set_reprocess_status(session_id, {
            "running": True,
            "phase": "starting",
            "processed": 0,
            "total": 0,
            "error": "",
            "startedAt": int(time.time()),
            "updatedAt": int(time.time()),
            "heartbeatAt": int(time.time()),
            "finishedAt": None,
            "window": int(window_override or 0),
            "jobId": str(job_id or ""),
            "ownerStartedAt": SERVER_STARTED_AT,
            "stopRequested": False,
        })
        result = rebuild_notes_for_session(
            session_id=session_id,
            party_override=party_override,
            regenerate_summary=regenerate_summary,
            progress_cb=progress,
            window_override=window_override,
            stop_event=stop_event,
        )
        _set_reprocess_status(session_id, {
            "running": False,
            "phase": "done",
            "processed": int(result.get("rebuiltChunks") or 0),
            "error": "",
            "finishedAt": int(time.time()),
            "updatedAt": int(time.time()),
            "heartbeatAt": int(time.time()),
            "window": int(window_override or 0),
            "jobId": str(job_id or ""),
            "ownerStartedAt": SERVER_STARTED_AT,
            "stopRequested": False,
        })
    except ReprocessStopped:
        _set_reprocess_status(session_id, {
            "running": False,
            "phase": "stopped",
            "error": "",
            "finishedAt": int(time.time()),
            "updatedAt": int(time.time()),
            "heartbeatAt": int(time.time()),
            "window": int(window_override or 0),
            "jobId": str(job_id or ""),
            "ownerStartedAt": SERVER_STARTED_AT,
            "stopRequested": True,
        })
    except Exception as e:
        _set_reprocess_status(session_id, {
            "running": False,
            "phase": "error",
            "error": str(e),
            "finishedAt": int(time.time()),
            "updatedAt": int(time.time()),
            "heartbeatAt": int(time.time()),
            "window": int(window_override or 0),
            "jobId": str(job_id or ""),
            "ownerStartedAt": SERVER_STARTED_AT,
        })
    finally:
        _unregister_reprocess_job(session_id, job_id=job_id)

def rebuild_clean_transcript_for_session(session_id: str, party_override: str = ""):
    session_dir = init_session(session_id)
    transcripts = _load_all_transcripts(session_id)
    if not transcripts:
        raise RuntimeError("No transcripts.jsonl entries found for this session.")

    if party_override.strip():
        party_path = os.path.join(session_dir, "party.txt")
        with open(party_path, "w", encoding="utf-8") as f:
            f.write(party_override.strip() + "\n")

    party = _read_party_meta(session_id)
    clean_transcript_path = os.path.join(session_dir, "clean_transcript.txt")
    transcript_items = []
    for item in transcripts:
        chunk_index = int(item.get("chunkIndex", -1))
        raw_text = str(item.get("text") or "").strip()
        if chunk_index < 0 or not raw_text:
            continue
        transcript_items.append({
            "chunkIndex": chunk_index,
            "text": raw_text,
        })

    cleaned_count = 0
    lines_out = []
    started_at = int(time.time())
    _set_clean_transcript_status(session_id, {
        "running": True,
        "phase": "cleaning",
        "processed": 0,
        "total": len(transcript_items),
        "error": "",
        "startedAt": started_at,
        "updatedAt": started_at,
        "finishedAt": None,
    })

    with open(clean_transcript_path, "w", encoding="utf-8") as f:
        f.write("")

    try:
        for idx, item in enumerate(transcript_items):
            chunk_index = int(item["chunkIndex"])
            raw_text = str(item["text"])
            prev_chunk_text = transcript_items[idx - 1]["text"] if idx > 0 else ""
            next_chunk_text = transcript_items[idx + 1]["text"] if idx + 1 < len(transcript_items) else ""
            cleaned = clean_transcript_chunk_with_openai(
                raw_text,
                party=party,
                chunk_index=chunk_index,
                prev_chunk_text=prev_chunk_text,
                next_chunk_text=next_chunk_text,
            )
            lines_out.append(f"[{chunk_index:04d}] {cleaned}".rstrip())
            cleaned_count += 1
            with open(clean_transcript_path, "w", encoding="utf-8") as f:
                if lines_out:
                    f.write("\n".join(lines_out).strip() + "\n")
                else:
                    f.write("")
            _set_session_status_fields(session_id, {
                "cleanTranscriptUpdatedAt": int(time.time()),
            })
            _set_clean_transcript_status(session_id, {
                "running": True,
                "phase": "cleaning",
                "processed": cleaned_count,
                "total": len(transcript_items),
                "error": "",
                "updatedAt": int(time.time()),
                "finishedAt": None,
            })

        _set_session_status_fields(session_id, {
            "cleanTranscriptUpdatedAt": int(time.time()),
        })
        _set_clean_transcript_status(session_id, {
            "running": False,
            "phase": "done",
            "processed": cleaned_count,
            "total": len(transcript_items),
            "error": "",
            "updatedAt": int(time.time()),
            "finishedAt": int(time.time()),
        })
    except Exception as e:
        _set_clean_transcript_status(session_id, {
            "running": False,
            "phase": "error",
            "processed": cleaned_count,
            "total": len(transcript_items),
            "error": str(e),
            "updatedAt": int(time.time()),
            "finishedAt": int(time.time()),
        })
        raise

    return {
        "rebuiltChunks": cleaned_count,
        "cleanTranscript": read_text(clean_transcript_path, ""),
    }

def backfill_missing_transcripts_for_session(session_id: str):
    session_dir = init_session(session_id)
    if not os.path.isdir(session_dir):
        raise RuntimeError("Session directory not found.")

    missing = _missing_transcript_audio_chunks(session_id)
    total = len(missing)
    started_at = int(time.time())
    _set_transcript_backfill_status(session_id, {
        "running": True,
        "phase": "backfilling",
        "processed": 0,
        "total": total,
        "error": "",
        "updatedAt": started_at,
        "finishedAt": None,
    })
    if total <= 0:
        return {
            "backfilledChunks": 0,
            "remainingMissing": 0,
            "totalMissing": 0,
        }

    recovered_indexes = []
    try:
        for item in missing:
            chunk_index = int(item.get("chunkIndex", -1))
            path = str(item.get("path") or "")
            if chunk_index < 0 or not path:
                continue
            text = transcribe_with_openai(path)
            _append_transcript(session_id, chunk_index, text)
            _rewrite_transcript_artifacts(session_id)
            recovered_indexes.append(chunk_index)
            _set_transcript_backfill_status(session_id, {
                "running": True,
                "phase": "backfilling",
                "processed": len(recovered_indexes),
                "total": total,
                "error": "",
                "updatedAt": int(time.time()),
                "finishedAt": None,
            })

        remaining_missing = _missing_transcript_audio_chunks(session_id)
        now = int(time.time())

        def mutate(status):
            status["transcriptBackfillUpdatedAt"] = now
            err = status.get("transcriptError") or {}
            try:
                err_idx = int(err.get("chunkIndex", -1))
            except Exception:
                err_idx = -1
            if err_idx in recovered_indexes and not remaining_missing:
                status.pop("transcriptError", None)
        _update_session_status(session_id, mutate, default={})

        _set_transcript_backfill_status(session_id, {
            "running": False,
            "phase": "done",
            "processed": len(recovered_indexes),
            "total": total,
            "error": "",
            "updatedAt": now,
            "finishedAt": now,
        })
        return {
            "backfilledChunks": len(recovered_indexes),
            "remainingMissing": len(remaining_missing),
            "totalMissing": total,
        }
    except Exception as e:
        _set_transcript_backfill_status(session_id, {
            "running": False,
            "phase": "error",
            "processed": len(recovered_indexes),
            "total": total,
            "error": str(e),
            "updatedAt": int(time.time()),
            "finishedAt": int(time.time()),
        })
        raise

def _run_transcript_backfill_job(session_id: str, job_id: str = ""):
    try:
        _set_transcript_backfill_status(session_id, {
            "running": True,
            "phase": "starting",
            "processed": 0,
            "total": 0,
            "error": "",
            "startedAt": int(time.time()),
            "updatedAt": int(time.time()),
            "finishedAt": None,
            "jobId": str(job_id or ""),
            "ownerStartedAt": SERVER_STARTED_AT,
        })
        result = backfill_missing_transcripts_for_session(session_id=session_id)
        _set_transcript_backfill_status(session_id, {
            "running": False,
            "phase": "done",
            "processed": int(result.get("backfilledChunks") or 0),
            "total": int(result.get("totalMissing") or 0),
            "remainingMissing": int(result.get("remainingMissing") or 0),
            "error": "",
            "updatedAt": int(time.time()),
            "finishedAt": int(time.time()),
            "jobId": str(job_id or ""),
            "ownerStartedAt": SERVER_STARTED_AT,
        })
    except Exception as e:
        _set_transcript_backfill_status(session_id, {
            "running": False,
            "phase": "error",
            "error": str(e),
            "updatedAt": int(time.time()),
            "finishedAt": int(time.time()),
            "jobId": str(job_id or ""),
            "ownerStartedAt": SERVER_STARTED_AT,
        })
    finally:
        _unregister_transcript_backfill_job(session_id, job_id=job_id)

def _run_clean_transcript_job(session_id: str, party_override: str, job_id: str = ""):
    try:
        _set_clean_transcript_status(session_id, {
            "running": True,
            "phase": "starting",
            "processed": 0,
            "total": 0,
            "error": "",
            "startedAt": int(time.time()),
            "updatedAt": int(time.time()),
            "finishedAt": None,
            "jobId": str(job_id or ""),
            "ownerStartedAt": SERVER_STARTED_AT,
        })
        result = rebuild_clean_transcript_for_session(session_id=session_id, party_override=party_override)
        _set_clean_transcript_status(session_id, {
            "running": False,
            "phase": "done",
            "processed": int(result.get("rebuiltChunks") or 0),
            "error": "",
            "updatedAt": int(time.time()),
            "finishedAt": int(time.time()),
            "jobId": str(job_id or ""),
            "ownerStartedAt": SERVER_STARTED_AT,
        })
    except Exception as e:
        _set_clean_transcript_status(session_id, {
            "running": False,
            "phase": "error",
            "error": str(e),
            "updatedAt": int(time.time()),
            "finishedAt": int(time.time()),
            "jobId": str(job_id or ""),
            "ownerStartedAt": SERVER_STARTED_AT,
        })
    finally:
        _unregister_clean_transcript_job(session_id, job_id=job_id)

def transcribe_async(session_id: str, chunk_index: int, path: str):
    try:
        text = transcribe_with_openai(path)
        _append_transcript(session_id, chunk_index, text)
        try:
            with NOTES_LOCK:
                notes_obj = generate_notes_with_openai(session_id, chunk_index)
                _append_notes(session_id, chunk_index, notes_obj)
        except Exception as e:
            _set_notes_error(session_id, chunk_index, str(e))
    except Exception as e:
        _set_transcript_error(session_id, chunk_index, str(e))

class Handler(SimpleHTTPRequestHandler):
    # IMPORTANT: this handler will be instantiated with directory=BASE_DIR (see main()).

    def _send_json(self, code: int, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or "0")
        return self.rfile.read(length) if length > 0 else b""

    def do_GET(self):
        # Root: go to your real file name
        if self.path == "/" or self.path.startswith("/?"):
            self.send_response(302)
            self.send_header("Location", "/dnd-audio.html")
            self.end_headers()
            return

        # Backward compat: underscore -> hyphen
        if self.path.startswith("/dnd_audio.html"):
            self.send_response(302)
            self.send_header("Location", "/dnd-audio.html")
            self.end_headers()
            return

        parsed = urlparse(self.path)
        if parsed.path == "/api/sessions/list":
            try:
                qs = parse_qs(parsed.query)
                limit = int((qs.get("limit") or ["50"])[0])
                limit = max(1, min(200, limit))
                campaign_id = str((qs.get("campaignId") or [""])[0]).strip()
                if campaign_id:
                    campaign_id = _safe_campaign_id(campaign_id)
                self._send_json(200, {"ok": True, "sessions": list_sessions(limit, campaign_id=campaign_id)})
            except Exception as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/api/campaigns/list":
            try:
                qs = parse_qs(parsed.query)
                limit = int((qs.get("limit") or ["100"])[0])
                self._send_json(200, {"ok": True, "campaigns": list_campaigns(limit)})
            except Exception as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/api/campaign/get":
            try:
                qs = parse_qs(parsed.query)
                campaign_id = _safe_campaign_id((qs.get("campaignId") or [""])[0])
                campaign = read_campaign(campaign_id)
                self._send_json(200, {
                    "ok": True,
                    "campaign": campaign,
                    "contextPreview": _build_context_snapshot(campaign).get("contextText") or "",
                })
            except Exception as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/api/sessions/get":
            try:
                qs = parse_qs(parsed.query)
                session_id = safe_session_id((qs.get("sessionId") or [""])[0])
                data = read_session_text(session_id)
                self._send_json(200, {"ok": True, "sessionId": session_id, **data})
            except Exception as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/api/tracking/state":
            try:
                qs = parse_qs(parsed.query)
                session_id = safe_session_id((qs.get("sessionId") or [""])[0])
                tracking_state = _get_tracking_state(session_id)
                self._send_json(200, {"ok": True, "sessionId": session_id, "trackingState": tracking_state})
            except Exception as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/api/prep/get":
            try:
                qs = parse_qs(parsed.query)
                session_id = safe_session_id((qs.get("sessionId") or [""])[0])
                prep = _read_prep_context(session_id)
                self._send_json(200, {
                    "ok": True,
                    "sessionId": session_id,
                    "prepContext": prep,
                    "prepContextText": _prep_context_text(prep),
                })
            except Exception as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/api/notes/default-prompt":
            try:
                self._send_json(200, {"ok": True, "prompt": _default_notes_system_prompt()})
            except Exception as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            return

        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/campaign/save":
            try:
                body = self._read_body().decode("utf-8") if self.headers.get("Content-Length") else ""
                data = json.loads(body) if body else {}
                campaign_id = _safe_campaign_id(str(data.get("campaignId") or data.get("name") or "default"))
                campaign = write_campaign(campaign_id, {
                    "campaignId": campaign_id,
                    "name": str(data.get("name") or campaign_id),
                    "party": str(data.get("party") or ""),
                    "campaignSummary": str(data.get("campaignSummary") or ""),
                    "dungeonMakerJson": data.get("dungeonMakerJson") if isinstance(data.get("dungeonMakerJson"), dict) else {},
                    "sessionSummaries": data.get("sessionSummaries") or [],
                    "createdAt": data.get("createdAt"),
                })
                self._send_json(200, {
                    "ok": True,
                    "campaign": campaign,
                    "contextPreview": _build_context_snapshot(campaign).get("contextText") or "",
                })
            except Exception as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/api/campaign/import-dungeon":
            try:
                body = self._read_body().decode("utf-8")
                data = json.loads(body) if body else {}
                campaign_id = _safe_campaign_id(str(data.get("campaignId") or ""))
                campaign = read_campaign(campaign_id)
                dungeon = data.get("dungeonMakerJson")
                if not isinstance(dungeon, dict):
                    raise ValueError("dungeonMakerJson must be a JSON object.")
                campaign["dungeonMakerJson"] = dungeon
                campaign = write_campaign(campaign_id, campaign)
                self._send_json(200, {
                    "ok": True,
                    "campaign": campaign,
                    "contextPreview": _build_context_snapshot(campaign).get("contextText") or "",
                })
            except Exception as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/api/session/context/refresh":
            try:
                body = self._read_body().decode("utf-8")
                data = json.loads(body) if body else {}
                session_id = safe_session_id(str(data.get("sessionId") or ""))
                session_dir = init_session(session_id)
                status_path = os.path.join(session_dir, "status.json")
                status = read_json(status_path, default={})
                campaign_id = str(status.get("campaignId") or data.get("campaignId") or "default")
                status, campaign = _assign_session_campaign(session_id, campaign_id)
                self._send_json(200, {
                    "ok": True,
                    "sessionId": session_id,
                    "campaignId": status.get("campaignId") or "",
                    "campaignName": campaign.get("name") or status.get("campaignId") or "",
                    "contextSnapshot": status.get("contextSnapshot") or {},
                })
            except Exception as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/api/session/assign-campaign":
            try:
                body = self._read_body().decode("utf-8")
                data = json.loads(body) if body else {}
                session_id = safe_session_id(str(data.get("sessionId") or ""))
                campaign_id = str(data.get("campaignId") or "").strip()
                if not campaign_id:
                    raise ValueError("campaignId is required.")
                status, campaign = _assign_session_campaign(session_id, campaign_id)
                self._send_json(200, {
                    "ok": True,
                    "sessionId": session_id,
                    "campaignId": status.get("campaignId") or "",
                    "campaignName": campaign.get("name") or status.get("campaignId") or "",
                    "contextSnapshot": status.get("contextSnapshot") or {},
                })
            except Exception as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/api/session/start":
            try:
                body = self._read_body().decode("utf-8") if self.headers.get("Content-Length") else ""
                data = json.loads(body) if body else {}
                session_id = safe_session_id(str(data.get("sessionId", "")))
                session_name = sanitize_session_name(data.get("sessionName", ""))
                campaign_id = _safe_campaign_id(str(data.get("campaignId") or "default"))
                party = str(data.get("party") or "").strip()
                campaign = read_campaign(campaign_id)
                session_dir = init_session(session_id, campaign_id=campaign_id)
                status_path = os.path.join(session_dir, "status.json")
                status = read_json(status_path, default={})
                if session_name:
                    status["sessionName"] = session_name
                status["campaignId"] = campaign_id
                status["contextSnapshot"] = _build_context_snapshot(campaign)
                if party:
                    write_text(os.path.join(session_dir, "party.txt"), party + "\n")
                    status["party"] = party
                status["updatedAt"] = int(time.time())
                write_json_atomic(status_path, status)
                self._send_json(200, {
                    "ok": True,
                    "sessionId": session_id,
                    "sessionName": session_name,
                    "campaignId": campaign_id,
                    "campaignName": campaign.get("name") or campaign_id,
                    "contextSnapshot": status.get("contextSnapshot") or {},
                    "sessionDir": os.path.relpath(session_dir, BASE_DIR),
                    "statusUrl": f"/uploads/{session_id}/status.json",
                })
            except Exception as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/api/upload":
            try:
                qs = parse_qs(parsed.query)
                session_id = safe_session_id((qs.get("sessionId") or [""])[0])
                chunk_index_str = (qs.get("chunkIndex") or [""])[0]
                chunk_index = int(chunk_index_str)

                session_dir = init_session(session_id)

                blob = self._read_body()
                if not blob:
                    raise ValueError("Empty body; expected audio bytes in POST body.")

                content_type = self.headers.get("Content-Type") or ""
                ext = _ext_from_content_type(content_type)
                filename = f"chunk_{chunk_index:04d}.{ext}"
                out_path = os.path.join(session_dir, filename)
                with open(out_path, "wb") as f:
                    f.write(blob)

                update_status_for_chunk(session_id, chunk_index, filename, len(blob))

                # Kick off transcription in the background (non-blocking)
                t = threading.Thread(
                    target=transcribe_async,
                    args=(session_id, chunk_index, out_path),
                    daemon=True,
                )
                t.start()

                self._send_json(200, {
                    "ok": True,
                    "sessionId": session_id,
                    "chunkIndex": chunk_index,
                    "filename": filename,
                    "bytes": len(blob),
                    "statusUrl": f"/uploads/{session_id}/status.json",
                })
            except Exception as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            return
        if parsed.path == "/api/meta":
            try:
                body = self._read_body().decode("utf-8")
                data = json.loads(body) if body else {}

                session_id = safe_session_id(str(data.get("sessionId", "")))
                party = (data.get("party") or "").strip()

                if not party:
                    raise ValueError("No party metadata provided.")

                session_dir = init_session(session_id)

                # Write party.txt
                party_path = os.path.join(session_dir, "party.txt")
                with open(party_path, "w", encoding="utf-8") as f:
                    f.write(party + "\n")

                # Also reflect into status.json for live UI
                status_path = os.path.join(session_dir, "status.json")
                status = read_json(status_path, default={})
                status["party"] = party
                status["updatedAt"] = int(time.time())
                write_json_atomic(status_path, status)

                self._send_json(200, {
                    "ok": True,
                    "sessionId": session_id,
                    "saved": "party.txt"
                })
            except Exception as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/api/session/name":
            try:
                body = self._read_body().decode("utf-8")
                data = json.loads(body) if body else {}
                session_id = safe_session_id(str(data.get("sessionId", "")))
                session_name = sanitize_session_name(data.get("sessionName", ""))
                session_dir = init_session(session_id)
                status_path = os.path.join(session_dir, "status.json")
                status = read_json(status_path, default={})
                status["sessionName"] = session_name
                status["updatedAt"] = int(time.time())
                write_json_atomic(status_path, status)
                self._send_json(200, {"ok": True, "sessionId": session_id, "sessionName": session_name})
            except Exception as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/api/session/notes/timeline/update":
            try:
                body = self._read_body().decode("utf-8")
                data = json.loads(body) if body else {}
                session_id = safe_session_id(str(data.get("sessionId", "")))
                item_id = str(data.get("itemId") or "").strip()
                if not item_id:
                    raise ValueError("itemId is required.")

                timeline_items = {str(item.get("id") or ""): item for item in _timeline_entries(session_id)}
                if item_id not in timeline_items:
                    raise ValueError("Timeline item not found.")

                edited_text = str(data.get("editedText") or "").strip()
                priority = _coerce_priority(data.get("priority"))

                overrides = _read_notes_overrides(session_id)
                current = overrides.get(item_id)
                if not isinstance(current, dict):
                    current = {}

                original_text = str(timeline_items[item_id].get("originalText") or "").strip()
                if edited_text and edited_text != original_text:
                    current["editedText"] = edited_text
                else:
                    current.pop("editedText", None)

                if priority != 0:
                    current["priority"] = priority
                else:
                    current.pop("priority", None)

                if current:
                    current["updatedAt"] = int(time.time())
                    overrides[item_id] = current
                else:
                    overrides.pop(item_id, None)

                _write_notes_overrides(session_id, overrides)
                _rewrite_notes_timeline_file(session_id)
                structured = _latest_notes_structured(session_id)
                updated_item = next((item for item in (structured.get("timelineItems") or []) if str(item.get("id") or "") == item_id), None)
                self._send_json(200, {
                    "ok": True,
                    "sessionId": session_id,
                    "item": updated_item,
                    "notesTimeline": structured.get("timelineItems") or [],
                    "notesTimelineText": structured.get("timelineText") or "",
                })
            except Exception as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/api/prep/save":
            try:
                body = self._read_body().decode("utf-8")
                data = json.loads(body) if body else {}
                session_id = safe_session_id(str(data.get("sessionId", "")))
                prep_context = data.get("prepContext")
                if prep_context is None:
                    prep_context = {}
                _write_prep_context(session_id, prep_context)
                self._send_json(200, {
                    "ok": True,
                    "sessionId": session_id,
                    "prepContext": _read_prep_context(session_id),
                    "prepContextText": _prep_context_text(_read_prep_context(session_id)),
                })
            except Exception as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/api/prep/import":
            try:
                body = self._read_body().decode("utf-8")
                data = json.loads(body) if body else {}
                session_id = safe_session_id(str(data.get("sessionId", "")))
                prep_context = data.get("prepContext")
                if not isinstance(prep_context, dict):
                    raise ValueError("prepContext must be a JSON object.")
                _write_prep_context(session_id, prep_context)
                self._send_json(200, {
                    "ok": True,
                    "sessionId": session_id,
                    "prepContext": prep_context,
                    "prepContextText": _prep_context_text(prep_context),
                })
            except Exception as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/api/tracking/event":
            try:
                body = self._read_body().decode("utf-8")
                data = json.loads(body) if body else {}
                payload = _validate_tracking_event_payload(data)
                session_id = payload["sessionId"]
                event = _new_tracking_event(
                    session_id=session_id,
                    entity_type=payload["entityType"],
                    entity_id=payload["entityId"],
                    entity_name=payload["entityName"],
                    event_type=payload["eventType"],
                    delta=payload.get("delta"),
                    hp=payload.get("hp"),
                    note=payload.get("note"),
                )
                with TRACKING_LOCK:
                    _append_tracking_event(session_id, event)
                    tracking_state = _get_tracking_state(session_id)
                self._send_json(200, {
                    "ok": True,
                    "sessionId": session_id,
                    "event": event,
                    "trackingState": tracking_state,
                })
            except Exception as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/api/tracking/monster/add":
            try:
                body = self._read_body().decode("utf-8")
                data = json.loads(body) if body else {}
                session_id = safe_session_id(str(data.get("sessionId") or ""))
                name = str(data.get("name") or "").strip()
                if not name:
                    raise ValueError("Monster name is required.")
                hp = int(data.get("hp"))
                entity_id = _next_monster_id(session_id, name)
                event = _new_tracking_event(
                    session_id=session_id,
                    entity_type="monster",
                    entity_id=entity_id,
                    entity_name=name,
                    event_type="create",
                    hp=hp,
                )
                with TRACKING_LOCK:
                    _append_tracking_event(session_id, event)
                    tracking_state = _get_tracking_state(session_id)
                self._send_json(200, {
                    "ok": True,
                    "sessionId": session_id,
                    "event": event,
                    "trackingState": tracking_state,
                })
            except Exception as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/api/tracking/monster/remove":
            try:
                body = self._read_body().decode("utf-8")
                data = json.loads(body) if body else {}
                session_id = safe_session_id(str(data.get("sessionId") or ""))
                entity_id = str(data.get("entityId") or "").strip()
                if not entity_id:
                    raise ValueError("entityId is required.")
                state = _get_tracking_state(session_id)
                name = entity_id
                for m in state.get("monsters") or []:
                    if str(m.get("entityId")) == entity_id:
                        name = str(m.get("name") or entity_id)
                        break
                event = _new_tracking_event(
                    session_id=session_id,
                    entity_type="monster",
                    entity_id=entity_id,
                    entity_name=name,
                    event_type="remove",
                )
                with TRACKING_LOCK:
                    _append_tracking_event(session_id, event)
                    tracking_state = _get_tracking_state(session_id)
                self._send_json(200, {
                    "ok": True,
                    "sessionId": session_id,
                    "event": event,
                    "trackingState": tracking_state,
                })
            except Exception as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/api/tracking/undo":
            try:
                body = self._read_body().decode("utf-8")
                data = json.loads(body) if body else {}
                session_id = safe_session_id(str(data.get("sessionId") or ""))
                entity_id = str(data.get("entityId") or "").strip()
                if not entity_id:
                    raise ValueError("entityId is required.")

                with TRACKING_LOCK:
                    events = _read_tracking_events(session_id)
                    target = _last_mutable_event_for_entity(events, entity_id)
                    if not target:
                        raise ValueError("No mutable event to undo for this entity.")
                    before = _entity_state_before_event(events, entity_id, str(target.get("eventId") or ""))
                    undo_type = str(target.get("eventType") or "").lower()
                    entity_type = str(target.get("entityType") or "party").lower()
                    entity_name = str(target.get("entityName") or entity_id)
                    undo_extra = {
                        "undoEventType": undo_type,
                        "undoOfEventId": str(target.get("eventId") or ""),
                    }
                    if undo_type in ("damage", "heal"):
                        undo_extra["delta"] = abs(_event_delta(target))
                    elif undo_type == "hp_set":
                        undo_extra["hp"] = int(before.get("hp") or 0)
                    elif undo_type == "note":
                        undo_extra["note"] = str(before.get("note") or "")
                    else:
                        raise ValueError("Unsupported undo target.")

                    event = _new_tracking_event(
                        session_id=session_id,
                        entity_type=entity_type,
                        entity_id=entity_id,
                        entity_name=entity_name,
                        event_type="undo",
                        **undo_extra,
                    )
                    _append_tracking_event(session_id, event)
                    tracking_state = _get_tracking_state(session_id)

                self._send_json(200, {
                    "ok": True,
                    "sessionId": session_id,
                    "event": event,
                    "trackingState": tracking_state,
                })
            except Exception as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/api/notes/test":
            try:
                body = self._read_body().decode("utf-8")
                data = json.loads(body) if body else {}
                transcript_text = (data.get("transcript") or "").strip()
                party = (data.get("party") or "").strip()
                summary = (data.get("summary") or "").strip()
                prep_context_text = (data.get("prepContextText") or "").strip()
                system_prompt = (data.get("prompt") or "").strip()
                window = data.get("window") or 2
                chunk_from = data.get("chunkFrom")
                chunk_to = data.get("chunkTo")
                use_context = bool(data.get("useContext", True))

                payload = _notes_test_payload(
                    transcript_text=transcript_text,
                    party=party,
                    prior_summary=summary,
                    system_prompt=system_prompt,
                    window=window,
                    chunk_from=chunk_from,
                    chunk_to=chunk_to,
                    use_context=use_context,
                    prep_context_text=prep_context_text,
                )

                self._send_json(200, {
                    "ok": True,
                    **payload,
                })
            except Exception as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/api/notes/test/save-markdown":
            try:
                body = self._read_body().decode("utf-8")
                data = json.loads(body) if body else {}
                session_meta = data.get("sessionMeta") if isinstance(data.get("sessionMeta"), dict) else {}
                payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
                result = data.get("result") if isinstance(data.get("result"), dict) else {}
                if not payload or not result:
                    raise ValueError("payload and result are required.")
                path = _save_notes_lab_run(session_meta, payload, result)
                self._send_json(200, {
                    "ok": True,
                    "path": path,
                    "relativePath": os.path.relpath(path, BASE_DIR),
                })
            except Exception as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/api/summary/generate":
            try:
                body = self._read_body().decode("utf-8")
                data = json.loads(body) if body else {}
                session_id = safe_session_id(str(data.get("sessionId", "")))
                summary = generate_game_summary_for_session(session_id)
                self._send_json(200, {
                    "ok": True,
                    "sessionId": session_id,
                    "gameSummary": summary,
                })
            except Exception as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/api/narrative/generate":
            try:
                body = self._read_body().decode("utf-8")
                data = json.loads(body) if body else {}
                session_id = safe_session_id(str(data.get("sessionId", "")))
                narrative_guidance = str(data.get("narrativeGuidance") or "")
                target_word_count = int(data.get("narrativeWordCount") or 600)
                narrative = generate_game_narrative_for_session(
                    session_id,
                    narrative_guidance=narrative_guidance,
                    target_word_count=target_word_count,
                )
                self._send_json(200, {
                    "ok": True,
                    "sessionId": session_id,
                    "gameNarrative": narrative,
                })
            except Exception as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/api/session/reprocess":
            try:
                body = self._read_body().decode("utf-8")
                data = json.loads(body) if body else {}
                session_id = safe_session_id(str(data.get("sessionId", "")))
                party_override = str(data.get("party") or "")
                regenerate_summary = bool(data.get("regenerateSummary", True))
                result = rebuild_notes_for_session(
                    session_id=session_id,
                    party_override=party_override,
                    regenerate_summary=regenerate_summary,
                )
                self._send_json(200, {
                    "ok": True,
                    "sessionId": session_id,
                    **result,
                })
            except Exception as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/api/session/reprocess/stop":
            try:
                body = self._read_body().decode("utf-8")
                data = json.loads(body) if body else {}
                session_id = safe_session_id(str(data.get("sessionId", "")))
                status_path = os.path.join(UPLOADS_DIR, session_id, "status.json")
                status = read_json(status_path, default={})
                rep = _normalize_reprocess_status(session_id, status, persist=True)

                if rep.get("running"):
                    requested = _request_reprocess_stop(session_id, str(rep.get("jobId") or ""))
                    if requested:
                        _set_reprocess_status(session_id, {
                            "running": True,
                            "phase": "stop_requested",
                            "updatedAt": int(time.time()),
                            "heartbeatAt": int(time.time()),
                            "stopRequested": True,
                        })
                        self._send_json(200, {
                            "ok": True,
                            "sessionId": session_id,
                            "stopRequested": True,
                            "running": True,
                        })
                        return

                _set_reprocess_status(session_id, {
                    "running": False,
                    "phase": "stopped",
                    "error": "",
                    "finishedAt": int(time.time()),
                    "updatedAt": int(time.time()),
                    "heartbeatAt": int(time.time()),
                    "stopRequested": False,
                })
                self._send_json(200, {
                    "ok": True,
                    "sessionId": session_id,
                    "stopRequested": False,
                    "running": False,
                    "cleared": True,
                })
            except Exception as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/api/session/reprocess/start":
            try:
                body = self._read_body().decode("utf-8")
                data = json.loads(body) if body else {}
                session_id = safe_session_id(str(data.get("sessionId", "")))
                party_override = str(data.get("party") or "")
                regenerate_summary = bool(data.get("regenerateSummary", True))
                window_override = data.get("windowOverride")
                if window_override is not None:
                    window_override = max(1, min(20, int(window_override)))

                # Avoid concurrent rebuilds for the same session.
                status = read_json(os.path.join(UPLOADS_DIR, session_id, "status.json"), default={})
                rep = _normalize_reprocess_status(session_id, status, persist=True)
                if rep.get("running"):
                    self._send_json(409, {"ok": False, "error": "Reprocess already running for this session."})
                    return
                clean_status = _normalize_clean_transcript_status(session_id, status, persist=True)
                if clean_status.get("running"):
                    self._send_json(409, {"ok": False, "error": "Clean transcript rebuild is already running for this session."})
                    return
                backfill_status = _normalize_transcript_backfill_status(session_id, status, persist=True)
                if backfill_status.get("running"):
                    self._send_json(409, {"ok": False, "error": "Transcript backfill is already running for this session."})
                    return

                job_id = uuid.uuid4().hex
                stop_event = threading.Event()
                t = threading.Thread(
                    target=_run_reprocess_job,
                    args=(session_id, party_override, regenerate_summary, window_override, job_id, stop_event),
                    daemon=True,
                )
                _register_reprocess_job(session_id, job_id, stop_event, t)
                t.start()
                self._send_json(200, {"ok": True, "sessionId": session_id, "started": True, "window": window_override, "jobId": job_id})
            except Exception as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/api/session/clean-transcript/start":
            try:
                body = self._read_body().decode("utf-8")
                data = json.loads(body) if body else {}
                session_id = safe_session_id(str(data.get("sessionId", "")))
                party_override = str(data.get("party") or "")
                status = read_json(_session_status_path(session_id), default={})
                rep = _normalize_reprocess_status(session_id, status, persist=True)
                if rep.get("running"):
                    self._send_json(409, {"ok": False, "error": "Notes rebuild is already running for this session."})
                    return
                backfill_status = _normalize_transcript_backfill_status(session_id, status, persist=True)
                if backfill_status.get("running"):
                    self._send_json(409, {"ok": False, "error": "Transcript backfill is already running for this session."})
                    return
                clean_status = _normalize_clean_transcript_status(session_id, status, persist=True)
                if clean_status.get("running"):
                    self._send_json(409, {"ok": False, "error": "Clean transcript rebuild already running for this session."})
                    return

                job_id = uuid.uuid4().hex
                t = threading.Thread(
                    target=_run_clean_transcript_job,
                    args=(session_id, party_override, job_id),
                    daemon=True,
                )
                _register_clean_transcript_job(session_id, job_id, t)
                t.start()
                self._send_json(200, {
                    "ok": True,
                    "sessionId": session_id,
                    "started": True,
                    "jobId": job_id,
                })
            except Exception as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/api/session/transcripts/backfill/start":
            try:
                body = self._read_body().decode("utf-8")
                data = json.loads(body) if body else {}
                session_id = safe_session_id(str(data.get("sessionId", "")))
                status = read_json(_session_status_path(session_id), default={})
                rep = _normalize_reprocess_status(session_id, status, persist=True)
                if rep.get("running"):
                    self._send_json(409, {"ok": False, "error": "Notes rebuild is already running for this session."})
                    return
                clean_status = _normalize_clean_transcript_status(session_id, status, persist=True)
                if clean_status.get("running"):
                    self._send_json(409, {"ok": False, "error": "Clean transcript rebuild is already running for this session."})
                    return
                backfill_status = _normalize_transcript_backfill_status(session_id, status, persist=True)
                if backfill_status.get("running"):
                    self._send_json(409, {"ok": False, "error": "Transcript backfill already running for this session."})
                    return

                missing = _missing_transcript_audio_chunks(session_id)
                if not missing:
                    self._send_json(200, {
                        "ok": True,
                        "sessionId": session_id,
                        "started": False,
                        "missingTranscriptChunks": [],
                    })
                    return

                job_id = uuid.uuid4().hex
                t = threading.Thread(
                    target=_run_transcript_backfill_job,
                    args=(session_id, job_id),
                    daemon=True,
                )
                _register_transcript_backfill_job(session_id, job_id, t)
                t.start()
                self._send_json(200, {
                    "ok": True,
                    "sessionId": session_id,
                    "started": True,
                    "jobId": job_id,
                    "missingTranscriptChunks": [int(item.get("chunkIndex") or -1) for item in missing if int(item.get("chunkIndex") or -1) >= 0],
                })
            except Exception as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            return

        self._send_json(404, {"ok": False, "error": "Unknown endpoint."})

def main():
    ensure_dir(UPLOADS_DIR)
    host, port = "127.0.0.1", 8000

    # This is the key improvement: handler serves files relative to BASE_DIR no matter where you run from.
    handler_cls = partial(Handler, directory=BASE_DIR)

    httpd = ThreadingHTTPServer((host, port), handler_cls)
    print(f"Serving on http://{host}:{port}")
    print(f"Base dir: {BASE_DIR}")
    print(f"Uploads : {UPLOADS_DIR}")
    httpd.serve_forever()

if __name__ == "__main__":
    main()
