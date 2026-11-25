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

## 2026-11-25 — Claude Code (Fable 5.2), orchestrator — state after #94, #140 and #160 merged; three workers in flight
- **Done:** since the orchestrator entry below this one's worker entries (same date; it holds the session's decisions and lessons, which still stand), merged by squash with the owner's go-ahead: #94 (PR #161), #140 (PR #162), #160 (PR #163). Filed with the owner's go-ahead: #164 and #165. #159's scope gained a bullet (a secret handed to a `build` step passes every shape test). `main` is at `6cb82b6` plus hand-off commits: 1,644 agent, 1,357 server and 188 web tests.
- **In flight** (three, the limit; none shares a code file):
  - **#136** (check the CA bundle's access as the service account), branch `136-ca-access-as-service`. Owes a briefed round. It will widen `test_invariants.py`'s lists.
  - **#159** (stop `publish-release` when it cannot tell whether the tag has a release; hold which steps are given a secret), branch `159-release-lookup-fail-closed`. Owes a briefed round.
  - **#164** (say in the job's error how many DNS replies were refused for size or record count), branch `164-dns-refusal-reasons`. Owes a briefed round.
  - Nothing wakes the orchestrator when a worker opens its PR: list open PRs at the start of each turn, then watch, verify and brief.
- **Queue:** #93 and #20 after #164, one at a time (all in `probe.py`); #165 (refuse an agent the server cannot answer before its enrolment token is spent) after #136, since both reach the installer's exchange; #31 (deployment document from spec §8) is unblocked and not started. M3 (#26–#30, #32) waits on #25.
- **Decisions since the last orchestrator entry:**
  - P3s from a briefed round or an outside read go back to the PR's worker to fix in the same PR, on stretch issues too (#94), not only on follow-ups; the orchestrator reads the fix against the reviewer's remedy, with Codex clean on the new head.
  - A reviewer's recommendation that reaches past the PR's files (#164, #165) is put to the owner as a new issue instead.
  - Before merging a PR whose CI ran against an older `main`, either update its branch (when the two may interact) or run the suite on the merged tree locally (when their files are disjoint: done for #162 after #161).
- **Reviewer models:** GPT-6.1 Sol completed the rounds on #156, #140 and #94, and was stopped by its safety filter on #160, as on #132, #153 and #147: every time on work that builds hostile input (DNS replies, pipelined requests, a shell harness). GPT-6 Sol with Daybreak Blue completed each restarted round. The orchestrator proposed starting rounds on DNS replies on GPT-6 Sol directly; the owner has not answered, so the roster's order stands: 6.1 first.
- **State:** nothing half-finished in `main`; CI green on it. `PROTOCOL_VERSION` is 2. Review briefs, reviews and hand-over files are in `.dev/<issue>/` in the main checkout (not committed).
- **Waiting on the owner:**
  - The first build of the server image with `CMD ["python", "-m", "app.serve"]` (#132); no proxy has been run against spec §8.
  - The first release, by `.agents/releases.md`: nothing has been published. Step 3 is now the base-image refresh (#94), with a recheck of the DNS ceiling's premise when the image's Python changes (#160); step 5 checks the cross-attempt artefact download (#147).
  - #5 and #25 (`owner` label), deferred. Permission to send packets on the test host covered 2026-11-23 only.
- **Next:** verify the three PRs as they open and brief Sol on each; then the queue above.

## 2026-11-25 — Claude Code (Opus 5.5), worker — #160: a record budget on DNS replies before parsing (PR #163)
- **Merged:** 2026-11-25, squash `6cb82b6`, at head `78aa7b3`; branch deleted. Entry committed by the orchestrator, with the review and state lines brought up to date. The head held `main` with PRs #161 and #162, and all nine checks passed on it.
- **Done:** [PR #163](https://github.com/DeusMaximus/TLG-IPAM/pull/163) (`160-dns-record-budget`) closes #160.
  - `agent/src/tlg_ipam_agent/probe.py`:
    - `DNS_QUESTIONS_MAX = 1`, `DNS_RECORDS_MAX = 32` (answer, authority and additional together, as the header claims, OPT included).
    - `unread(data)` judges both limits from the length and the header alone; `ReplyTooManyRecords(DNSException)` sits beside `ReplyTooLarge`.
    - `QuerySocket.datagram_received` applies #155's source rule to both: from the server, the attempt ends and the next server is asked; from anywhere else, dropped.
  - Spec §5.2: the budget, what it refuses (the 32-PTR-plus-OPT reply among them), understated and overstated counts, and the derived ceiling in place of the 29,646 estimate.
  - Brief §4.6 and the `AGENTS.md` invariant are updated, and a recheck step is added to #94's base-image procedure (`AGENTS.md`, Commands): when the image's Python changes, rerun the premise tests on it.
  - Tests: 36 new in `test_probe.py`. Agent suite on `main`: 1,644.
- **Review:** Codex and Greptile were clean on `002a5ef`. GPT-6.1 Sol was stopped by its safety filter a few minutes into the briefed round and posted nothing (`.dev/160/manual-handover.md`); the round ran on GPT-6 Sol with Daybreak Blue: GO at `002a5ef`, the ceiling held (its strongest accepted reply made 1,528 comparisons), seven mutations red, two P3s.
  - P3 1 (the ceiling's argument) fixed in `91ca355` and `78aa7b3`: the 13th visit is the predecessor of the zero-perturb cycle, OPT is counted (1,651 with one), the premise test is called a tripwire, the 1,736 is scoped to 64-bit CPython with 3.13's probe, and named hashes are tested. Read by the orchestrator against the remedy; Codex clean on `78aa7b3`.
  - P3 2 (say why a reply was refused) is filed as #164.
  - Before the round the orchestrator checked the dictionary premise independently: 350,800 hashes on Python 3.15, worst total 1,694, no probe more than 12 over.
- **Decisions:** PR Deliberate calls 1–7: one budget across the three sections, 32 = 2 × 16; zero questions allowed (FORMERR, NOTIMP); a datagram shorter than a header is left to dnspython; the ceiling is derived, its premise test is a tripwire, and the premise is rechecked whenever the interpreter changes; no per-reason count in the job's error here; TC replies are still parsed before refusal.
- **Measured (M1 Ultra):** the ceiling is 1,736 name comparisons (1,651 with an OPT). Worst reply built: 1,426 comparisons, 23 ms parse, loop held 24–25 ms (was about 116 ms). The premise held on 3.13.12 (200,000 hashes) and in the image's 3.13.18 (20,007 hashes): at most 12 over.
- **State:** merged. No schema change; `PROTOCOL_VERSION` 2; `test_invariants.py` unchanged.
- **Waiting on the owner:** nothing from this PR.
- **Untested:** the ceiling over every hash, and on Python 3.11, 3.12 and 3.14, which a native install can run; 32-bit builds; real network paths.
- **Next:** #164, then #93 or #20, one at a time: all touch `probe.py`.

## 2026-11-25 — Claude Code (Opus 5.5), worker — #140: parser and validation errors repeat no argument; the token's form in the wire contract (PR #162)
- **Merged:** 2026-11-25, squash `873b52a`, at head `46c450e`; branch deleted. Entry committed by the orchestrator, with the state lines brought up to date. Before the merge the orchestrator ran the agent suite on this head merged with `main` after PR #161: 1,608 passed.
- **Done:** [PR #162](https://github.com/DeusMaximus/TLG-IPAM/pull/162) (`140-parser-errors-token-form`) closes #140.
  - `8259adb`, parser:
    - `cli._Parser`'s errors quote nothing typed. argparse's message is kept only when it is made of the parser's own names; unrecognized arguments are counted; every error ends with `cli.TYPED_TOKEN`. Help unchanged.
    - `agent_protocol` states the token form once (`TOKEN_SECRET_*`, `AGENT_TOKEN_FORM`, `ENROL_TOKEN_FORM`), held on the server side (`test_agents.py`) and the agent side (`test_contract.py`).
  - `a8c40a0`, the briefed round's two P3s:
    - `config.parse_allowed` names a bad item by its place, for the installer and the running agent alike. `install._ca_file` and the CA access refusal name the problem, never the path.
    - End-to-end validation cases cover a token in every option's value.
    - The `PROTOCOL_VERSION` rule is qualified as necessary but not sufficient wherever it is stated, held by `test_e2e.py` through the real routes: a server issuing a longer secret answers this release's agent `200` at any protocol, and its enrolment token is spent on a token it refuses.
  - `46c450e` merges `main` (`b1357fb`) with no hand-written change.
  - Spec §5.6, brief §4.2 and the `AGENTS.md` "Enrolment tokens" and "wire contract" bullets updated.
- **Decisions:** PR Deliberate calls 1–8. Call 4 was replaced at the reviewer's overrule; call 7: the running agent's `ALLOWED_CIDRS` message changes too; call 8: the enrolment compatibility mechanism is not designed here.
- **Review:** briefed round (Codex, GPT-6.1 Sol): GO at `8259adb` with two P3s (31,700 generated command lines on five Pythons, no token in any output; nine mutations red). Both fixed in `a8c40a0`, answered on the PR, and read by the orchestrator against the remedies. Codex automatic review clean on `46c450e`. Greptile 5/5 on `8259adb`; it has not read the fix, and the briefed GO stands in for it.
- **State:** merged. No schema change; `PROTOCOL_VERSION` 2; no new agent import or listed name. No token's form changed.
- **Waiting on the owner:** nothing from this PR.
- **Untested:** a real root or systemd install; the container image locally (CI's `agent` job checks it); translated argparse messages.
- **Next:** #136 (the CA bundle's access checked as the service account), which edits `install.py`, the CA access message changed here included. The enrolment compatibility mechanism is filed as a follow-up.

## 2026-11-25 — Claude Code (Opus 5.5), worker — #94: pin the agent's base image by digest (PR #161)
- **Merged:** 2026-11-25, squash `c652f2d`, at head `7d298f1`; branch deleted. Entry committed by the orchestrator, with the state lines brought up to date.
- **Done:** [PR #161](https://github.com/DeusMaximus/TLG-IPAM/pull/161) (`94-agent-base-image-digest`) closes #94.
  - `agent/Dockerfile`: `FROM python:3.13-slim@sha256:7249d558…`, the tag's multi-platform index (covers amd64 and arm64).
  - `release.yml` build job: the new step *The base image's pin, against its tag* warns, never fails, when the tag has moved on from the pin. Its comment names the one `FROM` shape its `sed` reads. The publish jobs are unchanged.
  - `AGENTS.md`: Commands gets "The agent's base image": a one-line `&&` chain that reads the digest, checks its form and moves the pin. The subsection says the test holds the pin's shape, not that the digest is the index. The "One version" bullet names the base pin.
  - `.agents/releases.md`, checklist step 3: refresh the base in its own PR before the bump PR, whose Tests section records the base digest and that its Release run reported it current.
  - `.agents/releases.md`, *Re-run all jobs*: corrected; the base is now fixed and only timestamps differ (measured: file times and 296 `.pyc` files).
  - Brief §2.4 and spec §5.6: one sentence each.
- **Tests:** 26 new in `test_release.py`.
  - Parsing: the `FROM` parser splits at Docker's white space and lines only at newlines. The one-stage test holds the shape the step's `sed` reads, and a tab-separated second stage is counted and refused.
  - Workflow: the step runs under bash against a stand-in `docker` for three registry answers (current, moved on, unreadable). On the PR's own Release run on the runner it printed `python:3.13-slim still names sha256:7249…`, and both platforms built from the pin.
  - Documentation: the `AGENTS.md` line runs as written without `set -e`, for one good answer and six bad ones.
- **Decisions:** PR Deliberate calls 1–7. The pin is the index digest. The digest moves by hand at each release, not by Dependabot. The server's Dockerfile is not pinned: it is built where it runs and never published. A stale pin is a warning, not a failure. Rebuilding a tag to the same digest is out of scope. The outside read overruled none.
- **Review:** Greptile read `b6fbc68` (one P2, fixed in `df40a87`). In place of its re-run, an outside read by Codex (GPT-6.1 Sol) at `df40a87`: GO, one P3 (a tab-separated second `FROM` went uncounted) and three corrections, all fixed in `7d298f1` and read by the orchestrator against the remedy, with the reviewer's mutant replayed. Codex automatic review clean on every head.
- **State:** merged. No schema change; `PROTOCOL_VERSION` unchanged; no new agent import or listed name.
- **Waiting on the owner:** nothing from this PR. At each release, checklist step 3's base refresh comes first.
- **Known gap, not this PR's:** a secret given to a `build` step through `env` passes every shape test in `test_release.py`. Added to #159's scope with the owner's go-ahead.
- **Untested:** Docker Hub's anonymous rate limit on the extra lookup (it would show as the "could not read" warning); GNU sed running the documented line outside CI.
- **Next:** #159 (fail-closed release lookup in `publish-release`).

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
