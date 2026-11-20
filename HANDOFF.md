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

## 2026-11-21 — Claude Code (Fable 5.2), orchestrator — fifteen PRs merged since 2026-11-20; #111 and #115 in flight
- **Done:** fifteen PRs squash-merged on the owner's go-ahead, thirteen implemented by Opus 5.5 and two (#108, #114) by Sonnet 5.5. The remote branches are deleted. Worker entries are below or in the archive (`grep -n '^## ' .agents/handoff/2026-11.md`).
  - Wave 1, from `main` at `b481075`: #82 (#65) `3cbfa63`, #83 (#79) `1987465`, #84 (#14) `7014b58`.
  - Wave 2: #95 (#75) `e76471a`, #96 (#78) `aa95f64`, #98 (#68) `5155f8c`, #102 (#85) `df336cb`, #103 (#99) `c34feae`, #106 (#104) `8120cd2`, #108 (#105) `b5dd357`, #109 (#101) `e84ff26`, #110 (#87) `d06018e`, #112 (#89) `d321f0d`, #113 (#97) `80bce03`, #114 (#107) `e78c531`.
  - Reviews (Codex, GPT-6.1 Sol): briefed rounds on #83 and #84 (each NO-GO, then GO), on #96 and #103 (each GO, then GO again after the orchestrator sent P3s back: on #96 one was a case the issue itself asks to close, on #103 both were in code the PR added and one loosened the invariant tripwire), and on #106, #109, #110 and #112 (each GO; #109 was briefed as one because it rewrote the scope reader; #112 came back with no finding). One outside read each on #82, #95, #98, #102, #108, #113 and #114 in place of Greptile, which reviewed nothing (monthly cap until 2026-11-22; its MCP `trigger_code_review` is still untried).
  - Issues filed, each on the owner's go-ahead: #85 to #94, #97, #99 to #101, #104, #105, #107, #111, #115. That covers every follow-up from today's reviews and everything earlier entries listed as unfiled (from #80: #89, #90, #91; from #13: #92, #93, #94).
- **Decisions (the owner's):**
  - #84 merged before its real-VLAN acceptance line was run; that check and the five left from PR #70 are #88.
  - #84's deliberate calls merged as written. The 2026-11-16 interim rule ends: a complete scan on the agent's own link clears a stored PTR name.
  - `list_scan_findings` returns `next_cursor`, `returned`, `note`, `findings` in place of `findings`/`total`.
  - A subnet lists at most eight DNS servers and a job carries the first three (#75).
  - #103 merged with a known gap in the tripwire (an alias of `os` got a write past its `os.open` check); #104 closed it the same day by removing the `os.open` exception. No agent module opens a file itself: it checks the path with `os.stat` and dnspython opens it.
  - The owner asked for every ready M2a issue to be started (2026-11-20, after #106).
- **State:** green. `main` is `e78c531` plus hand-off commits; CI is green on `e78c531`. Measured: 890 agent tests on the tree `main` now has; 944 server tests on `main` at `80bce03`, with no server file changed since; 111 web tests at `d06018e`, with nothing under `web/` changed since. No PR is open.
  - In flight, one worker each, both cut from `80bce03`: #111 (scan-target follow-ups, Opus 5.5; the orchestrator will give it a briefed round because it edits the scope reader) and #115 (a test that failed once under load, Sonnet 5.5). #115's worker loads the machine on purpose (a load average over 150 was seen): local suite runs beside it can take many times longer.
  - A subnet's CIDR is stored in its canonical spelling since #97; a row stored another way keeps it until its next edit, and jobs and the findings listing use the canonical form meanwhile.
  - Since #87 the packet budget bounds when a packet is handed to the kernel, with no catching up: a scan takes longer than before (254 addresses at 200 pps: 5.2 s against 3.9 s in the review's container), and one that only just fitted its timeout can now end incomplete. One pacer spans jobs, and the agent makes its own DNS sockets (`probe.QuerySocket`).
  - Sonnet 5.5 on #105 and #107 (test plumbing): PRs open in ten and thirty minutes, counts and mutation tables matched every re-measurement, each stayed inside its files.
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
- **Next:** the rest of M2a.
  - In flight: #111 (`services/scans.py`, the scan-target route, `web/`) and #115 (`agent/tests/test_probe.py`).
  - Nothing else in M2a is ready without the owner: #100 and #91 are decisions, #88 needs a real network.
  - #20 (stretch): it must measure its largest `fdns` result set against `body_limit.BODY_MAX` and add its names to `test_answer_limit.py`. #93 is a stretch in M2b.
  - Then M2b (#21–#24, with #92 and #94 beside #23 and #24) and M3 (#26–#32). #86 and #90 close before #26 exposes `/mcp`; no OAuth grant may be issued before #28's write-scope checks are in force (noted on #26 and #28).

## 2026-11-21 — Claude Code (Sonnet 5.5) — #107: a test of its own for each tripwire restriction, the pacer's lock, icmplib's send order
- **Done:** PR #114, closes #107, tests and wording only. No file under `agent/src/` changed.
  - **Item 1, `test_invariants.py`:** one refused source per forbidden name, prefix and file keyword. They are written out, not read from the lists, each asserting its own exact reason in three modules (snippet, `arp.py`, `probe.py`). `test_every_entry_of_each_list_has_a_case` makes a new entry need a case.
  - **Item 1, mutations:** 71 per-entry mutations (60 names, 6 prefixes, 5 keywords), all red on their own cases and reasons, none red on another entry's.
  - **Item 2:** `PREFIX_EXCEPTIONS` removed.
  - **Item 3:** one `AGENTS.md` sentence.
  - **Item 4, `test_e2e.py`:** the test's assumptions stated in its docstring, nothing loosened.
  - **Item 5, `test_pacing.py`:** `HeldClock` tests for the pacer's lock, with the lock removed they fail on the ordering.
  - **Item 6, `test_icmplib_send.py`:** a new file running `IcmpProber` through the installed `icmplib.async_ping`, replacing only `ICMPv4Socket._create_socket`. It has a control with an injected `await`, and an `await` injected into icmplib or removed from `_Pool.run` turns it red.
  - **Reviews:** outside review by Codex GPT-6.1 Sol, GO at `127f4a6`, one P3 answered by `045ebc5`. Codex's automatic review is clean on `045ebc5`.
  - **Merged:** `e78c531`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #107 closed. The once-failed FIFO test is filed as #115. The head moved twice after the outside read: `045ebc5` (one docstring; the orchestrator checked `test_e2e.py` parses to the same tree) and `4a6d40f`, a merge of `main` at `80bce03` for one `AGENTS.md` conflict with #113, which the orchestrator checked identical to an automatic merge outside `AGENTS.md`, with the agent bullet the branch's and every other line `main`'s. CI was green and Codex clean on `4a6d40f`. The orchestrator re-measured: 890 agent tests on `4a6d40f`; at `127f4a6`, the pacer's lock removed (exactly the two new tests fail), five forbidden names exempted in turn (each fails only its own three cases) and two prefixes removed. Its own server run on the merge commit did not finish, the machine being loaded by #115's worker; CI's backend job passed on that tree.
- **Decisions:**
  - The checker's behaviour is unchanged.
  - A new list entry now needs a case (equality, not subset).
  - `exec` and `exec_module` in `FORBIDDEN` are redundant with the `exec` prefix; left as they are.
  - Item 4 changed no code, only the docstring.
- **State:** green. Agent 890 (was 650); server 824 and web 111 untouched. CI green on `045ebc5`, Codex clean, Greptile not run (cap until 2026-11-22). Linux aarch64 container passed 890.
  - **100 full-suite runs:** 99 passed. Run 33 failed `test_probe.py::test_a_fifo_put_at_the_path_after_the_look_holds_one_thread_until_a_writer_comes`, which looks like a load flake from the 0.3 s read timeout. It is not filed.
  - **The once-failed e2e test:** passed 100 of 100 in the suite and 360 of 360 alone under load about 40. A 2.2 s process stall after the first send makes it fail, with the scan complete and no error.
- **Waiting on the owner:** merge #114. Whether to file the `test_probe.py` FIFO test flake (a read timeout of 0.3 s under load).
- **Next:** #97 and #89 (in flight, other workers). The merge takes `main` in with a merge commit; this PR touches none of their files except the one `AGENTS.md` sentence.

## 2026-11-21 — Claude Code (Opus 5.5), worker — #97: subnet CIDR in its canonical spelling (PR #113)
- **Done:** [PR #113](https://github.com/DeusMaximus/TLG-IPAM/pull/113) closes #97, head `6fb8dbb`, cut from `main` at `6a68173`. Two commits: `d600887` (the change) and `6fb8dbb` (one test, for the outside review's P3). On `d600887`: CI green (4/4) and Codex clean. On `6fb8dbb`: CI and Codex pending when this entry was written. Greptile not run (monthly cap); CodeRabbit skips the repo.
  - New `ipnet.canonical_cidr`.
  - `services/subnets`: create and every update that sends `cidr` store the canonical spelling. A CIDR change is judged by network. Refusals and update/delete summaries name networks canonically.
  - `services/jobs`: a job sends `canonical_cidr(subnet.cidr)`.
  - `services/findings.list_named`: orders, pages and answers `subnet_cidr` canonically. A legacy long spelling had pushed the cursor past `CURSOR_MAX`, so the listing refused its own cursor; shown red.
  - `agent/tests/test_answer_limit.py`: built from a subnet stored with the review's padded spelling.
  - Docs: brief §3.2, §4.5, the §5.2 `list_scan_findings` row and the acceptance criteria; spec §5.7; the `AGENTS.md` wire-contract and IP-maths bullets.
  - Outside review (Codex, GPT-6.1 Sol) at `d600887`: GO, all six calls kept, one P3 (no mixed-legacy paging regression).
    - `6fb8dbb` adds it: two `same` subnets stored as `10.0.0.0/255.255.255.0` and `10.0.0.0/0.0.0.127`, 52 findings in canonical order.
    - It is shown red on a sort by the stored spelling: the /24's 26 findings are skipped.
    - The PR body now says that one database-only inconsistent row can tell the network comparison from the string comparison.
  - **Merged:** `80bce03`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #97 closed. CI was green and Codex clean on `6fb8dbb`. No follow-up issue: the outside read's one P3 was answered by `6fb8dbb`, which Sol did not review; the orchestrator checked it adds one test and changes no other, and that with the listing sorted by the stored spelling exactly that test fails. The orchestrator re-measured: 849 server tests on `6fb8dbb`; at `d600887`, 650 agent tests, and with the job echoing the stored `cidr` the new check-in test and the answer-limit test fail.
- **Decisions (PR body, calls 1–6):**
  - Store the canonical spelling and also send it.
  - No migration: a legacy row keeps its spelling until a write sends `cidr`, and the UI form's next save fixes it.
  - No `PROTOCOL_VERSION` raise.
  - Subnet reads return what is stored.
  - Duplicates are refused within a section only (brief §3.2).
  - `services/scans.py` is not edited. `scans._label` still prints a legacy row's stored spelling; left for #111.
- **State:** 849 server tests (+25), 650 agent tests, 111 web tests; lint and build pass. No schema change; nothing under `agent/src/` or `web/`. Measured: 104,978 bytes sent, bound 106,762, the padded spelling as stored 109,276.
- **Waiting on the owner:** merge, once CI and Codex are green on `6fb8dbb`.
- **Next:**
  - #111 can start; `scans.py` is untouched here.
  - A findings cursor issued before the deploy for a legacy row with a short non-canonical spelling may skip or repeat that subnet's findings once (call 5; the review measured it).

## 2026-11-21 — Claude Code (Opus 5.5), worker — #89: body cap, padded Content-Length; /mcp refusal line (PR #112)
- **Done:** [PR #112](https://github.com/DeusMaximus/TLG-IPAM/pull/112) closes #89, head `54527ee`, cut from `main` at `6a68173`. CI green (5/5) on `54527ee`. Codex clean on `54527ee`. Greptile not run (monthly cap). CodeRabbit skips the repo.
  - `body_limit._declared_length` is replaced by `_declared_over(scope, cap) -> bool | None`, built on two helpers.
  - `_length_digits` strips spaces and tabs at both ends, accepts ASCII digits only, and strips leading zeroes.
  - `_over` compares the digit count first, then calls `int()` on at most `len(str(cap))` digits. Python's 4,300-digit conversion limit stays on.
  - Docs: the `AGENTS.md` cap bullet and owner-key bullet ("Credential refusals on `/mcp`…", with the cap's 413 for an oversized body); brief §4.3, first cap bullet. The spec is unchanged.
  - `test_startup._uvicorn` now takes extra uvicorn options.
  - **Merged:** `d321f0d`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #89 closed. The briefed round (Codex, GPT-6.1 Sol) ran after this entry was written: GO at `54527ee` with no finding, none of the five calls overruled, 234 raw framing probes under both parsers with no body past the cap and no 5xx. No follow-up issue. The orchestrator re-measured: 919 server tests on the head; raw requests at a real uvicorn with httptools, on `main` (500 for 4,500 leading zeroes; `100 Continue` for an over-cap length with a trailing space or tab) and on the head (401 for the padded small request, 413 for each over-cap one).
- **Decisions (PR body, calls 1–5):**
  - Only space and tab count as optional whitespace. Any other stray byte makes the value unusable, and the body is counted.
  - A padded length within the cap is trusted, like a plain one.
  - The MCP SDK's own body limit re-reads the length and counts a zero-padded one. That code is not ours and is unchanged.
  - The real-server tests run both parsers; the h11 rows pin h11's own 400 for the zeroes.
- **State:** green.
  - Server 919 passed (824 + 95), agent 650, web untouched. No schema or wire change.
  - Reproduced on `main` first: a 500 with a traceback, and `100 Continue` followed by a wait. Against `main`'s `body_limit.py`, 47 of the new tests fail and the httptools fixture's teardown catches the traceback.
  - The two real uvicorn servers add about 17 s to the suite.
- **Waiting on the owner:** the briefed round (the code runs before authentication), then merge.
- **Next:** the briefed round on #112. Not covered: servers other than uvicorn, HTTP/2.

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
