# TODO

## Current Priorities

1. Add a previous-session summary field in Prep and include it in notes/summary generation context.
2. Add explicit session selector inside Prep and Tracker to avoid target ambiguity.
3. Run notes-window A/B checks (`N=2` vs `N=12`) and lock production default.
4. Improve prep context import/patch workflow for partial per-session updates.
5. Harden Tracker UX for fast combat updates (optional batch apply mode).

## Operational Checklist (Per Session)

1. Verify `.env` has `OPENAI_API_KEY`.
2. Start server and confirm `http://127.0.0.1:8000/` loads.
3. Confirm active session target before using Prep/Tracker.
4. Record session chunks.
5. After session: rebuild notes/summary with larger window.
6. Generate final summary and narrative.
7. Spot-check tracker consistency versus notes.

## Risks To Watch

1. Running the wrong server (`python -m http.server`) causes failed API calls.
2. Port 8000 conflicts can make startup seem broken.
3. Missing `.env` or invalid key blocks transcription/summaries.
4. Mixed session targeting in Prep/Tracker can pollute data.
