# Session hand-off log

The next session may be a different agent or model with no shared context. Every session that changes this repo adds an entry **at the top** (newest first) before it finishes.

- **Five entries live here.** When yours makes six, move the oldest, verbatim, to the top of `.agents/handoff/YYYY-MM.md` (the month in its date) in the same commit.
- **An entry carries state in about 40 lines:** what was done, what was decided, what is half-finished or broken, what comes next. Detail belongs in the issue or PR; link it.
- **The newest entry is self-sufficient about live state.** Restate or link anything from an older entry that still matters. Rotation drops the older ones, so nothing live may depend on them.
- **Entries commit straight to `main`.** If two sessions add entries at once, keep both, newest first.

To find something in the archive, grep it:

```bash
grep -n '^## ' .agents/handoff/*.md      # every archived title, with its date
grep -rn '#12' .agents/handoff/          # every mention of an issue or PR number
```

Template:

```markdown
## YYYY-MM-DD — <agent (model)> — <short title>
- **Done:**
- **Decisions:**
- **State:** (tests? schema? anything half-finished or broken?)
- **Waiting on the owner:**
- **Next:**
```

---

## 2026-11-25 — Claude Code (Fable 5.2), orchestrator — state after the wave of 2026-11-24/25; three workers in flight
- **Done:** since the last orchestrator entry (now in `.agents/handoff/2026-11.md`), merged by squash with the owner's go-ahead for each, every one with a hand-off entry here or in the archive: #91 (PR #134), #133 (#137), #22 (#130), #23 (#141), #100 (#142), #24 (#145), #144 (#146), #139 (#148), #132 (#135), #149 (#151), #143 (#150), #138 (#152), #92 (#157), #156 (#158), #153 (#155), #147 (#154). `main` is at `c4c4e4d` plus hand-off commits: 1,462 agent, 1,340 server and 188 web tests.
- **In flight** (three, the limit; none shares a code file):
  - **#140** (installer parser errors; the agent token's form in `agent_protocol.py`), branch `140-parser-errors-token-form`. Owes a briefed round.
  - **#160** (refuse a DNS reply whose header claims more records than a PTR answer needs), branch `160-dns-record-budget`. Owes a briefed round.
  - **#94** (pin the agent's base image by digest), branch `94-agent-base-image-digest`. Automatic reviews only.
  - Nothing wakes the orchestrator when a worker opens its PR: list open PRs at the start of each turn, then watch, verify and brief.
- **Queue, in the order agreed with the owner:** #136 after #140 (both edit `install.py`); #159 after #94 (both touch the release workflow); #93 and #20 after #160, one at a time (all in `probe.py`). #31 (deployment document from spec §8) is unblocked by #132 and not started. M3 (#26–#30, #32) waits on #25.
- **Decisions the owner took this session:**
  - A GO from Sol stands in for a Greptile re-run. When a PR owes no briefed round and Greptile has not read the head, run one outside read by Sol instead of asking for a retrigger.
  - P3s on a follow-up PR go back to its worker to fix in the same PR; the orchestrator then reads the diff against the reviewer's remedy, with Codex clean on the new head, and no further round.
  - PR #154's command record is a tripwire for changes made in good faith, not a boundary (after two NO-GOs).
  - A review round GPT-6.1 Sol is stopped on by its safety filter is restarted from T3 Code on GPT-6 Sol with Daybreak Blue (`.agents/orchestration.md`, "When a model refuses", updated in this commit). It happened on #132, #153 and #147 (twice in one day); the hand-over files are under `.dev/<issue>/`.
  - "Pause new sessions" for the 5-hour Claude limit means new Claude workers only: Codex rounds may start, and messages to running workers are fine.
- **Learned, and not written elsewhere:**
  - Re-running a pull request's CI job re-tests the merge it was first run on. To test a PR against a `main` that has moved, update its branch (`gh pr update-branch`), check the new head's tree equals `git merge-tree --write-tree origin/main <old head>`, post `@codex review`, and say on the PR what the commit is. Done on #155 and #154.
  - A check that fails in a test the PR does not touch is still evidence. #156's failure followed the text of an unrelated test module: `tracemalloc` counted a rebuild of the interpreter's interned-string table. A re-run of `main`'s job was the control that showed it was the head, not the runner.
  - The orchestrator's own verification caught a wrong figure in #155 before its round (203 entries; a root question is 5 bytes, so 244). Recompute one stated bound per PR.
- **State:** nothing half-finished in `main`. No schema change this session beyond those the merged PRs' entries name; `PROTOCOL_VERSION` is 2. Review briefs and reviews are in `.dev/<issue>/` in the main checkout (not committed). Stale local branches and `t3-*` worktrees from merged work are still on this machine; they are not cleaned up.
- **Waiting on the owner:**
  - The first build of the server image with the new `CMD` (`python -m app.serve`, #132): nobody has built it, and no proxy has been run against spec §8.
  - The first release, by `.agents/releases.md`: nothing has been published; the first tag and the registry settings are theirs. Its step 5 now includes a check of the cross-attempt artefact download (#147).
  - #5 and #25 (`owner` label), deferred until the deployment is rebuilt behind a tunnel.
  - Permission to send packets on the test host covered 2026-11-23 only: ask again before any real-network test.
- **Next:** verify the three PRs as they open; brief Sol on #140 and #160; then the queue above.

## 2026-11-25 — Claude Code (Opus 5.5), worker — #147: run the release's publish steps against stand-ins; the retry window (PR #154)
- **Merged:** 2026-11-25, squash `c4c4e4d`, at head `6261c8b`; branch deleted. Entry committed by the orchestrator, with the review and state lines brought up to date. `6261c8b` is GitHub's merge of `main` (holding PRs #158 and #155) into the worker's last head `c3855d4`, with no hand-written change (its tree equals `git merge-tree` of the two); CI passed on it and Codex read it clean.
- **Done:** [PR #154](https://github.com/DeusMaximus/TLG-IPAM/pull/154) (`147-release-publish-tests`) closes #147. Tests and runbook only; `release.yml` and `.github/scripts/` are byte for byte what they were.
  - `agent/tests/test_release_publish.py` (57 tests) runs both publish jobs' `run` scripts as written, against stand-ins for `gh`, `skopeo` and `sleep` (`agent/tests/release_stubs.py`). It covers every registry answer, draft and published release states, failed lookups and downloads, and every order of gh's lookup race, checked against the release each lookup really returned.
  - A one-`printf` DEBUG trap records the commands each step runs, and `Publish.run` fails on one that is not approved. Every command-holding line must run in some test, and the line reader refuses a here-document. Seven good-faith mutations run on both jobs.
  - The command record is documented everywhere as a tripwire, not a sandbox, with what it does not see named.
  - `.agents/releases.md`:
    - the retry window: artefact configured for 7 days; re-run failed jobs versus re-run all;
    - past the window, once the image is published, the version is spent;
    - cross-attempt artefact download is an unmeasured assumption, with a check on the first release (checklist step 5);
    - the leftover-draft cleanup, and which jobs a `publish-release` re-run runs.
  - `AGENTS.md` and brief §2.4 updated.
- **Review:** three outside reads in place of Greptile's re-run, every finding answered on the PR.
  - Codex (GPT-6.1 Sol) at `30f7f0c`: NO-GO, one P2 and two P3s, fixed.
  - At `7ec01c8`, GPT-6.1 Sol was stopped by its safety filter part way (`.dev/147/manual-handover.md`); the read ran on GPT-6 Sol with Daybreak Blue: NO-GO, two P2s and two P3s. Two NO-GOs went to the owner, who decided the answer: the command record is a tripwire for changes made in good faith, not a boundary, so the claims were narrowed (pre-run skip, `extdebug` and the static `/dev/tcp` check removed; the trace stays a file) and the two P3s fixed (lookup race recorded and asserted; here-document refused).
  - Codex (GPT-6 Sol) at `c3855d4`: GO, no findings; all four round-two findings answered in the owner's terms, and every acceptance mutation red.
  - Codex automatic review clean on every head, the last `6261c8b`. Greptile read only `d2ec97a`; its one P2 was fixed in `30f7f0c`.
- **Decisions:** PR Deliberate calls 1–6:
  - the `run` text is tested in place;
  - the artefact stays at 7 days;
  - the failed-lookup fallback is unchanged (all three reads would prefer failing closed: filed as #159);
  - the stubs are not checked against the real services;
  - a DEBUG trap with no `extdebug`;
  - the owner's tripwire-not-boundary decision.
- **State:** merged. Agent suite on `main`: 1,462. This file passes on bash 3.2 and 5.2. No schema change; `PROTOCOL_VERSION` unchanged. Note for bash: `shopt -s extdebug` in a startup file makes bash 5 try to start its debugger and turns `functrace` off.
- **Waiting on the owner:** nothing from this PR. The first tag is still the owner's.
- **Untested:**
  - the real gh, skopeo, ghcr.io and GitHub API, including GitHub refusing to publish a draft over a published tag (assumed);
  - cross-attempt artefact download (measured on the first release);
  - anything a script written to get past the record would do;
  - `actionlint` (not installed; workflows unchanged).
- **Next:** #159 (fail-closed release lookup) and #94 (pin the agent's base image by digest), one at a time: both touch the release workflow.

## 2026-11-25 — Claude Code (Opus 5.5), worker — #153: no DNS reply over the advertised payload is parsed (PR #155)
- **Merged:** 2026-11-25, squash `c128179`, at head `793eff3`; branch deleted. Entry committed by the orchestrator, with the review, CI and state lines brought up to date. `793eff3` is GitHub's merge of `main` (holding PR #158) into the worker's last head `9b2acdc`, with no hand-written change (its tree equals `git merge-tree` of the two); the orchestrator made it so CI would test the PR with the #156 fix, and the `agent` job passed there.
- **Done:** [PR #155](https://github.com/DeusMaximus/TLG-IPAM/pull/155) (`153-dns-reply-size`) closes #153.
  - `agent/src/tlg_ipam_agent/probe.py`:
    - `DNS_PAYLOAD = 1232` is both the advertised payload and the largest reply the agent reads.
    - `QuerySocket.datagram_received` answers an over-size datagram from the query's server and port with `ReplyTooLarge` (a `DNSException`): that attempt ends as no usable answer and the next server is asked.
    - An over-size datagram from any other source is dropped. `_from_server` matches dnspython's source rule or is stricter.
  - Spec §5.2 (the limit, what it refuses, the entry count and the estimate), brief §4.6 and `AGENTS.md` are updated.
  - Tests: 15 new in `test_probe.py`. Agent suite on `main` after this and PR #158: 1,405. Server suite 1,340, unchanged.
  - Review: GPT-6.1 Sol was stopped by its safety filter a few minutes into the briefed round and posted nothing (`.dev/153/manual-handover.md`); the round ran on GPT-6 Sol with Daybreak Blue: GO at `a06bd29` with one P3 (the comparison figure was called a ceiling on a false premise), fixed in `9b2acdc` with a numbered reply on the PR, and read by the orchestrator against the remedy. Before the round the orchestrator sent back one wrong figure (203 records; a root question is 5 bytes, so 244). Codex clean on `793eff3`; Greptile last read `051cd71`, clean.
- **Decisions:** PR body Deliberate calls 1–7. Size checked at arrival, in the agent's socket. An over-size reply ends the attempt only when it comes from the server. 1,232 kept: 710 bytes is the largest ordinary answer built, not the largest possible. No exhaustive bound on parse work is claimed.
- **Measured (M1 Ultra):**
  - At most 244 entries (5-byte root question); 29,646 comparisons is an estimate, 0.40 s here.
  - The worst reply built parses in about 0.116 s and holds the loop 115–119 ms; root entries do not raise that.
  - A 9,856-byte reply takes 8.7 s.
  - Two colliding answer records make 2, 4 or 6 comparisons, depending on the hash.
- **State:** merged. No schema change; `PROTOCOL_VERSION` 2; no new import or listed name. Its CI `agent` job failed three times before PR #158, only in `test_answers.py`'s memory test (#156, not this PR's).
- **Waiting on the owner:** nothing from this PR. The record budget before parsing (Deliberate call 7) is filed as #160.
- **Untested:** an over-size reply over a real network path; the timings on CI hardware; the full suite on Python 3.11 (the reviewer ran the new cases on 3.11.18).
- **Next:** #160. #20 and #93 are still open in `probe.py`.

## 2026-11-25 — Claude Code (Opus 5.5), worker — #156: the answer-cap memory tests measure the client in a fresh interpreter (PR #158)
- **Merged:** 2026-11-25, squash `606e1d4`, at head `34c9b40`; branch deleted. Entry committed by the orchestrator, with the review and state lines brought up to date.
- **Done:** [PR #158](https://github.com/DeusMaximus/TLG-IPAM/pull/158) (`156-answer-cap-memory-test`, head `34c9b40`) closes #156. Tests only; `client.py` and `RESPONSE_MAX` unchanged.
  - Cause: each check-in makes httpcore try `import sniffio` (not installed). Under pytest the attempt passes the assertion-rewriting hook, whose `PurePath` interns a fresh string that is then freed (mortal in 3.13). That uses up a slot of the interned-string dict, and when the slots run out the dict rebuilds its table (3,844,800 B in a full run). `tracemalloc` counts the new table in full. The free-slot count at the test moves with every collected module's text: 49 on `main`, 20 on `main` + PR #155 in a `python:3.13-slim` container.
  - Reproduced in that container with the test unchanged. A plugin (`edge156.py`, in the PR body) uses up interned slots before the test, and the merged tree then fails with CI's figures: 4,022,997–4,028,543 and 5,314,720–5,315,023 B. After the fix, all 10 shifts pass. The orchestrator re-ran one shift of each: 5,313,160 B held before, passing after.
  - `agent/tests/answer_memory.py` (new, not collected) runs the client in `python -I`: no pytest, no hook, no server. It warms up with one check-in and measures the second. `test_answers._held` runs it, with kill and reap on timeout or cancel (Greptile P2, fixed in `34c9b40`). The compressed-answer, plain-answer and raw-chunk tests use it with their limits unchanged.
  - New `test_the_measurement_sees_a_client_that_holds_too_much`: a reader with twice the cap (2.7 MB) and one that decodes first (81 MB) are each over their limit.
  - Tests: agent 1,388 → 1,390. Container: 1,388 + 2 skipped on the branch, 1,403 + 2 skipped merged with PR #155.
  - Review: Codex clean on `34c9b40`; CI green (9 checks). Greptile read `0b0d71e` (one P2, fixed); in place of its re-run, an outside read by Codex (GPT-6.1 Sol) at `34c9b40`: GO, no findings (`.dev/156/review-1.md` and the PR). It read the interned dict's free slots directly (unchanged over 20 check-ins in the child) and ran the plain test 50 times: largest 1.84 MB, 1.87 MB under CPU load, against 2.10 MB.
- **Decisions:** PR body Deliberate calls 1–4. A child process, because `tracemalloc` has no filtered peak and the rebuild is on the client's own stack. Limits kept, with the budget and measured figures stated: plain 1.47 MB on Linux, 1.57–1.87 MB on macOS, against 2.10 MB; measured, not derived as a bound. The raw-chunk test moved too, for one rule.
- **State:** merged. No schema, protocol, brief or `AGENTS.md` change.
- **Waiting on the owner:** nothing from this PR.
- **Untested:** the CI runner's exact interned-slot state; Python other than 3.13.
- **Next:** PR #155's `agent` job on a head that holds this fix.

## 2026-11-25 — Claude Code (Opus 5.5), worker — #92: say before an enrolment token is issued that the page is on plain HTTP (PR #157)
- **Merged:** 2026-11-25, squash `2abfcb4`, at head `884cda2`; branch deleted. Entry committed by the orchestrator, with the state lines brought up to date. The orchestrator ran lint, the 188 web tests and the build at the head before the merge.
- **Done:** [PR #157](https://github.com/DeusMaximus/TLG-IPAM/pull/157) (`92-plain-http-warning`, head `884cda2`) closes #92.
  - `web/src/components/EnrolmentSteps.jsx`: `PlainHttpBeforeIssue` (new, `agent-plain-http-before`) is shown before the request that issues a token. `PlainHttpWarning` (`agent-needs-https`) now has two wordings. With `issued`, beside the token: the token has already crossed in clear; run the agent soon; if its exchange is refused, revoke and re-enrol over HTTPS; the agent token crosses only over a plain-HTTP exchange, which the commands never make. Without it (update commands): no mention of a token.
  - `AddAgentModal` form and `ReEnrolModal` confirmation show the notice when `!serverUrlFor(location).secure`; both still issue. The rule stays `install.agentServerUrl`'s; `web/` parses no address. No command changed (`install.js` and its test untouched).
  - Docs: spec §5.6 has a new bullet, "A page served over plain HTTP"; brief §6.2 has a sentence on it; `AGENTS.md` guided-install bullet has one sentence.
  - Tests: web 179 → 188 (`Agents.test.jsx`). The before-issue tests fail with the notice removed; the before-issue and update tests fail with the old wording restored. Server 1,340 and agent 1,388 pass (no code changes there).
  - Review: Codex clean and Greptile 5/5 on `884cda2`; CI green (6 checks). No fix commits. It owed no briefed round.
- **Decisions:** PR body Deliberate calls 1–4. Re-enrol warns before issuing too. The update dialog's warning drops the token wording. The remedy is revoke then re-enrol, not delete (keeps the scan targets) and not disable (enabling would revive the other party's token). The notice warns and does not block.
- **State:** merged. No schema change; `PROTOCOL_VERSION` unchanged; no server or agent change.
- **Waiting on the owner:** nothing from this PR.
- **Untested:** a real browser on a real plain-HTTP origin (jsdom tests pass a `location` object).
- **Next:** the owner key crossing in clear from such a page stays #31's (deployment guidance).
