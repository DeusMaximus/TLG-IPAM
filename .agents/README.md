# .agents/ — process material for coding agents

What agents, and the people working with them, need *between* sessions. It lives here rather than in the always-loaded `AGENTS.md` so it costs context only when read.

- `review-brief.md` — the PR-body shape and the fill-in template for briefing an external reviewer. Read it when opening a PR or asking for a review round.
- `handoff/YYYY-MM.md` — archived `HANDOFF.md` entries, verbatim, newest first, one file per month. `HANDOFF.md` in the repo root keeps the five most recent; its header has the rotation rule and the grep recipe. Grep an archive; do not read one whole.

Scratch that should not be committed (filled-in review briefs, reviews, spike working files) goes in `.dev/<issue>/`, which is gitignored.

## Add these when first needed

Each starts the day it has real content, written from what happened in this repo.

| File | Start it when | Holds |
|------|---------------|-------|
| `lessons.md` | a defect or review finding teaches something a rule should remember | case histories behind the rules in `AGENTS.md`: append-only, stable headings, linked from the rule |
| `testing-and-review.md` | test or review procedure outgrows the Workflow section of `AGENTS.md` | procedure, edited in place |
| `spikes/<issue>/` | an issue calls for a spike (the M3 client-compatibility spike is the first) | the harness and raw output a later session can re-run; the issue comment is the record |
| `releases.md` | M2b, when a release means publishing the server image, the agent image, and the agent wheel | the release runbook |
