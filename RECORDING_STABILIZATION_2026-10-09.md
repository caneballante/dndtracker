# Recording stabilization — October 9, 2026

## Scope and baseline

Baseline: `177831f8fb91014d343e157e484475b94a31fed1` (October 8 reliability implementation). Read `product_intent.md`, the October 7 review, October 8 repair report, and the relevant server/recorder/queue/controller code. Their conclusions were checked against the code rather than assumed correct.

The pre-existing tracked work was only `campaigns/parmedia-redux/campaign.json`; the two untracked campaign directories and `dungeonshare/.codex/` were left untouched. A local tracked-diff snapshot and baseline commit ID were saved under ignored `.pycache_tmp/stabilization-2026-10-09-baseline*`. No real recording, transcript, campaign, model, prompt, price, or live server was changed. No new dependency, modular extraction, commit, or push was performed for this assignment.

## What the code demonstrated

1. `init_session()` read/created/assigned campaign metadata without the session mutation lock. An initializer could read older status, then replace status after an upload or capture update had written newer fields. Atomic replacement prevented torn JSON, but did not prevent stale read/modify/write overwrites.
2. Campaign assignment/context refresh, tracking-state persistence, party metadata, and session renaming also performed uncoordinated read/modify/write operations on the same file. Locking just upload and capture mutations was insufficient.
3. Generic `read_json()` propagated access/sharing errors. During upload, initialization preceded reading the socket body. A transient status access error could therefore fail the upload with an unread body. Existing replacement retries covered `PermissionError`, but not all Windows sharing/lock errors represented as `OSError`.
4. Incomplete uploads returned 400. The browser correctly retains permanently rejected chunks, but does not automatically retry them. A truncated transport should instead be retryable.
5. Queue-wide `storageError` was cleared by any later successful checkpoint. An earlier completed chunk retained only in memory could then be described by the UI as backed up. This was a misleading durability claim, not evidence of durable storage.

These mechanisms explain possible failures; they do not identify the initiating cause of the historical October 7 local connection aborts.

## Changes and rationale

- All in-process status writers now use the same per-path lock, including initialization and the four remaining metadata/tracking writers. Existing mutations merge fields while holding that lock. Repeated initialization leaves existing metadata, chunks, capture fields, and timestamps alone unless it must fill a missing campaign ID.
- Lock keys normalize Windows path casing; the lock is reentrant so a locked mutation/initializer can call the locked reader without deadlocking. Upload takes its audio lock before the status lock; status-only operations do not take the audio lock. No socket body is read while holding either lock.
- Session-status reads and atomic replacements retry access denied/sharing/lock errors (`PermissionError`, Windows codes 5/32/33), at most six attempts with 385 ms total deliberate delay per failing operation. Exhaustion propagates an error; it never substitutes an empty object for inaccessible status. Replacement retries retain the same temporary file and leave the old file intact until replacement succeeds. Non-sharing I/O errors propagate immediately. Generic JSON reading and non-status replacement behavior remain unchanged.
- Upload validates session/index/digest and Content-Length before allocation. It consumes and validates audio before status operations. Oversized requests receive 413; invalid headers receive 400; rejected requests close their connection rather than leaving unread bytes eligible for reuse. Body read failures and incomplete transfers receive retryable 503 responses. Status/disk failures continue to return 503.
- A new 512 MiB per-request limit accommodates a 30-minute stereo 48 kHz, 16-bit WAV (345,600,044 bytes). This bounds one body allocation; it is not a global memory limit. Unusually high sample rates/channel counts may exceed it; the rejected chunk remains in the browser for export/manual attention. This deliberately small correction retains the existing whole-body upload model rather than adding a streaming file pipeline.
- The queue tracks which completed items actually reached durable storage. A later checkpoint cannot hide a memory-only completed item. Existing status text explicitly warns to keep the page open/export pending audio. The server-version message now says queued rather than claiming a backup.

## Failure isolation and recovery review

| Interaction | Verified behavior / remaining concern |
|---|---|
| Upload failure vs capture | Queue transport does not own MediaRecorder. Production recorder/queue/controller functions were exercised together with unavailable server, initialization rejection, validation rejection, and invalid receipt. The next recorder continued; queued bytes survived restoration; no confirmed gap was introduced. |
| Durable vs memory vs not emitted | Successful enqueue/checkpoint means a completed storage operation. Failed completed-item storage means memory retention only, now reported separately. Before MediaRecorder delivers data, that audio is neither enqueued nor checkpointed. Crashes can lose memory-only and not-yet-emitted audio. |
| Checkpoint vs rollover | Checkpoint writes are serialized per recorder. Final enqueue waits for those writes; rollover starts the replacement without waiting for upload. Stop/start recording remains susceptible to a physical scheduling gap; no gapless claim is justified. |
| Recorder callbacks/recovery | Identity checks prevent an old recorder's stop/error callback from interrupting its replacement. `beginRecovery()` synchronously changes state and rejects a second attempt while recovering. Recovery still requires user action; no automatic competing restart loop was added. |
| Pause/resume | Pause stops the current recorder and retains its final data; resume creates a new recorder. Watchdog baseline tests cover long pauses/recovery. Rapid Pause/Resume/Stop while the final data/stop events are queued needs browser fault testing: finalization may observe the boundary before the late callback queues its audio. This is a sequencing concern, not a reproduced audio-loss result in this assignment. |
| Stop/finalization | Existing tests verify pending audio drains before normal finalization and retries after server restoration. IndexedDB cleanup failure after a server receipt can keep an item pending and delay finalization. localStorage failure can prevent persisting finalization intent. These paths need targeted follow-up; no broad state-machine change was made. |
| Checkpoint-prefix recovery | Only the contiguous prefix can be assembled. Noncontiguous remaining pieces stay stored, but warnings can later be cleared by successful writes. A later fragment alone is not a playable recording. Future recovery UI should retain an explicit unresolved-fragment inventory. |
| Capture health | Heartbeat/network failure is distinct from confirmed capture interruption. A checkpoint timestamp does not prove all earlier fragments were durably saved. Recovered gap estimates and late callbacks lack a fully shared recording-generation identity. Keep these limitations visible; do not treat timestamp health as an end-to-end audio completeness proof. |
| Transcription | Offline tests call no provider. Existing bounded workers/job intent are unchanged. A dispatch failure after audio/status persistence may wait for restart recovery; raw/WAV representation changes after a status-write failure can leave an unindexed file and need cross-format reconciliation. Do not infer exactly-once processing from the audio receipt alone. |

Further architectural work requires approval. Prioritize evidence of loss over cosmetic state-machine cleanup.

## Verification

New deterministic tests use temporary folders and synthetic audio. They cover initialization paused at its first write while a chunk update requests the same lock; 12 simultaneous initializers/chunk/metadata updates; idempotent repeated initialization; transient and exhausted read/replace failures; preservation of the previous status on replacement exhaustion; non-sharing errors; pre-body rejection; body-read and post-body initialization failure; lost acknowledgment and retry; and actual localhost HTTP upload/static routes.

Browser-function tests exercise the production MediaRecorder lifecycle function with controlled events, the real queue/controller/upload receipt logic, durable restoration after failure, and memory-only status after a later successful checkpoint. They are deterministic function tests, not a real microphone/browser endurance test.

Final commands:

```powershell
$env:TEMP = Join-Path (Get-Location) '.pycache_tmp'
$env:TMP = $env:TEMP
python -m unittest tests.test_recording_storage tests.test_recording_stability tests.test_capture_reliability tests.test_capture_reliability_ui tests.test_static_ui_assets tests.test_live_player_assistant tests.test_session_reconciliation tests.test_status_file_stability
python -m unittest tests.test_session_evidence tests.test_campaign_world_relationship
node --test tests/recording_stability_ui.test.js tests/recording_storage_ui.test.js tests/capture_reliability_ui.test.js
git diff --check
```

Results: **86 main Python tests, 13 additional session-evidence/campaign tests, and 35 JavaScript tests passed**. The Python UI wrapper repeats some Node checks; these are not 134 independent scenarios. An initial new JavaScript test exposed a test-harness timing assumption around asynchronous digest/enqueue; it now waits for explicit enqueue completion. No final regression remained. `git diff --check` passed, and the pre-existing campaign diff's SHA-256 matched the saved baseline exactly.

The actual HTTP test initially failed with sandbox `WinError 10013`; the same test passed with approved loopback access outside the sandbox. It starts its own ephemeral `127.0.0.1` listener, serves the real page, injects initialization failure, then verifies save/retry receipts. Port 8000 and the active server were not touched. All test provider paths are mocked or deferred. Existing repository guidance already documents this sandbox restriction; no further configuration change was needed.

## Remaining limits and next milestone

Locks coordinate one server process, not independent processes or external editors. Permanent permissions/disk failures remain actionable errors. JSON parse-error fallback is unchanged; corrupt on-disk status and hard power-loss recovery need a separate policy. Whole-body requests and WAV conversion can still pressure memory. Original uncheckpointed audio cannot be reconstructed.

**Next milestone: a measured four-hour Windows recording acceptance run with an independent reference and controlled failures.** It must establish actual duration, interval continuity, recovery, and safe finalization before modular extraction or additional live features. See `RECORDING_SOAK_TEST.md`. No real-time four-hour microphone test was run here, and this build is **not certified for unattended use**.
