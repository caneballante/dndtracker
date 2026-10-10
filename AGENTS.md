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

- During an active recording, verify changes with isolated synthetic data; do not restart port 8000 or reload the live page. `python tests/offline_recording_preview.py` starts a separate browser fixture with a synthetic tone, no microphone, and no external API calls. Fixture data stays under ignored `.pycache_tmp/`; sandbox temporary folders can fail with Windows access errors.
- If that fixture prints a listening URL but local requests time out, run the same fixture with approved local-network access outside the sandbox; do not change ports or firewall settings. This resolved the observed sandbox loopback isolation.

- New browser CSS/JS must be listed explicitly in `server.py`'s `PUBLIC_STATIC_FILES`. Run `test_static_ui_assets.py`; DOM-only tests do not verify HTTP asset delivery. Never allow the whole repository to be served.
- For backend or route verification, start/restart on port `8000` and use the stop script only after production is explicitly verified safe to restart under item 6 below. Otherwise, use a separate isolated test listener.
- If port `8000` is occupied, identify its owner before acting. Never terminate an unknown process merely because it owns that port.
- Early transcript/notes 404s are expected before the first chunk finishes processing.
- `favicon.ico` 404 is harmless.

## Engineering Principles and Reliability Requirements

1. **Audio integrity comes first.** Recording and durable audio preservation take precedence over transcription, AI notes, recaps, performance and convenience. Never discard potentially recoverable audio or report successful finalization when expected audio may be missing.
2. **Establish invariants before changes.** For recording, storage, recovery and finalization, state what must always hold. Distinguish recorder attempts, captured audio, durable local checkpoints, server acknowledgments and genuinely missing audio. An attempt that never started must not create an unresolvable required chunk; genuinely missing audio must never be silently ignored.
3. **Test interacting operations.** Exercise relevant asynchronous combinations and event orderings: interruption during rollover, delayed/missing callbacks, storage failures, retries, recovery, Stop/Pause and finalization. Include stale callbacks and competing operations, not only happy paths.
4. **Diagnose and reproduce before fixing.** Inspect evidence, identify the initiating failure, reproduce it in a failing regression when possible, apply the smallest safe correction and confirm it passes. Label confirmed causes and hypotheses separately. Broad refactoring is not a substitute for diagnosis.
5. **Require realistic acceptance.** Unit and mocked-browser tests are necessary but insufficient for critical recording changes. Require isolated real-browser/microphone verification and appropriate endurance testing before deployment to the normal environment or reliability claims. Document untested scenarios and known limits; short tests do not prove game-length reliability.
6. **Protect production and private data.** Never restart/reload the production server/browser during recording or pending audio saves. Before deployment, verify capture is stopped, all pending browser audio saves have completed, and audio finalization is either complete or safely persisted for later resumption. Deferred transcription and AI processing do not themselves block deployment, provided their state is preserved and no active work will be interrupted. Server metadata alone cannot prove the browser queue is empty. Use separate test storage and browser origin/profile, synthetic audio for automated tests and explicitly authorized microphone/reference audio for acceptance. Preserve original recordings, logs, browser storage and unrelated changes. No paid AI requests in tests without explicit authorization.
7. **Control complexity.** Prefer the smallest change that preserves existing behavior and reduces risk. Before adding asynchronous operations, states, fallbacks or dependencies, evaluate their interactions with recording, storage, recovery and finalization. Avoid unnecessary architecture changes and game-night setup steps.
8. **Preserve truthful system state.** Distinguish microphone activity, recorder operation, durable browser backup, server save and AI processing. Do not claim successful recovery until new audio is durably checkpointed. Do not manufacture, renumber, delete or silently skip missing audio to satisfy integrity checks. Show actual recovery stages/failures and label interruption timing estimates as estimates; do not claim sample-accurate continuity without reference evidence.
