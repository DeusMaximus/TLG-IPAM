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

## 2026-11-16 — Claude Code (Sonnet 5.5) — #61: start-up test ignores proxy and certificate settings (PR #66)
- **Done:** [PR #66](https://github.com/DeusMaximus/TLG-IPAM/pull/66) closes #61, head `de48ed8` (one commit on `main` at `8f02697`). Tests only: `api/tests/test_startup.py`; no file under `api/app/`, no document, no schema change.
  - The three loopback requests in `test_uvicorn_starts_and_serves_with_a_real_key` share one `httpx.Client(trust_env=False)`.
  - The test sets `SSL_CERT_FILE` (a missing file), `HTTP_PROXY=http://127.0.0.1:9` and empty `NO_PROXY`/`no_proxy` itself, after the child has started, so the child does not inherit them and every default run guards the fix. With `trust_env=False` removed and nothing set outside, it fails with `FileNotFoundError`.
  - Both acceptance commands fail on `main` (1 failed, 33 passed) and pass here (34 passed); output is in the PR body. Backend suite 425 passed on Python 3.13, unchanged.
  - **Merged:** `c15ce8f`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #61 closed. Both automatic reviews covered `de48ed8`, the only commit, with no findings. The orchestrator ran both acceptance commands on the head (34 passed each), the suite (425), and the mutation (default `trust_env`: the test fails), and 425 again on `main` after the merge. Also filed today, on the owner's say-so: #65 (M2a, `bug`), API datetimes carry no UTC offset after a read and the older views show them as local time. Live state after this merge: two PRs are open. #67 (#18, subnet scanning settings) is at `f8d7cf1` and ready for the owner's merge: CI green, Codex clean and Greptile 5/5 on that head, five findings fixed, and lint, 51 tests and build re-run by the orchestrator. #64 (#11) is at `cb83075` with CI green, Codex clean, Greptile 5/5 and 533 passing; its briefed round by GPT-6.1 Sol is running (brief at `.dev/11/review-brief-1.md`). No worker is building. Ready and not started: none that is free of #11's files; #59, #60 and #65 wait for #64 to merge. Proposed to the owner and not yet filed: a field on the scan-target response saying whether the agent can scan the subnet and whether ARP is available, which #18 needs for two of its messages. The `owner` issues #5 and #25 still wait on the owner.
- **Decisions:** one shared client rather than per-call `trust_env=False`; hostile settings set in the test rather than left to the environment; set after the child starts. All three are listed under Deliberate calls in the PR.
- **State:** CI green on `de48ed8` (backend, frontend, locks). Codex: no findings. Greptile: 5/5, no findings. No fix commit, so no review needs a retrigger. CodeRabbit skipped (below its star threshold); it is not one of the required reviews. Not owed a briefed round (tests only, no auth/token/ingest/packet code).
- **Waiting on the owner:** merge.
- **Next:** nothing from this issue. `ALL_PROXY` and the `REQUESTS_CA_BUNDLE`/`CURL_CA_BUNDLE` spellings are not set by the test; `trust_env=False` covers them.

## 2026-11-16 — Claude Code (Opus 5.5) — #16: Agents page (PR #63)
- **Done:** [PR #63](https://github.com/DeusMaximus/TLG-IPAM/pull/63) closes #16, head `b89283f`. Frontend only, no `api/` change, no schema change.
  - New: `web/src/pages/Agents.jsx` at `/agents` (sidebar item under Dashboard). Each row shows a status dot from `last_checkin_at`, the version, hostname, platform, interfaces, the subnets covered (from `/scan-targets`), the token prefix or "revoked", and `last_error`. Row actions: Disable/Enable, Revoke and Delete, the last two with confirmations. The list refreshes every 30 s.
  - New: `web/src/components/AddAgentModal.jsx`. Name the agent, then see the `tlga_…` token once with the M2a container command.
  - New: `web/src/utils/agents.js` with `parseServerTime`, `agentStatus`, `containerCommand` and `AGENT_IMAGE`.
  - Changed: `Modal.jsx` gains `role="dialog"` and the `closeOnBackdrop` and `maxWidth` props.
  - Brief: §6.2 has the Agents view; §7 has my lines only.
  - Tests: 29 frontend (6 + 23 new); backend 420, unchanged.
  - **Merged:** `7103b4b`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #16 closed. Before the merge, Greptile re-reviewed `b89283f` after the owner's retrigger (5/5, both findings fixed). The orchestrator re-ran lint, the 29 tests and the build on the head and on `main` after the merge, and confirmed the token-once test goes red when the dialog is made to keep the token in session storage. The worker's run against a real backend (curl and headless Chromium, on `0f0fca5`) was not repeated. Follow-up proposed to the owner and not yet filed: the API returns datetimes read back from SQLite without an offset (`/changes` `ts`, the agents list), and `ActivityItem`, `HostDrawer` and `HostTable` parse them as local time; the orchestrator confirmed the offset-less values against a running app. Live state after this merge: one PR is open, #64 (#11), at `373f94d` with CI green, Codex clean and 522 passing; Greptile is re-reviewing that head after the owner's retrigger, and the briefed round (brief at `.dev/11/review-brief-1.md`) starts when it has. No other worker is in flight. Ready and not started: #18, #61; #59 waits for #11 (same file), #60 follows #11. The `owner` issues #5 and #25 still wait on the owner.
- **Decisions:** 8 under Deliberate calls in the PR. The main ones:
  - The token lives only in the dialog's state, and no close path works while create is in flight.
  - Offset-less server datetimes are read as UTC, because SQLite drops the offset on reads.
  - The M2a command is the spec's M2b shape with `IPAM_AGENT_TOKEN` and no volume. The image is `ghcr.io/deusmaximus/tlg-ipam-agent:<server version>`, in one constant that #24 should confirm.
  - Status thresholds: online within 3 min, late within 15 min, then offline.
- **State:** CI green on `b89283f`.
  - Codex: one P2 on `0f0fca5`, fixed; no findings on `b89283f`.
  - Greptile: P1 and P2 on `0f0fca5`, both fixed and replied to on their threads; it has not reviewed `b89283f`.
  - Checked by hand on `0f0fca5` with `curl` and headless Chromium against `API_KEY=dev-key`; details in the PR body.
- **Waiting on the owner:** retrigger Greptile on `b89283f`, then merge.
- **Next:**
  - File a follow-up issue: offset-less datetimes are read as local time in `ActivityItem` and `HostTable`. Fix it either by having the API emit offsets or by using `parseServerTime` everywhere.
  - #23 builds on this page: guided install, enrolment, re-enrol, the version flag.
  - #24 should confirm or change `AGENT_IMAGE` in `web/src/utils/agents.js`.
  - Whichever of #11 / #51 / #63 merges second rebases. The overlap with this PR is the brief's §7 only.
