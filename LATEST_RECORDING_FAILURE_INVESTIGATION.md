# Latest recording failure investigation — October 9, 2026

**Investigation complete; production fixes not implemented.** This is the Phase 1–3 deliverable requested by the user. Application code, production services, recorded audio, browser storage, and campaigns were left unchanged. No transcription or paid AI requests were run. Audio was validated as PCM and measured without playback or conversational inspection. Existing transcript and notes text was not inspected.

## Finding summary

| Finding | Classification | Evidence |
|---|---|---|
| The interruption alert occurred during a normal two-minute rollover | **CONFIRMED** | `recorder_not_recording` with `inactive` at 13:54:05.683, final chunk event at .687, replacement recorder start at .759; source-code timing window reproduced in memory |
| The health controller can falsely treat an expected rollover as stopped capture and retain that alarm after the next recorder starts | **CONFIRMED** | Production `captureSignals()` plus controller reproduced `interrupted` during expected rollover and remained interrupted after a recording replacement |
| This mechanism initiated the incident alarm | **LIKELY, strongly supported** | Exact reason and callback order match the defect; the event does not include the recorder's expected-stop reason, so the browser's complete internal state was not captured |
| Recovery was invoked, accepted, and restarted recording within the original session | **CONFIRMED** | Recovery accepted at 13:56:21.700, replacement start at .980, recovered event at .981, followed by a saved 17.160-second final chunk |
| Recovery's meter restart can be cleared by asynchronous cleanup of the previous meter | **CONFIRMED code defect; LIKELY explanation of post-recovery frozen bars** | In-memory execution of the unchanged meter functions reproduced a new analyser becoming null after the old context close promise resolved |
| The initial frozen meter's cause is established | **UNKNOWN** | No AudioContext state, analyser, frame-callback, track-muted, or console history was available |
| 137 seconds of audio was lost | **UNSUPPORTED** | That figure is the persisted alarm interval, while two more chunks continued to be saved during it; no source reference exists |
| AI processing or local transport caused this incident | **UNKNOWN; not supported by available incident evidence** | Upload acknowledgments continued, all transcriptions succeeded, and no upload-failure event was recorded; historical resource/HTTP traces are unavailable |

The main distinction is between an observed `inactive` sample at a recorder transition, the subsequent persisted warning, and actual saved audio. The alarm persisted even while replacement recorders continued. A motionless meter alone cannot prove capture failure.

## Affected sessions and builds

All times below are October 9 in America/Los_Angeles (UTC−07:00). All four sessions belong to `test-campaign`, verified from their status metadata.

| Role | Session ID | Created | Stop | Audio/chunks | Processing mode and state |
|---|---|---|---|---|---|
| Earlier offline comparison | 1791576539018 | 13:08:59.018 | 13:30:49 | 21:49.500 / 11 | Deferred; waiting for transcription; no provider processing evidenced |
| Original interrupted live test | **1791578519151** | **13:41:59.151** | **13:56:39** | **14:38.880 / 9** | Live transcription; all 9 succeeded; finalization `finalized` |
| Immediate Stop/Start replacement | **1791579400475** | **13:56:40.475** | **13:56:50** | **9.660 seconds / 1** | Live transcription; succeeded; `ready_for_reconciliation` |
| Most recent manually stopped run | **1791579442243** | **13:57:22.243** | **14:05:06** | **7:44.400 / 4** | Live transcription; all 4 succeeded; `ready_for_reconciliation` |

The recorded original live run spans approximately 14:40, not 20 minutes. The user's approximate duration is retained as an observation rather than overriding the session timestamps. The last run lasted about 7:44, so its clean stop does not establish 15-minute or longer endurance.

Client diagnostics identify `4.5-checkpoints`. The local server was restarted at 13:05:44 and its health response was verified as `serverBuild: 4.5-checkpoints` before these runs. Repository HEAD is `14b3fed5395cfa9ed656d369bf3e1b992a9b5f9a`; no working-tree recording-code changes were present. A build string alone cannot identify every asset byte used by the actual recording tab; its browser/version and loaded-asset hashes were inaccessible.

`status.transcriptionModel` and session AI-usage metadata confirm **gpt-4o-transcribe-diarize**, with **gpt-4o-mini** for rolling live notes. The original live session also records later session reconciliation using **gpt-5.6-sol**. These were existing user-initiated operations, not work triggered by this investigation.

## Incident timeline

Millisecond times are client diagnostic timestamps; integer-second times are server/status timestamps. They are not synchronized audio sample times.

| Time | Evidence / interpretation |
|---|---|
| 13:41:59.814 | Original recorder emitted `onstart` |
| 13:44:01.052–13:52:05.082 | Chunks 0–4 acknowledged sequentially; transcription and live-note usage records followed each upload |
| 13:54:05.683 | First interruption diagnostic: `recorder_not_recording`, recorder state `inactive`; no track-ended, stream-inactive, recorder-error or unexpected-stop event |
| 13:54:05.687 | Chunk 5 emitted, 4 ms after the interruption diagnostic |
| 13:54:05.759 | Next recorder started, 76 ms after the interruption diagnostic |
| 13:54:06.108 | Chunk 5 acknowledged; upload-start-to-acknowledgment 76 ms |
| 13:54:53 / 13:54:56 | Existing usage records show chunk transcription / rolling-note processing completed after the alert; they are completion/audit times, not proof of request-start times |
| 13:56:05.693 / .750 | Chunk 6 emitted and next normal recorder started while alarm state remained interrupted |
| 13:56:06.186 | Chunk 6 acknowledged; decoded duration 120.000 seconds |
| 13:56:20.911 | Acknowledge Alarm received; it silenced the alarm without restarting capture |
| 13:56:21.700 | `capture_recovery_started`: `beginRecovery()` accepted same-session recovery |
| 13:56:21.716 / .840 | Old recorder flushed chunk 7; 16.020 seconds saved and acknowledged |
| 13:56:21.980 / .981 | Replacement recorder `onstart`; `capture_recovered`; only 280–281 ms after recovery acceptance |
| 13:56:39.126 / .256 | Recovery recorder emitted final chunk 8, 17.160 seconds, and received its server acknowledgment |
| 13:56:39 | Stop requested for original session |
| 13:56:40.475 | Immediate replacement session created by Start |
| 13:56:50 | Short replacement manually stopped |
| 13:56:52 | Original transcription completion / capture finalization completion diagnostic |
| 13:57:22.243 | Most recent run started |
| 13:57:45 | Original session reconciliation finalized; separate processing continued after capture stopped |
| 14:05:06–14:05:07.225 | Most recent run stopped, final chunk acknowledged |
| 14:05:50 | Most recent run reached `ready_for_reconciliation`; no recorded interruption |

Persisted gap bounds for the original session are **13:54:04.931–13:56:21.981 (137 seconds rounded)**. The opening timestamp comes from the last saved/acknowledged timestamp used by `interrupt()`, not an independently measured loss onset. This interval spans continued recording and is a health/alarm estimate, not a verified acoustic gap. Original evidence was not rewritten.

## Recorder and microphone diagnosis

**CONFIRMED:** `captureSignals()` reports the raw MediaRecorder state unless `recorderStarting` is set. A scheduled rollover marks expected stop reason `chunk_rollover` and calls `activeRecorder.stop()`; that makes the recorder inactive before queued final-data and stop callbacks complete. The watchdog can run in this window. `observe()` accepts only `recording` or `starting`, so it declares an interruption. The expected-stop reason is not included in the signal it examines.

`onstop` subsequently queues final audio and constructs the next recorder. `recorderStarted()` updates its timestamp but does not clear the prior interruption state. The alarm therefore remains until explicit recovery even if capture is advancing. This exact state sequence was reproduced using production `captureSignals()` and the controller in a Node VM with mocked signals and no real device, network, files, or provider activity. It is a deterministic code reproduction, not a real-browser endurance test.

**CONFIRMED regression attribution:** Git blame places the incompatible watchdog condition, recorder-start tracking, and expected-rollover handling in `177831f8` (October 9 durable recording changes). The stop/start chunk cycle itself predates these changes. This is a recent health-detection regression around an existing normal transition.

**UNKNOWN:** microphone muted status, AudioContext suspension, source silence, frame throttling, CPU pressure, and the exact instantaneous stream state at the alert. Later stored heartbeat fields describe a live track and active stream, but are stale snapshots and do not prove their state for every earlier moment. The incident logs contain no explicit microphone-ended or MediaRecorder error event. Missing diagnostics cannot rule out every transient device/browser problem.

## Persistence and audio integrity

All original live indices **0–8**, immediate replacement index **0**, and latest indices **0–3** exist once in their status inventory. Every corresponding WAV file was read completely and every 16-bit PCM sample decoded without inspecting or printing sample values. All are mono, 48 kHz, uncompressed PCM; status byte counts and file sizes agree. Full SHA-256 values are retained in the inventory below; all 14 live-test file hashes are distinct.

Original decoded total is **878.880 seconds**, compared with **879.312 seconds** between first recorder-start and final chunk-emitted callbacks (difference 432 ms) and **880 seconds** in integer-second capture status. This supports continued saved capture through the alarm interval; it does not prove complete source continuity. Rollover timing proxies for chunks 0–6 range from −9 to +57 ms. The explicit recovery boundary between chunks 7 and 8 has a +210 ms proxy residual. These are `(next start callback − current start callback − decoded duration)` and cannot establish physical gaps/overlaps without reference audio. No 137-second missing block is supported by durations or file sequence.

**UNKNOWN:** last durable browser checkpoint before alarm, IndexedDB transaction/quota failures, residual checkpoint fragments, pending browser queue cleanup, and exact acoustic continuity. Checkpoint operations are not logged individually in the server capture-event file. An audio acknowledgment proves server receipt, not browser storage cleanup. Stop/Start left the original server files available, but preservation of every unsaved browser item cannot be established without that browser's storage evidence.

## Local server, transport, and AI concurrency

Original session has exactly 9 upload-start and 9 acknowledgment events, one each per index. Recorded upload-start-to-acknowledgment intervals are **25–92 ms**. No upload-failed/retry, recovery-failed, track-ended, recorder-error, or unexpected-recorder-stop event was found. All 9 transcriptions succeeded and `transcriptionFailures` is empty. This incident does not exhibit the failed local fetch pattern documented on October 7.

Both `.pycache_tmp/server-logs/20261009-130544-296.out.log` and `.err.log` are empty. No usable HTTP-access history or Python exception trace exists for this run. Body-read failures, Windows connection aborts, status-file sharing conflicts, short failures before diagnostic delivery, and hidden localFetch retries therefore cannot be conclusively excluded. Historical CPU/memory telemetry was not collected. File conversion occurs in the browser before upload; upload acknowledgment time does not include all conversion/backup latency.

The interruption coincides precisely with a normal rollover. The nearest subsequent AI usage entries are about 47–50 seconds later. Usage entries measure completed/accounted processing and do not reveal exact concurrent request start times. Existing live transcription/notes continued after the alarm, and no provider failure is recorded. **No causal link to AI load is established.** The same rollover race can occur in offline mode; the clean offline run does not disprove a timing-dependent race.

The previous server-version mismatch was verified and resolved before these sessions. There is no incident evidence of a health-endpoint mismatch during this test, but the actual tab's cached asset hashes remain unknown.

## Recovery: what succeeded and what remains defective

1. **CONFIRMED:** button action reached `beginRecovery()` and was accepted; the diagnostic is emitted only after acceptance. Duplicate recovery requests are rejected while state is `recovering`.
2. **CONFIRMED by code path and later events:** queue readiness check did not abort recovery; index allocation advanced without duplicating the saved indices.
3. **UNKNOWN:** exact server-status response/fallback during recovery. That stage has no dedicated success/error diagnostic. `localFetch` has a default 5-second timeout and recovery waits for it before replacing the recorder, so an unavailable server can delay capture recovery.
4. **CONFIRMED by trace/code:** old recorder flushed chunk 7. The implementation stops old tracks, obtains a new stream, restarts the meter, and constructs a new recorder before its observed `onstart`. Exact device details and individual `getUserMedia` duration are not recorded.
5. **CONFIRMED:** replacement `onstart` and recovered-state transition occurred within 281 ms of acceptance. Final chunk 8 proves subsequent completed audio reached disk. Durable checkpoint resumption is **UNKNOWN** without browser evidence.
6. **CONFIRMED:** no second interruption or recovery-failed event was recorded before Stop, about 17 seconds later. Thus this incident does not demonstrate recovery failing outright, being stuck until Stop, or immediately retriggering the watchdog.
7. **CONFIRMED code defect:** `startMeter()` calls asynchronous `stopMeter()` without awaiting it. The latter awaits closing the old AudioContext, then clears global `meterCtx` and `meterAnalyser`, which may already refer to the replacement. A mocked old-context close reproduced: new analyser exists immediately, then becomes null when old close resolves. The meter frame loop exits when the analyser is null. MediaRecorder does not depend on that analyser, so the meter can freeze while recording succeeds. Git blame shows this meter defect predates the recent recording changes; the new recovery path makes it relevant.
8. **CONFIRMED feedback limitations:** recovery is declared on `onstart`, before a new durable checkpoint. The global banner still says `RECORDING INTERRUPTED — AUDIO CAPTURE STOPPED` during recovery, although the main status says recovery in progress. Recovery failure logs the exception, but the main banner remains generic. The UI's definitive gap wording overstates a timestamp estimate.
9. **CONFIRMED latent control hazard by code review, not reproduced in the incident:** recovery has asynchronous waits without a shared operation-generation cancellation guard. Stop/Pause can alter state while it waits, then the resumed operation resets `stopRequested`/`paused` and can create another stream. No event evidence says that happened here; it warrants a narrow regression before changing the code.

Acknowledge Alarm correctly only silences the alarm; it is not a recovery action. The original recovery **did restart capture in the same session**, despite the user's inability to see clear confirmation.

## Alternatives and missing evidence

- Initial meter freeze may involve suspended AudioContext, hidden-tab requestAnimationFrame throttling, muted input, or a separate meter callback issue. None is confirmed from these artifacts.
- Real short capture gaps at normal rollover and explicit recovery are possible. The available data does not prove their exact size or absence.
- October 7 initiating local transport failures remain unresolved historically; the current incident's explicit failure reason is different.
- No browser-side console, frame/meter state, checkpoint inventory, or live operation state was available through the exposed browser connection. Only the in-app Help tab was exposed. Prior native inspection awaited app access and timed out. No recording tab was refreshed, closed, or newly opened for this investigation.

To preserve missing evidence manually, keep the original recording tab open. Capture screenshots of session ID, main status, interruption panel, Last browser backup, Last saved, and pending-save count. Save the Technical log and browser console errors, omitting conversational/transcript text. Read/export metadata only from the existing origin's localStorage capture/finalization records and `dungeontracker-audio` IndexedDB `chunks` store: IDs, session ID/index/part, MIME type, byte size, created timestamps, attempts/retry status, and prepared flag. Do not clear records, invoke Retry/Recover, evaluate app actions, or export audio content merely to inspect state. Record browser/version, microphone device and whether the tab was backgrounded. If a recording is active, avoid actions that pause or reload it.

## Proposed minimal stabilization and tests — awaiting review

1. **Recognize bounded expected rollover.** Mark a transition before scheduled `stop()` and expose it to health observation/heartbeat. Treat a known rollover briefly as transitioning while keeping track-ended, stream-inactive, unexpected-stop, error, and stalled-transition alarms intact. Clear the transition when the matching next recorder starts; bound it so a lost stop/start callback cannot be hidden. Reproduce a watchdog/heartbeat tick between `stop()` and `onstop`, and a delayed/missing restart, including late callbacks from old recorders.
2. **Make meter replacement safe.** Detach old context/analyser/frame state synchronously before awaiting close, or await completion under an operation guard; never clear a newer meter after an older promise resolves. Test delayed/rejected close, rapid restarts, recovery, and Stop during replacement. Keep this independent of recording lifecycle.
3. **Give recovery explicit progress and truthful confirmation.** Identify stages (preparing local save, reconnecting microphone, recorder started, awaiting new checkpoint). Confirm restored durable capture after a successful checkpoint belonging to the replacement generation; surface the specific failing stage with retry. Describe alarm/gap estimates as unverified until backed by audio evidence. Preserve the original historical events; do not rewrite the 137-second record silently.
4. **Guard recovery against control conflicts.** Use a small attempt token/ownership check across awaits; Stop cancels a pending recovery and any late returned stream is stopped instead of replacing state. Keep session ID and indexes monotonic, block repeated recovery, and define Pause interaction. Do not let a failed recovery re-enable Start while an active stream belongs to the current session. Tests must cover delayed `getUserMedia`, Stop/Pause during status/device waits, repeated clicks, local-server timeout, and old callback completion.
5. **Add only essential diagnostics.** Metadata-only stage timestamps, recorder generation/index, expected-stop reason, checkpoint receipt, meter AudioContext state and UI-frame heartbeat, and upload/exception outcomes. No raw audio, transcript, prompts, keys, or campaign content in logs. This is needed to resolve the initial meter freeze and any repeat transport failure; it is not an architectural logging redesign.

After review and targeted fixes, run the requested isolated, explicitly enabled real interruption test with synthetic reference audio and separate storage/no API credentials. Exercise actual unexpected recorder stop, acknowledgment, same-session recovery, durable checkpoint resumption, retries, rapid controls, rollover, and temporary local-server unavailability. No test-only control should exist in ordinary production use. Follow with a Windows/browser/microphone manual run using a reference or independent recorder and separately approved live AI workload. The existing four-hour acceptance plan remains the reliability gate.

## Preservation and verification method

Only source inspection, metadata/event reading, complete PCM validation, SHA-256 hashing, git history inspection, and in-memory mocked reproductions were performed. An initial reproduction command had a JavaScript syntax error and was corrected; it did not touch production. Meter reproduction was checked after a full event-loop turn to allow the old close promise to settle. No production patch, new persistent test, dependency install, commit, push, or restart was performed.

The earlier report `RECORDING_20_MIN_TEST_REPORT.md` describes the separate offline session and remains distinct. Existing campaign changes and untracked campaign directories were preserved. Session content was not copied into this report.

### Saved audio inventory

SHA-256 below refers to the saved WAV, not `sourceSha256` (which identifies the original compressed blob before browser WAV conversion). Timestamps are filesystem write times, not acoustic source boundaries.

| Session | File | Bytes | PCM seconds | Saved local time | SHA-256 |
|---|---|---:|---:|---|---|
| 1791578519151 | chunk_0000.wav | 11606444 | 120.900 | 13:44:01.013 | `48cc533e900127f842db7b05f1a24fd0e269dd91fdc64eb452912c6e116ebd58` |
| 1791578519151 | chunk_0001.wav | 11612204 | 120.960 | 13:46:02.039 | `cf2a99c15ddc76ad071e8eb5581263801d300b9ef3d61f655fd7b8742e6edaab` |
| 1791578519151 | chunk_0002.wav | 11612204 | 120.960 | 13:48:03.021 | `24062ac745bbe838ff8356d3b6b086e0b791080713e4e1ce0a8f825feb75b1cf` |
| 1791578519151 | chunk_0003.wav | 11612204 | 120.960 | 13:50:04.042 | `b8e8eb4119287f140b77836e24cf7c1ee23fae6f58ed0452b452fa077604b52e` |
| 1791578519151 | chunk_0004.wav | 11612204 | 120.960 | 13:52:05.026 | `b86df725742a85836c407e001a7733ad84aba451e13616d537731ae8d5989264` |
| 1791578519151 | chunk_0005.wav | 11612204 | 120.960 | 13:54:06.055 | `94232e1c042032588f3cbde7a9d7ea2c6f4320146a71c089a835d97d47c9547f` |
| 1791578519151 | chunk_0006.wav | 11520044 | 120.000 | 13:56:06.143 | `10b93a9ff9bbccabdf8ecb6f86f8b9f95c8ec150aa3b95412c35a9a9b1e9a985` |
| 1791578519151 | chunk_0007.wav | 1537964 | 16.020 | 13:56:21.825 | `cc4ff84e04598219e3dc0a2ed2a7c57021d09274c420223e80bd4edc9659792c` |
| 1791578519151 | chunk_0008.wav | 1647404 | 17.160 | 13:56:39.239 | `26fbe8074512d4648bd4f442d410e448a77523388f9c1b30c86e4a14de701e21` |
| 1791579400475 | chunk_0000.wav | 927404 | 9.660 | 13:56:50.439 | `36582dbf144fe205f0ef0e6ac854f2dfbe960821dcc4d5f8a4aecd83ad986442` |
| 1791579442243 | chunk_0000.wav | 11520044 | 120.000 | 13:59:22.742 | `e9cceed84205c4d00f640909b93ed83ef308ca9a5a6d4dbd5a9fde322078fe7b` |
| 1791579442243 | chunk_0001.wav | 11520044 | 120.000 | 14:01:22.742 | `7216e6e7021a63c708ecae9e4b493eaae164229b19aa542d5dc6e1b7753a705f` |
| 1791579442243 | chunk_0002.wav | 11520044 | 120.000 | 14:03:22.757 | `5d10af405858705c885e54a8da54768bde9c8ae89564c6849159b4ee7895a256` |
| 1791579442243 | chunk_0003.wav | 10022444 | 104.400 | 14:05:07.194 | `d524cf3b1894ad1188d2b1aaba37bf10f1ae67b4fbfd6570627f33206827b7be` |
