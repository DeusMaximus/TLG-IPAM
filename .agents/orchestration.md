# Orchestrating v0.2

Read this when you are the **orchestrator**: the session that hands issues to worker sessions, checks what comes back, and keeps `HANDOFF.md` current. A worker needs only `AGENTS.md`, `HANDOFF.md`, and its issue.

The orchestrator writes briefs and checks results. Application code is written by workers.

## The loop

Run these in order for each issue. Each step ends when its last line is true.

1. **Pick.** Read the newest `HANDOFF.md` entry and the open issues in the earliest unfinished milestone. An issue is **ready** when every issue in its "Depends on" line is closed and it does not carry the `owner` label. Done when you can name the ready issues.
2. **Decide what runs together.** Up to three ready issues at once, and only when their Scope sections name different code files. Two issues that both edit `models.py`, `main.py`, or `mcp_server.py` run one after the other. Two issues may both edit the brief, the README, or `AGENTS.md` when they touch different sections: tell each worker which sections the other owns, and the PR that merges second rebases. Done when each issue you will start has no code file in common with another in flight, and no docs section.
3. **Choose the model** from the roster below. Done when each issue has a seat.
4. **Launch.** One standalone thread per issue, in its own worktree cut from `origin/main`, on branch `<issue>-<slug>`, with the brief below. Done when the thread reports a run in progress.
5. **Wait** for the worker's final message. The worker answers its own review findings; the PR is its work until it hands back. Nothing wakes you when a worker thread finishes, so watch its PR once it opens: you are woken when checks or reviews change. A wake is not a hand-back. Read the worker's last message each time, and use the wait to read the diff. Done when the final message has the PR URL, head sha, review states, and a hand-off entry.
6. **Verify** with the checklist below. Done when every line is ticked or reported as failed.
7. **Report to the owner:** ready or not, which claims you re-measured and which are the worker's alone, and any follow-up worth an issue. Done when the owner has what they need to decide the merge.
8. **Close out** once the owner merges or tells you to: squash-merge, delete the remote branch, and commit the hand-off entry (rule below). Done when `main` has the entry and the issue is closed.

## Roster

The owner's working experience, as of 2026-11-13. Edit in place when a seat changes or a model surprises you in either direction.

| Seat | Model | Use for |
|------|-------|---------|
| Orchestrator | Claude Fable 5.2, high | This loop, and questions about the shape of the work |
| Implementer | Claude Opus 5.5, high | The default for any issue with logic in it: services, auth, ingest, the agent package, UI with state. On #6, #7, #8, #36 and #45 its counts and mutation results matched every re-measurement |
| Implementer, mechanical | Claude Sonnet 5.5, high | Docs, configuration, dependencies, test plumbing. Proven here on #3, #4 and #35 (dependencies, docs, test plumbing); give it one UI issue and record the result in this table before relying on it for more |
| Briefed reviewer | GPT-6.1 Sol through Codex, high | Every briefed round. It is strict and finds what the implementer missed. Quota is ample: when unsure whether a PR owes a briefed round, run one. Three rounds so far (#44, #48, #49): each GO, each with P3s the automatic reviews had not raised |
| Light second read | GLM 5.3, or Mistral Large 4 | Optional, for a PR that owes no briefed round but where you want one more reader. Mistral Large 4 is nearly as good and much cheaper on its plan |
| Manual fallback | GPT-6 with Daybreak Blue, in the Codex app | Work a model here has refused. The owner runs it by hand; no session here can start it. See "When a model refuses" |

- Implementers are Claude models and the reviewers are not, so every PR gets a read from outside the author's model family. Keep that true when a seat changes.
- Move an issue up to Fable 5.2 as implementer when it has failed two review rounds, or when the work is deciding what to build (a spike's write-up, a spec change).

## The worker brief

A worker starts with no context from you. The brief carries what `AGENTS.md`, `HANDOFF.md`, and the issue do not. Keep it to these parts, in this order:

```markdown
Implement GitHub issue #‹N› in this repository: "‹title›". Read the issue first (`gh issue view ‹N›`); its Scope and Acceptance lists are the definition of done.

Before you start, read `AGENTS.md` and the newest entry in `HANDOFF.md`. The spec sections the issue cites are in `docs/v0.2-spec.md` ‹§›.

What is already known, so you do not have to rediscover it:
- You are in a fresh git worktree cut from `origin/main` (`‹sha›`), on branch `‹N›-‹slug›`. Create the virtual environment and `node_modules` you need. Use Python 3.13 for the venv, as CI does.
- ‹Each other issue in flight, and the files or document sections it owns. "No other issue is in flight" when that is so.›
- ‹Facts this session has measured that bear on the issue: versions, a failing command, a decision taken since the issue was written.›

Scope: only what issue #‹N› lists. ‹Name the neighbouring issue that owns the nearest out-of-scope work.›

Rules beyond `AGENTS.md`:
- The repository is public. Commits and the PR carry no person's name, real domain, local path containing a username, or reference to the owner's other projects.
- Open one PR that closes #‹N›. It owes ‹the automatic reviews only / the automatic reviews and a briefed round, which the orchestrator will start and brief: do not write a review brief›. Wait for Codex and Greptile, then fix each finding or reply on its thread with the reason it stands.
- Do not finish until both reviews have posted. If you cannot subscribe to the PR, wait on its checks from the shell until the `Greptile Review` check completes, then read its comments. Greptile usually takes three to five minutes.
- The automatic reviews do not run again after a fix commit. If you push after they have reviewed, comment `@codex review` on the PR and say in your final message that Greptile needs a manual retrigger.
- The owner merges.
- End your final message with your hand-off entry, written to the template in `HANDOFF.md`.
- If a safety filter or a refusal stops you on a step, stop there. Push what you have, and report the step, the message you got word for word, and what is left on the acceptance list.

Finish with: the PR URL, its head sha, the state of CI and of each automatic review, each acceptance line you could not verify and why, then the hand-off entry.
```

Give facts, not instructions on how to do the work. A brief that needs more than the "already known" list to be usable means the issue is underspecified: fix the issue.

When an acceptance line can only be shown by running something (a restore, a CI failure, a route that does not exist yet), say so in the "already known" list and ask for the commands and output in the PR body. The workers on #4, #36 and #8 each met such a line.

## Verifying a result

A worker's report is a claim. Before telling the owner a PR is ready:

- [ ] CI is green at the head sha the worker named, and that sha is still the head.
- [ ] Codex and Greptile have both finished, and every finding has a fix or a reply on its thread.
- [ ] Both reviews cover the current head. Neither re-runs after a fix commit: comment `@codex review` on the PR, and ask the owner to retrigger Greptile. The Codex summary comment names the commit it reviewed.
- [ ] Each acceptance line in the issue maps to evidence in the PR body.
- [ ] **One claim CI does not cover is re-measured by you**: a Docker build, a count, a command from the documentation run from scratch. Pick the claim the acceptance list leans on most.
- [ ] When another PR has merged since the branch was cut, the suite is run on the PR merged with `main`. The count on the PR head alone is then out of date.
- [ ] The diff stays inside the issue's Scope. Anything extra is either reverted or listed under the PR's Deliberate calls.
- [ ] The diff and PR body contain no personal names, real domains, or local paths.
- [ ] Counts and procedural claims in the hand-off entry are checked against the repo. (The #3 worker reported five entries in `HANDOFF.md` when there were three.)
- [ ] For an issue labelled `briefed-review`: the brief is filled from `.agents/review-brief.md`, the round has run, and the verdict is GO.

Tell the owner which lines you ticked by running something and which by reading.

## Briefed rounds

For an issue labelled `briefed-review`, after the automatic reviews are answered:

1. Fill the template in `.agents/review-brief.md` and save it as `.dev/<issue>/review-brief-<round>.md` in the main checkout.
2. Start the reviewer from the roster in a worktree, with the one line: *"Follow the review brief at `<absolute path>`"*. A reviewer started as a delegated task gets a worktree cut from `main`, not from the PR head: the brief's Environment paragraph tells it to fetch and check out the head sha detached.
3. The round does not need the worker's final message. Start it as soon as both automatic reviews cover the current head and their findings are answered.
4. A P1 or P2 goes back to the worker's thread to fix. A fix means a new head, so both automatic reviews and the round repeat until the verdict is GO at the current head.
5. P3s on a GO become follow-up issues you propose to the owner. Folding one into the PR moves the head and restarts all three reviews.

The briefs used so far are in `.dev/6/`, `.dev/45/` and `.dev/8/` in the main checkout. They are not committed; where they are present, read one before writing the next.

## When a model refuses

Parts of v0.2 look like offensive tooling to a safety filter although they are ordinary network administration on the owner's own network: the ICMP and ARP sweeps (#13, #14), the OAuth server and its consent page (#26–#29), and tests that feed hostile input to ingest. A worker, a reviewer, or you may be refused or cut off partway through.

A refusal is a hand-over to the owner. They continue the work by hand in the Codex app with the manual-fallback model from the roster.

1. Leave the branch as it is, with everything committed and pushed.
2. Write `.dev/<issue>/manual-handover.md` in the main checkout. The session that picks it up has no context, so the file stands alone:
   - the repository, the branch, and the issue number, with "read `AGENTS.md` and the issue first";
   - what is done, by commit;
   - the step that was refused, and the refusal message word for word;
   - what is left on the issue's acceptance list;
   - how to finish: push to the same branch, and add the model's name to the PR body's opening line.
3. Tell the owner the path and that the issue is waiting on them. Carry on with other ready issues.
4. When the branch moves again, verify it as you would any worker's result. The part written by the fallback model is from the briefed reviewer's own model family, so its briefed round goes to a Claude model that did not write the PR.

Send the same task to another model in the roster first only when the refusal is plainly about wording in your brief. One retry; then hand over.

## Hand-off entries

- A worker in a worktree cannot commit to `main`. It ends its final message with its entry, and you commit it.
- **One entry per issue, committed after the merge.** Take the worker's entry as written and add a `Merged:` line under **Done** with the merge commit and any follow-up issues filed. The worker wrote its entry before the reviews finished, so the `Merged:` line also says how each review ended. Commit it before the merge only when the PR will sit unmerged past the end of your session.
- Then apply the rotation rule in `HANDOFF.md`'s header. Count the entries; do not take a worker's count.
- **End your own session with an orchestrator entry on top.** It holds live state for the next orchestrator: what is merged, what is in flight, which issues are ready, and the wave you would start next.

## Stop for the owner

Ask before any of these. Everything else in the loop is yours.

- Merging a PR.
- Filing, closing, or relabelling an issue. Propose it in your report.
- Starting an issue labelled `owner`, or the part of an issue that needs a real network, a real account, or a deployed instance.
- A spec change that alters a row in the spec's §9 Decisions table.
- A briefed round that returns NO-GO twice on the same PR.
- A refusal or safety block on legitimate work. Hand it over as "When a model refuses" describes.
- Changing a seat in the roster.
