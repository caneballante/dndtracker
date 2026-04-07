# Campaign Refactor Plan

## Goal

Introduce a `Campaign` layer above sessions so long-lived context stays organized, concurrent campaigns remain separate, and live session tools stop carrying too much responsibility.

This plan also defines a cleaner UX boundary:

- `Campaign` owns reusable context and memory.
- `Session` owns live capture and outputs.
- `Session Context Snapshot` is the compiled prompt context used during a session.

## Why This Refactor

The current app grew around a session-first model:

- prep context is stored per session
- party roster is effectively session data
- campaign continuity is manual and scattered
- test tools can vary prompt inputs without a clear source-of-truth context model

That makes the product harder to reason about, especially once multiple campaigns are active at the same time.

## Target Model

### Campaign

A campaign is the persistent container for reusable context.

Suggested campaign fields:

- `campaignId`
- `name`
- `party`
- `campaignSummary`
- `dungeonMakerJson`
- `sessionSummaries`
- `updatedAt`

Suggested semantics:

- `party`: editable roster used for name normalization and tracker initialization
- `campaignSummary`: editable running memory for the campaign
- `dungeonMakerJson`: latest relevant export from Dungeon Maker; overwrite is acceptable
- `sessionSummaries`: ordered text entries, manually appended at first

### Session

A session remains the container for live and generated artifacts.

Suggested session fields:

- `sessionId`
- `campaignId`
- `sessionName`
- `contextSnapshot`
- existing transcript / notes / tracker / output fields

Suggested semantics:

- session owns the audio, transcript, notes, tracker state, summary, and narrative
- session points to exactly one campaign
- session keeps the compiled context it used, so live behavior is explainable and reproducible

### Session Context Snapshot

This is the important new middle layer.

It should be generated from campaign data and then stored with the session.

Suggested snapshot fields:

- `campaignId`
- `campaignName`
- `partyText`
- `campaignSummaryText`
- `recentSessionSummariesText`
- `prepContext`
- `prepContextText`
- `createdAt`
- `sourceRevision`

Suggested semantics:

- snapshot is the default prompt context during live play
- snapshot does not change silently mid-session
- snapshot can be refreshed explicitly if the DM wants updated context

## Prompt Strategy

### What Should Be Stored

Store broadly:

- full editable campaign summary
- all session summaries
- latest relevant Dungeon Maker export
- full party roster

### What Should Be Sent To The Model

Send selectively:

- party roster
- short campaign summary
- recent relevant session summaries
- relevant prep context for the current adventure area
- tracker state and recent tracker events
- rolling transcript window during live notes

Do not blindly send the entire campaign history every time. More context helps only if it is still relevant and compact enough to stay high-signal.

### Recommended Live Notes Context

For live notes generation, use:

- locked session context snapshot
- rolling transcript window
- tracker state
- tracker recent events
- rolling summary so far

### Recommended Rebuild Context

For post-session rebuilds, use a richer context:

- session transcript / clean transcript
- session notes and rolling summary
- tracker state and events
- locked snapshot by default
- optional explicit rebuild with current campaign context

## Dungeon Maker Workflow

The proposed workflow is intentionally simple:

1. In Dungeon Maker, export only the rooms/content relevant to the current session or near-term play.
2. Import that JSON at the campaign level.
3. Overwrite the prior campaign dungeon JSON.
4. Refresh the session context snapshot only when desired.

This is preferable to early merge logic because it keeps the mental model simple and avoids stale prep accumulating in prompts.

## Session Summary Workflow

Short-term:

- after each session, manually review the generated game summary
- manually append or edit a campaign-level session summary entry

Later:

- optionally provide a one-click "Append to campaign memory" action
- optionally derive a condensed campaign summary from recent session summaries

Manual append first is the right choice because it keeps the human in the loop and avoids locking in bad summaries automatically.

## UX Boundaries

### Campaign Tab

This should become the home for durable prep and continuity.

Recommended contents:

- campaign selector
- campaign name
- party roster editor
- campaign summary editor
- Dungeon Maker JSON import / replace
- session summaries viewer/editor
- compiled context preview

### Sessions Tab

This should remain focused on session artifacts and live observation.

Recommended contents:

- session selector
- session name
- transcript view
- notes view
- notes state view
- game summary
- game narrative
- rebuild actions
- small context status line such as "Using snapshot from Campaign X"

Sessions should not be the main place where campaign data is edited.

### Test Tab

The test tools are still valuable, but they need clearer boundaries.

Recommended rule:

- tests can vary parameters
- tests should not silently vary context

Recommended controls:

- session selector
- notes window size
- chunk range
- use locked session context
- optionally "use current campaign context" as an explicit alternative
- prompt override for advanced testing

Recommended removals or demotions:

- freeform editing of party/context as the default workflow

Reason:

- the more axes a test changes at once, the less useful the result becomes
- for live trust, we want reproducible runs against a known context snapshot

## First Implementation Phase

### Data Model

Add campaign storage with simple local JSON files:

- `campaigns/<campaignId>/campaign.json`

Initial campaign contents:

- metadata
- party
- campaign summary
- dungeon prep JSON
- session summaries

Update sessions so they include:

- `campaignId`
- `contextSnapshot`

### API

Add campaign endpoints:

- list campaigns
- get campaign
- save campaign
- import campaign Dungeon Maker JSON
- append/update campaign session summary
- build session snapshot from campaign

### UI

Minimal first pass:

- rename `Prep` to `Campaign`
- move party editor there
- add campaign summary text area
- add session summaries pane
- keep Sessions tab mostly intact
- add simple campaign selector and session-to-campaign assignment

### Behavior

- starting a session requires or defaults a campaign
- starting or refreshing a session creates a context snapshot
- live note generation uses the snapshot, not raw mutable campaign state

## Second Phase: Usability And Polish

The app now has enough depth that usability needs to become a dedicated phase, not incidental cleanup.

### Main UX Problems Today

- features were added where space existed, not where users expect them
- tab responsibilities overlap
- context source is not visible enough
- advanced tools mix with live-use tools
- session targeting can be ambiguous

### Polish Goals

- make the information architecture obvious
- reduce fear of editing the wrong session or campaign
- improve scanability during live play
- separate everyday workflow from advanced debugging

### Suggested UI Direction

1. Clarify the top-level navigation:
- Recording
- Live Notes
- Tracker
- Campaign
- Sessions
- Test

2. Add persistent identity/status chips near the top:
- active session
- active campaign
- context mode: locked snapshot vs refreshed

3. Make live tabs calmer:
- bigger, cleaner read panes
- clearer status text
- fewer competing controls

4. Move advanced controls behind lower-emphasis areas:
- test harness
- prompt override
- chunk window experimentation

5. Improve terminology:
- prefer `Campaign` over `Prep` for durable context
- prefer `Session Context Snapshot` over hidden implicit behavior

## Recommendation

Build the campaign layer first, but keep the first iteration narrow:

- campaign storage
- session-to-campaign link
- locked session snapshot
- campaign summary + party + Dungeon Maker JSON + session summaries

Then do a dedicated usability phase after the architecture is stable. That will make the UI cleanup much more coherent, because the underlying concepts will finally be clean.
