# Controlled 60-minute live-transcription endurance test

Status: **COMPLETED — saved-audio/lifecycle checks PASS; reference continuity and final browser observations INCONCLUSIVE.** The verification did not initiate provider calls or inspect conversational content. The exact-order isolated USB recovery/rollover check passed with user-confirmed responsive meter and readable recovery history. Production was safely restarted with the repaired build; health, campaign routes and static source hashes passed. Existing production-browser reload and visual confirmation remain pending before starting this test.

## Before starting

- Finish the isolated USB recovery test, keeping replacement capture active for at least 2 minutes 15 seconds after recovery. Review the saved replacement chunk, subsequent recorder start, meter response and absence of a false alarm.
- Confirm all production tabs have stopped capture, no pending browser uploads or in-memory backups, and no unresolved save/finalization/recovery operations. Waiting for intentionally deferred transcription is distinct from pending audio; do not trigger transcription of historical offline tests. Server metadata and stale historical session-open flags alone cannot establish the current browser's queue state.
- Preserve current production files and unrelated working-tree changes. Record listener identity, build file hashes and production recording-file metadata before the supported stop/start procedure. Verify the correct replacement process, health endpoint and every allowlisted static asset against the current build. Update the existing production tab only after its queue has settled; do not clear storage or open competing recording tabs.
- Retain all isolated recordings, logs, browser profile and origin. Do not delete the test environment to prepare production.

## Reference and settings

Use nonprivate reference audio you own, with a distinguishable, nonrepeating time code. Save an independent reference recording on another device or independent recorder, started before tracker capture. Keep the original reference source too. Confirm both recordings actually contain the reference signal; a microphone level bar alone is insufficient.

Record in a clearly named new test session, using the approved production browser and USB microphone. Set **2-minute chunks**. Uncheck **Offline recording — transcribe later** to enable the existing live-transcription/Recent Play workflow. Keep existing AI models/prompts unchanged. Record the actual provider/model settings from runtime metadata, not guessed defaults. This live run incurs provider charges and should be explicitly started by the user after production update verification.

## Execution and evidence

1. Note session ID, browser/version, microphone, start time and independent reference start time. Start tracker capture; verify meter activity and advancing Last Browser Backup.
2. Run for at least **60 minutes** with the normal Recent Play workload. Do not refresh, clear storage or switch recording tabs. Observe the meter and status at each rollover, with detailed notes at minutes 2, 10, 20, 30, 45 and 60. Record any warnings and exact time/reason. Verify completed chunks save even if transcript/Recent Play updates lag.
3. Observe live transcription and Recent Play updates after the first processed chunks. Record provider failures/retries and their effect on capture separately. Do not call an AI delay a recording loss. Do not add a fault injection to this first endurance baseline; that belongs in a separately documented follow-up.
4. After 60 minutes, press Stop once. Keep the tab and reference recorder running until the final audio save is acknowledged, then stop the reference recorder. Note final pending-save status. Allow requested transcription/notes/finalization work to settle; retain evidence if it fails rather than clearing the queue.
5. Review metadata and decode every saved audio file. Expect approximately 30 full two-minute chunks plus any final partial chunk, subject to actual start/Stop timing. Verify consecutive indexes, expected final boundary, acknowledged uploads, no missing files and complete decoded payloads. Check no recovery/rollover false alarms occurred. Report actual total duration rather than assuming it equals 60 minutes.
6. Align tracker audio against the independent time-coded reference at each boundary. Distinguish recorder callback timing from measured audio continuity. Report measured gaps/overlaps and alignment uncertainty; only claim sample-level continuity if the reference and analysis actually support it.
7. Review HTTP/capture logs for upload failures, retries, recorder/device/storage interruptions and recovery, and provider/Recent Play processing metadata for backlog and errors. Keep diagnostics metadata-only; do not publish transcripts or private campaign content in the report.

## Verdict

PASS requires complete recoverable audio, responsive metering, durable backup progression, consecutive saved chunks and a correct final audio boundary, no false capture interruption, and sustained live-transcription/Recent Play operation or clearly isolated provider errors that did not stop audio capture. Any unresolved missing audio or browser queue is a FAIL; missing observations/reference evidence must be marked INCONCLUSIVE for the affected claim.

A 60-minute PASS supports this particular controlled workload. It does **not** establish game-length reliability. Follow it with the existing multi-hour soak acceptance using the actual Windows browser/microphone and independent reference, before treating a full D&D session as dependable.

## Interim observation — October 9, 7:13 PM Pacific

Verified directory and status ID **1791596995796** before inspecting metadata. Capture started at **6:49:56 PM**; latest heartbeat was **7:13:07 PM**, with healthy state, active stream/live track, recording recorder, no interruption/recovery requirement or connection-loss flag. Eleven normal rollovers and upload acknowledgments appear; replacement recorder index **11** is in progress. Saved indexes **0–10** are consecutive complete readable PCM WAV payloads, totaling **1,320.66 seconds (22:00.66)**. Index 0 is 119.88 seconds, indexes 1–9 are 120 seconds each, and index 10 is 120.78 seconds. The slightly long chunk is recorded evidence, not proof of duplication or acoustic continuity.

All eleven completed chunks report successful live transcription. Transcript/notes artifact modification times advanced through **7:12:25/27 PM**; only filenames, sizes, timestamps and processing status were checked, not text or audio content. Capture events include two screen-wake-lock losses/reacquisitions but no capture interruption, track-ended or recovery events. The run remains active: finalization, final audio boundary and the 60-minute verdict are unverified. Browser checkpoint progression, dynamic meter/Recent Play rendering and existence/alignment of a separate reference recording still require user confirmation. Server heartbeat alone cannot verify browser checkpoint durability or queue emptiness.

## Completed run — session 1791596995796

Capture ran **October 9, 6:49:56–7:50:14 PM Pacific**, approximately **60 minutes 18 seconds** according to integer-second server timestamps. All **31 consecutive WAV files (indexes 0–30)** exist and contain their complete declared PCM payloads. Total decoded duration is **3,617.28 seconds (60:17.28)**. The final partial chunk is **4.20 seconds**. The 0.72-second difference from coarse elapsed time is not proof of lost audio: start/Stop timestamp precision, recorder boundaries and encoding timing differ. Several full chunks are 120.78–120.96 seconds; do not infer duplicated audio from length alone.

All **30 normal rollovers** led to a replacement recorder; logged transition intervals were **57–85 ms**. All **31 chunks** were emitted, uploaded and acknowledged, and all **31 live transcriptions succeeded**. No capture interruption, recovery, track-ended or recorder-error event was recorded. Six screen-wake-lock loss events were accompanied by six acquisitions during the run; no corresponding capture interruption appears. Metadata shows notes/transcript artifacts were produced; their text and the recording's conversational content were not inspected.

Stop is persisted, capture state is **stopped**, and pending audio finalization reports **completed**, expected final index **30**. Server state is **ready_for_reconciliation**, with no missing, pending, failed or unexpected chunks. This proves settled audio/transcription prerequisites; it does not claim Session Memory reconciliation or every optional AI output is complete. Duplicate stop/finalize-start metadata entries do not imply duplicated audio: there is one saved file per expected index and a completed finalization event.

**PASS for this run's file integrity, sequential indexes, observed recorder lifecycle, successful live transcription and settled audio finalization. Overall reference-based acceptance remains INCONCLUSIVE.** The phone reference reportedly started at **7:14 PM**; its audio has not been supplied or compared, and it cannot verify earlier intervals. No sample-accurate continuity, measured acoustic gaps/overlaps or game-length reliability claim is made. Browser queue emptiness, meter behavior throughout the full run, advancing browser backups and Recent Play rendering still require the user's final observations. HTTP log entries lacking session IDs cannot establish session-specific retry absence; metadata evidence is retained in `.pycache_tmp/real-mic-20261009-153804-0da78fe6/production-endurance-final-evidence.json`.

Next: confirm the stopped browser reports backup ready with no pending saves and report any meter/Recent Play warnings; preserve and compare the overlapping phone reference at chunk boundaries. Then perform a multi-hour representative session with a reference recording started before tracker capture. No further recording, AI processing, restart, code change or data cleanup was triggered by this read-only review.

### Final browser evidence and build label

The user supplied a screenshot for session **1791596995796** showing **STOPPED**, elapsed **01:00:18**, and **Local audio backup ready** below the Start/Stop buttons. This confirms the browser-reported audio save queue is settled; IndexedDB was not directly inspected. The screenshot also shows a rendered Player Companion response, but is not evidence that meter/Recent Play rendering remained responsive throughout every rollover. The independent phone recording remains preserved but uncompared, so acoustic continuity remains INCONCLUSIVE.

At the user's request, this checkpoint is named **Stable audio build**. This is a release/checkpoint label supported by passing automated regressions, isolated physical recovery acceptance and the completed one-hour live-transcription file/lifecycle checks. It does not certify reference-based continuity or game-length reliability. Production campaign records, recordings and unrelated local changes are excluded from the stabilization commit.
