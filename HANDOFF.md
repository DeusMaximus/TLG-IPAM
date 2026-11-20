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

## 2026-11-20 — Claude Code (Fable 5.2), orchestrator — waves 1 and 2: six PRs merged; #99 and #85 in flight
- **Done:** six PRs squash-merged on the owner's go-ahead, all implemented by Opus 5.5. The remote branches are deleted.
  - Wave 1, from `main` at `b481075`: #82 (#65) `3cbfa63`, #83 (#79) `1987465`, #84 (#14) `7014b58`. Their worker entries are in the archive (`grep -n '^## ' .agents/handoff/2026-11.md`).
  - Wave 2: #95 (#75) `e76471a`, #96 (#78) `aa95f64`, #98 (#68) `5155f8c`. Their worker entries are below.
  - Reviews (Codex, GPT-6.1 Sol): briefed rounds on #83, #84 (each NO-GO, then GO) and #96 (GO, then GO again after the orchestrator sent two of its P3s back, because one was a case #78 itself asks to close). One outside read each on #82, #95 and #98 in place of Greptile, which reviewed nothing (monthly cap until 2026-11-22; its MCP `trigger_code_review` is still untried).
  - Issues filed, each on the owner's go-ahead: #85 to #94, #97, #99, #100, #101. That covers every follow-up from today's reviews and everything earlier entries listed as unfiled (from #80: #89, #90, #91; from #13: #92, #93, #94).
- **Decisions (the owner's):**
  - #84 merged before its real-VLAN acceptance line was run; that check and the five left from PR #70 are #88.
  - #84's deliberate calls merged as written. The 2026-11-16 interim rule ends: a complete scan on the agent's own link clears a stored PTR name.
  - `list_scan_findings` returns `next_cursor`, `returned`, `note`, `findings` in place of `findings`/`total`.
  - A subnet lists at most eight DNS servers and a job carries the first three (#75).
- **State:** green. `main` is `5155f8c` plus hand-off commits. Measured on `main` after the last merge: 804 server tests, 493 agent tests, 103 web tests, lint and build pass. No PR is open.
  - In flight, started after this commit: #99 on the agent lane and #85 on the `schemas.py` lane, one worker each.
  - ARP is on by default: a new target's methods are `icmp`, `arp`, `rdns`, and the server issues `arp` whenever the agent has an interface on the subnet. No agent built from this `main` has run on a real network.
  - Codex's state for a PR is in its summary comment, as `codex-review-state:v1` JSON (`commit_sha`, `has_any_findings`, `status`); a clean pass is also a plain comment naming the commit. CodeRabbit posts a notice on each PR that it skips the repository; ignore it.
  - The worktrees of merged branches and finished reviews under the T3 worktree directory on this machine are still there; none was removed.
- **Waiting on the owner:**
  - #88, on a test host on a real VLAN. Until it is done, a target on a subnet that matters can be set to `icmp`, `rdns`.
  - #100: whether a scan in which no DNS server gave a usable answer keeps clearing stored names. The issue holds the four options.
  - #91: whether the 2 MiB bound on a bulk import stands, or the dialog batches, or fields are bounded.
  - Three lines proposed for the worker brief in `.agents/orchestration.md`, used in every brief today but not added to the file: how a clean Codex result arrives, that a worker never changes git config, and that a worker never edits `HANDOFF.md`.
  - #5 and #25 (`owner` label).
- **Next:** the rest of M2a. Every issue below is ready except where it says so.
  - `agent/` (`probe.py`, `scan.py`), one after the other, each with a briefed round: #99 (in flight), then #87. #100 after the owner decides; #93 is a stretch in M2b.
  - `schemas.py` and the hosts service: #85 (in flight).
  - No file in common with those two lanes or each other, so any can run beside them: #101 (`services/scans.py`, the scan-targets route, `ScanSettings.jsx`), #97 (`ipnet.py`, `services/subnets.py`, `services/jobs.py`, `agent/tests/test_answer_limit.py`), #89 (`body_limit.py`, briefed round).
  - #20 (stretch) last; it must measure its largest `fdns` result set against `body_limit.BODY_MAX` and add its names to `test_answer_limit.py`.
  - Then M2b (#21–#24, with #92 and #94 beside #23 and #24) and M3 (#26–#32). #86 and #90 close before #26 exposes `/mcp`; no OAuth grant may be issued before #28's write-scope checks are in force (noted on #26 and #28).

## 2026-11-20 — Claude Code (Opus 5.5) — #68: scan targets report what their agent can run (PR #98)
- **Done:** [PR #98](https://github.com/DeusMaximus/TLG-IPAM/pull/98) closes #68, head `7c4349c`, cut from `main` at `e76471a`. CI green on `7c4349c`; Codex clean on `7c4349c`; Greptile not run (monthly cap); CodeRabbit skips the repo (under 10 stars).
  - `ScanTargetOut.runnable_methods` (new `jobs.runnable_methods`, called from `scans.target_out`): `jobs.job_methods` applied on read to the agent's last reported interfaces. `[]` = cannot be scanned by this agent; null = not known (no agent, or `agent.interfaces is None`). Never stored.
  - Scope ranges on the scan-target answer gain `start_int`/`end_int` (`schemas.ScopeRangeOut`, a subclass of `agent_protocol.ScanRange`); a job's `ranges` are unchanged.
  - UI: `ScanSettings` shows "MAC collection unavailable" / "Cannot be scanned by this agent" from `utils/scans.agentLimit` whether or not a job is out (`macUnavailable` removed), and `last_scan_error` as text under the last scan. `utils/findings.scopeChecker` compares a host's `ip_int` with the integer bounds, so the host table reads "not scanned" for hosts in an unscanned pool or an excluded range.
  - Web test fixtures no longer compute `ip_int` from address strings (literal integers).
  - Docs: brief §3.10, §4.2, §6.1, §6.2; spec §5.8; `AGENTS.md` (a sentence on the scan-scope bullet; a new bullet: what a target's agent can run is decided by `job_methods` alone, REST only).
  - **Merged:** `5155f8c`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #68 closed. An outside read (Codex, GPT-6.1 Sol) ran in place of Greptile after this entry was written: GO at `7c4349c` with one P3, filed as #101 with its note on stale interfaces (the list now reads one agent per target; the two messages do not say how old the agent's report is). It overruled none of the eight calls, and found none of the new fields in ten real MCP calls. Codex was clean on `7c4349c`. The orchestrator re-measured: 804 server and 103 web tests, lint and build on the head; two mutations of its own each turned tests red; 804 server and 493 agent merged with #96.
- **Decisions (PR body, Deliberate calls 1–8):** scope membership as integer bounds on the REST-only scan-target answer, not per host (host answers are on MCP); the field is the list of methods, not an enum; "not known" keys on `interfaces is None`, not `last_checkin_at`; a disabled agent is judged by its last interfaces.
- **State:** green. Server 804 tests (795 on `main`), agent 465 (no agent file touched), web 103 (94); lint and build pass. No schema, protocol or lock change.
  - Touches nothing #78 (PR #96) owns. Whichever of the two merges second takes `main` in with a merge commit; the overlap is at most `AGENTS.md` and the brief, in different bullets and sections.
  - The README's `ALLOWED_CIDRS` row ("the Scanning section shows why a job was refused") is now true.
- **Waiting on the owner:** merge of #98.
- **Next:** #85 (datetime follow-ups in `schemas.py` and the hosts service) can start once #98 merges.

## 2026-11-20 — Claude Code (Opus 5.5) — #78: no resolver to ask is not a completed lookup (PR #96), briefed round 1 answered
- **Done:** [PR #96](https://github.com/DeusMaximus/TLG-IPAM/pull/96) closes #78, head `3b953e3`, cut from `main` at `270b47d`.
  - `628c79c`:
    - `probe.DnsResolver.servers(job_servers)` gives the job's servers, else the host's `resolv.conf`, read for every job and never cached. Only IP addresses are kept, filtered before the `MAX_DNS_SERVERS` cut.
    - With no server, `scan.run` makes no lookup, sends no DNS packet, and reports `complete: false` with `NO_DNS_SERVER`.
    - A lookup that sent nothing raises `ProbeError`.
  - Briefed round 1 (Codex, GPT-6.1 Sol): GO at `628c79c` with three P3s. `3b953e3` answers it:
    - (1) A lookup that runs out its own 2 s after only unsent attempts raises "no query sent". A lookup the job's deadline stops before its first attempt is still reported as the PTR timeout.
    - (3) The first `ProbeError` cancels the lookups in flight (`_Pool(cancel_on_failure=True)`, PTR lookups only); the ICMP sweep still drains.
    - A built-in `TimeoutError` test holds the order of the two `except` clauses.
    - Wording fixed in spec §5.2, `AGENTS.md`, the brief and the code comments.
  - Docs across both commits: spec §5.2, §5.6, §5.7; the brief; both READMEs; the `AGENTS.md` agent bullet.
  - Tests: agent 491 (463 on `main`); server 789, with no server file changed. Six mutants caught at `628c79c` and five more at `3b953e3`. The Docker `--network none` runs and the commands are in the PR body.
  - CI green and Codex clean ("Didn't find any major issues") on `3b953e3`. Greptile not run (monthly cap).
  - **Merged:** `aa95f64`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #78 closed. The second briefed round ran after this entry was written: GO at `3b953e3` with two P3s (a datagram already handed to the socket can still go out after the cancellation; two pool mutations survive the suite). Those, the once-failed failover test and the `resolv.conf` read on the event loop are filed as #99. Round 1's finding 2 (call 6) is #100, a decision for the owner. Showing `last_scan_error` in the UI came with #68. Codex was clean on `3b953e3`. The orchestrator re-measured: 491 agent tests on the head, 493 agent and 795 server merged with `main`, and at `628c79c` the built image with an empty `resolv.conf` and no network (complete with no name on `main`, incomplete with the fixed error on the PR).
- **Decisions:**
  - Cancel, not drain, for lookups in flight (call 7): nothing is sent after the reported failure.
  - Finding 2 / call 6 (a total resolver outage still clears names) stands in this PR by the orchestrator's decision. It is for the owner, as its own issue.
  - No `protocol.py`, `test_contract.py`, `PROTOCOL_VERSION`, schema or lock change.
- **State:** green on `3b953e3`. A short second briefed round is owed. `test_failover_to_the_next_server_is_paced_with_the_icmp_sweep[::1]`, a test from before this PR, failed once locally and passed 23 times after.
- **Waiting on the owner:**
  - The second round, then the merge.
  - The issue for call 6 / finding 2.
  - A follow-up for the web UI not showing `last_scan_error`, though the README's `ALLOWED_CIDRS` row says the Scanning section shows it.
  - The synchronous `resolv.conf` read on the event loop (a FIFO or stalled mount; true on `main` too).
- **Next:** #87 (the pacer and send loops) builds on this `probe.py` and `scan._Pool`. #75 is in flight on the server; whichever of #75 and #96 merges second takes `main` in with a merge commit.

## 2026-11-20 — Claude Code (Opus 5.5) — #75: bound a subnet's dns_servers and what a job carries (PR #95)
- **Done:** [PR #95](https://github.com/DeusMaximus/TLG-IPAM/pull/95) closes #75, head `6352707`.
  - Job bound: `agent_protocol.DNS_SERVERS_MAX = 3`; `Job.dns_servers` has `max_length` set to it. `services/jobs` sends `subnet.dns_servers[:3]`, the first three in the order listed.
  - Subnet bound: `schemas.SUBNET_DNS_SERVERS_MAX = 8`, checked in `services/subnets._check_dns_servers` on create and on any update that sends `dns_servers`. Over it: `422` "dns_servers: a subnet may list at most 8 DNS servers, not N".
  - New `agent/tests/test_answer_limit.py` builds the largest check-in answer through the real route and the agent's real client. 104,978 bytes; 106,762 padded to the longest addresses; limit (`RESPONSE_MAX`) 1,048,576; the unbounded pre-#75 answer was 1,153,843. Shown red with the cut removed.
  - `test_contract.py` checks `probe.MAX_DNS_SERVERS == agent_protocol.DNS_SERVERS_MAX`.
  - Docs: brief §3.2, §4.5 (new paragraph "The answer fits the agent") and one acceptance line; spec §5.7; the `AGENTS.md` wire-contract bullet.
  - **Merged:** `e76471a`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #75 closed. The owner kept eight as the subnet bound. An outside read (Codex, GPT-6.1 Sol) ran in place of Greptile after this entry was written: GO at `6352707` with one P3, filed as #97 (a subnet's CIDR is stored as typed, so a zero-padded prefix reaches the job; the "largest answer" test assumes the canonical spelling). Codex was clean on `6352707`. The orchestrator re-measured: 795 server and 465 agent tests on the head, and with the job's cut removed three tests go red (the answer-limit test on the model's own `max_length`, and on the size assertion only when that bound is removed too; the PR body now says so).
- **Decisions (deliberate calls on the PR, for the owner to overrule):**
  - Subnet bound 8, job bound 3: the field records what a subnet hands out; a job carries what the agent uses.
  - A subnet stored with a longer list keeps it. An update that leaves the list out or resends it unchanged is applied; changing it must meet the bound.
  - No `PROTOCOL_VERSION` raise: same shape, and the server now sends a subset of what it could before.
  - The agent does not start refusing a job with more than three servers.
- **State:** green.
  - CI passed on `6352707` (backend, agent, frontend, locks).
  - Codex completed on `6352707` with no findings; Greptile not run (monthly cap).
  - Server 795, agent 465 tests. Web and locks untouched. No schema change.
- **Waiting on the owner:** merge PR #95. Whether 8 stands as the subnet bound or becomes 3.
- **Next:**
  - #78 and #75 both edit `AGENTS.md` (different bullets) and the brief. The PR that merges second takes `main` in with a merge commit.
  - #20 must add its `fdns` names to the bound in `test_answer_limit.py`, which asserts `fdns == []` until then.
  - #68 and #85 follow in `schemas.py`.

## 2026-11-20 — Claude Code (Fable 5.2), orchestrator — wave 1 finished: #65, #79 and #14 merged; no work in flight
- **Done:** three PRs squash-merged on the owner's go-ahead, each with its worker's entry below: #82 (#65) as `3cbfa63`, #83 (#79) as `1987465`, #84 (#14) as `7014b58`. All three ran at once from `main` at `b481075`, implemented by Opus 5.5. The remote branches are deleted.
  - Briefed rounds (Codex, GPT-6.1 Sol): #83 and #84 each NO-GO, then GO on the fix commits. #82 got one Sol round, GO, as the second outside read: Greptile reviewed none of the three (monthly cap until 2026-11-22; its MCP `trigger_code_review` is still untried).
  - #84 conflicted with #83 in `AGENTS.md` only (adjacent bullets). Its worker took `main` in with a merge commit; the orchestrator checked the merge mechanical and Codex re-reviewed it.
  - Issues filed, on the owner's go-ahead: #85 (two datetime follow-ups from #82), #86 (`list_scan_findings`: bound the work per call and the size of one finding, before `/mcp` is exposed), #87 (the agent's send rate: late-wake bursts, kernel ARP outside `MAX_PPS`, no yield in a late send loop), #88 (`owner`: every real-network check on the scan agent).
- **Decisions (the owner's):**
  - #84 merged before its second acceptance line (a real VLAN) was run. That check, and the five left from PR #70, are #88.
  - #84's deliberate calls merged as written: the agent's own address is reported alive with its MAC and no frame; a full send queue fails the send and the scan reads incomplete; a scan 30 s past its timeout is cancelled and reports no observations; the #13 pool now cancels its work in flight.
  - The 2026-11-16 interim rule ends: a complete scan on the agent's own link now clears a stored PTR name.
  - `list_scan_findings` returns `next_cursor`, `returned`, `note`, `findings` in place of `findings`/`total`. A finding over the page budget still comes whole, alone on its page.
- **State:** green. `main` is `7014b58` plus this commit; CI is green on `7014b58`. Measured on `main` after the last merge: 789 server tests, 463 agent tests, 94 web tests, lint and build pass. No PR is open; no worker and no review is running.
  - ARP is on by default: a new target's methods are `icmp`, `arp`, `rdns`, and the server issues `arp` whenever the agent has an interface on the subnet. No agent built from this `main` has run on a real network.
  - Codex's state for a PR is in its summary comment, as `codex-review-state:v1` JSON (`commit_sha`, `has_any_findings`, `status`); a clean pass is also a plain comment naming the commit.
  - The worktrees of merged branches and finished reviews under the T3 worktree directory on this machine are still there; none was removed.
- **Waiting on the owner:**
  - #88, on the test host on a real VLAN. Until it is done, a target on a subnet that matters can be set to `icmp`, `rdns`.
  - Three follow-ups from #80, still not filed: (1) a `Content-Length` padded with thousands of zeroes answers 500 with a traceback before authentication under httptools, and one with trailing whitespace misses the unread refusal (`body_limit._declared_length`); (2) the owner-key line in `AGENTS.md` says every `/mcp` refusal is an RFC 6750 401, and a 413 is not; (3) the cap bounds one body, not aggregate memory or upload duration for unauthenticated chunked bodies, which matters before #26.
  - Whether the 2 MiB bound on a bulk import stands as shipped. #86 now carries the related question of a per-field limit on `domain` and `notes`.
  - Three lines proposed for the worker brief in `.agents/orchestration.md`, used in this wave's briefs but not added to the file: how a clean Codex result arrives, that a worker never changes git config, and that a worker never edits `HANDOFF.md`.
  - #5 and #25 (`owner` label).
  - Three optional follow-ups the #13 worker raised, not filed: a warning in the Add agent dialog when the UI is open over plain HTTP; a resolver allowlist on the agent's host; pinning the agent's base image by digest.
- **Next:** wave 2, all in M2a.
  - `agent/`, one after the other, each with a briefed round: #78 (no resolver is not a completed lookup), then #87.
  - `schemas.py`, one after the other: #75, #68 (also `web/`), and #85 (also the hosts service). A new datetime field in a response model must be `UtcDatetime`; the test from #65 fails otherwise. An agent lane and a schema lane can run together.
  - #20 (stretch) last; it must measure its largest `fdns` result set against `body_limit.BODY_MAX`.
  - Then M2b (#21–#24) and M3 (#26–#32). #86 closes before #26 exposes `/mcp`; no OAuth grant may be issued before #28's write-scope checks are in force (noted on #26 and #28).
