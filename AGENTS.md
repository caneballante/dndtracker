# DnD Tracker Guidance

DnD Tracker is a local web app for recording DnD sessions in audio chunks, transcribing with OpenAI, generating DM notes, and producing end-of-session summaries and narrative recaps.

## Commands

- Start the server on Windows with `.\start-server.ps1` or `start-server.cmd`.
- Stop the server on Windows with `.\stop-server.ps1` or `stop-server.cmd`.
- Default local URL: `http://127.0.0.1:8000/`.
- If running directly, use `python server.py`.
- Do not start with `python -m http.server`; POST routes will fail with `501 Unsupported method`.

## Environment And Secrets

- `.env` must exist for OpenAI-backed features and should include `OPENAI_API_KEY`.
- Optional variables include `NOTES_WINDOW_CHUNKS`, `NOTES_MODEL`, `SUMMARY_MODEL`, `CLEAN_TRANSCRIPT_MODEL`, `NARRATIVE_MODEL`, and `DND_UPLOAD_TOKEN`.
- Do not read, print, commit, or expose secret values from `.env`.
- Defaults live in `server.py`.

## Data Safety

- Session data is written under `uploads/<sessionId>/`.
- Treat uploads, transcripts, prep context, party notes, and generated summaries as private campaign data.
- Do not delete or rewrite session folders unless the user explicitly asks.
- When changing transcript, notes, rebuild, or summary behavior, preserve existing output filenames unless the task requires a format change.

## Implementation Notes

- Main backend logic lives in `server.py`.
- The main browser UI is `dnd-audio.html`.
- Legacy PHP upload files exist; `DND_UPLOAD_TOKEN` is only for that path.
- Product intent emphasizes table speed, local-first control, structured outputs, and DM/tracker input as the source of truth.
- Avoid workflows that increase game-night setup friction.

## Verification

- For backend or route changes, start the server and verify `http://127.0.0.1:8000/`.
- If port `8000` is busy, use the stop script before trying unrelated ports.
- Early transcript/notes 404s are expected before the first chunk finishes processing.
- `favicon.ico` 404 is harmless.
