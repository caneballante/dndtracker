# Recording stability fixes — October 8, 2026

Implemented in the working tree; the live server was not restarted and real campaign recordings were not modified. No commit or push was made.

## Changes

- Browser checkpoints target ten seconds. Reopening recovers a continuous encoded prefix from IndexedDB. Completed audio stays queued until the local server acknowledges it.
- Queue reads blobs on demand, retries failed saves, and continues uploading when browser quota blocks an expanded WAV backup. A stable source digest makes retries across encoded/WAV representations idempotent. Retrying reloads the metadata corresponding to the stored bytes.
- Web Locks allow one recording/queue owner per origin. Other tabs disable capture controls and identify the owning-tab situation instead of claiming a recording failure. Persisted chunk reservations prevent reuse after reload.
- Recovery can start capture while the local server is unavailable. It restores a valid interval, keeps old recorder callbacks from interrupting replacements, and resets watchdog timing after pause/recovery.
- Stop persists finalization intent and retries after queued saves finish. Offline recordings settle to Saved — Transcribe Later. Delayed timers/heartbeats indicate uncertainty rather than inventing confirmed capture gaps; late server events cannot reopen a stopped recording.
- Session initialization merges status under its lock. Uploads use atomic durable file writes and reject empty or structurally incomplete audio. Missing mode defaults to offline.
- Live transcription uses two workers and persists explicit job intent for restart recovery. Offline and legacy pending chunks do not automatically trigger provider spending. Backfill skips invalid chunks and continues valid ones.
- Full WAV diarization refuses sessions containing compressed recovery chunks rather than silently omitting them. Backfill Missing Transcripts handles those chunks.
- The new page checks the server build before upload. Starting an already-running server no longer kills it or opens another recording tab. New launches write timestamped logs under `.pycache_tmp/server-logs/`.

## Verification

- 73 Python tests covering recording storage, stability, capture reliability, static assets, live player assistant, and session reconciliation.
- 33 JavaScript tests covering capture state, queue durability, quota/retry behavior, ownership, recovery, and deferred finalization.
- A simulated five-hour backlog (150 two-minute chunk records) drains in order without keeping every blob in memory. This is not a five-hour real-time soak test.
- Isolated in-app browser fixture uses a synthetic tone, temporary storage, and no microphone or external AI. Saving outage, reopening, ownership, recovery, and Stop were exercised.
- WAV header verification confirmed a 120-second completed chunk and a 70.08-second checkpoint recovery after closing the tab mid-chunk. Stop subsequently drained the queue and displayed Saved — Transcribe Later with no fixture JavaScript errors.
- PowerShell launcher syntax and `git diff --check` passed.

## Remaining limits and safe activation

Apply between sessions: Stop, wait for pending audio to save, stop/restart the server, then reload the page. Use the same browser and `http://127.0.0.1:8000/`; browser storage and ownership are origin-specific.

Ten seconds is a checkpoint target, not a guaranteed loss bound. Browser scheduling, system sleep, disk failure/quota, closing the page, and device loss can still interrupt capture. Audio not yet checkpointed cannot be reconstructed. Recorder stop/start rollover is not proven gapless. A true five-hour microphone soak with sleep/device/network fault injection remains outstanding before claiming unattended game-night reliability. Historical lost audio and error records were preserved; these fixes cannot recreate missing sound.
