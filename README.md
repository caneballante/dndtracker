# DnD Tracker (Local)

Local web app for recording DnD sessions in audio chunks, transcribing with OpenAI, generating DM notes, and producing end-of-session summary/narrative.

## Quick Start (Windows)

1. Ensure `.env` exists in this folder and contains at least:
   - `OPENAI_API_KEY=...`
2. Start server:
   - `start-server.ps1`
   - or `start-server.cmd`
3. Open:
   - `http://127.0.0.1:8000/`
4. Stop server:
   - `stop-server.ps1`
   - or `stop-server.cmd`

## Quick Start (macOS)

1. Ensure `.env` exists in this folder and contains at least:
   - `OPENAI_API_KEY=...`
2. Start server:
   - `./start-server.command`
3. Open:
   - `http://127.0.0.1:8000/`
4. Stop server:
   - `./stop-server.command`

## Required Environment

- `OPENAI_API_KEY` (required)

Optional:
- `NOTES_WINDOW_CHUNKS`
- `NOTES_MODEL`
- `SUMMARY_MODEL`
- `CLEAN_TRANSCRIPT_MODEL`
- `NARRATIVE_MODEL`

Defaults are in `server.py`.

## Common Problems

1. `501 Unsupported method ('POST')`
   - Cause: started `python -m http.server` instead of `server.py`.
   - Fix: run `start-server.ps1`/`start-server.command` or `python server.py`.

2. Port 8000 already in use
   - Fix: run `stop-server.ps1`/`stop-server.command`, then restart.

3. Early 404 for transcript/notes
   - Expected before first chunk finishes processing.

4. `favicon.ico` 404
   - Harmless.

## Session Data Layout

Per session folder: `uploads/<sessionId>/`

- `status.json`
- `transcript.txt`
- `clean_transcript.txt`
- `transcripts.jsonl`
- `notes.txt`
- `notes.jsonl`
- `notes_summary.txt`
- `game_summary.txt`
- `game_narrative.txt`
- `tracking_events.jsonl`
- `prep_context.json`
- `party.txt`

## Docs

- Migration and architecture context: `MIGRATION_CONTEXT.md`
- Active priorities and next tasks: `TODO.md`
