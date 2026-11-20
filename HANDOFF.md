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

## 2026-11-21 — Claude Code (Fable 5.2), orchestrator — twelve PRs merged since 2026-11-20; #107, #97 and #89 in flight
- **Done:** twelve PRs squash-merged on the owner's go-ahead, eleven implemented by Opus 5.5 and one (#108) by Sonnet 5.5. The remote branches are deleted. Worker entries are below or in the archive (`grep -n '^## ' .agents/handoff/2026-11.md`).
  - Wave 1, from `main` at `b481075`: #82 (#65) `3cbfa63`, #83 (#79) `1987465`, #84 (#14) `7014b58`.
  - Wave 2: #95 (#75) `e76471a`, #96 (#78) `aa95f64`, #98 (#68) `5155f8c`, #102 (#85) `df336cb`, #103 (#99) `c34feae`, #106 (#104) `8120cd2`, #108 (#105) `b5dd357`, #109 (#101) `e84ff26`, #110 (#87) `d06018e`.
  - Reviews (Codex, GPT-6.1 Sol): briefed rounds on #83 and #84 (each NO-GO, then GO), on #96 and #103 (each GO, then GO again after the orchestrator sent P3s back: on #96 one was a case the issue itself asks to close, on #103 both were in code the PR added and one loosened the invariant tripwire), and on #106, #109 and #110 (each GO; #109 was briefed as one because it rewrote the scope reader). One outside read each on #82, #95, #98, #102 and #108 in place of Greptile, which reviewed nothing (monthly cap until 2026-11-22; its MCP `trigger_code_review` is still untried).
  - Issues filed, each on the owner's go-ahead: #85 to #94, #97, #99 to #101, #104, #105, #107, #111. That covers every follow-up from today's reviews and everything earlier entries listed as unfiled (from #80: #89, #90, #91; from #13: #92, #93, #94).
- **Decisions (the owner's):**
  - #84 merged before its real-VLAN acceptance line was run; that check and the five left from PR #70 are #88.
  - #84's deliberate calls merged as written. The 2026-11-16 interim rule ends: a complete scan on the agent's own link clears a stored PTR name.
  - `list_scan_findings` returns `next_cursor`, `returned`, `note`, `findings` in place of `findings`/`total`.
  - A subnet lists at most eight DNS servers and a job carries the first three (#75).
  - #103 merged with a known gap in the tripwire (an alias of `os` got a write past its `os.open` check); #104 closed it the same day by removing the `os.open` exception. No agent module opens a file itself: it checks the path with `os.stat` and dnspython opens it.
  - The owner asked for every ready M2a issue to be started (2026-11-20, after #106).
- **State:** green. `main` is `d06018e` plus hand-off commits; CI is green on `d06018e`. Measured on `main` after the last merge: 824 server tests, 650 agent tests, 111 web tests, lint and build pass. No PR is open.
  - In flight, started after this commit, one worker each: #107 (agent tests, Sonnet 5.5), #97 (subnet CIDR spelling, Opus 5.5), #89 (body cap, Opus 5.5, briefed round).
  - Since #87 the packet budget bounds when a packet is handed to the kernel, with no catching up: a scan takes longer than before (254 addresses at 200 pps: 5.2 s against 3.9 s in the review's container), and one that only just fitted its timeout can now end incomplete. One pacer spans jobs, and the agent makes its own DNS sockets (`probe.QuerySocket`).
  - Sonnet 5.5 on #105 (test plumbing): PR open in ten minutes, counts and mutation table matched every re-measurement, stayed inside its files.
  - ARP is on by default: a new target's methods are `icmp`, `arp`, `rdns`, and the server issues `arp` whenever the agent has an interface on the subnet. No agent built from this `main` has run on a real network.
  - Since #99 the agent reads the host's `resolv.conf` in one daemon thread. A read that gets stuck (a stalled mount) leaves every later scan that relies on the host's resolvers incomplete until the read returns or the agent restarts.
  - Codex's state for a PR is in its summary comment, as `codex-review-state:v1` JSON (`commit_sha`, `has_any_findings`, `status`); a clean pass is also a plain comment naming the commit. CodeRabbit posts a notice on each PR that it skips the repository; ignore it.
  - The worktrees of merged branches and finished reviews under the T3 worktree directory on this machine are still there; none was removed.
- **Waiting on the owner:**
  - #88, on a test host on a real VLAN. Until it is done, a target on a subnet that matters can be set to `icmp`, `rdns`.
  - #100: whether a scan in which no DNS server gave a usable answer keeps clearing stored names. The issue holds the four options.
  - #91: whether the 2 MiB bound on a bulk import stands, or the dialog batches, or fields are bounded.
  - Three lines proposed for the worker brief in `.agents/orchestration.md`, used in every brief today but not added to the file: how a clean Codex result arrives, that a worker never changes git config, and that a worker never edits `HANDOFF.md`.
  - #5 and #25 (`owner` label).
- **Next:** the rest of M2a. Three issues run at once at most (`.agents/orchestration.md`).
  - In flight: #107 (`agent/tests/`, one sentence of the `AGENTS.md` agent bullet; automatic reviews), #97 (`ipnet.py`, `services/subnets.py`, `services/jobs.py`, `agent/tests/test_answer_limit.py`), #89 (`body_limit.py`, briefed round).
  - Ready, to start as slots free: #111 (`web/src/utils/scans.js`, the Agents page, `services/scans.py`; after #97, which also edits how a subnet's scope is keyed only if it touches `scans.py`, so check its diff first).
  - Waiting on the owner's decision: #100 (agent files), #91.
  - #20 (stretch) last; it must measure its largest `fdns` result set against `body_limit.BODY_MAX` and add its names to `test_answer_limit.py`. #93 is a stretch in M2b.
  - Then M2b (#21–#24, with #92 and #94 beside #23 and #24) and M3 (#26–#32). #86 and #90 close before #26 exposes `/mcp`; no OAuth grant may be issued before #28's write-scope checks are in force (noted on #26 and #28).

## 2026-11-21 — Claude Code (Opus 5.5), worker — #87: MAX_PPS on the wire (PR #110)
- **Done:** [PR #110](https://github.com/DeusMaximus/TLG-IPAM/pull/110) closes #87, head `7d8e91a`, cut from `main` at `9431428`. CI green; Codex clean on `7d8e91a`; Greptile not run (monthly cap); CodeRabbit skips the repo.
  - `scan.Pacer`: each grant at least `1/MAX_PPS` after the last, by the clock at the grant; no catching up after a late wake; re-sleeps after an early one; waiters served in turn (an `asyncio.Lock`); a cancelled waiter takes no slot. After `PACE_YIELD_EVERY` (64) grants in a row without a sleep, the next grant first yields.
  - The paced `_Pool` (ICMP) yields once after each start, so the echo request goes before the next grant.
  - `probe.QuerySocket`: the agent's own UDP socket, made before the slot and given to `dns.asyncquery.udp` as `sock`. The query is handed over in the grant's step, or dropped as an unsent attempt (`BlockingIOError`) if the socket can't take it; the socket is aborted when the attempt ends.
  - `loop.Agent` keeps one pacer for all jobs; `scan.run(pacer=None)` falls back to one per job.
  - Docs: spec §5.2 and §5.6, the `AGENTS.md` agent bullet (#107's file-keyword sentence untouched), both READMEs, the brief.
  - **Merged:** `d06018e`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #87 closed. The briefed round (Codex, GPT-6.1 Sol) ran after this entry was written: GO at `7d8e91a`, all four earlier measurements fixed, none of the nine calls overruled, two P3s (the pacer's ordering test passes with its lock removed; nothing would notice an icmplib that awaits before it sends), added to #107 as items 5 and 6. The round measured the cost of the strict bound, now in the PR body: 254 addresses at 50 and 200 pps took 18.1 s and 5.2 s against 15.3 s and 3.9 s on `main`, and a scan that completed inside a 5 s timeout on `main` ended incomplete here (names kept). Codex was clean on `7d8e91a`. The orchestrator re-measured: 650 agent tests on the head, and one real `icmp,arp,rdns` scan on a Docker `--internal` network with the image built from the head (complete; the ping-dropping host found by ARP; four PTR names through the new socket; queries no less than 0.204 s apart at 5 pps).
- **Decisions (PR body, calls 1–9):** strict bound on grants, no burst; item 4 closed (query dropped, not kept) and the budget carried across jobs; the kernel's ARP stays outside the budget by name, with numbers measured on a Docker `--internal` network (silent address: 3 requests 1 s apart; live peer: 1; `arp_accept 0`, so sweeping ARP first does not stop it). Yield bounds stated in sends: 64 for a ready task, fewer than 192 for one a timer wakes.
- **State:** agent 650 passed (macOS, and Linux in `python:3.13-slim`); server 808; web untouched. New `agent/tests/test_pacing.py` (19); +3 in `test_probe.py`, +1 in `test_loop.py`; `test_a_datagram_a_cancelled_lookup_had_handed_over_is_not_recalled` replaced; the failover test's `first-query-late` case tightened (late pacing sleep; grants and hand-offs asserted with no tolerance). No schema or wire change; tripwire lists unchanged.
- **Waiting on the owner:** the briefed round (packet-sending code), then merge. The real-wire rate is still #88's.
- **Next:** #107 on the same agent files, after #110 merges (its 1 pps end-to-end test changed in one way: a lookup's second slot now always lands outside its own 2 s). Not covered here: Python 3.11, a real full UDP send buffer, uvloop.

## 2026-11-21 — Claude Code (Opus 5.5), worker — #101: scan-target list in fixed statements; capability messages dated (PR #109)
- **Done:** [PR #109](https://github.com/DeusMaximus/TLG-IPAM/pull/109) closes #101, head `f0a9771`, cut from `main` at `9431428`. CI green (5/5) on `f0a9771`; Codex clean on `f0a9771`; Greptile not run (monthly cap); CodeRabbit skips the repo.
  - `ebe340e` (the issue): `scans.targets_out` builds every scan-target answer from one read each of subnets, agents and the scope's three inputs; `target_out` is its one-target case. `GET /scan-targets` runs 6 statements at 1, 50 and 500 targets (was 6 / 251 / 2,501). `scans._scopes_as_committed` is the one reader of the scope's inputs; `_scope_as_committed`, `scan_scope`, check-in, ingest and `drop_out_of_scope` are its one-subnet case, and `_scope` is unchanged. `ScanTargetOut` gains `agent_checkin_at` (`UtcDatetime`) and `agent_checkin_refused`, both null exactly when `runnable_methods` is. `ScanSettings` dates both capability messages, and shows a line on its own when there is no message but the report is not recent. Docs: brief §3.10, §4.2, §6.2; spec §5.8; `AGENTS.md` scope bullet and #68's bullet.
  - Briefed review (Codex, GPT-6.1 Sol): GO at `ebe340e`. It kept calls 1–6 and overruled the reasoning of call 7 (no `IN` chunking).
  - `f0a9771`, prose only (`AGENTS.md`, two docstrings in `services/scans.py`): the list's statements bind one parameter per distinct subnet or agent, so the SQLite build's parameter limit (32,766 by default; 250,000 in the image and CI) is the ceiling on one request, and past it the list fails. The PR body's call 7 and a Review section were updated to match.
  - **Merged:** `e84ff26`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #101 closed. The review's two P3s are filed as #111. The orchestrator briefed the review as a briefed round, because the PR rewrote the scope reader that check-in and ingest use; the reviewer's 66-subnet comparison against `main` matched range for range and its four grouping mutations each failed tests. `f0a9771` came after the round and was not reviewed by Sol: the orchestrator checked `scans.py` parses to the same tree once docstrings are ignored, and Codex was clean on it. The orchestrator re-measured: 814 server tests on `f0a9771`; at `ebe340e`, 628 agent and 111 web tests, lint and build, and with the agent read put back in the per-target loop the statement-count test fails at all three sizes.
- **Decisions:** PR body, Deliberate calls 1–7. "Last reported" is served as two facts (last check-in time, and whether it was refused), because no column stores the last accepted check-in's time. The time rides on the polled scan-target answer, not on `GET /agents`.
- **State:** green. Server 814 (808 on `main`), agent 628 (no agent file touched), web 111 (103); lint and build pass. No schema, protocol or lock change. Touches none of #87's or #105's files.
- **Waiting on the owner:** merge of #109.
- **To file (orchestrator):** one follow-up issue for the review's two P3s:
  - an `agent_checkin_at` in the future reads as "0s ago" and hides the line (`agentReport` counts it as recent);
  - an `IN` list past the SQLite build's parameter limit fails `GET /scan-targets` with a bare 500 (reproduced at 32,767 targets on SQLite 3.50.4).
- **Next:** nothing left on #101 beyond that issue. Optional and unfiled, from the #98 review: separating `schemas.ScopeRangeOut` from `agent_protocol.ScanRange`.

## 2026-11-20 — Claude Code (Sonnet 5.5), worker — #105: last_seen comparison and the datetime walk's gaps (PR #108)
- **Done:** [PR #108](https://github.com/DeusMaximus/TLG-IPAM/pull/108) closes #105, head `3fd0407`, cut from `main` at `9431428`. Tests and wording only; no file under `api/app/`, `agent/` or `web/` changed.
  - `test_last_seen.py`: two route-level tests with a hand-set `last_seen` and a later observation. Repeating the hand-set value leaves `updated_at` alone; writing the scan time the answer shows reaches the stored column and moves `updated_at`. The issue's mutant (compare against the answer's `last_seen`) passes all 808 old tests and fails both.
  - `test_datetimes.py`: the walk visits computed fields (return annotation through a `TypeAdapter`), samples every position of a tuple (`Unsampled` if it cannot fill one), dumps root models as their root, and its failure message names `REQUEST_MODELS`. New tests refuse a `REQUEST_MODELS` entry that no request body is validated against (`ITEM_MODELS` = `BulkHostItem`, `FindingKey`), and fail if a router route declares a `response_model` or an MCP tool advertises anything but a `dict` output. Each guard has a mutation that turns its test red (table in the PR body).
  - The outside review (Codex, GPT-6.1 Sol, GO at `957a50b`, three P3s) is answered in `3fd0407`, which touches `test_datetimes.py` and one `AGENTS.md` sentence only. Named limits in the walk's docstring: a serializer on the model that holds a computed field, union branches after the first, unsampled shapes (`Sequence`, `Mapping`, `TypedDict`, dataclass, `NamedTuple`, enum, non-`str` dict keys). Two pinning tests, `test_the_walk_does_not_see_a_serializer_on_the_model_holding_a_computed_field` and `test_the_walk_checks_only_the_first_branch_of_a_union`, are meant to be flipped when the walk improves. The served-model pin is described as a declaration pin (routers under `app.routers`, MCP output schemas), not output discovery. The request-model pin compares names; `ITEM_MODELS` is an allowance with no check behind it.
  - `AGENTS.md` datetime bullet rewritten to claim what the walk checks and name its limits; the agent-protocol sentence now says it is the agent wire. Brief §4.3: one clause on comparing with the stored column.
  - **Merged:** `b5dd357`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #105 closed. No follow-up issue: the outside read's three P3s were answered in the PR. `3fd0407` came after that read and was not reviewed by Sol: the orchestrator checked it adds two tests and their helper models, changes no existing function body in `test_datetimes.py`, and edits one `AGENTS.md` bullet; Codex was clean on it. The orchestrator re-measured: 818 server tests on `3fd0407`; at `957a50b`, with `update_host` comparing against the answer's `last_seen`, exactly the two new `test_last_seen.py` tests fail.
- **Decisions:** served models are pinned, not discovered (routes return an envelope dict, so output discovery has nothing to read). A tuple position the walk cannot fill raises instead of being skipped. The walk does not fail on unsupported shapes: that would change the guard and wants its own review.
- **State:** server 818 tests (808 on `main`, +10). Agent and web suites not re-run locally, nothing under them changed; CI agent and frontend pass. CI green on `3fd0407`; Codex clean on `3fd0407`; Greptile not run (monthly cap); CodeRabbit skips the repo. No schema change. No defect found in `api/app/`.
- **Waiting on the owner:** merge #108.
- **Next:** unchanged from the previous entry. #87 and #101 are still in flight. #105's lane (`api/tests/test_datetimes.py`, `test_last_seen.py`, the datetime bullet) is free once #108 merges.

## 2026-11-20 — Claude Code (Opus 5.5), worker — #104: the os.open exception removed from the tripwire; round 1 GO
- **Done:** PR #106 (head `ff1ae52`, closes #104). `probe.read_resolv_conf` checks the path with `os.stat` (regular file, at most `RESOLV_CONF_MAX`) and lets dnspython open and parse it (`Resolver(configure=False).read_resolv_conf(path)`); no agent module opens a file itself, and `open` is a listed name in every spelling again. `test_invariants.py` lost `os.open`'s allowance, the flag and rebinding checks, `test_os_open_only_reads`; every `REFUSED` case lists exactly the reasons `_violations` gives, alone and appended to arp.py and probe.py; a file keyword passed as a string (`**{"filename": …}`) counts as that keyword; the review's false alarms have a test of their own. New resolver tests: the four non-files through `DnsResolver` with no thread left behind, the FIFO-swapped-after-the-look bound, the review's 16 parser cases. Docs: AGENTS.md invariant 1, spec §5.2 and a new §5.6 bullet, agent/README.md, the brief.
- **Round 1 (Codex, GPT-6.1 Sol) at `da64104`: GO**, both #103 findings fixed, all eight calls accepted, two P3s. Finding 2 (README overstated the stat-time checks) fixed in `ff1ae52`, prose only in `agent/README.md`; the PR body's call 5 now names the two looser rows (`import stat`, `import threading`) and has a Round 1 section. Finding 1 goes to a follow-up issue; the checker and tests are unchanged.
- **Merged:** `8120cd2`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #104 closed. Round 1's finding 1 (44 of the 60 forbidden names, and several prefixes and keywords, have no test of their own) is filed as #107, with the dead `PREFIX_EXCEPTIONS`, the `AGENTS.md` sentence on file keywords and the once-failed end-to-end test. `ff1ae52` came after the round and was not reviewed by Sol: it changes `agent/README.md` only, the orchestrator read the diff, and Codex was clean on it. The orchestrator re-measured: 628 agent tests on `ff1ae52` and on `main` after the merge; at `da64104`, five functions appended to `probe.py` in turn (the alias and function-default bypasses, a plain `os.open`, an `io.FileIO` writer, `logging`'s `filename` keyword) each fail 26 to 36 of the 142 invariant cases.
- **Decisions:** the issue's preferred fix; what it gives up (size checked when the path is looked at, not a bound on the read; a FIFO swapped in after the look blocks the one read thread until a writer comes) is in the spec, AGENTS.md, README and docstrings. Kept from #99: `stat`/`threading`, all 14 added forbidden names, `READ_OPENERS`/`FILE_KEYWORDS`/`OPENED_WHERE`. Keyword routes get a string-constant check, not a listed name.
- **State:** CI green on `ff1ae52`; Codex reviewed `ff1ae52` with no major issues; Greptile not run (monthly cap). Agent 628 tests (macOS; Linux `--network none` at `da64104`), server 808. No second review round planned.
- **Waiting on the owner:** merge #106. File the follow-up for finding 1: seven retained names (`__delattr__`, `hardlink_to`, `link_to`, `mkfifo`, `mknod`, `openpty`, `symlink_to`), `capath`/`certfile`/`keyfile`, the prefixes `spawn`/`fork`/`posix_spawn`/`subprocess_`, and 44 single-name exemptions in all (`eval`, `popen`, `write_bytes`, `vars` among them) survive removal one at a time. It could also carry these, none of them filed: removing the dead `PREFIX_EXCEPTIONS` and its stale comment, and the AGENTS.md wording the review suggested (it lists file keywords among the "reads"; `logging`'s `filename` can write).
- **Next:** #87 on the same files, after #106 merges. Not filed: `test_e2e.py::test_lookups_that_send_nothing_keep_the_stored_names_at_one_packet_a_second` failed once in 31 full runs under load (real clock at 1 pps) and passed six reruns.
