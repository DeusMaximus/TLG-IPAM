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

## 2026-11-16 — Claude Code (Sonnet 5.5) — #73: agent-token documents (PR #74)
- **Done:** [PR #74](https://github.com/DeusMaximus/TLG-IPAM/pull/74) closes #73, head `9e759f0` (against `main` at `688f987`). CI green (5 checks). Codex: no findings. Greptile: 5/5, no comments. 597 backend tests pass (594 + 3). Documents and one test only; the one `api/app/` change is a docstring in `services/jobs._claim`.
  - The order of refusals is written down in spec §5.7, brief §4.5 and the wire-contract bullet of `AGENTS.md`. A loaded row inside the gap gets 429 with no statement, whatever the owner committed since, and the next request gets 403. A row the gap lets through reaches the claim, which answers 403 when the owner's change has landed and 429 when only the slot is gone.
  - New test `test_a_recent_slot_is_refused_with_429_before_the_owner_refusal_is_read` (disable, revoke, delete) in `api/tests/test_checkin.py`. Removing the early check in `_claim` fails all three variants.
  - "A refused request never writes `last_used_at`" is now "refused before its check-in slot, or a refused result set" in brief §3.9, §4.1, spec §4.2, §5.1 and the agent-token bullet; `409`/`422` check-ins are named as writing it. Brief §3.8 points at §4.5 for which of 429/403 applies.
  - The agent-token bullet says "authenticated" through `require_agent`, and that the check-in claim revalidates state and revocation in SQL. Bare 500s now include a database error that is not an `OperationalError` (spec §4.2, brief §4.1, `AGENTS.md`).
  - Ingest source guard renamed `test_the_ingest_module_binds_neither_hosts_nor_the_change_log`. Its docstring and the `AGENTS.md` sentence say it checks names bound in `services/results` and does not follow imports.
  - **Merged:** `c6e579e`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #73 closed. Both automatic reviews covered `9e759f0`, the only commit, with no findings. The orchestrator re-measured 597 on the head and on `main` after the merge, confirmed the `api/app/` diff is the one docstring, and confirmed all three cases of the new test go red with the early refusal removed. Live state after this merge: two PRs are open, both with a worker fixing review findings. #70 (#13, the agent package) is at `9ea136b`: round 1 NO-GO fixed in `dbb2f1c`, `main` merged in `ce60198`, then a Codex P2 on that head (a PTR lookup's timeout ran out while it queued for a packet slot) fixed in `9ea136b`; CI green and Codex clean there, 359 agent tests in the orchestrator's run. Its second briefed round is running on `ce60198`, so the one commit after it owes a short round of its own. #72 (#15, findings) is at `f962a58`: round 1 NO-GO with one P2 (an `unknown_host` key does not identify the hostname and status an accept writes) and two P3s; the worker is fixing them and has to merge this `main`, with which it conflicts in `AGENTS.md`. Waiting for a free lane: #60 (shares `main.py` with #72), #65 and #68 (share `schemas.py` with #72). The `owner` issues #5 and #25 still wait on the owner.
- **Decisions:**
  - Documented the early 429; did not change it so a disable always answers 403.
  - Chose to reword the guard rather than follow imports: `results` reaches `changes` through `scans` and `agents`, so a closure check cannot pass. `test_ingest_writes_no_host_and_no_change_entry` carries the runtime fact.
  - Fixed the same absolute wording in brief §4.1 and spec §5.1, outside the three sentences the issue listed, because the acceptance line covers every document.
- **State:** green; nothing half-finished; no schema change. Python 3.13.12. Wording acceptance lines (2 and 3) are checked by reading, not by a test.
- **Waiting on the owner:** the merge. Automatic reviews only, no briefed round (documents and a test).
- **Next:** whichever of #70 (#13) and #72 (#15) merges after #74 rebases onto it. `AGENTS.md` bullets are single long lines, so expect a conflict in the wire-contract bullet (#70) or the scan-scope bullet (#72) against this edit, which changed the claim sentence, the agent-token bullet and the "Scan ingest never writes `hosts`" bullet. #13 owns spec §5.7's paragraph on how an agent handles each refusal; the order paragraph I added to the §5.7 claim bullet is separate from it.

## 2026-11-16 — Claude Code (Opus 5.5) — #59: check-in and results follow-ups (PR #71)
- **Done:** [PR #71](https://github.com/DeusMaximus/TLG-IPAM/pull/71) closes #59, head `3e612bb` (merges `main` at `1525a91`). CI green. Codex: no findings on `3e612bb`. Greptile: 5/5, no comments. 594 tests pass (571 + 23).
  - `services/agents.authenticate` only reads. It leaves the accepted token's id in `session.info`.
  - New `agents.record_token_use`: a SQL-conditional `UPDATE` that runs inside the serving request's transaction. Check-in calls it after its claim; results ingest calls it after `_release`. A refused request never writes token use.
  - `jobs._claim`: the compare-and-set also requires the agent `active` and the token unrevoked. If it loses, it answers 429 or 403. If it wins, it refreshes the whole agent row before the audit entry is built.
  - `jobs.checkin` and `results.ingest`: on `OperationalError`, roll back and answer 503 UNAVAILABLE via `errors.database_unavailable`. That logs the SQLite error name, never SQL, parameters or the bearer. The message says "busy" only for a lock.
  - Results ingest: a failure of the pre-commit encoding is rolled back and answered `500 INTERNAL_ERROR` in the error shape, via `errors.internal_error`.
  - Tests:
    - a barrier race test at `_claim` that catches the time-condition mutant 10/10;
    - the prune `entity_type` case;
    - `echo_ip` tests for each half of the rule, at the helper and through the route;
    - same-session rollback tests for check-in and ingest;
    - lock tests at a 50 ms busy timeout and real read-only-database tests (`conftest.app_database`), plus `write_lock` moved into `conftest.py`.
  - Docs:
    - Brief: §3.6, §3.8, §3.9, §4.1, §4.3, §4.5 (new **The claim** bullet), §4.6.
    - Spec: §4.2, §5.1 (new bullet), §5.7.
    - `AGENTS.md`: the agent-token and wire-contract bullets.
  - **Merged:** `072dc73`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #59 closed. Before the merge, the briefed round by GPT-6.1 Sol returned GO on `3e612bb` with two documentation P3s, posted on the PR: it replayed all eleven earlier findings against a real server and caught all 21 of its mutants. The orchestrator re-measured 594 on the head and on `main` after the merge, and confirmed the claim's state condition is guarded. Follow-ups on the owner's say-so: #73 (M2a, `documentation`) for the 429-before-403 order, the three too-absolute sentences and the ingest source guard's wording; and a note on #21 that enrolment must not reuse the per-request token context and that every new agent route records token use itself. Live state after this merge: two PRs are open. #70 (#13, the agent package) is at `797e249`: its first briefed round returned NO-GO with two P2s (DNS packets escape `MAX_PPS`; a slow response defeats the retry and lease bounds) and one P3, posted on the PR by the orchestrator because the reviewer's own clean-up was refused; the worker is fixing them and has to merge this `main` in. #72 (#15, findings) is at `f962a58` with CI green, Codex and Greptile clean and 614 passing; its briefed round is running (brief at `.dev/15/review-brief-1.md`). Waiting for a free lane: #60 (shares `main.py` with #72), #65 and #68 (share `schemas.py` with #72). The `owner` issues #5 and #25 still wait on the owner.
- **Decisions:** ten under Deliberate calls in the PR. The main ones:
  - An owner disable, revoke or delete after authentication refuses the in-flight check-in (403). Results ingest does not repeat that check.
  - Token use is recorded by served requests only: a check-in that takes its slot, or an accepted result set.
  - There are two 503 messages under one code, chosen by SQLite's error code.
  - A failed rollback, a failed authentication `SELECT`, and non-`OperationalError` database errors stay a bare 500.
  - The token id travels in `session.info`, so `auth.py` and its source-guard anchors are unchanged.
- **State:** green; nothing half-finished; no schema change. `api/tests/conftest.py` changed: it gained `write_lock` and `app_database`. Not verified: a real full disk or I/O failure (injected in the same-session tests), losing the response after a results commit, and Python 3.12.
- **Waiting on the owner:** the briefed round (author is Claude, so the reviewer should come from another model family), then the merge.
- **Next:**
  - #13 (agent): `last_used_at` now means "last served use".
  - The agent should treat these responses as follows:
    - 403 after an owner disable: stop.
    - 503 "busy": back off and retry.
    - 503 "unavailable": back off longer; the owner needs to look at the server.
    - 500 `INTERNAL_ERROR` on results: a server bug, so don't retry tightly.
  - #60: shared body cap.
  - #15: unaffected beyond its `main.py` line.

## 2026-11-16 — Claude Code (Opus 5.5) — #12: last_seen derived from observations (PR #69)
- **Done:** [PR #69](https://github.com/DeusMaximus/TLG-IPAM/pull/69) closes #12, head `47caa6d`, against `main` at `6845664`. CI green. Codex: no findings on `039837e` or `47caa6d`. Greptile: 4/5 on `039837e`, one P2 (test helper parsed the IP by hand), fixed in `47caa6d` and answered on its thread; not re-reviewed since. Automatic reviews only; the owner merges.
  - `services/hosts.py`: `hosts_out` joins in each host's observation (`(subnet_id, ip_int)`, `last_seen_at`, never `alive`) in one statement; `last_seen` is the later of the stored and the scan value, and the new `last_seen_source` (`manual`/`scan`/null) says which; a tie is reported as `scan`. Two statements per batch of any size.
  - `schemas.py`: `HostOut.last_seen_source`; a `last_seen` sent with an offset is converted to UTC before storage (SQLite was storing the wall-clock time and dropping the zone).
  - `mcp_server.py`: `get_host`, `list_hosts`, `update_host` docstrings (what clearing `last_seen` does). `EXPECTED_TOOLS` unchanged.
  - Docs: brief §3.4, §3.11, §5.2; spec §5.4 (three bullets under `Host.last_seen`); `AGENTS.md` invariant-3 bullet, one sentence.
  - Tests: new `api/tests/test_last_seen.py` (12). 571 passed (559 + 12). Mutation table in the PR body.
  - **Merged:** `272564a`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #12 closed. Before the merge, Greptile re-reviewed `47caa6d` after the owner's retrigger (5/5, finding fixed). The orchestrator re-measured 571 on `039837e` and on `main` after the merge, and confirmed both one-query tests go red when observations are queried once per host. Live state after this merge: no PR is open. Two workers are building, both Opus 5.5: #13 (the agent package, branch `13-agent-package`, `briefed-review`) and #59 (check-in and results follow-ups, branch `59-checkin-results-followups`, `briefed-review`); #59 has to pick up this PR's one-line change to `test_results.py` when it merges `main`. #15 (findings) is now ready and is the next to start. Waiting for a free lane: #60 (shares `main.py` and `test_results.py` with #59), #65 and #68 (share `schemas.py` with #15). The `owner` issues #5 and #25 still wait on the owner.
- **Decisions:** six under Deliberate calls in the PR. The main ones:
  - The match key is `(subnet_id, ip_int)`, because subnets may overlap across sections.
  - A tie counts as `scan`.
  - Offset input is converted to UTC.
  - Above `EXACT_LOOKUP_MAX` (500) hosts, the query reads the batch's subnets whole, to stay under SQLite's 32766 bound-value limit.
  - One line of #59's `test_results.py` changed: it asserted the response's `last_seen` stays null after ingest.
- **State:** green, nothing half-finished. No schema change, nothing under `web/`. Read-back datetimes still carry no offset (#65). Whichever of #59 and #69 merges second rebases `test_results.py`.
- **Waiting on the owner:** retrigger Greptile on `47caa6d`; merge.
- **Next:**
  - #19 shows `last_seen_source` in the UI.
  - #15 reads observations for findings; its `stale` finding should use the same `last_seen` rule (`hosts._last_seen`).
  - #65 can rely on stored host `last_seen` being UTC.

## 2026-11-16 — Claude Code (Opus 5.5) — #11: scan results ingest and observations (PR #64)
- **Done:** [PR #64](https://github.com/DeusMaximus/TLG-IPAM/pull/64) closes #11, head `69a9b77` (merges `main` at `a7f96d6`; earlier merge `c51b8a1` brought in #51). CI green. Codex: one P2 on `9a427eb` fixed; clean on `373f94d`, `cb83075` and `69a9b77`. Greptile: three findings on `9a427eb`/`373f94d`, all fixed and answered on their threads; 5/5 on `cb83075`; needs a retrigger on `69a9b77`. Briefed round 1 (GPT-6.1 Sol) NO-GO at `cb83075`: P2 fixed and both review P3s handled in `c6d8acc` (the lock-during-ingest 500 → #59). Round 2 not started. The owner merges.
  - `services/results.py` (new): `POST /api/v1/agent/results`.
    - Refuses the whole set with `409 JOB_NOT_CURRENT` unless `job_id` is both the agent's and a target's `current_job_id`.
    - Clears both lease copies in one transaction, each a compare-and-set. `last_scan_at` = `job_issued_at`.
    - Rejects records one by one, judged by `job_methods` and by the scope read under the write lock.
    - Only a `complete` job issued a liveness method marks addresses dead. The 90-day prune covers every subnet.
    - The answer is built and encoded before the commit. Nothing is written to `hosts` or the change log.
  - `agent_protocol.py`: results shapes; `rdns_name` (a hostname, or dropped whole); `scan_error`; `echo_ip` (only short, ASCII, printable strings, otherwise null); `RESULTS_BODY_MAX` (2 MiB, read after auth, `413`).
  - `services/scans.py`:
    - `scan_scope` reads the subnet, the target's `scan_dhcp_pool` and the excluded ranges as committed columns, never from loaded objects and never refreshing them.
    - `drop_out_of_scope` takes the write lock (`take_write_lock`), decides membership in Python, and deletes by id in batches of 500.
    - Every scope-narrowing write calls `drop_out_of_scope`; subnet deletion deletes the subnet's observations.
  - Schema, additive: table `observations`; nullable `scan_targets.last_scan_error`, kept out of audit snapshots.
  - Docs:
    - Brief: §2.3, §3.6, §3.8, §3.10, new §3.11, §4.1 (one sentence), §4.2, §4.5, new §4.6, §7, §10.
    - Spec: §5.3, §5.4, §5.7.
    - `AGENTS.md`: audit bullet (four exceptions), scan-scope bullet (deletion on scope change, write-lock rule), wire-contract bullet, new bullets for invariants 3 and 8.
  - Tests: 559 on `69a9b77` (425 + 134). Every mutant in the PR's tables is killed; the barrier race test kills the both-predicates mutant 10/10.
  - **Merged:** `f720f88`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #11 closed. Before the merge, the second briefed round by GPT-6.1 Sol returned GO on `69a9b77` with two P3s, posted on the PR: it replayed its reproduction against a real server (200, the valid record stored, both lease copies cleared) and sent the same input through every other agent string on both routes with no further 500. Greptile did not re-review `69a9b77`; under the rule adopted the same day that is optional after a GO at the current head. The orchestrator re-measured 559 on the head and on `main` after the merge, confirmed the fix's mutation red, and confirmed the merge commit `69a9b77` identical to a mechanical merge. The P3s went into existing issues on the owner's say-so: #59 now also owns the 503 for a lock during results persistence, tests for each half of the `echo_ip` rule, and the wording of the pre-commit guarantee in spec §5.7 and the brief (it promises response construction and encoding, not delivery); #13 carries a note that a rejection has no record index. Live state after this merge: no PR is open.
- **Decisions:** 14 under Deliberate calls in the PR; round 1 accepted all of them. The main ones:
  - `error` lives in `scan_targets.last_scan_error`, made printable and capped.
  - A record carrying a field its job was not issued to collect is rejected whole.
  - An address named in a rejected record is not marked dead.
  - A refusal of a whole result set is not recorded on the agent.
  - A late result for an expired lease is accepted until either lease copy is overwritten.
  - `fdns` entries are checked for shape and not stored until #20.
  - The PR body has a table of every scope caller and whether it runs under the write lock.
- **State:** green, nothing half-finished. No route reads observations yet. #15 must add dismissals to `drop_out_of_scope`. Any new caller that writes on the strength of the scope must hold the write lock first (`AGENTS.md`). `_capped_json` is route-specific until #60. A database lock during results persistence is still a bare 500 (#59). Round 1 noted that `ip_int` as BIGINT cannot hold IPv6, on hosts as on observations; this is inherited, and IPv6 support needs an additive representation change.
- **Waiting on the owner:** retrigger Greptile on `69a9b77`; the second briefed round; merge.
- **Next:**
  - #12: `last_seen` from observation timestamps, not `alive`.
  - #15: findings and dismissals. Dismissals join `drop_out_of_scope`. Treat `rdns_name` and `last_scan_error` as data.
  - #20: `fdns` address validity, issued-host membership and `fdns_results`.
  - #59: the 503 for a lock during results and check-in persistence.
  - #60: one shared body cap.
  - #13: the agent, against `agent_protocol.ResultsRequest`.

## 2026-11-16 — Claude Code (Opus 5.5) — #18: subnet scanning settings and reserved-range Scan checkbox (PR #67)
- **Done:** [PR #67](https://github.com/DeusMaximus/TLG-IPAM/pull/67) closes #18. Merged at head `f8d7cf1` (squash `31d9e44`). Frontend and brief §6.2/§7 only; no `api/` change, no schema change.
  - New: `web/src/components/ScanSettings.jsx`, a Scanning section on the subnet view.
    - Summary of the target. Last scan time (read as UTC) and duration, marked "incomplete" when the job did not finish. Whether a scan was requested or a job is out. The server's `scope`.
    - Settings form: agent, methods, interval (blank = manual only), enabled, "Scan DHCP pool" (off by default, disabled with no pool). It sends `PUT`, or `POST` for a new target, and shows the server's refusal inline.
    - Scan now (`POST /subnets/{id}/scan`) shows the server's 202, 409 or 429 message.
    - "MAC collection unavailable — agent is not on this subnet" shows while an issued job's `job_methods` lacks `arp`.
    - The target reloads every 30 s.
  - New: `web/src/utils/scans.js`: form ↔ body, the `ScanTargetCreate` defaults, `macUnavailable`, formatting.
  - Changed: `ReservedRanges.jsx`. Each chip has a "Scan" `.tlg-check` box that sends `PATCH scan_excluded`; an excluded chip reads "not scanned". The Reserve range dialog has "Scan this range", ticked by default.
  - Changed: `SubnetView.jsx`.
    - Both sections are keyed by subnet id, so a draft or an open dialog never carries over to another subnet.
    - Their writes go through one `refreshTreeAndView`. It reloads the view even when `refreshTree()` fails, and reports that failure apart from the write.
  - Tests: frontend 29 → 51 (16 in `SubnetView.scanning.test.jsx` against a stateful stand-in server and the real `TreeProvider`; 6 in `scans.test.js`).
    - Removing `refreshTree()` fails both acceptance tests.
    - The four review-fix tests each fail against `688b53e`'s sources.
  - Checked by hand on `688b53e` with headless Chromium against `API_KEY=dev-key`; details in the PR body.
  - **Merged:** `31d9e44`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #18 closed. CI was green on `f8d7cf1`, Codex found nothing there, and Greptile re-reviewed it at 5/5 after the owner's retrigger. The orchestrator re-ran lint, the 51 tests and the build on the head and on `main` after the merge, and confirmed the draft-across-subnets test goes red with the subnet-id keys removed. The hand run against a real backend is the worker's alone. Follow-up filed on the owner's say-so: #68 (M2a), the scan-target response says what its agent can run, for the two messages under **Next**. Process change the same day, approved by the owner and written into `.agents/orchestration.md`: a briefed round starts once CI is green and the first automatic pass is answered, without waiting for a Greptile re-review of a fix commit, and after a GO at the current head a Greptile re-review is optional. Live state after this merge: one PR is open, #64 (#11), at `cb83075` with CI green, Codex clean, Greptile 5/5 and 533 passing; its briefed round by GPT-6.1 Sol is running (brief at `.dev/11/review-brief-1.md`, report to `.dev/11/review-1.md`). No worker is building. Waiting for #64 to merge: #59, #60, #65, #68 (each touches a file it changes), then #12 and #15. The `owner` issues #5 and #25 still wait on the owner.
- **Decisions:** 7 under Deliberate calls in the PR. The main ones:
  - The UI never validates or works out scope; it shows the server's `scope` and its refusal text.
  - The settings form saves with a button; the range box saves on click.
  - Scan now is shown whenever a target exists; the server's 409 says why it cannot run.
  - No confirmation dialog for scope-narrowing changes, only help text that says data is deleted.
  - No delete of a scan target from the UI; it is out of #18's scope, and disabling the target stops scanning.
- **State:** green; nothing half-finished.
  - Codex: 2 P2s on `688b53e`, fixed. The orchestrator reports no findings on `f8d7cf1`.
  - Greptile: P1 and P2, P2 on `688b53e`, fixed and answered on their threads. The orchestrator reports 5/5 on `f8d7cf1` after a re-review.
  - Not seen against a real backend: the MAC warning (needs an agent, #13), last scan time and duration (needs ingest, #11 / PR #64).
  - The PR is not linked to a T3 thread (the link tool needed a thread id).
- **Waiting on the owner:** nothing for #18.
- **Next:**
  - Two things #18's third Scope bullet wanted that the server does not report. One field fixes both: e.g. `arp_available` / `scannable_by_agent` on `ScanTargetOut`, computed with `jobs.job_methods` against the agent's last-reported interfaces. The UI's `macUnavailable` in `web/src/utils/scans.js` would then read that field.
    1. **"MAC collection unavailable" between jobs.** `ScanTargetOut` has no field saying whether the target's agent is on the subnet's link. The UI can only read it from `job_methods` while a job is out, and ingest clears `job_methods`.
    2. **"Cannot be scanned by this agent".** Nothing reports it: for an `arp`-only target on a routed agent, check-in issues no job and stores nothing.
  - Once PR #64 merges, show `last_scan_error` (agent-originated text, render as text only) in the Scanning section's status.
  - #19 (findings panel, badge, last-seen, "show dynamic") builds on the Scanning section in `ScanSettings.jsx` and on `SubnetView`.
  - #65: offset-less datetimes; this PR reads them with `parseServerTime`.
