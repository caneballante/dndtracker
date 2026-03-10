# Product Intent: DnD Session Co-Pilot

## Why This Exists
Running a great DnD session requires juggling storytelling, rulings, pacing, combat state, and memory across weeks. This tool exists to reduce DM cognitive load during play and improve continuity between sessions.

## Core Outcome
Give the DM a reliable “second brain” that captures what happened, organizes it into usable outputs, and reduces prep overhead for the next session.

## Primary User
- Solo DM running live table sessions (primary)
- Possible future: trusted friends using their own API keys

## Problems To Solve
1. Important events are forgotten or misremembered after a long session.
2. Manual note-taking during play disrupts flow and attention.
3. Between-session continuity is hard (NPCs, hooks, loot, consequences).
4. Combat tracking and attribution from transcript alone can be inaccurate.
5. Setup friction before session (tools, context, prompts) creates stress.

## Product Principles
1. **Table speed first**: no workflows that interrupt gameplay.
2. **Accuracy over flash**: post-session correctness matters more than live novelty.
3. **Structured outputs**: notes must be parseable and actionable.
4. **Local-first control**: simple, private, and robust setup.
5. **Human-in-the-loop truth**: explicit DM/tracker inputs outrank model guesses.

## Intended User Experience
### Before Session
1. Start new session in one click.
2. Load party roster.
3. Load prep context (rooms/monsters/NPCs) for that session only.

### During Session
1. App records in chunks automatically.
2. Transcript and DM notes update in background.
3. Tracker tab captures authoritative HP/damage/notes quickly.
4. DM can ignore most UI unless needed.

### After Session
1. Rebuild notes with larger context window for quality.
2. Generate high-quality game summary for next-session prep.
3. Optionally generate narrative recap.
4. Save outputs as campaign memory.

## What “Success” Looks Like
1. DM can run a full session without wrestling with tooling.
2. End-of-session summary is good enough to prep from directly.
3. Notes preserve chronological events and key state changes.
4. Continuity quality improves session-over-session.
5. Confidence increases that “what happened” is captured accurately.

## Scope (Current)
1. Local web UI + Python backend.
2. Audio chunk upload/transcription.
3. Cleaned transcript generation.
4. Structured DM notes (`timeline`, `state`, `summary`).
5. Session browser with rebuild + summary + narrative.
6. Tracker tab for authoritative combat updates.
7. Prep tab for session-scoped JSON context import/edit.

## Known Tradeoffs
1. Live attribution can be imperfect from transcript-only speech.
2. Final quality depends on context window tuning and post-session rebuild.
3. More context improves quality but adds latency/cost.
4. Without campaign linking, prior-session continuity is still partly manual.

## Near-Term Product Direction
1. Add dedicated `previous session summary` field in Prep.
2. Make prep/tracker session targeting explicit and unambiguous.
3. Continue prompt/window testing for best accuracy-to-latency balance.
4. Keep tracker as truth source for combat state.
5. Preserve low-friction startup for game-night reliability.

## Non-Goals (For Now)
1. Full VTT replacement.
2. Fully autonomous campaign memory without DM review.
3. Multi-user cloud SaaS workflow.
4. Complex automation that increases table friction.

## Product Promise
This tool should make the DM calmer at the table, not busier: capture more, miss less, and make next-session prep dramatically easier.
