# Four-hour Windows recording acceptance test

Status: procedure only; **not performed for the October 9 assignment**. Unit tests and a 150-chunk synthetic backlog cannot certify continuous recording. Budget at least four hours of actual capture plus fault/recovery and inspection time. A constant oscillator alone cannot detect duplicated intervals.

## Setup between game sessions

1. Finish and save any real recording before testing. Use a **separate test checkout/workspace**, separate browser profile and its documented local server at `http://127.0.0.1:8000/`. The test workspace must have empty synthetic `uploads` and campaign folders and no `.env` credentials. Do not copy campaign recordings or credentials. Stop/start only that test server. Record the commit, Windows/browser versions, microphone, sample rate, chunk interval, free disk space and browser storage estimate. Do not clear existing browser data.
2. Choose **Offline recording — transcribe later**, two-minute chunks, and one recording tab. Do not run transcription, notes, backfill or any AI action. Avoid test folders whose recordings are automatically deleted when the server exits: the short-lived `offline_recording_preview.py` fixture is unsuitable for the server-restart portion because it removes its temporary data on exit.
3. Prepare at least 4 hours 15 minutes of nonrepeating **synthetic/reference audio** you own. It must include a continuous signal and uniquely numbered audible time markers at least once per second. Keep its original file as the independent reference; a repeated music loop or constant tone is insufficient. Play it through a separate device/speaker into the actual microphone. If a suitable source or waveform-alignment tool is unavailable, record that prerequisite as missing rather than passing the continuity test. No extra dependency installation is authorized by this procedure.
4. Also record the source continuously with an independent recorder or second device. This separates a source/playback failure from tracker loss. Note the actual source offsets of Start, every fault action, recovery, and Stop; use a wall-clock action log. For precise continuity, align decoded tracker audio against the reference waveform, allowing for microphone latency and clock drift. Auditory marker checks alone detect second-scale problems but do not prove subsecond continuity.

## Run and fault schedule

Start reference playback first. Press Start once. Record source offset and wall time. Keep the actual computer workload representative of game night. Obtain at least **four hours of active capture**, excluding deliberate pauses/closed-page time; extend the run after faults as necessary.

| Active capture time | Action and required observation |
|---|---|
| 0–60 min | Continuous baseline. Checkpoint and completed-file timestamps must advance; log any warning. Do not infer a save from the sound bar. |
| 60–65 min | Stop only the isolated test server, keeping the page and microphone running. Pending browser audio must grow; the recorder must not require Restart/Recover solely because the server is down. Restart the same test server/workspace and wait for the queue to drain. |
| About 100 min | Disconnect internet for five minutes, leaving loopback and the local server available. Capture and local saves must continue; offline mode must make no provider requests. Restore internet. |
| About 130 min | Immediately after a visible checkpoint, note its time and close the test tab mid-chunk. Reopen the same URL/profile, wait for saved checkpoint recovery, and explicitly Recover. Log the intentional period without a recording page. Audio through the last successful checkpoint should remain recoverable. Audio after that checkpoint and before recovery is an expected exposure, not a zero-loss promise. |
| About 170 min | Pause for 30 seconds, Resume once, and verify the next checkpoint/file. Log the pause as deliberately excluded audio. Do a separate short rapid-controls run after the four-hour baseline; do not disguise its losses as normal pauses. |
| At least 240 min active | Stop the test server once more, then press Stop in the still-open recording page. It must stop capture and retain pending audio/finalization. Restart the same server, keep the page open, and wait for zero pending saves and Saved — Transcribe Later. |

Record every extra recovery, refresh or manual retry. Unexpected need for intervention fails the unattended-use gate even if audio is eventually recoverable. Do not test sleep or unplug the device during the baseline; run those as separate, explicitly interrupted-capture cases afterward.

## Inspect the preserved evidence

- Keep source/reference, independent recording, tracker chunk files, `status.json`, `capture_events.jsonl`, test-server logs, action log, and screenshots of final pending/save state. No deletion or backfill is required.
- Confirm one logical saved chunk per index. Reserved/skipped indexes alone do not prove a lost interval; use the reference markers. Multiple extensions at one index require investigation, not arbitrary selection or summing.
- Decode and inspect every saved file, including recovered WebM/Ogg if present. A nonempty file or WAV header is insufficient. Record each chunk's first/last source marker, decoded duration and measured boundary alignment to the preceding chunk.
- Sum decoded samples divided by sample rate per file. Compare to the reference interval from actual capture Start to Stop minus documented pauses/page-closed intervals, including the measured partial-checkpoint boundary. Record clock drift separately; do not assign all duration differences to drift.
- Check marker order across **all** files for missing, repeated and out-of-order seconds. Align adjacent decoded waveforms against the independent reference to identify subsecond gaps/overlap; this is essential at every two-minute rollover and recovery boundary. A total duration that happens to match can hide a missing interval balanced by a duplicate.
- Verify audio through each last successful checkpoint after reopening, and through Stop after finalization drains. Confirm every acknowledged filename exists and is readable. Confirm queue restoration did not create conflicting audio or dispatch AI work.

Suggested worksheet:

| Chunk/file | Source start/end | Decoded seconds | Gap/overlap vs previous | Server receipt | Fault association |
|---|---|---:|---|---|---|
| … | … | … | … | … | … |

## Acceptance decision

Pass only with at least four hours of measured active capture, no unexplained missing/duplicate/reordered interval at the analysis resolution, all files decodable, bounded intentional recovery loss documented, automatic outage drain, and successful finalization after Stop. Record the measurement resolution and maximum observed gap/overlap; do not claim sample-exact continuity from one-second marker checks. Any unmeasured boundary, unresolved pending item, unplayable checkpoint recovery, false save claim, or unexplained duration difference leaves the gate **not passed**.

The real microphone, acoustic source, browser scheduling and Windows device behavior have not been endurance-tested by the automated suites. If the reference source/analysis setup is not available, a four-hour background run is useful exploratory evidence but cannot establish unattended reliability. The next milestone is to equip and complete this acceptance run, before starting the proposed modularization.
