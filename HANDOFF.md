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

## 2026-11-20 — Claude Code (Fable 5.2), orchestrator — waves 1 and 2: nine PRs merged; #87, #105 and #101 in flight
- **Done:** nine PRs squash-merged on the owner's go-ahead, all implemented by Opus 5.5. The remote branches are deleted. Worker entries are below or in the archive (`grep -n '^## ' .agents/handoff/2026-11.md`).
  - Wave 1, from `main` at `b481075`: #82 (#65) `3cbfa63`, #83 (#79) `1987465`, #84 (#14) `7014b58`.
  - Wave 2: #95 (#75) `e76471a`, #96 (#78) `aa95f64`, #98 (#68) `5155f8c`, #102 (#85) `df336cb`, #103 (#99) `c34feae`, #106 (#104) `8120cd2`.
  - Reviews (Codex, GPT-6.1 Sol): briefed rounds on #83 and #84 (each NO-GO, then GO), on #96 and #103 (each GO, then GO again after the orchestrator sent P3s back: on #96 one was a case the issue itself asks to close, on #103 both were in code the PR added and one loosened the invariant tripwire), and on #106 (GO). One outside read each on #82, #95, #98 and #102 in place of Greptile, which reviewed nothing (monthly cap until 2026-11-22; its MCP `trigger_code_review` is still untried).
  - Issues filed, each on the owner's go-ahead: #85 to #94, #97, #99 to #101, #104, #105, #107. That covers every follow-up from today's reviews and everything earlier entries listed as unfiled (from #80: #89, #90, #91; from #13: #92, #93, #94).
- **Decisions (the owner's):**
  - #84 merged before its real-VLAN acceptance line was run; that check and the five left from PR #70 are #88.
  - #84's deliberate calls merged as written. The 2026-11-16 interim rule ends: a complete scan on the agent's own link clears a stored PTR name.
  - `list_scan_findings` returns `next_cursor`, `returned`, `note`, `findings` in place of `findings`/`total`.
  - A subnet lists at most eight DNS servers and a job carries the first three (#75).
  - #103 merged with a known gap in the tripwire (an alias of `os` got a write past its `os.open` check); #104 closed it the same day by removing the `os.open` exception. No agent module opens a file itself: it checks the path with `os.stat` and dnspython opens it.
  - The owner asked for every ready M2a issue to be started (2026-11-20, after #106).
- **State:** green. `main` is `8120cd2` plus hand-off commits; CI is green on `8120cd2`. Measured on `main` after the last merge: 808 server tests, 628 agent tests; 103 web tests at `c34feae`, with nothing under `web/` changed since. No PR is open.
  - In flight, started after this commit, one worker each: #87 (agent lane, Opus 5.5), #105 (tests, Sonnet 5.5), #101 (scan-target list, Opus 5.5).
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
- **Next:** the rest of M2a. Three issues run at once at most (`.agents/orchestration.md`); start the next as a worker hands back.
  - `agent/` (`probe.py`, `scan.py`) and the `AGENTS.md` agent bullet, one after the other: #87 (in flight, briefed round; it carries a comment with the measured spill of a held datagram into the next job), then #107 (tests only, automatic reviews). #100 after the owner decides; #93 is a stretch in M2b.
  - In flight beside it: #105 (`api/tests/`, the `AGENTS.md` datetime bullet) and #101 (`services/scans.py`, the scan-targets route, `ScanSettings.jsx`).
  - Ready, to start as slots free, no file in common with the above or each other: #97 (`ipnet.py`, `services/subnets.py`, `services/jobs.py`, `agent/tests/test_answer_limit.py`), #89 (`body_limit.py`, briefed round).
  - #20 (stretch) last; it must measure its largest `fdns` result set against `body_limit.BODY_MAX` and add its names to `test_answer_limit.py`.
  - Then M2b (#21–#24, with #92 and #94 beside #23 and #24) and M3 (#26–#32). #86 and #90 close before #26 exposes `/mcp`; no OAuth grant may be issued before #28's write-scope checks are in force (noted on #26 and #28).

## 2026-11-20 — Claude Code (Opus 5.5), worker — #104: the os.open exception removed from the tripwire; round 1 GO
- **Done:** PR #106 (head `ff1ae52`, closes #104). `probe.read_resolv_conf` checks the path with `os.stat` (regular file, at most `RESOLV_CONF_MAX`) and lets dnspython open and parse it (`Resolver(configure=False).read_resolv_conf(path)`); no agent module opens a file itself, and `open` is a listed name in every spelling again. `test_invariants.py` lost `os.open`'s allowance, the flag and rebinding checks, `test_os_open_only_reads`; every `REFUSED` case lists exactly the reasons `_violations` gives, alone and appended to arp.py and probe.py; a file keyword passed as a string (`**{"filename": …}`) counts as that keyword; the review's false alarms have a test of their own. New resolver tests: the four non-files through `DnsResolver` with no thread left behind, the FIFO-swapped-after-the-look bound, the review's 16 parser cases. Docs: AGENTS.md invariant 1, spec §5.2 and a new §5.6 bullet, agent/README.md, the brief.
- **Round 1 (Codex, GPT-6.1 Sol) at `da64104`: GO**, both #103 findings fixed, all eight calls accepted, two P3s. Finding 2 (README overstated the stat-time checks) fixed in `ff1ae52`, prose only in `agent/README.md`; the PR body's call 5 now names the two looser rows (`import stat`, `import threading`) and has a Round 1 section. Finding 1 goes to a follow-up issue; the checker and tests are unchanged.
- **Merged:** `8120cd2`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #104 closed. Round 1's finding 1 (44 of the 60 forbidden names, and several prefixes and keywords, have no test of their own) is filed as #107, with the dead `PREFIX_EXCEPTIONS`, the `AGENTS.md` sentence on file keywords and the once-failed end-to-end test. `ff1ae52` came after the round and was not reviewed by Sol: it changes `agent/README.md` only, the orchestrator read the diff, and Codex was clean on it. The orchestrator re-measured: 628 agent tests on `ff1ae52` and on `main` after the merge; at `da64104`, five functions appended to `probe.py` in turn (the alias and function-default bypasses, a plain `os.open`, an `io.FileIO` writer, `logging`'s `filename` keyword) each fail 26 to 36 of the 142 invariant cases.
- **Decisions:** the issue's preferred fix; what it gives up (size checked when the path is looked at, not a bound on the read; a FIFO swapped in after the look blocks the one read thread until a writer comes) is in the spec, AGENTS.md, README and docstrings. Kept from #99: `stat`/`threading`, all 14 added forbidden names, `READ_OPENERS`/`FILE_KEYWORDS`/`OPENED_WHERE`. Keyword routes get a string-constant check, not a listed name.
- **State:** CI green on `ff1ae52`; Codex reviewed `ff1ae52` with no major issues; Greptile not run (monthly cap). Agent 628 tests (macOS; Linux `--network none` at `da64104`), server 808. No second review round planned.
- **Waiting on the owner:** merge #106. File the follow-up for finding 1: seven retained names (`__delattr__`, `hardlink_to`, `link_to`, `mkfifo`, `mknod`, `openpty`, `symlink_to`), `capath`/`certfile`/`keyfile`, the prefixes `spawn`/`fork`/`posix_spawn`/`subprocess_`, and 44 single-name exemptions in all (`eval`, `popen`, `write_bytes`, `vars` among them) survive removal one at a time. It could also carry these, none of them filed: removing the dead `PREFIX_EXCEPTIONS` and its stale comment, and the AGENTS.md wording the review suggested (it lists file keywords among the "reads"; `logging`'s `filename` can write).
- **Next:** #87 on the same files, after #106 merges. Not filed: `test_e2e.py::test_lookups_that_send_nothing_keep_the_stored_names_at_one_packet_a_second` failed once in 31 full runs under load (real clock at 1 pps) and passed six reruns.

## 2026-11-20 — Claude Code (Opus 5.5) — #99: agent rDNS follow-ups (PR #103), briefed rounds 1 and 2 answered
- **Done:** [PR #103](https://github.com/DeusMaximus/TLG-IPAM/pull/103) closes #99, head `ca2a194`, cut from `main` at `7345d2a`. CI green and Codex clean on `ca2a194`; Greptile not run (monthly cap); CodeRabbit skips the repo.
  - `e11b06f` (the issue):
    - Cancellation wording qualified, no transport abort (call 1): no query is handed to a socket after a lookup that sent nothing; one already handed over may be sent by asyncio after the result set.
    - Two pool guards kill #96's round-2 mutants. `FakeResolver` follows `probe.usable_servers` and refuses `()`.
    - The failover flake was `EADDRINUSE` on `::1` (1/200 on `main`), fixed by `responders_on_one_port`; its gap assertion became the pacer's schedule bound.
    - `resolv.conf` is read by `probe.read_resolv_conf` in one daemon thread: 1 s bound, regular files only, at most 64 KiB parsed.
  - Round 1 (Codex, GPT-6.1 Sol): GO at `e11b06f`, two P3s sent back. Answered in `3a64754`:
    - Tripwire: `io` dropped, with dnspython given lines cut as a text file is, pinned against dnspython opening the file. Write openers are forbidden names; `os.open` is held to an unrebound `os`; read openers are listed per module; 21 bypass cases.
    - `scan.run` bounds the server choice by the job's deadline (PTR timeout on expiry; the read keeps its lock); real-clock regression added.
    - Wording fixes. Agent 602; real-clock tests 50/50 on macOS and Linux.
  - Round 2: GO at `3a64754`. Finding 2 fixed; finding 1 fixed with a remainder. Answered by the prose-only `ca2a194`: `AGENTS.md`, `test_invariants.py` comments/docstrings and call 7 now say what the checks enumerate and what they do not see (an alias of `os`; `logging`'s `filename` / `ssl`'s `cafile` passed as `**{…}`). `agent/README.md` notes the deadline case.
  - **Merged:** `c34feae`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #99 closed. Round 2's remainder and its two P3s are filed as #104, whose preferred fix removes the `os.open` exception (check with `os.stat`, let dnspython open the file). The spill of a held datagram into the next job is a comment on #87. `ca2a194` came after round 2 and was not reviewed by Sol: the orchestrator checked it is prose only (no file under `agent/src/`; `test_invariants.py` parses to the same tree once docstrings and comments are ignored), and Codex was clean on it. The orchestrator re-measured: 602 agent tests on `ca2a194`; the three round-1 bypasses appended to `arp.py` and `probe.py` each fail invariant tests at `3a64754`; 808 server tests merged with #102.
- **Decisions:** PR body, Deliberate calls 1–8; call 7 rewritten in round 1 and narrowed in round 2; calls 1–6 and 8 stand.
- **State:** green on `ca2a194`. Agent 602 (493 on `main`), server 804 (no server file changed); web untouched; no schema, protocol or lock change.
- **Waiting on the owner:** the merge.
- **To file (orchestrator):** a follow-up issue for round 2's remainder and P3s:
  - an alias or default of `os` defeats `os.open`'s read-only check in `probe.py`;
  - the flag negatives pass with `_os_opens` made inert (test them in `probe.py` and assert the reason);
  - star imports and `match` `**rest` are unseen by the rebinding walker;
  - keyword openers passed as `**{…}`.

  Call 1's measured cost (a held datagram in the next job's first second) goes to #87.
- **Next:** #87 (pacer, send loops, spec §5.6's `MAX_PPS` wording) on the same files once this merges; the failover test's `first-query-late` case is the one #87 should tighten.

## 2026-11-20 — Claude Code (Opus 5.5) — #85: an unchanged last_seen is not a change; recursive datetime schema walk (PR #102)
- **Done:** [PR #102](https://github.com/DeusMaximus/TLG-IPAM/pull/102) closes #85, head `0c101f5`, cut from `main` at `7345d2a`. CI green on `0c101f5` (5/5); Codex clean on `0c101f5` (`has_any_findings: false`); Greptile not run (monthly cap); CodeRabbit skips the repo.
  - `hosts.update_host` compares the request's `last_seen` with the stored column as instants and skips the assignment when they are equal. A PUT that repeats the stored value, in any offset form, no longer moves `updated_at`.
  - `test_every_datetime_field_in_the_schemas_is_serialised_with_an_offset` now walks every model `schemas.py` defines except those in `REQUEST_MODELS`, and every model they reach (nested, list, dict, optional) wherever it is defined. Samples have the field's own shape; `AwareDatetime` and pydantic's other constrained datetimes count as datetimes.
  - `test_the_walk_finds_bare_datetimes_wherever_they_are_and_passes_annotated_ones` keeps the issue's three mutations in the suite.
  - The `AGENTS.md` datetime bullet and brief §3.4 (`updated_at`) and §4.3 are updated.
  - **Merged:** `df336cb`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #85 closed. An outside read (Codex, GPT-6.1 Sol) ran in place of Greptile after this entry was written: GO at `0c101f5` with two P3s, filed as #105 (no test holds the stored-column comparison when an observation exists; the walk does not see computed fields, with smaller gaps listed there). It overruled none of the five calls, and found that the edit form never sends `last_seen`, so a plain save cannot write a scan's time into the hand-set column. Codex was clean on `0c101f5`. The orchestrator re-measured: 808 server tests on the head, and with the service comparison disabled the repeated-value test fails.
- **Decisions:**
  - The fix compares instants in the service; the validator still returns an aware value. Making offset-less input naive there would have left explicit-offset input moving `updated_at`.
  - An unchanged PUT still writes one `update` change entry with `before == after`, as on `main`. Only the row update and the `updated_at` move are gone.
  - The walk lists request models rather than response models, so a new model is checked as a response until it is listed.
  - The walk follows `agent_protocol` models that an owner response holds (`ScopeRangeOut` inherits `ScanRange`). Agent-route answers are never roots.
  - Fields typed `dict` or `Any` (audit snapshots) stay outside the walk; the snapshot test covers them.
- **State:** server suite 808 passed, agent suite 493 passed locally; web untouched. No schema change. Nothing half-finished.
- **Waiting on the owner:** merge of #102.
- **Next:** nothing left on #85. The `schemas.py` lane is free once #102 merges.

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
