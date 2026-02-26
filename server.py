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
NOTES_MODEL_DEFAULT = "gpt-4o-mini"
NOTES_WINDOW_DEFAULT = 5
SUMMARY_MODEL_DEFAULT = "gpt-4o-mini"

SESSION_ID_RE = re.compile(r"^[0-9]{8,20}$")  # timestamp-ish

def safe_session_id(s: str) -> str:
    s = (s or "").strip()
    if not SESSION_ID_RE.match(s):
        raise ValueError("Invalid sessionId (expected 8-20 digits).")
    return s

def ensure_dir(p: str):
    os.makedirs(p, exist_ok=True)

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
    structured = _latest_notes_structured(session_id)
    return {
        "transcript": read_text(transcript_path, ""),
        "cleanTranscript": read_text(clean_transcript_path, ""),
        "notes": read_text(notes_path, ""),
        "summary": read_text(summary_path, ""),
        "gameSummary": read_text(game_summary_path, ""),
        "notesState": structured.get("state") or {},
        "notesLatestStructured": structured.get("latest"),
    }

def update_status_for_chunk(session_id: str, chunk_index: int, filename: str, nbytes: int):
    session_dir = os.path.join(UPLOADS_DIR, session_id)
    status_path = os.path.join(session_dir, "status.json")

    status = read_json(status_path, default={
        "sessionId": session_id,
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
        "If a field has no info, use empty string or empty array.\n"
    )

def _default_game_summary_system_prompt() -> str:
    return (
        "You are a D&D campaign recapper. Produce a readable game summary in plain text Markdown.\n"
        "Prioritize chronological events, major discoveries, NPC interactions, combat outcomes, loot, and open hooks.\n"
        "Do not invent facts. If names are unclear, use neutral descriptions.\n"
        "Format with short sections: Summary, Timeline, Key NPCs, Loot/Treasure, Outstanding Hooks.\n"
    )

def generate_game_summary_from_text(transcript_text: str, party: str = "", notes_text: str = "", notes_summary: str = "") -> str:
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
        "Transcript for the full game session:\n"
        f"{transcript_text}\n"
    )
    return _chat_complete_text(system_prompt, user_prompt, _summary_model())

def generate_game_summary_for_session(session_id: str) -> str:
    session_dir = os.path.join(UPLOADS_DIR, session_id)
    clean_transcript = read_text(os.path.join(session_dir, "clean_transcript.txt"), "").strip()
    raw_transcript = read_text(os.path.join(session_dir, "transcript.txt"), "").strip()
    transcript_text = clean_transcript or raw_transcript
    notes_text = read_text(os.path.join(session_dir, "notes.txt"), "")
    notes_summary = read_text(os.path.join(session_dir, "notes_summary.txt"), "")
    party = _read_party_meta(session_id)

    summary = generate_game_summary_from_text(
        transcript_text=transcript_text,
        party=party,
        notes_text=notes_text,
        notes_summary=notes_summary,
    )

    out_path = os.path.join(session_dir, "game_summary.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(summary.strip() + "\n")

    status_path = os.path.join(session_dir, "status.json")
    status = read_json(status_path, default={})
    status["gameSummaryUpdatedAt"] = int(time.time())
    write_json_atomic(status_path, status)
    return summary

def generate_notes_with_openai(session_id: str, chunk_index: int):
    api_key = _openai_api_key()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set. Add it to .env or your environment.")

    party = _read_party_meta(session_id)
    summary = _read_notes_summary(session_id)
    window = _notes_window()
    recent = _load_recent_transcripts(session_id, window)

    transcript_block = "\n".join(
        [f"[{obj.get('chunkIndex', '?')}] {obj.get('text', '').strip()}" for obj in recent]
    ).strip()

    system_prompt = _default_notes_system_prompt()

    user_prompt = (
        "Party data (if any):\n"
        f"{party if party else '(none)'}\n\n"
        "Rolling summary so far:\n"
        f"{summary if summary else '(none)'}\n\n"
        f"Recent transcript window (last {window} chunks):\n"
        f"{transcript_block if transcript_block else '(none)'}\n"
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

        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/session/start":
            try:
                body = self._read_body().decode("utf-8") if self.headers.get("Content-Length") else ""
                data = json.loads(body) if body else {}
                session_id = safe_session_id(str(data.get("sessionId", "")))
                session_dir = init_session(session_id)
                self._send_json(200, {
                    "ok": True,
                    "sessionId": session_id,
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

        if parsed.path == "/api/notes/test":
            try:
                body = self._read_body().decode("utf-8")
                data = json.loads(body) if body else {}
                transcript_text = (data.get("transcript") or "").strip()
                party = (data.get("party") or "").strip()
                summary = (data.get("summary") or "").strip()
                system_prompt = (data.get("prompt") or "").strip()
                window = data.get("window") or 2

                notes_obj = generate_notes_from_text(
                    transcript_text=transcript_text,
                    party=party,
                    summary=summary,
                    system_prompt=system_prompt,
                    window=window,
                )

                self._send_json(200, {
                    "ok": True,
                    "notes": notes_obj,
                    "window": window,
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
