#!/usr/bin/env python3
import json
import os
import re
import time
import threading
import uuid
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
ENV_PATH = os.path.join(BASE_DIR, ".env")
OPENAI_API_KEY = None
NOTES_LOCK = threading.Lock()
REPROCESS_LOCK = threading.Lock()
TRACKING_LOCK = threading.Lock()
NOTES_MODEL_DEFAULT = "gpt-4o-mini"
NOTES_WINDOW_DEFAULT = 5
SUMMARY_MODEL_DEFAULT = "gpt-4o-mini"
CLEAN_TRANSCRIPT_MODEL_DEFAULT = "gpt-4o-mini"
NARRATIVE_MODEL_DEFAULT = "gpt-4o-mini"

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

def write_json_atomic(path: str, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)

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

def init_session(session_id: str) -> str:
    ensure_dir(UPLOADS_DIR)
    session_dir = os.path.join(UPLOADS_DIR, session_id)
    ensure_dir(session_dir)

    status_path = os.path.join(session_dir, "status.json")
    status = read_json(status_path, default=None)
    if status is None:
        status = {
            "sessionId": session_id,
            "sessionName": "",
            "createdAt": int(time.time()),
            "updatedAt": int(time.time()),
            "chunks": [],
            "latestChunkIndex": -1,
            "notes": "",
        }
        write_json_atomic(status_path, status)

    return session_dir

def list_sessions(limit: int = 50):
    ensure_dir(UPLOADS_DIR)
    sessions = []
    for name in os.listdir(UPLOADS_DIR):
        session_dir = os.path.join(UPLOADS_DIR, name)
        if not os.path.isdir(session_dir):
            continue
        if not SESSION_ID_RE.match(name):
            continue
        status_path = os.path.join(session_dir, "status.json")
        status = read_json(status_path, default={})
        mtime = int(os.path.getmtime(session_dir))
        sessions.append({
            "sessionId": name,
            "sessionName": str(status.get("sessionName") or ""),
            "updatedAt": status.get("updatedAt") or mtime,
            "createdAt": status.get("createdAt") or mtime,
            "chunkCount": len(status.get("chunks") or []),
            "statusUrl": f"/uploads/{name}/status.json",
        })
    sessions.sort(key=lambda s: s.get("updatedAt", 0), reverse=True)
    return sessions[:limit]

def read_session_text(session_id: str):
    session_dir = os.path.join(UPLOADS_DIR, session_id)
    transcript_path = os.path.join(session_dir, "transcript.txt")
    clean_transcript_path = os.path.join(session_dir, "clean_transcript.txt")
    notes_path = os.path.join(session_dir, "notes.txt")
    summary_path = os.path.join(session_dir, "notes_summary.txt")
    game_summary_path = os.path.join(session_dir, "game_summary.txt")
    game_narrative_path = os.path.join(session_dir, "game_narrative.txt")
    party_path = os.path.join(session_dir, "party.txt")
    structured = _latest_notes_structured(session_id)
    status = read_json(os.path.join(session_dir, "status.json"), default={})
    tracking_state = status.get("trackingState") or _get_tracking_state(session_id)
    prep_context = _read_prep_context(session_id)
    return {
        "transcript": read_text(transcript_path, ""),
        "cleanTranscript": read_text(clean_transcript_path, ""),
        "notes": read_text(notes_path, ""),
        "party": read_text(party_path, ""),
        "summary": read_text(summary_path, ""),
        "gameSummary": read_text(game_summary_path, ""),
        "gameNarrative": read_text(game_narrative_path, ""),
        "sessionName": str(status.get("sessionName") or ""),
        "reprocessStatus": status.get("reprocessStatus") or {},
        "notesState": structured.get("state") or {},
        "notesLatestStructured": structured.get("latest"),
        "trackingState": tracking_state,
        "prepContext": prep_context,
        "prepContextText": _prep_context_text(prep_context),
    }

def update_status_for_chunk(session_id: str, chunk_index: int, filename: str, nbytes: int):
    session_dir = os.path.join(UPLOADS_DIR, session_id)
    status_path = os.path.join(session_dir, "status.json")

    status = read_json(status_path, default={
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

    write_json_atomic(status_path, status)

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

    status_path = os.path.join(session_dir, "status.json")
    status = read_json(status_path, default={})
    status["transcriptLatest"] = text
    status["transcriptUpdatedAt"] = int(time.time())
    write_json_atomic(status_path, status)

def _set_transcript_error(session_id: str, chunk_index: int, message: str):
    session_dir = os.path.join(UPLOADS_DIR, session_id)
    status_path = os.path.join(session_dir, "status.json")
    status = read_json(status_path, default={})
    status["transcriptError"] = {
        "chunkIndex": chunk_index,
        "message": message,
        "updatedAt": int(time.time()),
    }
    write_json_atomic(status_path, status)

def _read_party_meta(session_id: str) -> str:
    session_dir = os.path.join(UPLOADS_DIR, session_id)
    party_path = os.path.join(session_dir, "party.txt")
    try:
        with open(party_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""

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
    session_dir = init_session(session_id)
    status_path = os.path.join(session_dir, "status.json")
    status = read_json(status_path, default={})
    status["prepUpdatedAt"] = int(time.time())
    status["updatedAt"] = int(time.time())
    write_json_atomic(status_path, status)

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
    return {
        "latest": latest,
        "state": _merge_notes_state(entries),
        "summary": _read_notes_summary(session_id),
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
    for row in rows:
        role = str(row.get("role") or "Player").strip() or "Player"
        if role.lower() != "player":
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

    try:
        with urlopen(req, timeout=180) as resp:
            raw = resp.read().decode("utf-8")
    except HTTPError as e:
        detail = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"OpenAI API error: HTTP {e.code} {detail}")
    except URLError as e:
        raise RuntimeError(f"OpenAI API connection error: {e}")

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

    try:
        with urlopen(req, timeout=120) as resp:
            payload = resp.read().decode("utf-8")
    except HTTPError as e:
        detail = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"OpenAI API error: HTTP {e.code} {detail}")
    except URLError as e:
        raise RuntimeError(f"OpenAI API connection error: {e}")

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
        "Treat tracker HP/damage state as authoritative when it conflicts with transcript narration.\n"
        "Do not invent facts. Use the party roster to normalize transcript names to the correct character names whenever context supports it.\n"
        "Prefer character names consistently. If a name is unclear and cannot be matched confidently, use a neutral description.\n"
        "Format with short sections: Summary, Timeline, Key NPCs, Loot/Treasure, Outstanding Hooks.\n"
    )

def _default_game_narrative_system_prompt() -> str:
    return (
        "You are a fantasy chronicler retelling a D&D session as a humorous high-fantasy narrative.\n"
        "Write approximately 600 words in plain text Markdown (no code fences).\n"
        "Accuracy is the top priority. Build the narrative from the DM notes timeline first, then use the transcript to add detail.\n"
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
        "Use the provided party roster to normalize likely mis-heard player/character names when context supports it.\n"
        "Prefer character names consistently. If uncertain, keep the original wording.\n"
        "Improve punctuation, sentence breaks, and paragraphing for readability.\n"
        "Do not summarize; this is still a transcript chunk.\n"
    )

def clean_transcript_chunk_with_openai(chunk_text: str, party: str = "", chunk_index: int = -1) -> str:
    chunk_text = (chunk_text or "").strip()
    if not chunk_text:
        return ""
    user_prompt = (
        f"Chunk index: {chunk_index}\n\n"
        "Party roster (if any):\n"
        f"{party.strip() if party else '(none)'}\n\n"
        "Transcript chunk to clean:\n"
        f"{chunk_text}\n"
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
        "Tracker state (authoritative for hp/damage when present):\n"
        f"{tracker_state_text.strip() if tracker_state_text else '(none)'}\n\n"
        "Recent tracker events (if any):\n"
        f"{tracker_events_text.strip() if tracker_events_text else '(none)'}\n\n"
        "Session prep context (rooms/monsters/npcs, if any):\n"
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
    prep_context_text: str = "",
) -> str:
    transcript_text = (transcript_text or "").strip()
    if not transcript_text:
        raise RuntimeError("No transcript text available for narrative generation.")

    system_prompt = _default_game_narrative_system_prompt()
    user_prompt = (
        "Party roster (if any):\n"
        f"{party.strip() if party else '(none)'}\n\n"
        "DM notes timeline (if any):\n"
        f"{notes_text.strip() if notes_text else '(none)'}\n\n"
        "DM notes rolling summary (if any):\n"
        f"{notes_summary.strip() if notes_summary else '(none)'}\n\n"
        "Session prep context (rooms/monsters/npcs, if any):\n"
        f"{prep_context_text.strip() if prep_context_text else '(none)'}\n\n"
        "Transcript for the full game session:\n"
        f"{transcript_text}\n"
    )
    return _chat_complete_text(system_prompt, user_prompt, _narrative_model())

def generate_game_summary_for_session(session_id: str) -> str:
    session_dir = os.path.join(UPLOADS_DIR, session_id)
    clean_transcript = read_text(os.path.join(session_dir, "clean_transcript.txt"), "").strip()
    raw_transcript = read_text(os.path.join(session_dir, "transcript.txt"), "").strip()
    transcript_text = clean_transcript or raw_transcript
    notes_text = read_text(os.path.join(session_dir, "notes.txt"), "")
    notes_summary = read_text(os.path.join(session_dir, "notes_summary.txt"), "")
    party = _read_party_meta(session_id)

    tracking_state = _get_tracking_state(session_id)
    tracking_state_text = _tracking_state_text(tracking_state)
    tracking_events_text = _recent_tracking_events_text(session_id, limit=40)
    prep_context_text = _prep_context_text(_read_prep_context(session_id))

    summary = generate_game_summary_from_text(
        transcript_text=transcript_text,
        party=party,
        notes_text=notes_text,
        notes_summary=notes_summary,
        tracker_state_text=tracking_state_text,
        tracker_events_text=tracking_events_text,
        prep_context_text=prep_context_text,
    )

    out_path = os.path.join(session_dir, "game_summary.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(summary.strip() + "\n")

    status_path = os.path.join(session_dir, "status.json")
    status = read_json(status_path, default={})
    status["gameSummaryUpdatedAt"] = int(time.time())
    write_json_atomic(status_path, status)
    return summary

def generate_game_narrative_for_session(session_id: str) -> str:
    session_dir = os.path.join(UPLOADS_DIR, session_id)
    clean_transcript = read_text(os.path.join(session_dir, "clean_transcript.txt"), "").strip()
    raw_transcript = read_text(os.path.join(session_dir, "transcript.txt"), "").strip()
    transcript_text = clean_transcript or raw_transcript
    notes_text = read_text(os.path.join(session_dir, "notes.txt"), "")
    notes_summary = read_text(os.path.join(session_dir, "notes_summary.txt"), "")
    party = _read_party_meta(session_id)
    prep_context_text = _prep_context_text(_read_prep_context(session_id))

    narrative = generate_game_narrative_from_text(
        transcript_text=transcript_text,
        party=party,
        notes_text=notes_text,
        notes_summary=notes_summary,
        prep_context_text=prep_context_text,
    )

    out_path = os.path.join(session_dir, "game_narrative.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(narrative.strip() + "\n")

    status_path = os.path.join(session_dir, "status.json")
    status = read_json(status_path, default={})
    status["gameNarrativeUpdatedAt"] = int(time.time())
    write_json_atomic(status_path, status)
    return narrative

def _generate_notes_with_context(session_id: str, chunk_index: int, recent: list, summary_override: str = "", window_override: int = None):
    api_key = _openai_api_key()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set. Add it to .env or your environment.")

    party = _read_party_meta(session_id)
    prep_context_text = _prep_context_text(_read_prep_context(session_id))
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
        "Session prep context (rooms/monsters/npcs, if any):\n"
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

def generate_notes_from_text(transcript_text: str, party: str = "", summary: str = "", system_prompt: str = "", window: int = 2):
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

    try:
        with urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
    except HTTPError as e:
        detail = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"OpenAI API error: HTTP {e.code} {detail}")
    except URLError as e:
        raise RuntimeError(f"OpenAI API connection error: {e}")

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

    status_path = os.path.join(session_dir, "status.json")
    status = read_json(status_path, default={})
    status["notesLatest"] = timeline[-1] if timeline else ""
    status["notesUpdatedAt"] = int(time.time())
    write_json_atomic(status_path, status)

def _set_notes_error(session_id: str, chunk_index: int, message: str):
    session_dir = os.path.join(UPLOADS_DIR, session_id)
    status_path = os.path.join(session_dir, "status.json")
    status = read_json(status_path, default={})
    status["notesError"] = {
        "chunkIndex": chunk_index,
        "message": message,
        "updatedAt": int(time.time()),
    }
    write_json_atomic(status_path, status)

def rebuild_notes_for_session(session_id: str, party_override: str = "", regenerate_summary: bool = True, progress_cb=None, window_override: int = None):
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
    status_path = os.path.join(session_dir, "status.json")
    status = read_json(status_path, default={})
    status.pop("notesError", None)
    status.pop("gameSummaryUpdatedAt", None)
    status["notesLatest"] = ""
    status["notesUpdatedAt"] = int(time.time())
    write_json_atomic(status_path, status)

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
    with NOTES_LOCK:
        processed = []
        for item in transcripts:
            chunk_index = int(item.get("chunkIndex", -1))
            if chunk_index < 0:
                continue
            processed.append(item)
            recent = processed[-window:]
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
        if progress_cb:
            progress_cb({"phase": "summary", "running": True, "processed": rebuilt, "total": total})
        summary_text = generate_game_summary_for_session(session_id)

    if progress_cb:
        progress_cb({"phase": "done", "running": False, "processed": rebuilt, "total": total})

    return {
        "rebuiltChunks": rebuilt,
        "gameSummary": summary_text,
    }

def _set_reprocess_status(session_id: str, patch: dict):
    session_dir = init_session(session_id)
    status_path = os.path.join(session_dir, "status.json")
    status = read_json(status_path, default={})
    current = status.get("reprocessStatus") or {}
    current.update(patch or {})
    status["reprocessStatus"] = current
    status["updatedAt"] = int(time.time())
    write_json_atomic(status_path, status)

def _run_reprocess_job(session_id: str, party_override: str, regenerate_summary: bool, window_override: int = None):
    def progress(p):
        payload = {
            "running": bool(p.get("running", True)),
            "phase": str(p.get("phase") or "notes"),
            "processed": int(p.get("processed") or 0),
            "total": int(p.get("total") or 0),
            "error": "",
            "updatedAt": int(time.time()),
            "window": int(p.get("window") or 0),
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
            "finishedAt": None,
            "window": int(window_override or 0),
        })
        result = rebuild_notes_for_session(
            session_id=session_id,
            party_override=party_override,
            regenerate_summary=regenerate_summary,
            progress_cb=progress,
            window_override=window_override,
        )
        _set_reprocess_status(session_id, {
            "running": False,
            "phase": "done",
            "processed": int(result.get("rebuiltChunks") or 0),
            "error": "",
            "finishedAt": int(time.time()),
            "updatedAt": int(time.time()),
            "window": int(window_override or 0),
        })
    except Exception as e:
        _set_reprocess_status(session_id, {
            "running": False,
            "phase": "error",
            "error": str(e),
            "finishedAt": int(time.time()),
            "updatedAt": int(time.time()),
        })

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

    cleaned_count = 0
    lines_out = []
    for item in transcripts:
        chunk_index = int(item.get("chunkIndex", -1))
        if chunk_index < 0:
            continue
        raw_text = str(item.get("text") or "").strip()
        if not raw_text:
            continue
        cleaned = clean_transcript_chunk_with_openai(raw_text, party=party, chunk_index=chunk_index)
        lines_out.append(f"[{chunk_index:04d}] {cleaned}".rstrip())
        cleaned_count += 1

    with open(clean_transcript_path, "w", encoding="utf-8") as f:
        if lines_out:
            f.write("\n".join(lines_out).strip() + "\n")
        else:
            f.write("")

    status_path = os.path.join(session_dir, "status.json")
    status = read_json(status_path, default={})
    status["cleanTranscriptUpdatedAt"] = int(time.time())
    write_json_atomic(status_path, status)

    return {
        "rebuiltChunks": cleaned_count,
        "cleanTranscript": read_text(clean_transcript_path, ""),
    }

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
                self._send_json(200, {"ok": True, "sessions": list_sessions(limit)})
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

        if parsed.path == "/api/session/start":
            try:
                body = self._read_body().decode("utf-8") if self.headers.get("Content-Length") else ""
                data = json.loads(body) if body else {}
                session_id = safe_session_id(str(data.get("sessionId", "")))
                session_name = sanitize_session_name(data.get("sessionName", ""))
                session_dir = init_session(session_id)
                if session_name:
                    status_path = os.path.join(session_dir, "status.json")
                    status = read_json(status_path, default={})
                    status["sessionName"] = session_name
                    status["updatedAt"] = int(time.time())
                    write_json_atomic(status_path, status)
                self._send_json(200, {
                    "ok": True,
                    "sessionId": session_id,
                    "sessionName": session_name,
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
                )

                self._send_json(200, {
                    "ok": True,
                    **payload,
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
                narrative = generate_game_narrative_for_session(session_id)
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
                rep = status.get("reprocessStatus") or {}
                if rep.get("running"):
                    self._send_json(409, {"ok": False, "error": "Reprocess already running for this session."})
                    return

                t = threading.Thread(
                    target=_run_reprocess_job,
                    args=(session_id, party_override, regenerate_summary, window_override),
                    daemon=True,
                )
                t.start()
                self._send_json(200, {"ok": True, "sessionId": session_id, "started": True, "window": window_override})
            except Exception as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/api/session/clean-transcript":
            try:
                body = self._read_body().decode("utf-8")
                data = json.loads(body) if body else {}
                session_id = safe_session_id(str(data.get("sessionId", "")))
                party_override = str(data.get("party") or "")
                result = rebuild_clean_transcript_for_session(
                    session_id=session_id,
                    party_override=party_override,
                )
                self._send_json(200, {
                    "ok": True,
                    "sessionId": session_id,
                    **result,
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
