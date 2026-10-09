# 20-minute recording reliability test — October 9, 2026

**Conclusion: INCONCLUSIVE — preflight stopped; recording was not started.**

## Environment and candidate revision

- Candidate baseline: `177831f8fb91014d343e157e484475b94a31fed1`, plus the uncommitted October 9 stabilization changes. Testing HEAD alone would not test the current stabilized application.
- OS reported by Windows: `Microsoft Windows NT 10.0.26200.0`.
- Available browser automation: Codex In-app Browser (`iab`, ID 2). Browser version was not measured. The other exposed surface is Codex MCP Apps, not a general recording browser.
- Audio source: none started. Neither synthetic browser capture nor hardware microphone capture was exercised.
- Actual recording duration: **0 seconds**.
- Intended mode: Offline recording — transcribe later, two-minute chunks. No application settings were changed.

Read the October 8 repair report, October 9 stabilization report, soak-test procedure, and repository guidance before preflight. Existing application changes, campaign changes, and untracked files were preserved.

## Blocking condition

The request explicitly requires an isolated workspace **and a separate browser profile**, and directs stopping if safe isolation cannot be established.

The available browser inventory and documented capabilities expose the existing in-app browser and only visibility/viewport controls. They do not expose profile creation, a fresh browser context, or evidence that this browser is a separate test profile. No connected Chrome/Edge browser was available. Native computer control is disabled. Repository guidance also says the browser CLI runtime is unavailable and must not be installed for verification.

This does **not** demonstrate a MediaRecorder or application failure. Earlier short synthetic-browser tests indicate that real browser recording can be exercised. The blocker is establishing the requested profile isolation for this run. A fresh localhost port provides origin-specific localStorage/IndexedDB separation, but that is not equivalent to a separate browser profile and was not silently substituted.

No server was started/stopped, no recording page was opened or reloaded, no microphone was accessed, and port 8000 was untouched. No credentials were accessed and no transcription/AI requests were made.

## Results

| Measurement | Result |
|---|---|
| Completed audio chunks / checkpoints | 0 / 0; test not started |
| Decoded duration | Not measured; no test audio exists |
| Reference order, gaps, overlaps, duplicates, analysis resolution | Unverified |
| Per-chunk index, duration, receipt and continuity | Not applicable; no completed chunks |
| Upload failures or retries | Not exercised |
| Queue drain / finalization | Not exercised |
| Recorder restarts / empty-file loop | Not exercised |
| Browser JS / Python server / Windows file-access / IndexedDB errors | Runtime behavior unverified; no test runtime started |
| Memory and disk activity | Runtime behavior unverified |
| External provider spending from this test | None; no provider requests made |

There is no end-to-end pass and no observed application failure to repair. Previous unit tests and backlog simulations are not substitutes for this requested run. Readiness for the longer fault-injection test remains **unverified**.

## Evidence retained

Preflight evidence is in `.pycache_tmp/recording-20-min-preflight-1a0444ff/`: baseline revision, working-tree listing, scoped stabilization patch, SHA-256 hashes of the main recording code, and browser/OS capability notes. No real recordings or campaign contents were copied there. The only new non-ignored file from this verification attempt is this report. No application fixes, commit, or push were made.

## Smallest practical alternative

Accept **origin isolation in the existing in-app browser** for this synthetic test: copy only application code/assets into a persistent isolated test workspace, use a fresh localhost port and synthetic session data, block provider calls, and retain all evidence. This would exercise the actual MediaRecorder, timed checkpoints, two-minute rollover, uploads, and normal Stop/finalization for at least twenty minutes. It would not claim separate-profile or hardware-microphone coverage.

If separate-profile isolation remains mandatory, provide a supported browser connection to a dedicated test profile before starting. No extra runtime installation is proposed. Once either prerequisite is resolved, use a nonrepeating time-coded source and inspect every saved chunk and rollover boundary; a constant tone and total duration alone cannot establish continuity.
