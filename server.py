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
    return "application/octet-stream"

def _openai_api_key() -> str:
    global OPENAI_API_KEY
    if OPENAI_API_KEY:
        return OPENAI_API_KEY
    load_env_file(ENV_PATH)
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
    return OPENAI_API_KEY

def _append_transcript(session_id: str, chunk_index: int, text: str):
    session_dir = os.path.join(UPLOADS_DIR, session_id)
    ensure_dir(session_dir)
    transcript_path = os.path.join(session_dir, "transcript.txt")
    jsonl_path = os.path.join(session_dir, "transcripts.jsonl")

    line = f"[{chunk_index:04d}] {text.strip()}\n"
    with open(transcript_path, "a", encoding="utf-8") as f:
        f.write(line)
    with open(jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "chunkIndex": chunk_index,
            "text": text,
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

def transcribe_async(session_id: str, chunk_index: int, path: str):
    try:
        text = transcribe_with_openai(path)
        _append_transcript(session_id, chunk_index, text)
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

                filename = f"chunk_{chunk_index:04d}.webm"
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
