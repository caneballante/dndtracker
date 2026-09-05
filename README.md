# DnD Tracker (Local)

Local web app for recording DnD sessions in audio chunks, transcribing with OpenAI, generating DM notes, and producing end-of-session summary/narrative.

## Quick Start (Windows)

1. Ensure `.env` exists in this folder and contains at least:
   - `OPENAI_API_KEY=...`
2. Start server:
   - `start-server.ps1`
   - or `start-server.cmd`
3. Open:
   - `http://127.0.0.1:8000/`
4. Stop server:
   - `stop-server.ps1`
   - or `stop-server.cmd`

## Quick Start (macOS)

1. Ensure `.env` exists in this folder and contains at least:
   - `OPENAI_API_KEY=...`
2. Start server:
   - `./start-server.command`
3. Open:
   - `http://127.0.0.1:8000/`
4. Stop server:
   - `./stop-server.command`

## Required Environment

- `OPENAI_API_KEY` (required)

Optional:
- `NOTES_WINDOW_CHUNKS`
- `NOTES_MODEL`
- `SUMMARY_MODEL`
- `CLEAN_TRANSCRIPT_MODEL`
- `NARRATIVE_MODEL`
- `ENABLE_STRUCTURED_RECONCILIATION` (default off)
- `STRUCTURED_RECONCILIATION_MODEL` (default `gpt-5.6-sol`)
- `STRUCTURED_RECONCILIATION_REASONING_EFFORT` (default `high`)
- `STRUCTURED_RECONCILIATION_MAX_INPUT_TOKENS` (default/max `250000`)
- `STRUCTURED_RECONCILIATION_MAX_OUTPUT_TOKENS` (default `32000`)
- `ENABLE_RECONCILIATION_REFERENCE_RETRIEVAL` (default off)
- `RECONCILIATION_REFERENCE_MAX_SEARCHES` (default and maximum `5`)
- `RECONCILIATION_REFERENCE_RESULTS_PER_SEARCH` (default/maximum `5`)
- `ARENTORIA_DB_PATH` (optional; otherwise the sibling Arentoria database is discovered)
- `DND_UPLOAD_TOKEN` (only needed if deploying the legacy PHP uploader)

Defaults are in `server.py`.

## Common Problems

1. `501 Unsupported method ('POST')`
   - Cause: started `python -m http.server` instead of `server.py`.
   - Fix: run `start-server.ps1`/`start-server.command` or `python server.py`.

2. Port 8000 already in use
   - Fix: run `stop-server.ps1`/`stop-server.command`, then restart.

3. Early 404 for transcript/notes
   - Expected before first chunk finishes processing.

4. `favicon.ico` 404
   - Harmless.

## Session Data Layout

Per session folder: `uploads/<sessionId>/`

- `status.json`
- `transcript.txt`
- `clean_transcript.txt`
- `transcripts.jsonl`
- `notes.txt`
- `notes.jsonl`
- `notes_summary.txt`
- `game_summary.txt`
- `game_narrative.txt`
- `tracking_events.jsonl`
- `ai_usage.jsonl` (request/token/cost observations by AI stage)
- `session_event_operations.jsonl` (append-only structured event history)
- `session_events.json` (current structured event state)
- `session_highlight_operations.jsonl` (append-only session highlight/review history)
- `session_highlights.json` (current session highlight state)
- `reconciliation_reference_searches.jsonl` (compact reference-query audit; created only when used)
- `reconciliation_model_responses.jsonl` (append-only raw response and tool-call disposition audit)
- `prep_context.json`
- `party.txt`

## AI Usage And Pricing

Successful AI responses are recorded locally in each session's `ai_usage.jsonl`.
Token usage is saved when the provider exposes it. `GET /api/sessions/get` returns
an `aiUsage` aggregate grouped by stage and model, and transcription duration is
retained when available even without token usage.

Pricing estimates are calculated from `ai_pricing.json`. Unknown models or missing
usage produce `estimatedCost: null` plus an `unestimatedRequests` count; they are not
silently treated as free. Update the separate pricing file when provider rates change.

## Legacy DM Notes vs Session Memory

Legacy DM Notes are the historical per-chunk `gpt-4o-mini` pipeline stored in
`notes.jsonl` (plus its text/override files). They remain available under **Legacy tools**
for backward compatibility with existing Notes, recaps, handoff, and DungeonShare flows.

Session Memory is the separate whole-session `gpt-5.6-sol` reconciliation path. It writes
durable Events to `session_events.json` and Highlights to `session_highlights.json`, with
World-aware bounded reference retrieval when enabled. The session History UI loads these
stores directly and uses an explicitly confirmed **Build/Rebuild Session Memory** action;
it does not convert them into legacy Notes or regenerate a published recap.

## Structured Session Events

Structured events coexist with `notes.jsonl` and are changed only through the explicit
`GET/POST /api/session/events` path. Each event has a UUID-based durable identity and
retains its transcript chunk evidence. The JSONL operation history is authoritative;
the current JSON state can be rebuilt from it after an interrupted write. New events carry
a future-review status, and the operation API supports edit/importance changes plus explicit
keep/reject decisions without changing older stored records; a missing review status on an older
event is treated as pending review by future consumers.

## Session Highlights / Table Memory

Session highlights coexist with durable events and legacy notes through the separate
`GET/POST /api/session/highlights` path. A highlight records a selective memorable table
moment—not a world-state change—with a UUID-based durable ID, one or more categories,
confidence, transcript chunk provenance, participants, optional related event IDs, and a
future-review status (`pending`, `kept`, or `rejected`). Edits and keep/reject decisions are
append-only operations; the materialized highlight state can be rebuilt from its history.

Humor is selected only when transcript evidence indicates a genuinely memorable table beat,
such as sustained/repeated reaction or a later callback. Combat is recorded as combat memory:
distinctive, clutch, creative, costly, dramatic, or situation-changing contributions rather
than an attack-by-attack log. A moment can be both a durable event and a highlight, either one,
or neither.

## Ordered Evidence And Finalization

`GET /api/session/evidence` reads `transcripts.jsonl` into unique entries sorted by
`chunkIndex`; reconciliation code should use this API/helper rather than JSONL append
order. `POST /api/session/finalize` records the browser's final expected chunk and
maintains an idempotent barrier in `status.json`. The barrier moves through upload and
transcription waits and reaches `ready_for_reconciliation` only when every required
chunk is uploaded and successfully transcribed. A stable finalization ID plus an atomic
reconciliation claim prevents duplicate runs. The recording Stop flow waits for this barrier,
shows the non-billable cost preview, and requires explicit confirmation before it invokes the
reconciliation model.

## Session Memory Reconciliation

Set `ENABLE_STRUCTURED_RECONCILIATION=1`, restart the server, and inspect
`GET /api/session/reconciliation/status?sessionId=<id>`. A non-billable size/cost preview is
available through `POST /api/session/reconcile` with `{"sessionId":"<id>","dryRun":true}`.
Only an explicit POST with `{"sessionId":"<id>","confirm":true}` may start the
post-session Responses operation, and only after the finalization barrier is ready. With reference
retrieval enabled, the operation may use a bounded tool loop; every model response is counted separately.
An explicit historical rebuild also sends `"rebuild":true`; it creates a new audited finalization
attempt while preserving transcript/audio, legacy Notes, recap/handoff, and campaign data.

The request uses three explicit context classes:

- `sessionEvidence`: ordered transcript, tracker evidence, optional reviewer corrections, and the future
  `CANON`/`MARK` marker hook. Only this class may establish what happened in the current session.
- `orientationContext`: compact campaign/adventure identity, current roster, capped adventure-so-far
  background, and a small curated name list. It cannot prove current-session events.
- `referenceCanon`: authoritative background records with `dm_only`, `player_known`, or `unknown`
  visibility. No reference records are included automatically; identity records can be requested on demand.

Dry-run diagnostics report approximate bytes/tokens for transcript evidence, tracker data, human-review evidence,
orientation, structured events, up-front reference canon, retrieval metadata, and schema/instruction
overhead. Packet hashes make the context used by a committed batch traceable without duplicating the
campaign corpus into event history.

A per-session `reconciliation_benchmark.json` with `{"mode":"clean"}` forces both dry-run and
confirmed reconciliation through a clean benchmark packet: reviewer evidence is not read, markers and
up-front reference canon are empty, and a nonempty structured event store is rejected. The stored policy
cannot be silently overridden by omitting `benchmarkMode` from the confirmed request. Existing clean
benchmarks with `excludeReferenceCanon: true` remain tool-free; a new controlled clone must explicitly set
`enableReferenceRetrieval: true` and `excludeReferenceCanon: false` as well as enabling the server flag.

`reconciliation_context.py` provides the provider-neutral `search_campaign_reference(...)` abstraction.
For reconciliation, the server resolves `campaign.worldId`, reads that World's manifest, and adds only
the manifest-approved Master Canon and manifest-current curated Adventure References. Candidates, source
DOCX files, review/history records, and raw adventure exports are provenance rather than runtime search
sources. Deterministic authority ranking is `world_canon > structured_city > current_adventure > legacy`;
legacy providers remain opt-in. Player-safe mode removes DM-only entities/facts, while DM mode can expose
explicitly approved `dmOnlyNotes`. Existing local campaign/Dungeon Maker views and the read-only Arentoria
SQLite adapter remain available without changing their canonical storage. When enabled, the Responses
tool loop permits at most five searches and five compact results per search. Its JSONL audit
records campaign/World resolution, provider counts and authority classes, selected identities, suppressed
lower-authority conflicts, visibility mode, legacy policy, and provider errors without copying canon text.

Strict reconciliation output now contains separately validated durable event operations and
session highlight operations. Each stream is committed to its own append-only batch/history;
the event batch retains the validated highlight operation payload until the sibling commit so an
interrupted process can finish locally without another model request. Failures preserve evidence/current
state and enter a retryable `reconciliation_error` state. The existing
live notes, recap, narrative, campaign handoff, and DungeonShare paths are unchanged.

Reconciliation granularity follows durable decisions and state changes rather than transcript-note
count. The prompt preserves explicit decisions, relationship developments, consequential scene actions,
and actionable names; later clear transcript corrections supersede earlier mistakes. Before emitting
operations, the model is instructed to perform a silent whole-session coverage check and to lower
confidence when material evidence conflicts.

A future recap should consume reviewed/approved durable events and reviewed/approved highlights as
separate inputs, using transcript evidence for verification and orientation/reference material only
for interpretation. Event review can keep/edit/reject and adjust importance; highlight review can
keep/edit/reject. The current UI is read-only for these stores; no recap generation or review editing
is attached to Session Memory yet.

## Docs

- Migration and architecture context: `MIGRATION_CONTEXT.md`
- Active priorities and next tasks: `TODO.md`
