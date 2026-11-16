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

## 2026-11-16 — Claude Code (Opus 5.5) — #13: scan agent package, through three briefed rounds (PR #70)
- **Done:** [PR #70](https://github.com/DeusMaximus/TLG-IPAM/pull/70) closes #13, head `214fb82` (merges `main` at `9f47b28`: #59, #12, #15, #73). CI green. Agent 374, server 677 (674 on `main` + 3 new), Python 3.13.
  - `agent/`, package `tlg-ipam-agent`, httpx + icmplib + dnspython, hashed locks constrained to `api/requirements-dev.txt`:
    - Check-in loop with the spec §5.7 refusal rule (`agent_protocol.CHECKIN_RETRY_*`: 10–300 s jittered back-off; 13.5–15 min for refusals only the owner can clear). Only a 200 in the check-in shape queues a job.
    - Jobs one at a time. `guard.plan` refuses before any packet a job outside `ALLOWED_CIDRS`, with ranges outside its subnet, or over 4,096 addresses, and reports the refusal.
    - Paced ICMP sweep. PTR lookups sent query by query: one UDP query per server, at most 3 servers, EDNS(0)/1232, no retransmission, no TCP, truncated replies unused. Every datagram is paced by `MAX_PPS`; a lookup's 2 s start at its first slot.
    - 10-minute timeout gives `complete: false`; `arp`/`fdns` not run here also give `complete: false`.
    - HTTP: TLS always verified, https except to loopback; one 30 s deadline per request; results also capped by the lease; undecoded reads checked against 1 MiB before they are held.
    - Logs: no server text (status, code, validated counts); the token is redacted.
    - rtnetlink interfaces, at most `DUMP_MAX` held. Dockerfile: root in the container, `python -m`.
  - CI: `agent` job (pytest; image built and started with `--network none`); `locks` job checks the agent locks.
  - The one server rule change (`api/app/services/results.py`, round 3, owner's decision): an incomplete result set never clears a stored `rdns_name`; a name still replaces it. The wire is unchanged, so `PROTOCOL_VERSION` stays 1.
  - Docs: `AGENTS.md` (invariant 1 bullet, agent commands and locks, ingest rule); README "Scan agent"; brief §2.2, §3.11, §4.6, §7, §10; spec §5.4, §5.6, §5.7.
  - **Merged:** `de13ee9`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #13 closed. The fourth briefed round by GPT-6.1 Sol returned GO on `214fb82` with one P3, posted on the PR; it accepted the owner's server rule and confirmed with its own replays that names an incomplete scan never asked about now survive. Codex was clean on `214fb82`; Greptile last saw `cf65345`, which is optional after a GO at the current head. The orchestrator re-measured 374 agent and 677 server tests on the head and 677 on `main` after the merge, and confirmed the rule is guarded (two server and three agent end-to-end tests red without it). How the rounds went, since the order mattered: round 2 reviewed a head that moved under it, and its only blocker was already fixed; that second NO-GO went to the owner, who kept the worker on the issue. Round 3 found that an incomplete scan still cleared names it never looked up, because of an ingest rule from #11; the owner chose a server rule over leaving addresses out of the result set, which would have cost hosts their last-seen update on slow scans. The first round's review was posted by the orchestrator because the reviewer's own clean-up was refused by its sandbox; later briefs tell the reviewer to post first. Follow-ups filed on the owner's say-so: #75 (M2a) bounds a subnet's `dns_servers` and what a job carries; #78 (M2a, `briefed-review`) makes "no resolver to ask" an incomplete scan, not a complete one. Stated by the last review and worth carrying: a name kept by an incomplete scan can be stale, can raise a name finding, and can be accepted into a host; until #14, scans on an agent's own link are always incomplete. Not run, and the owner's to run: a real VLAN sweep, PTR lookups against the owner's DNS, TLS against the owner's certificate, the packet rate on the wire, and Python 3.11; commands are in the PR body. Live state after this merge: two PRs are open. #76 (#17, MCP tools) has a GO at `4ed7465` with three P3s (filed as #79) and the owner's approval to merge once its worker has merged this `main` in (one conflicted bullet in `AGENTS.md`). #77 (#19, findings UI) is at `1873ee8` with CI green and Codex clean, waiting on Greptile's re-review after the owner's retrigger. Also done the same day: #68 gained a scope-membership bullet from #19, and #26 and #28 a note on the order they ship in. Ready and not started: #14, #60, #65, #68, #75, #78, #79, #20. The `owner` issues #5 and #25 still wait on the owner.
- **Reviews:**
  - Codex: clean on `214fb82`; all Codex threads answered.
  - Greptile: last reviewed `cf65345`, both findings fixed and answered; no re-review needed.
  - Briefed rounds:
    - Round 1 (NO-GO `797e249`): DNS pacing, HTTP deadline, acknowledgments and logs; fixed in `dbb2f1c`.
    - Round 2 (NO-GO `ce60198`): low-rate PTR, the answer cap, truncated DNS replies; fixed in `9ea136b` and `e70d298`.
    - Round 3 (NO-GO `cfe8208`): incomplete scans clearing names, plus the no-append test; fixed in `214fb82`.
    - Round 4: can start now.
- **Decisions:** 17 Deliberate calls in the PR body. Rewritten: 12 (DNS, with which dnspython settings are tested and which are belt and braces) and 15 (deadline and bounded read). New: 16 (logs) and 17 (the name rule, replacing call 3 of PR #64). Consequence of 17: until #14, on an agent's own subnet names are only set or replaced, never cleared.
- **State:**
  - No schema change.
  - Server changes: the `agent_protocol.py` constants block and the one ingest rule.
  - Agent locks are reproducible with uv 0.13.6.
  - The agent test session deletes its temp directory.
- **Waiting on the owner:**
  - Briefed round 4.
  - #75 (a server-side limit on `dns_servers`).
  - Follow-ups to decide: an http-origin warning in the Add agent dialog; a resolver allowlist on the agent's host; pinning `python:3.13-slim` by digest.
  - The five not-run network checks in the PR body.
  - Merge.
- **Next:**
  - #14: ARP. Add `arp` to `protocol.SUPPORTED`, so complete scans mark addresses dead and clear names again. ARP requests take slots from the same `MAX_PPS` budget; report MACs.
  - #22 and #24: the wheel, publishing the image, aligning versions.

## 2026-11-16 — Claude Code (Opus 5.5) — #15: findings round-1 fixes (PR #72)
- **Done:** [PR #72](https://github.com/DeusMaximus/TLG-IPAM/pull/72), head `78a435b`: fix commit `d3ace54` on `f962a58`, plus a merge of `main` at `c6e579e`. CI green. 674 passed (597 + 77). `@codex review` posted after the push. Greptile has not re-reviewed `78a435b`.
  - R1 P2: findings carry a `fingerprint` (SHA-256 over key, `host_id`, `proposed`, 32 hex). Accept requires it; dismiss checks it only when sent. Dismissals are still matched on the four-part key (spec §9 unchanged).
  - R1 P3: a name accept makes the host's FQDN the PTR name, with a domain override in `proposed` when the PTR is outside the effective domain. `name_mismatch` compares FQDNs.
  - R1 P3: tests for recompute-only scope and randomised checks, dismissal matching, a competing writer, and a partial write before a refusal. 28 mutations, all red (table in the PR body).
  - The list route runs 9 statements at any size; a test pins it.
  - Docs: spec §5.5 ("What accepting writes", name rules, accept); brief §4.2 accept row and §4.7; `AGENTS.md` findings bullet. The `AGENTS.md` merge conflict kept `main`'s wording of three bullets.
  - **Merged:** `6ad221f`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #15 closed. What the PR ships as a whole, since the entry above covers only its last push: `services/findings.py` (findings computed on read inside the scan scope: `unknown_host`, `mac_missing`, `mac_mismatch`, `name_missing`, `name_mismatch`, `stale`), `GET /api/v1/subnets/{id}/findings`, `POST /api/v1/findings/accept` and `/dismiss` with per-record results and a cap of 1,000 records, accept through the hosts service under the write lock, the additive table `finding_dismissals`, and dismissals deleted with observations when an address leaves scope. The issue asked for automatic reviews only; the orchestrator added a briefed round because accept turns network-originated values into owner data. Round 1 by GPT-6.1 Sol was NO-GO on `f962a58` (the P2 and two P3s above) after both automatic reviews had passed it clean; round 2 was GO on `78a435b` with no findings, posted on the PR. Codex was clean on `78a435b`; Greptile did not re-review it, which is optional after a GO at the current head. The orchestrator re-measured 674 on the head and on `main` after the merge, confirmed the fingerprint comparison is guarded (six tests red without it), and checked the merge commit against both parents. Two notes from round 2 for later issues: a single-label PTR name stays a relative hostname, so its FQDN follows the subnet's domain and is not bound by the fingerprint; and #19 has to show when a proposal sets a domain override. Follow-up filed the same day on the owner's say-so: #75 (M2a), a bound on a subnet's `dns_servers` and on what a job carries, from the second briefed round on PR #70. Live state after this merge: one PR is open, #70 (#13, the agent package), at `9ea136b`. Its second briefed round returned NO-GO on the earlier head `ce60198`; the sole blocker there was already fixed in `9ea136b`. The owner chose to keep the worker on it: it is fixing two agent-side P3s (the answer limit checked after allocation; a truncated DNS reply accepted) and merging this `main`, and a short third round then covers everything since `ce60198`. Ready and not started: #17, #19, #65, #68, #75; #60 waits for #70 (both touch `agent_protocol.py`). The `owner` issues #5 and #25 still wait on the owner.
- **Decisions:** Deliberate call 3 changed (names keep the PTR as the FQDN); call 12 is new (fingerprint binding, a spec change). The reviewer's alternative for finding 2 (offer no automatic name for such a PTR) was not taken.
- **State:** green, nothing half-finished. The schema change is additive only: the new table `finding_dismissals`. Nothing under `web/` or `mcp_server.py` changed.
- **Waiting on the owner:** the second briefed round (the orchestrator starts it); retrigger Greptile on `78a435b`; the merge. Owner's call under §9: a dismissed `unknown_host` stays hidden when only its PTR name changes.
- **Next:**
  - #17: `list_scan_findings` returns `fingerprint` and `proposed`. Accepting over MCP goes through `create_host`/`update_host`, so it is not bound by the fingerprint.
  - #19: the UI sends `fingerprint` with accept.
  - #20: adds `fdns_mismatch` to `FindingType` and to `_evaluate`.

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
