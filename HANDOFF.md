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

## 2026-11-16 — Claude Code (Opus 5.5) — #51: structured 503 on a failed last_used_at write; lifespan in the /mcp refusal tests (PR #62)
- **Done:** [PR #62](https://github.com/DeusMaximus/TLG-IPAM/pull/62) closes #51, head `b01cd1a` (one commit on `main` at `1c6f1d5`). CI green (5 checks). Codex: no findings on `b01cd1a`. Greptile: 5/5, no findings, on `b01cd1a`. Briefed round owed (`briefed-review`), not started. Merge is the owner's.
  - `services/agents.authenticate`: a due `last_used_at` commit that raises `OperationalError` is rolled back and answered `503 UNAVAILABLE` ("Database busy; retry the request") through the new `errors.unavailable`, not a bare 500. Check order, the 10 s granularity and the read-only path inside the interval are unchanged.
  - `test_agents.py` 49 → 54: a real-lock test (`BEGIN IMMEDIATE` on a second connection, waits out the live `PRAGMA busy_timeout`, 5.2 s); a read-only-inside-the-interval check under the lock; refused revoked, disabled-agent and `enrol` tokens get 403 under the lock without waiting and leave `last_used_at` alone; `test_agent_token_is_refused_on_mcp` now runs inside the lifespan.
  - Docs: spec §4.2 (new bullet), brief §4.1 second paragraph, brief §4.3 (what `503 UNAVAILABLE` means), `AGENTS.md` agent-token bullet.
  - Measured: backend 420 → 425 on Python 3.13, 35.4 s. Four mutations each red (table in the PR body); the old harness failed the `/mcp` mutation on `RuntimeError: Task group is not initialized`, the new one on `assert 200 == 401`.
  - **Merged:** `3c5692c`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #51 closed. Before the merge, the briefed round by GPT-6.1 Sol returned GO on `b01cd1a` with three P3s, posted on the PR, and the orchestrator re-measured 425 on the head with the fix's mutation red, and 425 again on `main` after the merge. The P3s went into existing issues on the owner's say-so, with no new issue filed: #59 now also owns a neutral 503 message with a server-side log line, a same-session test of the rollback, and the 503 for a lock that outlasts the timeout later in the check-in (still a bare 500 today); #13 now owns the agent's back-off rule for `429` and `503`. Live state after this merge: PR #63 (#16, the Agents page) is open at `b89283f` with CI green and Codex clean; Greptile reviewed only `0f0fca5`, both its findings are fixed in `b89283f`, and it waits on a Greptile re-review of that head, then the owner's merge. The orchestrator re-measured its lint, 29 tests and build. One worker is in flight on #11 (Opus 5.5, branch `11-results-ingest`); no PR yet. Ready and not started: #18, #61; #59 waits for #11 (same file), #60 follows #11. The `owner` issues #5 and #25 still wait on the owner.
- **Decisions:** six under Deliberate calls in the PR: catch all `OperationalError` on that commit; refuse the request rather than serve it without the heartbeat; a 503 is reachable only with a usable token; no `Retry-After` (would need `main.py`, owned by #11); the explicit rollback is defensive (`get_session` closes the session anyway, and no mutation of it goes red); one real-lock test, stale case only.
- **State:** green, nothing half-finished. No schema, dependency or `auth.py` change, so the `test_auth.py` mutation anchors are untouched. A lock held past the busy timeout after authentication, in `jobs.checkin`, is still a bare 500 (#11's file). No file owned by another issue was touched.
- **Waiting on the owner:** start the briefed round on `b01cd1a` (reviewer outside the Claude family); merge PR #62.
- **Next:** #59 moves the token-use write into the check-in claim's transaction and makes it conditional in SQL. It must keep this PR's three acceptance tests green: `test_failed_last_used_write_is_a_structured_503`, `test_refused_token_is_403_and_never_reaches_the_write`, `test_agent_token_is_refused_on_mcp`. #59 must not start until #62 merges. Whichever of #11 and this PR merges second rebases; expected overlap is the `AGENTS.md` bullets and brief §4.1/§4.3 only.

## 2026-11-16 — Claude Code (Opus 5.5) — #10: agent check-in, job leasing, request-scan (PR #56)
- **Done:** [PR #56](https://github.com/DeusMaximus/TLG-IPAM/pull/56) closes #10, head `af66ca4` (`main` at `dc8924d` merged into the reviewed `c0b58d5` with a merge commit; the one conflict was `AGENTS.md`'s invariant bullets: `main`'s owner-key bullet, this branch's agent-token and wire-contract bullets). Nothing under `api/` differs from `c0b58d5` but `main`'s `config.py` and `test_startup.py`; the brief and spec merged cleanly with both sides intact. CI green. Codex: no issues on `af66ca4` (four P2s fixed on earlier heads). Greptile: reviewed `b0a507f` only and needs the owner's retrigger. Briefed review: round 1 NO-GO on `fe216d8` (six findings, all fixed in `b2a6254`); round 2 GO on `c0b58d5` with three P3s for a follow-up issue. Merge is the owner's.
  - `api/app/agent_protocol.py`: the wire contract (`PROTOCOL_VERSION = 1`; check-in request, interface, job and response shapes; caps on agent-reported fields, with `fullmatch` for capability tokens and MACs).
  - `services/jobs.py`: `checkin` runs the rate limit first (`_claim`: one per agent every 10 s, a check on the loaded row then a compare-and-set, `429` before any agent-row write), then the protocol check (`409`), then validation (`422`, recorded in `last_error` by field name only), then stores the fields and returns at most one job. Fair ordering by last attempt; `arp` dropped off-link, `fdns` held back until #20; the lease is a compare-and-set on the target keeping `job_methods`, with a copy on the agent that `agent_busy` reads; each agent keeps its newest 100 `source="agent"` audit entries.
  - `services/agents.authenticate` writes `last_used_at` at most every 10 s per token. This is throttled, not a compare-and-set, and can still write on a refused check-in (round 2 finding 1).
  - `services/changes.assistant_view`: MCP's `list_changes` withholds agent-reported values in `agent` snapshots.
  - `services/scans.request_scan`: 15-minute cooldown per subnet, as a compare-and-set.
  - Routes: `POST /api/v1/agent/checkin` (`require_agent` on the router, mounted outside the owner-key loop) and `POST /api/v1/subnets/{id}/scan` (`202`).
  - Schema, additive: `agents.current_job_id`, `agents.job_issued_at`, `scan_targets.job_methods`, all nullable.
  - Docs: brief §2.3, §3.6, §3.8–3.10, §4.1 (second paragraph), §4.2, the new §4.5, §5.2, §7, §10; spec §5.1, the §5.4 tables, §5.7 (including the results contract); `AGENTS.md` audit invariant, agent-token bullet and the new wire-contract bullet.
  - Tests: 399 passed on `af66ca4` (296 on `main` + 103 in `test_checkin.py`).
  - **Merged:** `57fcd94`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #10 closed. Before the merge, Greptile re-reviewed `af66ca4` after the owner's retrigger (5/5, no new findings). The orchestrator checked the merge commit `af66ca4` against both parents (identical to a mechanical merge outside `AGENTS.md`) and ran no third briefed round on it, since it carries no hand-written code. Follow-ups filed, on the owner's say-so: #59 (M2a, `briefed-review`) for round 2's three P3s and the wording, and #60 (M2a, `briefed-review`) for the shared body-size cap; both listed under **Next** below as still to file. With #50 also on `main`, the suite is 420. Live state after this merge: no PR is open and no worker is in flight. Ready to start: #11 (results ingest), #51, #16, #61; #59 and #51 change the same function and must not run together, and #60 should follow #11 or #11 should use its cap. The `owner` issues #5 and #25 still wait on the owner.
- **Decisions:** 19 under Deliberate calls in the PR. Since the first round: the agent holds its own copy of the lease (14); the audit is bounded by a rate limit and a retention cap (8, 16); the lease keeps the issued methods and the results contract checks both lease copies (17); agent text is withheld over MCP rather than narrowed on the wire (18); fair rotation without backoff (19); the cooldown lives on the target (15). The PR body states what a `429` actually guarantees: no agent-row write and no audit entry, while the token-use write is throttled, not eliminated.
- **State:** green, nothing half-finished. `conftest.py` lifts the check-in rate limit for every test, and the rate-limit tests restore it. No body-size cap on any route (JSON is parsed before auth, as on `main`). A scan request on a target whose agent is disabled waits until the agent is enabled.
- **Waiting on the owner:** retrigger Greptile on `af66ca4`, then merge.
- **Next:**
  - File the follow-up for round 2's three P3s:
    - Write the token heartbeat in the same transaction as a successful claim, conditional in SQL elsewhere, and cover the out-of-phase and concurrent cases.
    - Refresh every agent column after `_claim`, so audit snapshots and the actor do not carry a stale name or state.
    - Make the race test for the claim deterministic.
    - Fix the brief wording: §3.6 still says "append-only", and §3.8 says refused check-ins advance `last_checkin_at`.
  - File a follow-up for a body-size cap shared by every route.
  - #11: accept a result only when its `job_id` equals both the target's and the agent's `current_job_id`; check addresses against scope at ingest; judge liveness and MACs by `job_methods`; clear both lease copies in one transaction; set `last_scan_at` to `job_issued_at` and leave `scan_requested_at` alone; add the body cap; decide where a results `error` lives.
  - #17: wrap `scans.request_scan`.
  - #20: fill `fdns` and remove the drop in `jobs.job_methods`.
  - #51: test the `503` on the real check-in route.
