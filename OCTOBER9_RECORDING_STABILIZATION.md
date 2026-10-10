# October 9 targeted recording stabilization

Status: targeted patch implemented and automated regressions passed. **Real-browser/manual acceptance and four-hour endurance acceptance are not yet passed.** Production services and the user's recording tab were not restarted/reloaded. Historical session files and the original 137-second gap record were not rewritten.

## Changes

- `capture-reliability.js` recognizes an explicitly scheduled recorder rollover for at most **10 seconds**. An inactive recorder in this known interval is reported as transitioning to local health checks and server heartbeats. The matching replacement `onstart` clears it. Missing `onstop` or missing replacement `onstart` still produces `recorder_rollover_stalled`; ended tracks, inactive streams, recorder errors and normal storage watchdogs remain detectable.
- `dnd-audio.html` marks rollover before `stop()`. Old final callbacks still queue their audio even after the recorder is replaced. A recovery stop is included in the existing save drain barrier, so Stop chooses the final chunk boundary after late final data has been queued, rather than excluding the old tail.
- Meter cleanup detaches the old AudioContext, analyser and animation-loop ownership synchronously. Its asynchronous close cannot clear a newer meter. Stale frame callbacks are rejected by generation. Context/analyser errors affect only the meter and produce metadata diagnostics; they do not abort audio capture or recovery.
- The interruption panel shows **recovery requested → microphone reconnecting → recorder starting → recorder started/waiting for a checkpoint → new audio durably checkpointed**. Failure includes the actual stage and error. Recovery remains pending after `onstart` until a successful checkpoint from that attempt's replacement recorder. Old-session/old-attempt checkpoints cannot certify it. The gap end uses the replacement recorder start callback, while confirmation occurs later at durable save. Displayed gap duration is labeled a timing estimate.
- Recovery uses an attempt ID and ownership checks after asynchronous waits. Stop and Pause cancel it; repeated Recover is rejected while pending; late microphone results are stopped. Failed attempts allow Retry without creating another session. Index allocation keeps its existing monotonic rules. Start remains blocked during recovery; Stop is available. Microphone acquisition has a **15-second** timeout, recovery preparation/start stages have a 15-second observation limit, and started capture must produce a new durable checkpoint within **30 seconds**. Health observation runs every 5 seconds, so observer-driven alarms occur on the next observation after their deadline.
- Completion of an old recovery diagnostic cannot restart capture-health timers after Stop. Meter failure and server-status failure remain separate from microphone/recorder health. Recovery status lookup is best-effort with a one-second fetch timeout; local checkpoints/index reservations support recovery while the server is unavailable.
- `capture_reliability.py` and `server.py` accept the narrowly scoped rollover/recovery/meter events and metadata fields. Recovery cancellation/failure remains interrupted in server status. Diagnostics contain stages, IDs, indexes, timeout bounds, context state and device/storage error text; no audio samples, transcripts, campaign content, prompts, credentials or AI work were added.

The original investigation remains in `LATEST_RECORDING_FAILURE_INVESTIGATION.md`. This patch does not reinterpret or silently edit original server gap records. AI models, prompts, live transcription, rolling notes and Session Memory behavior were not changed.

## Automated evidence

Before applying the production fix, the new regression `health check between rollover stop and queued onstop preserves capture and saving` **failed**: the controller reported `interrupted` while the old recorder's normal final callbacks were queued. It now passes and asserts continued recording and retained final audio.

The tests execute the production recorder callback functions, controller, meter functions, recovery/Stop/Pause handlers and UI renderer in a VM with synthetic blobs, mocked browser devices/AudioContexts, controlled callback order and in-memory storage. They exercise:

- Health/heartbeat observations between scheduled stop and final callbacks; delayed `onstop` at 100 ms and 9 seconds; missing `onstop` and missing replacement `onstart`.
- Final `dataavailable` followed by `onstop` without awaiting the asynchronous checkpoint handler, matching browser dispatch semantics.
- Real ended-track detection during the rollover allowance; healthy local capture through server outage; queue retention/retry and old callback isolation in existing suites.
- Delayed/rejected old context close, stale frame callbacks, rapid meter restarts, continued meter use across rollover, and independent capture after meter construction/analyser failure.
- Accepted recovery, device failure and Retry, repeated clicks, Stop/Pause while device acquisition is pending, Stop during status lookup, timeout followed by late device success, and late recorder-start/diagnostic callbacks.
- No durable-recovery claim before a matching replacement checkpoint; failed/no checkpoint timeout; visible progress/failure stage; same session and increasing index allocation.
- Stop while an old recovery recorder's final-data callback is delayed: finalization waits, includes the final index, and retains the old synthetic audio.
- Backend event acceptance, capture-state semantics and rejection of audio/transcript/API-key/campaign-content fields from diagnostic records.

Final commands:

```powershell
$env:TEMP = Join-Path (Get-Location) '.pycache_tmp'
$env:TMP = $env:TEMP
python -m unittest discover -s tests -p 'test_*.py'
$testFiles = @(Get-ChildItem tests -Filter '*.test.js' -File | Where-Object Name -ne 'table_ready_ui.test.js' | Select-Object -ExpandProperty FullName)
& node --test @testFiles
git diff --check
```

Results: **204 Python tests run: 203 passed, 1 skipped. 60 JavaScript checks passed.** Python wrappers rerun some JavaScript tests; these counts are not 263 distinct scenarios. The optional `table_ready_ui.test.js` real-DOM test requires jsdom, which is unavailable. It was attempted and then reported as the Python suite's optional skip; no package was installed. The full Python suite includes the asset-serving contract test.

The first full Python run also identified a pre-existing stale Session Memory contract assertion for a direct finalization fetch inside `finalizeStoppedSession()`. HEAD already delegates that operation to the durable finalization settler. The test was updated to assert the save barrier, queued finalization-before-memory ordering, and deferred-mode exclusion; production Session Memory code was unchanged.

The isolated HTTP test initially hit sandbox `WinError 10013`. It passed with approved loopback access outside the sandbox, using synthetic temporary data and its own test listener. Port 8000, production uploads, browser storage and paid providers were not used by the tests. No commit or push was performed.

## Short controlled Windows/browser/microphone test

Run only after the target test build is started in a **persistent isolated workspace with empty test campaigns/uploads and a dedicated browser profile/origin**, using the real microphone. Keep the production server and tab untouched. The normal launcher fixes port 8000; do not invoke its stop script against the active production service. Use a separately configured test server/host for isolation. Do not use the temporary preview fixture for restart/preservation acceptance: it deletes its synthetic directory when the fixture exits.

1. Play a nonrepeating time-coded reference you own and capture it with an independent recorder. Choose deferred transcription, two-minute chunks, and one test tab. Record at least 15 minutes across several boundaries. Check input bars, Last browser backup, Last saved, completed files and pending-save count. No rollover alarm should occur.
2. Cause a genuine device interruption in this isolated run (for example, disconnect the selected USB microphone). Check the actual warning and error reason. Acknowledge must silence the alarm while leaving interruption visible. Reconnect the device and click Recover once. The session ID must stay the same. Watch stages; `Recorder started` alone is not success. Within the next checkpoint interval, expect `New audio durably checkpointed`, advancing backup time and responsive meter.
3. In a separate short test run, repeat interruption/recovery and press Stop while reconnecting. Late device completion must not start recording. Also exercise Pause cancellation and repeated Recover clicks. A denied/unavailable device must show its failure stage and permit retry.
4. Stop only the **isolated test server** while recording. Capture and durable browser checkpoints must continue with pending saves retained. Restart the same persistent test server and let pending saves drain. Keep its original browser tab/profile/storage intact.
5. Stop normally. Verify final partial chunk, monotonically increasing indexes, no missing expected files and an empty pending-save queue. Deferred mode should show Saved — Transcribe Later; it must not run transcription to make this test appear complete. Decode every file and align it to the independent source at each rollover/recovery boundary.
6. After the offline/device cases pass, repeat a separately approved live-transcription/rolling-notes run with nonprivate reference audio and controlled provider spending. Compare local saving and recovery behavior under that workload. This step was not run by Codex.

No Simulate Recording Failure control was added. It remains the separately requested next step. Hardware disconnect is a manual real fault, not proof that every possible browser-recorder fault has been exercised.

## Remaining limits

- No real-browser recorder, physical microphone, browser IndexedDB or full DOM acceptance was exercised for this patch. VM tests validate application logic and callback ordering; they cannot certify platform scheduling or device behavior.
- Ten-second rollover and 15/30-second recovery bounds are conservative detection limits, not promises of sample continuity. A suspended browser/event loop can delay observers. Source alignment and the four-hour Windows acceptance test remain required.
- If a browser never delivers an old recorder's final callback during recovery, Stop's save barrier remains unresolved rather than silently finalizing without that data. Keep that tab/storage and collected diagnostics; do not clear storage to force completion. This is a preserved-evidence limitation for the controlled browser test to examine.
- The initiating October 7 transport failure and initial October 9 meter freeze remain historical uncertainties. These changes address the reproduced rollover alarm and asynchronous replacement-meter race.
- Production is still running its prior loaded server code/tab. Applying the changed diagnostics and client behavior requires a later safe server restart and tab update after all production recordings and pending saves are finished. No such action was taken during this assignment.
