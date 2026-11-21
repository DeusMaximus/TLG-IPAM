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

## 2026-11-21 — Claude Code (Fable 5.2), orchestrator — seventeen PRs merged since 2026-11-20; #118 in flight
- **Done:** seventeen PRs squash-merged on the owner's go-ahead, fourteen implemented by Opus 5.5 and three (#108, #114, #117) by Sonnet 5.5. The remote branches are deleted. Worker entries are below or in the archive (`grep -n '^## ' .agents/handoff/2026-11.md`).
  - Wave 1, from `main` at `b481075`: #82 (#65) `3cbfa63`, #83 (#79) `1987465`, #84 (#14) `7014b58`.
  - Wave 2: #95 (#75) `e76471a`, #96 (#78) `aa95f64`, #98 (#68) `5155f8c`, #102 (#85) `df336cb`, #103 (#99) `c34feae`, #106 (#104) `8120cd2`, #108 (#105) `b5dd357`, #109 (#101) `e84ff26`, #110 (#87) `d06018e`, #112 (#89) `d321f0d`, #113 (#97) `80bce03`, #114 (#107) `e78c531`, #116 (#111) `687b34b`, #117 (#115) `8d60205`.
  - Reviews (Codex, GPT-6.1 Sol): briefed rounds on #83 and #84 (each NO-GO, then GO), on #96 and #103 (each GO, then GO again after the orchestrator sent P3s back: on #96 one was a case the issue itself asks to close, on #103 both were in code the PR added and one loosened the invariant tripwire), and on #106, #109, #110, #112 and #116 (each GO; #109 and #116 were briefed as rounds because they changed the scope reader; #112 and #116 came back with no finding). One outside read each on #82, #95, #98, #102, #108, #113, #114 and #117 in place of Greptile, which reviewed nothing (monthly cap until 2026-11-22; its MCP `trigger_code_review` is still untried).
  - Issues filed, each on the owner's go-ahead: #85 to #94, #97, #99 to #101, #104, #105, #107, #111, #115, #118. That covers every follow-up from these reviews and everything earlier entries listed as unfiled (from #80: #89, #90, #91; from #13: #92, #93, #94).
- **Decisions (the owner's):**
  - #84 merged before its real-VLAN acceptance line was run; that check and the five left from PR #70 are #88.
  - #84's deliberate calls merged as written. The 2026-11-16 interim rule ends: a complete scan on the agent's own link clears a stored PTR name.
  - `list_scan_findings` returns `next_cursor`, `returned`, `note`, `findings` in place of `findings`/`total`.
  - A subnet lists at most eight DNS servers and a job carries the first three (#75).
  - #103 merged with a known gap in the tripwire (an alias of `os` got a write past its `os.open` check); #104 closed it the same day by removing the `os.open` exception. No agent module opens a file itself: it checks the path with `os.stat` and dnspython opens it.
  - The owner asked for every ready M2a issue to be started (2026-11-20, after #106).
- **State:** green. `main` is `8d60205` plus hand-off commits. Measured on `8d60205`: 955 server, 893 agent and 118 web tests; lint and build pass. CI is green on `8d60205`. No PR is open. One worker is running, on #118 (Sonnet 5.5, tests only, `agent/tests/`), cut from `main` after this entry; its acceptance has it loop the agent suite in a container beside two server suites, which loads the machine.
  - A subnet's CIDR is stored in its canonical spelling since #97; a row stored another way keeps it until its next edit, and jobs, the findings listing and (since #111) the scan-target audit summary use the canonical form meanwhile.
  - Since #111 the scan-target list reads its subnets, agents and scope inputs in chunks of 500 ids, so the SQLite build's parameter limit no longer caps it, and the UI shows a server time more than 60 s ahead of the browser's clock as the time itself with "ahead of this browser's clock", never as an age.
  - Since #87 the packet budget bounds when a packet is handed to the kernel, with no catching up: a scan takes longer than before (254 addresses at 200 pps: 5.2 s against 3.9 s in the review's container), and one that only just fitted its timeout can now end incomplete. One pacer spans jobs, and the agent makes its own DNS sockets (`probe.QuerySocket`).
  - Since #99 the agent reads the host's `resolv.conf` in one daemon thread. A read that gets stuck (a stalled mount) leaves every later scan that relies on the host's resolvers incomplete until the read returns or the agent restarts. A read that takes over a second gives no server for that job: the scan is incomplete and names are kept. No README says so yet.
  - Timing tests under load: #115 made the `resolv.conf` read tests hold (200 of 200 full-suite runs beside two server suites). One end-to-end test still depends on spare wall-clock time and failed in loaded Linux containers (4 of 17 runs for the worker, 1 of 30 for the reviewer); it is #118, with the missing test of the one-second read bound. A worker that loads the machine on purpose makes other local suite runs fail on timeouts or take many times longer.
  - Sonnet 5.5 on #105, #107 and #115 (test plumbing): counts and mutation tables matched every re-measurement, and each stayed inside its files.
  - ARP is on by default: a new target's methods are `icmp`, `arp`, `rdns`, and the server issues `arp` whenever the agent has an interface on the subnet. No agent built from this `main` has run on a real network.
  - Codex's state for a PR is in its summary comment, as `codex-review-state:v1` JSON (`commit_sha`, `has_any_findings`, `status`); a clean pass is also a plain comment naming the commit. CodeRabbit posts a notice on each PR that it skips the repository; ignore it.
  - The worktrees of merged branches and finished reviews under the T3 worktree directory on this machine are still there; none was removed.
- **Waiting on the owner:**
  - #88, on a test host on a real VLAN. Until it is done, a target on a subnet that matters can be set to `icmp`, `rdns`.
  - #100: whether a scan in which no DNS server gave a usable answer keeps clearing stored names. The issue holds the four options.
  - #91: whether the 2 MiB bound on a bulk import stands, or the dialog batches, or fields are bounded.
  - Three lines proposed for the worker brief in `.agents/orchestration.md`, used in every brief since 2026-11-20 but not added to the file: how a clean Codex result arrives, that a worker never changes git config, and that a worker never edits `HANDOFF.md`.
  - #5 and #25 (`owner` label).
- **Next:**
  - In flight: #118 (`agent/tests/test_e2e.py`, `test_probe.py`), under the owner's 2026-11-20 go-ahead to start every ready M2a issue. When its PR opens: verify, one outside read in place of Greptile (or Greptile itself from 2026-11-22), then report for the merge decision.
  - Nothing else in M2a is ready without the owner: #100 and #91 are decisions, #88 needs a real network. #20 is a stretch: it must measure its largest `fdns` result set against `body_limit.BODY_MAX` and add its names to `test_answer_limit.py`. #93 is a stretch in M2b.
  - Then M2b (#21–#24, with #92 and #94 beside #23 and #24) and M3 (#26–#32). #86 and #90 close before #26 exposes `/mcp`; no OAuth grant may be issued before #28's write-scope checks are in force (noted on #26 and #28).

## 2026-11-21 — Claude Code (Sonnet 5.5) — #115: the resolv.conf read tests hold under load (PR #117)
- **Done:** PR #117 (`115-fifo-test-under-load`, head `1229e04`) closes #115. Tests only, in `agent/tests/test_probe.py`; nothing under `agent/src/` changed.
  - Cause: the FIFO-swapped-after-the-look test ended with a read that must succeed, made with `read_timeout=0.3`; on a loaded machine the read took longer, so `servers()` gave `()`. Reproduced on purpose under 60 CPU-bound processes (the original failed 2 of its first 7 runs; the review saw 3 of 4). What consumed the read's wall time is not established.
  - Round 1: reads a test expects to succeed get `PATIENT` (5 s); only the wait a test means to see run out keeps 0.3 s. Timing bounds are loosened; the call counts are exact; no assertion was removed.
  - Round 2 (Codex, GPT-6.1 Sol: GO, two P3s, both fixed): three tests whose oracle is not the host's scheduler (a call driven by hand that must not suspend; the best of up to 6 waits for loop gaps; the loop's clock skewed past the job's deadline with the read held). The rescue writer is now `FifoWriter`: its firing is observable, it is joined in cleanup, and none starts once the path has been replaced.
  - Mutations: #104's three and the review's three smaller ones all turn tests red on the head.
  - **Merged:** `8d60205`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #115 closed. The other end-to-end test and the missing test of the one-second default are filed as #118. Sol reviewed `90f931a`; `1229e04`, which answers its two P3s, it did not. The orchestrator checked that `1229e04` touches only `agent/tests/test_probe.py` and that each of the review's three timing mutations (0.2 s before the busy refusal, a 0.4 s block of the loop, cancellation 1 s late) now fails a test. It re-measured: 893 agent tests on `1229e04`; at `90f931a`, with the read's time bound removed two tests fail without a hang, and with the one-read-at-a-time refusal removed three do. It did not load the machine. On `main` at `8d60205`: 955 server, 893 agent, 118 web.
- **Decisions:** a longer timeout for real-thread passing paths; a skewed loop clock only where the oracle is order; a best-of-6 gap for the loop-stall check (a stall in the code is in every wait, the host's in few). `test_e2e.py` left alone.
- **State:** green. 893 agent tests on the head. CI green (agent, backend, frontend, locks). Codex completed on `1229e04` with no findings; Greptile not run (monthly cap). 200 of 200 macOS full-suite runs on `1229e04` with two server suites looping beside them. Linux container (`--network none`, offline wheels): 40 of 40 runs of `test_probe.py` on `90f931a`; not repeated on `1229e04`.
- **Waiting on the owner:** merge of #117. A new issue for `test_e2e.py::test_a_scan_cut_off_during_the_ptr_lookups_keeps_the_names_it_did_not_ask` (a ten-times-fast clock that needs spare wall time; failed 4 of 17 full-suite runs in the Linux container, reproduced by the review). The fix has to make lookup admission order against the deadline deterministic and keep asserting that unasked names survive, not just lower the expected count.
- **Next:** the review's operational note: a real host read over 1 s gives no server and an incomplete scan, which keeps names and retries once the read ends; whether slow real hosts justify a configurable bound is an open question with no evidence yet. A dedicated test of the production 1 s default bound is still missing (the review's mutation 1 → 4 s passes everything, on `main` too).

## 2026-11-21 — Claude Code (Opus 5.5), worker — #111: future check-in times; scan-target list in chunks (PR #116)
- **Done:** [PR #116](https://github.com/DeusMaximus/TLG-IPAM/pull/116) closes #111, head `612d5b9`, rebased onto `main` at `826eedb`. CI green (5/5); Codex clean on `612d5b9`; Greptile not run (monthly cap); CodeRabbit skips the repo.
  - Web: `agents.serverAge` is the one rule. A time more than `AHEAD_WITHIN_MS` (60 s, one check-in interval) ahead of the browser's clock has no known age. The Agents dot shows "Clocks disagree"; `relativeTime` gives "at <local time>, ahead of this browser’s clock"; `agentReport` is never `recent`, so the Scanning line stays. Within 60 s the time reads as now, as before.
  - Server: `scans._in_batches` gives distinct ids in chunks of `_IN_BATCH` (500). `targets_out` reads subnets and agents by chunk. `_scopes_as_committed` reads all three scope inputs per chunk through `_scopes_of_chunk`, which is the old body. Six statements up to 500; past that, +4 per chunk of subnets and +1 per chunk of agents. The one-subnet case runs the same three `IN (?)` statements as before.
  - `_label` uses `ipnet.canonical_cidr` (#97's loose end).
  - Docs: brief §3.10, §4.2, §6.2; spec §5.8; the `AGENTS.md` scope bullet and the bullet on what a target's agent can run (the "parameter limit is the ceiling" sentence replaced).
  - **Merged:** `687b34b`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #111 closed. The briefed round (Codex, GPT-6.1 Sol) ran after this entry was written: GO at `612d5b9` with no finding, both of its findings on PR #109 fixed. It could not re-measure the SQLite parameter limit of CI's Python, so the PR's 250,000 figure for it rests on the worker alone. No follow-up issue. CI was green and Codex clean on `612d5b9`. The orchestrator re-measured: 955 server, 890 agent and 118 web tests on the head, lint and build clean; with `_scopes_as_committed` reading only its first chunk, seven tests fail.
- **Decisions (PR body, calls 1–7):** the 60 s threshold and why. Show the absolute time and say the clocks disagree. Chunk the ids rather than page the list. Chunk size 500, inside the 999 limit of SQLite builds before 3.32. Chunks split by subnet id, the same for all three inputs.
- **State:** server 955 (+11), agent 890, web 118 (+7); lint and build pass. No schema or protocol change. A run during the #115 stress run (load ~195) produced timeout failures (at least some in `test_body_limit.py`) and vitest worker-start timeouts; it was clean once the load dropped.
- **Waiting on the owner:** the briefed round (it edits the scope reader check-in and ingest use), then merge.
- **Next:** none from this issue. Not covered: a real 250,001-target request, SQLite builds older than 3.32, a real browser's layout of the longer time text.

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
