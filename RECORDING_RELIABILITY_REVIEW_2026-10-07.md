# Recording reliability review — October 7, 2026

> Historical diagnosis below describes the pre-fix working tree. The October 8 implementation and verification are recorded in [RECORDING_STABILITY_FIXES_2026-10-08.md](RECORDING_STABILITY_FIXES_2026-10-08.md).

The current build protects completed audio substantially better than the build used earlier tonight, but I would not yet rely on it as the only unattended recording during a game. The failures were a combination of lost uploads, misleading capture alarms, broken recovery, and an empty-recording loop. The remaining defects include both data-loss risks and false failure states.

This review inspected the current working tree, operational capture logs, audio file metadata, and isolated reproductions. The most recent commit is `8942fc5` from September 6; there is no clean commit boundary identifying only tonight's edits. Campaign-content changes were outside this reliability review. All times below are October 7, Pacific daylight time.

## What actually happened

**Six normal chunk uploads failed almost immediately, and those audio files are absent from the server.** Each logged `Failed to fetch` after 15–17 milliseconds. This was a request to the local recording server, not an OpenAI transcription timeout. The former browser path discarded failed chunks instead of retaining and retrying them. That explains how a transient transport failure became lost audio.

| Session ID | Failed normal chunk | Failure time | Saved WAV files | WAV header duration |
|---|---:|---:|---:|---:|
| `1791420910630` | 2 | 6:01:11 PM | 5 | 4.705 min |
| `1791421361686` | 3 | 6:10:43 PM | 5 | 6.249 min |
| `1791421886625` | 4 | 6:21:33 PM | 4 | 8.039 min |
| `1791422507838` | 24 | 7:11:52 PM | 51 | 82.957 min |
| `1791427952019` | 21 | 8:36:44 PM | 25 | 42.967 min |
| `1791431052658` | 3 | 8:52:13 PM | 6 | 8.018 min |

There are 96 WAV files totaling approximately 152.94 minutes by their headers. The six missing normal chunks represent approximately 12 minutes, with additional interruption/restart intervals. This is not an exact missing-speech total: pauses, unfinished recordings, and overlap require a capture timeline, and file duration does not prove audible speech throughout. Browser backups in the user's actual recording browser were not inspected during this review.

**Recovery sometimes worked even while the display continued reporting failure.** Earlier recovery stopped the old recorder; its late `onstop` event then marked the newly recovered recording as interrupted. The logs pair recovery attempts with `unexpected_recorder_stop`, while subsequent short WAV files show that actual recording did resume. The sound meter monitored the microphone separately; it never proved that audio was saved. The alarm also repeats on a timer, so a beep was not evidence of a restart.

**Refreshing exposed a second, more severe bug.** The restored recording interval could remain zero. Recover then rapidly started and stopped MediaRecorder, producing headers without audio. The 6:21 PM session contains **1,236 files of 110 bytes**, saved between **7:47:11 and 7:51:40 PM**. Its status records **1,191 failed transcriptions**. Those empty files are not recoverable speech. The old server accepted them and launched transcription work, multiplying errors and load.

**Restarting the server did not update an already-running browser page or clear durable session history.** The captured server log shows the updated assets available at 8:40 PM, yet legacy uploads without the new mode parameter continued afterward. At 8:52 PM, the latest session still emitted the old `chunk_upload_failed` → `capture_interrupted` sequence; the current HTML no longer calls that failure method. This strongly suggests an older page runtime remained in use. A page-reload recovery is logged at 8:54:22; the final chunk at 8:54:32 has transcription state `queued`, consistent with the offline path. There is no client build fingerprint, so the exact version transition cannot be proven.

**The initiating transport fault remains unproven.** A captured server log contains Windows `ConnectionAbortedError`/10053 while responding to local status/event requests during the later error storm. That establishes local connection aborts, but does not identify why the earlier six uploads failed. There is insufficient evidence to blame Wi-Fi, antivirus, browser cancellation, connection pressure, or a specific server exception. Request correlation and retained browser diagnostics are needed. Increasing the listen backlog helps burst tolerance but does not establish the root cause.

Evidence: `uploads/<sessionId>/capture_events.jsonl`, `status.json`, WAV headers/file metadata, and `.pycache_tmp/tracker-server.stderr.log`. No transcript or campaign narrative was needed for these conclusions.

## Assessment of the fixes already made

| Change | Assessment |
|---|---|
| IndexedDB queue before upload, retry/backoff, retained failed chunks | Correct direction; prevents ordinary transient upload failures from discarding completed audio. Quota and concurrent-tab defects remain below. |
| Atomic audio writes, complete-length receipts, identical retry detection, conflict rejection | Valuable protection against partial transfer, lost acknowledgments, and accidental overwrite. Existing focused tests pass. |
| Offline transcription mode and locally served Bootstrap assets | Removes the internet requirement for recording with the local server running. Old tabs and restored session mode still need clear version/mode handling. |
| Restore/clamp the recording interval | Addresses the zero-interval empty-file loop. Deterministic recorder test passes. |
| Recorder identity checks and intentional recovery-stop tracking | Addresses stale stop/error events from a replaced recorder. Test also confirms the old recorder's final audio is retained. |
| Reject header-only WebM, zero-frame and simple truncated WAV | Blocks the observed 110-byte loop before new AI work. Validation is still incomplete for other malformed files. |
| Separate browser backup/server save indicators, tolerate heartbeat loss while locally capturing | Much better feedback, but current health/finalization state still has false positives and conflicting server/client states. |
| Sleep protection, capture audit, partial-evidence guards, reconciliation prompt | Useful safeguards and diagnosis. A missed heartbeat must not itself become proof of lost audio. Current server behavior still does that. |
| Larger HTTP backlog and retryable disk errors | Sensible resilience measures; do not resolve unbounded AI worker creation or status-write races. |

## Findings requiring follow-up

P1 means fix before treating the app as a dependable sole recorder. P2 means a significant correctness or operational issue to include in the stabilization release.

### 1. P1 — Browser storage failure blocks saving to a healthy server

At [recording-storage.js:95](C:/Users/mirol/dev/dndtracker/recording-storage.js:95), every upload first requires another successful browser-store write. If IndexedDB is full or unavailable, that write throws and upload is never attempted. The raw chunk may be only in memory. Converting compressed audio to WAV before this write increases storage pressure; startup also loads every queued blob with `getAll()`.

**Reproduced:** a throwing store with a healthy upload transport produced zero upload attempts, one in-memory chunk, and zero durable rows. The existing quota test actually asserts this behavior, so its passing result does not establish safety.

**Fix:** retain immutable compressed originals, avoid mandatory WAV conversion on the capture/save path, and support a verified local-server save when browser persistence fails. Never claim a browser backup that failed. Use stable IDs/hashes and receipt checking for this fallback. Read queue metadata first and load blobs on demand. Monitor capacity and request persistent storage where supported; persistence does not remove quota limits. [MDN storage quotas](https://developer.mozilla.org/en-US/docs/Web/API/Storage_API/Storage_quotas_and_eviction_criteria)

### 2. P1 — Two tabs can erase each other's pending backup

At [recording-storage.js:73](C:/Users/mirol/dev/dndtracker/recording-storage.js:73), the durable key is only `sessionId:index`; duplicate detection checks one tab's in-memory Map. Two tabs can allocate the same index and overwrite the same IndexedDB row. One tab then deletes that row after its own successful upload, removing the other tab's backup. Server conflict detection protects the server file, but does not protect this browser copy.

**Reproduced:** two queue instances with distinct audio at the same key; after tab A saved, tab B still showed one pending chunk in memory, but reloading the store recovered zero chunks.

**Fix:** one recording owner and one uploader per origin/session, plus transactional sequence allocation and unique immutable chunk IDs. Coordinate tabs with an exclusive lock and show which tab owns recording. Keep both payloads on any conflict. Also keep one canonical origin (`127.0.0.1:8000`), since switching to `localhost` uses different browser storage. [Web Locks API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Locks_API)

### 3. P1 — Refresh/crash still loses the unfinished recording; rollover creates gaps

[dnd-audio.html:2500](C:/Users/mirol/dev/dndtracker/dnd-audio.html:2500) starts MediaRecorder without periodic data delivery and stops it at the chosen chunk interval. The queue only receives audio after that stop. At the default setting, roughly two minutes remain outside the durable queue; a larger selected interval increases that exposure. Every normal rollover then waits at least 150 ms before creating the next recorder, leaving a deliberate capture gap plus scheduling delay.

**Fix:** keep capture continuous and journal ordered encoded fragments frequently, with a target of roughly 5–10 seconds between durable checkpoints. Assemble complete, validated files separately for transcription. Do not assume each MediaRecorder fragment is an independently decodable WebM, and do not use fragment count as elapsed time: delivery can be delayed by the browser. Test container assembly and sample continuity before replacing the current path. [MDN dataavailable](https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder/dataavailable_event)

### 4. P1 — Recovery requires the server even when browser storage is ready

[dnd-audio.html:1627](C:/Users/mirol/dev/dndtracker/dnd-audio.html:1627) requires a successful server status request before acquiring the microphone. A server outage/restart therefore prevents recovery despite a ready local queue.

**Reproduced:** failed status fetch led to `recoveryFailed` and zero microphone-start attempts.

**Fix:** retain a durable session manifest, recording epoch, and next sequence in the browser; recover locally, then reconcile/save when the server returns. Coordinate this with the tab-ownership fix to prevent index reuse. This limitation concerns an unavailable local server; an internet outage alone need not affect that server.

### 5. P1 — Session initialization can overwrite a newer chunk/status update

[server.py:7909](C:/Users/mirol/dev/dndtracker/server.py:7909) reads and later replaces the complete session status outside the shared status mutation lock. The frontend now starts initialization in the background and can retry it during queue upload, so it can overlap capture and upload updates. Atomic file replacement prevents a torn JSON file; it does not prevent replacing fresh data with an older snapshot.

**Reproduced:** interleaving a completed `update_status_for_chunk()` between initialization's read and write changed the chunk manifest from one entry back to zero. The audio file need not be lost, but recording/recovery/finalization can act on an incorrect boundary.

**Fix:** route all session mutations through the same lock/transaction; make initialization an idempotent metadata merge. Reconcile status against an append-only chunk manifest and actual saved files after restart.

### 6. P2 — Watchdog immediately re-interrupts healthy recording after a long pause/recovery

[capture-reliability.js:238](C:/Users/mirol/dev/dndtracker/capture-reliability.js:238) prefers the previous `lastChunkEmittedAtMs` over a newer recorder start. After a sufficiently long pause or interruption, that old timestamp is already outside the watchdog limit.

**Reproduced:** after a four-minute pause, Resume became `interrupted / chunk_watchdog_expired` one second after the healthy recorder started. Long recovery produced the same result.

**Fix:** scope progress deadlines to the current recording epoch; reset the expected next checkpoint when resuming or recovering. Detect actual recorder inactivity separately. A delayed page timer alone should mean “capture needs verification,” not a proven audio gap.

### 7. P2 — Normal Stop can report an error before a healthy final upload completes

[dnd-audio.html:2369](C:/Users/mirol/dev/dndtracker/dnd-audio.html:2369) waits for browser enqueue work, then immediately throws if any session chunk remains queued. It does not wait for server saves. `onSaved` does not resume finalization. The user sees an error and must manually finish finalization even after saving succeeds.

**Reproduced:** Stop reported `FINALIZATION ERROR`; the simulated healthy server then saved the final chunk and the queue reached zero, but the finalize endpoint was never called.

**Fix:** persist per-session “recording stopped; saving pending” state, drain the session queue, and automatically retry finalization when its last receipt arrives. Treat deferred transcription as a separate later stage. Preserve multiple pending sessions, rather than a single localStorage finalization slot.

### 8. P2 — Heartbeat gaps and old events leave contradictory, persistent errors

[server.py:1458](C:/Users/mirol/dev/dndtracker/server.py:1458) turns heartbeat expiry into a capture gap, even when the offline browser may still be recording. [server.py:1322](C:/Users/mirol/dev/dndtracker/server.py:1322) refuses to clear an interrupted state on subsequent healthy heartbeats. Meanwhile the locally active client ignores that interrupted response. Player Companion can remain blocked while the browser shows healthy capture. Status reads can repeatedly log the same heartbeat expiry; old events can reopen a stopped session because there is no event-generation/order check.

**Reproduced:** healthy recording heartbeat after expiry left server state `interrupted`; three status reads appended three more expiry events; a late pre-stop interruption event changed a finalizing session back to `sessionOpen=true`.

**Fix:** separate capture state, browser durability, server connectivity, transcription, and historical incidents. Heartbeat loss is connection uncertainty until audio evidence establishes a gap. Reconcile on reconnect, deduplicate incident IDs, and reject stale events using recording epoch plus sequence. Keep historical incidents visible without presenting them as current failures.

### 9. P2 — Existing bad files poison backfill; validation and AI work remain insufficiently isolated

[server.py:4670](C:/Users/mirol/dev/dndtracker/server.py:4670) selects every saved audio filename lacking a transcript without validating its audio. [server.py:6619](C:/Users/mirol/dev/dndtracker/server.py:6619) aborts the whole backfill on the first failing file. Tonight's old header-only files remain eligible. Live uploads still create one daemon transcription thread per new chunk at [server.py:7979](C:/Users/mirol/dev/dndtracker/server.py:7979), without a durable bounded job queue.

The new validator also measures WAV completeness using total file length and a fixed 44-byte header assumption. **Reproduced:** a WAV with metadata, 100 declared audio bytes, and only 2 actual audio bytes was accepted. Ogg/MP4 receive no comparable structural validation.

**Fix:** classify existing files without deleting or rewriting them; exclude structurally invalid audio from AI work and continue processing other valid files. Validate actual payload/duration, support per-chunk terminal and retryable failures, cap concurrent AI jobs, persist job state, and resume unfinished work after restart. Capture/storage must continue independently of all AI failures.

## Recommended release order

1. **Data safety:** resolve findings 1–5, preserve compressed originals, and enforce one recording owner. Make local saving the server default unless live transcription was explicitly selected for the session.
2. **Trustworthy controls:** resolve findings 6–8. Show separate “microphone capturing,” “saved through [time],” “pending local saves,” and “transcription” states. Show the actual active offline/live mode; the checkbox currently changes the latched mode only on Start, not during Recover.
3. **Controlled recovery and operations:** resolve finding 9; add client/server build IDs and capabilities to session events/receipts. A restart should reconnect the existing page; a required upgrade should be offered at a safe saved boundary. Update the launcher to recognize its own process and avoid force-killing unrelated port owners or opening duplicate recording tabs. Add bounded persistent request logs with correlation IDs, byte counts, durations, response status, disconnect phase, queue age/size, and worker counts; exclude audio, transcript contents, and secrets.
4. **Release gate:** run a four-hour recording on the actual Windows/browser/microphone setup, using a timestamped test signal to detect gaps and duplicates. Include internet loss, local-server loss/restart, lost receipts, browser close/reopen, a long pause, recovery after several minutes, storage quota failure, disk-write failure, two tabs, device disconnect, and minimized/locked-screen behavior. Verify every received chunk hash and the assembled audio timeline. Stop must settle automatically; reconnect must not create a false capture gap. Document the measured crash-loss window and unsupported sleep behavior rather than claiming zero loss.

For the next game, use an independent continuous backup recorder until that release gate passes. Keep the tracker on one tab and the same origin; avoid using refresh as recovery. This is a temporary operational recommendation, not a substitute for the fixes above.

## Verification and scope limits

**85 existing checks passed in this review:** 23 Node checks across the capture/queue suites, plus 62 Python checks covering storage, capture, static assets, Player Companion, and session reconciliation.

Commands:

```powershell
node --test tests/capture_reliability_ui.test.js tests/recording_storage_ui.test.js
$env:TEMP = Join-Path (Get-Location) '.pycache_tmp'
$env:TMP = $env:TEMP
python -m unittest tests.test_recording_storage tests.test_capture_reliability tests.test_static_ui_assets tests.test_live_player_assistant tests.test_session_reconciliation
```

Additional deterministic probes reproduced **11 adverse cases** described above. These assert the observed defects; they are not proof that those defects are fixed. They use real production functions with fake timers/storage/transports or isolated session files and make no external API calls:

```powershell
node .pycache_tmp/review-2026-10-07/probes.js
python .pycache_tmp/review-2026-10-07/probes.py
python .pycache_tmp/review-2026-10-07/inventory.py
```

The probes and synthetic fixtures remain under ignored `.pycache_tmp/review-2026-10-07/`. Earlier synthetic-browser checks covered basic rollover and queue survival across closing/reopening; they did not establish a game-length reliability guarantee. This review did not rerun a real-microphone browser test or a long-duration soak. No production code, server process, recording page, or existing session files were changed by this review.
