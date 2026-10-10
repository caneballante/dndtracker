# 20-minute recording verification — October 9, 2026

**Overall: INCONCLUSIVE for end-to-end reliability; PASS for saved-file completeness and PCM decoding.**

## Scope and verified identity

This report covers only session **1791576539018**, verified from `status.json` as belonging to **test-campaign**, created October 9 at **13:08:59 America/Los_Angeles (UTC−07:00)**. Identity was checked before audio validation. The user described this recording as an unrelated meeting and prohibited conversational inspection and provider processing. No audio was listened to, transcribed, summarized, or sent externally during verification. No application code or session files were changed, no server was restarted, and no recording test was run. Only this report was updated.

Later test-campaign sessions 1791578519151, 1791579400475, and 1791579442243 have transcription-success metadata. They are separate recordings, excluded from the conclusions below. The later live-transcription failure assignment was truncated and needs its remaining observations and exact session identification.

## Duration and saved files

- Capture status: started 13:08:59; stopped 13:30:49. Integer-second wall duration: **1,310 seconds (21:50)**.
- First recorder-start diagnostic to final chunk-emitted diagnostic: **1,309.603 seconds**. These are callback timestamps, not sample-clock timestamps.
- Fully decoded PCM duration: **1,309.500 seconds (21:49.500)**, 62,856,000 mono frames at 48,000 Hz.
- **11 completed files**, indices 0–10 inclusive: ten approximately two-minute chunks and one 108-second final chunk. All expected files exist, match status byte counts, and contain readable uncompressed 16-bit mono PCM. Total WAV bytes: **125,712,484**.
- Python standard-library `wave` read every frame; `struct.iter_unpack('<h', ...)` decoded every sample without printing sample values. Frame counts and payload lengths were checked. No playback or speech/content analysis was performed. ffmpeg/ffprobe were unavailable on PATH; no decoder was installed.
- All 11 whole-file SHA-256 values differ: no exact duplicate files. This does not rule out duplicated passages within different files.
- Status `sourceSha256` values are not WAV-file checksums: the browser hashes the original MediaRecorder blob before its WAV conversion. Their mismatch with saved WAV hashes is expected from that code path, not proof of corruption; original blobs were unavailable for comparison.

| Chunk | PCM seconds | File saved, local time | Upload-start to acknowledgment (ms) | Next recorder start minus estimated decoded end (ms) |
|---|---:|---|---:|---:|
| 0000 | 119.940 | 13:10:59.893 | 62 | 0 |
| 0001 | 120.120 | 13:13:00.095 | 51 | +62 |
| 0002 | 120.960 | 13:15:01.409 | 127 | +26 |
| 0003 | 120.480 | 13:17:01.567 | 64 | +45 |
| 0004 | 120.000 | 13:19:01.569 | 77 | −14 |
| 0005 | 120.000 | 13:21:01.548 | 55 | +9 |
| 0006 | 120.000 | 13:23:01.563 | 56 | +9 |
| 0007 | 120.000 | 13:25:01.595 | 73 | +13 |
| 0008 | 120.000 | 13:27:01.601 | 78 | +6 |
| 0009 | 120.000 | 13:29:01.555 | 70 | −1 |
| 0010 | 108.000 | 13:30:49.561 | 59 | N/A |

File timestamps are filesystem write times, not physical audio boundaries. The last column is `(next recorder_started client timestamp) − (current recorder_started client timestamp + decoded duration)`. It is a timing proxy, not a measured acoustic gap or overlap.

## Boundary continuity: evidence and limitations

All expected indices, emission events, upload starts, and acknowledgments occur once and in sequence. No missing whole interval or exact duplicate file was found. Normal chunk durations range from 119.940 to 120.960 seconds; that variability is consistent with scheduling rather than a missing two-minute chunk.

Across ten rollover boundaries, the next recorder-start callback follows the previous chunk-emitted callback by **53–79 ms**. Aligning decoded lengths to recorder-start callbacks gives residuals of **−14 to +62 ms**, totaling +155 ms across those boundaries. The complete decoded length is only **103 ms shorter** than the first-start-to-final-emission callback span, and 500 ms shorter than the coarse integer-second status duration. No unusually large gap or overlap is apparent in this timing evidence.

These residuals do not measure actual lost or repeated sound. Callback scheduling, codec padding, conversion, and clock granularity can affect them. WAV chunks contain no shared source-time reference. A missing interval could be offset by duplication elsewhere. Without a known reference source, sample-accurate continuity, absence of short dropouts, and absence of partial duplicated passages remain **unverified**.

## Uploads, interruptions, and logs

`capture_events.jsonl` contains 77 events: 11 recorder starts, 11 emissions, 11 upload starts, 11 acknowledgments, 14 screen-wake-lock acquisitions, 14 losses, and one each of keep-awake acquisition/release, Stop request, and finalization start.

No upload-failed, retry, capture-interrupted, or recovery event was recorded. Status has `interrupted=false`, `hadInterruption=false`, `partialCapture=false`, `recoveryRequired=false`, an empty gap list, and no transcription failures. Eleven recorder starts correspond to eleven normal chunk cycles, not eleven unexpected recovery attempts.

Screen wake lock was repeatedly lost/reacquired. These events did not coincide with a recorded capture interruption; their cause is not established. The final release occurred during Stop.

Both launcher logs, `.pycache_tmp/server-logs/20261009-130544-296.out.log` and `.err.log`, are **empty (0 bytes)**. They provide no usable HTTP-access, retry, or exception history. Therefore this review cannot certify that every transient failure or hidden retry was logged. The event stream and saved-file evidence support successful completion, but empty server logs are an observability limitation, not affirmative proof of no exceptions.

## Stop, finalization, and browser queue

All expected audio is server-side: uploaded indices 0–10, `missingChunks=[]`, `unexpectedChunks=[]`, `failedChunks=[]`. Final chunk acknowledgment was at 13:30:49.595.

Full processing finalization **did not complete**: status is `waiting_for_transcription`; `capture.state` remains `finalizing`, while `sessionOpen=false`. All 11 `pendingChunks` are pending **transcription**, not missing uploads. The client intentionally labels this deferred state `SAVED — TRANSCRIBE LATER`. It is compatible with the user's instruction not to transcribe; no processing was initiated to force completion.

Browser queue emptiness is **unverified**. Available browser tooling exposed only the in-app Help tab and no recording tab/profile. No new recorder page was opened, no browser storage was read from disk, and no reload or retry was triggered. Eleven acknowledgments prove receipt of the expected files, but cannot prove deletion of every IndexedDB checkpoint, pending item, or finalization record.

## Transcription / AI audit

All 11 chunks have `transcriptionStatus=queued` and `durableTranscriptionJob=false`; `transcribedChunks=[]`. There are no transcript, summary, notes-output, or model-response files in this session directory. The upload code uses `queued` for deferred transcription, skips provider enqueue in that mode, and startup recovery requires an explicitly pending durable job.

One Player Companion request was logged at **13:16:18**: `request_received`, followed by `insufficient_evidence`, outcome `insufficient`, `cached=false`. Code inspection confirms this branch returns before provider dispatch with zero new spend. Thus an AI-related UI request was attempted, but the records show it was rejected before an AI provider call.

**Local application evidence supports no transcription or AI-provider processing for this session.** No such processing was triggered by this verification. There is no network capture or provider-side billing audit, so this is not an independent network-level guarantee. The absence of transcription here must not be generalized to the separate later live-transcription sessions.

## Verdict and next test

**PASS:** expected saved audio inventory, complete PCM decoding, sequential chunk bookkeeping, and no large timing anomaly observed.

**INCONCLUSIVE overall:** reference-based continuity, browser queue drainage, and complete server exception/retry coverage remain unverified. Full processing is intentionally waiting for transcription; it was not marked successful or forced to finish. This recording alone does not establish game-night dependability or explain the separate live-transcription failure.

Next, after identifying the newer failure precisely, use an isolated session with a nonrepeating time-coded synthetic source for at least 30 minutes in deferred mode, retaining browser console and HTTP logs. Verify reference alignment across every rollover and final Stop, then run a separate controlled local-server outage/recovery case. Only after those pass should a separate live-transcription test with explicitly approved nonprivate audio exercise capture under processing load. None of these tests was started during this review.

## Earlier preflight (retained history)

The earlier automated 20-minute assignment stopped before recording because its requested separate browser profile could not be established. That attempt recorded 0 seconds and was INCONCLUSIVE, not an application failure. Its evidence remains in `.pycache_tmp/recording-20-min-preflight-1a0444ff/`. The real user recording assessed above is a separate run and supersedes that preflight-only result for saved-file verification.
