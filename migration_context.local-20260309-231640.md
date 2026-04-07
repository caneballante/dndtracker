# DnD Recorder Migration Context

## Project Snapshot

- Purpose: local web app for recording DnD sessions in audio chunks, transcribing, generating DM notes, and generating end-of-session summary/narrative.
- Runtime model: browser frontend + local Python server (`server.py`) + OpenAI API.
- Primary UX goal: reliable in-session capture with high-quality post-session outputs (summary continuity is more important than live display).

## Current Goals

1. Keep recording/transcription stable for long sessions.
2. Keep DM notes structured and useful (`timeline`, `state`, `summary`).
3. Improve factual accuracy with:
- party roster normalization
- tracker-authoritative HP/damage
- prep context import (rooms/monsters/NPCs)
4. Reduce setup friction before sessions.

## Next Priorities

1. Add a previous session summary field in Prep and wire it into live notes + summary context.
2. Add an explicit session selector inside Prep/Tracker (reduce ambiguity when no active recording session).
3. Run A/B tests on notes window defaults (`N=2` vs `N=12`) and lock final defaults.
4. Add import/patch ergonomics for prep context (session-scoped partial updates).
5. Harden tracker UX for fast combat use (optional: batch apply mode).

## Key Architecture And Decisions (With Why)

1. Local-first server (`server.py`)
- Why: simple setup, avoids cloud hosting/security complexity during development.

2. Chunked recording + async transcription
- Why: avoids giant uploads, keeps long sessions resilient.

3. Structured notes JSON contract
- Why: deterministic parsing in app (`timeline`, `state`, `summary`) and less fragile rendering.

4. Rolling notes window (`N` chunks)
- Why: gives context continuity without sending full transcript each call.

5. Post-session rebuild path
- Why: final quality is better when rerunning notes over larger context windows.

6. Tracker as authoritative combat state
- Why: transcript attribution can be wrong; explicit tracker events improve HP/damage accuracy.

7. Prep context as session-scoped JSON
- Why: DM prepares only near-term content; no need to load full campaign/dungeon at once.

## Data Flow (High Level)

1. Browser records chunk -> `POST /api/upload`.
2. Server stores audio chunk and starts async transcription.
3. Server appends transcript (`transcript.txt`, `clean_transcript.txt`, `transcripts.jsonl`).
4. Server generates notes JSON from rolling transcript context (+ party + prep context).
5. Server appends notes (`notes.txt`, `notes.jsonl`, `notes_summary.txt`).
6. Sessions tab can rebuild notes/summary and generate narrative.
7. Tracker events are appended to `tracking_events.jsonl`; derived snapshot is stored in `status.json`.

## Recent Changes Not Obvious From Code Reading Quickly

1. Test tab is now session-based, not tied to latest live transcript.
2. Reprocess moved to async start+poll (`/api/session/reprocess/start`) to avoid blocking UX.
3. Notes rebuild bug was fixed: previously repeated timeline due to incorrect chunk context handling in rebuild path.
4. Tracker tab added with quick +/- and typed HP actions plus per-entity undo.
5. Prep tab added for importing/editing JSON context per session.
6. Summary prompt now includes tracker context and prefers tracker HP/damage in conflicts.

## Known Bugs / Quirks And Workarounds

1. Wrong server startup command
- Symptom: `501 Unsupported method ('POST')`.
- Cause: running `python -m http.server` instead of `server.py`.
- Workaround: run `python server.py` (or platform launcher script).

2. Port already in use
- Symptom: server bind failure on startup.
- Workaround: stop existing process (`stop-server.ps1` or `stop-server.command`) then restart.

3. WAV conversion can fail on some chunks
- Behavior: app logs conversion failure and uploads original chunk format.
- Workaround: none needed; fallback path is expected and supported.

4. 404 for transcript/notes early in a session
- Cause: files are not created until first successful chunk processing.
- Workaround: wait until first chunk upload/transcription completes.

5. `favicon.ico` 404
- Harmless.

6. Prep/Tracker session target can be ambiguous
- Current rule: active live session ID first; otherwise selected Sessions-tab item.
- Workaround: verify `Session:` label in each tab before editing data.

7. Mic input issues in browser
- Workaround: Chrome mic permissions + select device in UI + `Reset Audio` button.

## Do-Not-Change Constraints

1. Keep app local-first (no forced hosted backend).
2. Keep OpenAI API key in local `.env`; do not commit secrets.
3. Do not remove post-session rebuild workflow (it is core to quality).
4. Do not make tracker optional for combat truth once used in a session (avoid dual-source conflicts).
5. Do not auto-link campaigns/sessions yet (controlled testing first).

## Important Files / Paths (This Workspace)

- Workspace root: `C:\Users\mirol\OneDrive\Documents\codex\dndtracker`
- App UI: `C:\Users\mirol\OneDrive\Documents\codex\dndtracker\dnd-audio.html`
- Main backend: `C:\Users\mirol\OneDrive\Documents\codex\dndtracker\server.py`
- Windows launcher: `C:\Users\mirol\OneDrive\Documents\codex\dndtracker\start-server.ps1`
- Windows stopper: `C:\Users\mirol\OneDrive\Documents\codex\dndtracker\stop-server.ps1`
- Runtime uploads root: `C:\Users\mirol\OneDrive\Documents\codex\dndtracker\uploads`

## Path Mapping (Mac -> Windows)

- `/Users/jonbridgman/Documents/dnd-local` -> `C:\Users\mirol\OneDrive\Documents\codex\dndtracker`
- `/Users/jonbridgman/Documents/dnd-local/uploads` -> `C:\Users\mirol\OneDrive\Documents\codex\dndtracker\uploads`

## Session Artifact Layout (Per Session)

- `uploads/<sessionId>/status.json`
- `uploads/<sessionId>/transcript.txt`
- `uploads/<sessionId>/clean_transcript.txt`
- `uploads/<sessionId>/transcripts.jsonl`
- `uploads/<sessionId>/notes.txt`
- `uploads/<sessionId>/notes.jsonl`
- `uploads/<sessionId>/notes_summary.txt`
- `uploads/<sessionId>/game_summary.txt`
- `uploads/<sessionId>/game_narrative.txt`
- `uploads/<sessionId>/tracking_events.jsonl`
- `uploads/<sessionId>/prep_context.json`
- `uploads/<sessionId>/party.txt`

## Sample Session IDs (Current Workspace)

- `1772069466219`
- `1772071619044`

## Operational Defaults To Remember

1. Chunk length is typically run at 2 minutes.
2. Larger notes context windows (for example 12) improve final summary quality.
3. Recommended flow: record -> rebuild notes/summary (larger window) -> generate final summary/narrative.
