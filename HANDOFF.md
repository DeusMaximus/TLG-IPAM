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

## 2026-11-24 — Claude Code (Fable 5.2), orchestrator — #120, #121 and #21 merged; #125 and #126 filed; nothing in flight
- **Done:** three PRs squash-merged on the owner's go-ahead, remote branches deleted. Worker entries are below.
  - #122 (#120) `ed4a7d6`, Sonnet 5.5: tests only. Automatic reviews only, both clean on the first head.
  - #123 (#121) `d067821`, Opus 5.5: a packet slot now ends with the event-loop step it was granted in, so hand-offs to the kernel are spaced, for all three packet kinds. One briefed round (Codex, GPT-6.1 Sol): GO with one P3.
  - #124 (#21) `0226262`, Opus 5.5: enrolment tokens, `POST /api/v1/agent/enrol`, re-enrol. Two briefed rounds: NO-GO (three P2s, one P3), then GO with one P3.
  - Filed on the owner's go-ahead, both tests only: #125 (no test fails if the DNS query leaves the step of its grant; from #123's round) and #126 (no test fails if ingest reads the credential before it holds the write lock; from #124's round 2).
  - `.agents/orchestration.md` has the three brief lines proposed since 2026-11-20 (`53afb78`): how a clean Codex result arrives, no git config changes, no `HANDOFF.md` edits.
- **Decisions:**
  - The owner's: go-ahead for #120, #121 and #21 (2026-11-24). #121 is fixed in the agent, not reworded in the spec, with one rule for all three packet kinds. #124 merged with its deliberate calls as written (the next three State lines).
  - The orchestrator's, told to the owner before the merge: #124's scope was extended into `services/results` to close round 1's third finding there. Ingest now rechecks the agent's state and the authenticating token under the write lock, which also closes the same gap for an owner revoke that was on `main`.
- **State:** green. `main` is `0226262` plus hand-off commits, and CI is green on `0226262` and on `d067821`. Measured on a local three-way merge whose tree equals `0226262`: 1019 server and 922 agent tests. Web: 125 tests, lint and build on #124 merged with `main` before #123, which touches no web file. No PR is open and no worker or review is running.
  - A new agent is created `pending` and enrolled by hand until #22: the Add dialog shows the `tlge_` token and a `curl` for the exchange, and the operator puts the returned `tlga_` token into the container command. Re-enrol is API-only until #23; the README gives the call. Agents enrolled before #21 are untouched.
  - The enrol throttle is global and in memory: 10 attempts that did not enrol per 60 s. One bad request every six seconds keeps new enrolments at `429`; enrolled agents are not affected.
  - `POST /agents` returns `enrol_token` and `enrol_token_expires_at` in place of `token`.
  - Owner writes that decide from an agent's tokens (re-enrol, update, revoke, delete) read them under the write lock (`agents._locked_agent`). The automatic reviews and the briefed round found that class one instance per pass, five in all.
  - Since #121 `PACE_YIELD_EVERY` is gone: the event loop turns between any two grants. Scans take longer again: Sol's single runs of a 254-address `icmp,arp,rdns` job at 200 pps went from 7.2 s to 7.7 s (macOS) and 7.9 s to 8.3 s (Linux container), the worker's ICMP-only sweep under 1%. One that only just fitted its timeout can end incomplete. The pair captured in #88 stays unattributed; the agent can no longer produce one.
  - Still true from earlier entries: ARP is on by default for a new target; a stuck `resolv.conf` read leaves later scans that rely on the host's resolvers incomplete until the agent restarts, and no README says so; a subnet's CIDR stored before #97 keeps its spelling until its next edit.
  - Greptile reviews again (cap over): it reviewed the first head of each PR and no later one. With a briefed GO on the final head its re-review was not asked for; on #122 it had the final head. Its MCP `trigger_code_review` is still untried.
  - Sonnet 5.5 on #120: PR open in under three minutes, counts and both mutations matched the re-measurement. Opus 5.5 on #121 and #21: counts matched every re-measurement; #21 took three fix commits before its GO.
  - Codex's state for a PR is in its summary comment (`codex-review-state:v1`); a clean pass is also a plain comment naming the commit. CodeRabbit skips the repository. Sol's web run failed on Node 26 (`localStorage` unavailable) and passed on Node 24, which CI and this machine's default use.
  - Review briefs and reviews are in `.dev/121/` and `.dev/21/` in the main checkout. The worktrees of merged branches under the T3 worktree directory are still there.
- **Waiting on the owner:**
  - A go-ahead for the next wave (below). The go-ahead of 2026-11-24 covered #120, #121 and #21.
  - #100 (whether a scan in which no DNS server gave a usable answer clears stored names) and #91 (the 2 MiB bound on a bulk import): decisions.
  - #5 and #25 (`owner` label). #25 comes before the rest of M3.
  - #92: #124's dialog already refuses to build a plain-HTTP command off loopback and warns. What is left of #92 is an owner who loads the UI itself over remote HTTP. Narrow it or close it.
- **Next:**
  - Proposed wave: #22 (the agent's exchange and the systemd installer; Opus 5.5, briefed) with #90 and #86 beside it (Opus 5.5, briefed; both close before #26 exposes `/mcp`). #125 and #126 fit beside any of them (Sonnet 5.5, tests only): #125 adds a test file under `agent/tests/`, #126 edits `api/tests/test_results.py`.
  - #23 is ready (it depended on #16 and #21) and shares the Add-agent dialog with #92: run them one after the other. #24 waits on #22; #94 goes with #24. #93 and #20 are stretches.
  - Then M3 (#26 to #32) after #25. No OAuth grant may be issued before #28's write-scope checks are in force.
  - No real-network test is planned. The owner's permission to send packets on the test host covered 2026-11-23 only: ask again first.

## 2026-11-24 — Claude Code (Opus 5.5), worker — #21: enrolment tokens, the enrol exchange and re-enrol (PR #124)
- **Done:** [PR #124](https://github.com/DeusMaximus/TLG-IPAM/pull/124) (`21-enrolment-tokens`, head `b45a723`) closes #21.
  - "Add agent" creates the agent `pending` with a single-use `tlge_` token good for an hour.
  - `POST /api/v1/agent/enrol` (`services/enrolment`, on its own `enrol_router`, outside `require_agent`) exchanges the bearer for the agent token. One transaction, under the write lock, with the time read after the lock: a compare-and-set on `used_at`, the old agent token revoked, the new one issued, the agent `active`, the agent's own audit entries pruned to 100.
  - `POST /agents/{id}/re-enrol` issues a new enrolment token.
  - A global throttle sits in front: 10 failed attempts per 60 s, in memory.
  - The exchange is logged `source="agent"` with `actor`.
  - Re-enrol, update, revoke and delete read the agent under the write lock (`agents._locked_agent`).
  - Scope extension decided by the orchestrator: results ingest rechecks the agent's state and its token under the write lock (`results._credential_live`), which also closes the owner-revoke race that was on `main`.
  - Docs: brief (new §4.8, §4.6), spec §4.2/§5.4/§5.6/§5.7, `AGENTS.md`, README.
  - No schema change; `PROTOCOL_VERSION` unchanged.
  - Merged: `0226262`, squash, on the owner's go-ahead, after #123; the remote branch is deleted. CI green and Codex clean on `b45a723`. Greptile reviewed only the first head (`c3804f1`: three findings, all fixed and answered) and was not retriggered. Briefed rounds (Codex, GPT-6.1 Sol): NO-GO at `c72e0a8`, GO at `b45a723` with one P3, filed as #126. The orchestrator ran 1019 server, 899 agent and 125 web tests on the head merged with `main`, removed the ingest credential check (its four cases went red), and on an earlier head the claim's fresh timestamp and its `used_at` condition (each caught). Nothing from this entry is waiting on the owner any more.
- **Decisions:** PR body Deliberate calls 1–17.
  - Optional body fields; null leaves the stored value.
  - One 403 for every refusal; the body is read only for a usable token.
  - Only live tokens are revoked.
  - Re-enrol is refused for a disabled agent and leaves leases to run out.
  - The create response field `token` became `enrol_token` plus `enrol_token_expires_at`.
  - The interim dialog never builds a plain-http command across a network.
- **State:** server 1019, agent 898, web 125 tests pass; lint and build clean. CI green and Codex clean on `b45a723`. Greptile last reviewed `c3804f1` (three P1s, all fixed and answered) and needs a manual retrigger. The first briefed round (Codex, GPT-6.1 Sol, on `c72e0a8`) was NO-GO with three P2s and a P3; all are fixed on `b45a723` (call 17). The branch is not merged with `main` at `c28abfb`. Until #22, a new agent is enrolled by hand with the dialog's `curl`; re-enrol is API-only until #23 (the README gives the call).
- **Waiting on the owner:** the second briefed round on `b45a723` (the orchestrator starts it), a Greptile retrigger, then merge.
- **Next:** #22 (the agent exchanges `IPAM_ENROL_TOKEN` itself; the systemd installer) and #23 (guided install, Re-enrol button, pending enrolments). PR #123's files were not touched.

## 2026-11-24 — Claude Code (Opus 5.5) — #121: packet slots measured from the hand-off; PR #123 open
- **Done:** PR #123 (head `2875149`), which closes #121.
  - On `main` the pacer measured each slot from the grant before it, so a hand-off delayed past a slot let the next packet go at once. New tests show it for all three kinds (gap 0 s). An in-process sweep with no packet sent showed it on `main` (0.045 ms apart in a Linux container); this head has no gap under 5.08 ms.
  - The fix, in `scan.py`: `Pacer` holds its turn until a `call_soon` callback after the step the slot was granted in, and measures the next slot from then. Each probe takes its own slot in `_Pool`. `PACE_YIELD_EVERY` is removed, since the loop now turns between any two grants.
  - Documents: spec §5.6, the README's `MAX_PPS` row, `agent/README.md`, `AGENTS.md` (the budget sentences only) and the brief §2.
  - Merged: `d067821`, squash, on the owner's go-ahead; the remote branch is deleted. CI green and Codex clean on `2875149`. Greptile reviewed only the first head (`01bcf3f`, the same P1 as Codex) and was not retriggered. Briefed round (Codex, GPT-6.1 Sol) at `2875149`: GO with one P3, filed as #125; it would overrule none of the seven deliberate calls, and measured a larger cost than the one below for a mixed job (about 5 to 6% at 200 pps, single runs). The orchestrator ran 921 agent tests on the head merged with `main`, on Python 3.13 and 3.11, and saw the 18 stall tests of the first head fail against `main`'s `scan.py`; it re-measured none of the timings. Nothing from this entry is waiting on the owner any more, and #120 is merged.
- **Decisions:** the owner's fork, option 1, with one rule for all three kinds. The PR's Deliberate calls give why it is a step-end callback rather than a stamp from each sender, and why the 64-send yield went.
- **State:** CI green on `2875149`. 920 agent tests (897 on `main`); server and web untouched; no schema change.
  - Codex: one P1 on `01bcf3f` (the pool hung when a lookup failure cancelled a task before its first step). Fixed in `2875149` with a test, both threads answered and resolved, and Codex clean on `2875149`.
  - Greptile reviewed `01bcf3f` (the same P1), so it reviews again after its cap; it needs a manual retrigger on `2875149`.
  - Cost of the fix: a 254-address sweep at 200 pps takes under 1% longer (4.41 s to 4.44 s for ICMP, 9.52 s to 9.59 s for ICMP and ARP, in a Linux container).
- **Waiting on the owner:** the briefed review round for #123 (a different model family; the orchestrator briefs it), the Greptile retrigger, and the merge. Older items as in the 2026-11-23 entry: #100, #91, #5, #25.
- **Next:** #120 (tests only, in `test_probe.py`). Then #20 as an M2a stretch, then M2b and M3 as listed in the 2026-11-23 entry.

## 2026-11-24 — Claude Code (Sonnet 5.5) — #120: the job-deadline test of the choice of servers no longer depends on its read thread's start
- **Done:** PR #122 (closes #120), head `848212f`. Tests only, one file: `agent/tests/test_probe.py::test_the_choice_of_servers_ends_with_the_jobs_deadline` freezes the event loop's clock (`frozen + skew`) before the scan is created, waits for the read thread with turns of the loop, moves the clock only once the read is held (+0.5 s: still running; +1.0 s: ended), and restores the real clock before its clean-up (the order of the #119 test). Parametrised on a delay in `stalled` before `entered.set()`: 0.0 and 1.5 s.
  - Measured (details in the PR body): the old test with a 1.5 s delay fails at `assert not run.done()`; the new test passes at 0.0 and 1.5 s. A 0.5 s sleep in the cancelled call (`scan.py`, applied then reverted) fails both cases; ignoring the job's deadline while the servers are chosen (`wait_for` removed, reverted) fails both. Agent suite 898 passed (897 plus the second case).
  - The neighbour `test_choosing_the_servers_ends_at_the_jobs_deadline` needs nothing: the read lock is taken before the thread starts and the job ends at its own deadline; it passes with a 1.5 s delay before the read is recorded.
  - Merged: `ed4a7d6`, squash, on the owner's go-ahead; the remote branch is deleted. CI green, Codex clean and Greptile clean (5/5, its first review since the monthly cap) on `848212f`, with no fix commit after them. The orchestrator re-ran the suite (898 passed), the 1.5 s delay against the old test, and both mutations: all as the worker reported. Not fixed, not filed: the late case waits for the read thread by spinning the event loop, so it keeps one core busy for 1.5 s; and the job's own clock is real, so the test still fails if the scan takes over a second of real time to reach the PTR stage, as it did before.
- **Decisions:** the job's own clock (`scan.Clock`, real time) is not frozen; only the loop's is. Nothing in the path reads it before the wait ends. No file under `agent/src/` changed.
- **State:** green. CI (backend, agent, locks, frontend) passes on `848212f`. Codex completed on the head with no findings. Greptile reviewed (5/5, no actionable issues; it did not run the tests). CodeRabbit skips the repo. No push followed the reviews, so no `@codex review` retrigger is owed. Not run under load, on purpose; only the 1.5 s delay was tried.
  - In flight when this was committed (the orchestrator's note): #121 on branch `121-pace-from-handoff` and #21 on branch `21-enrolment-tokens`, each an Opus 5.5 worker thread with no PR open yet, both owing a briefed round. The owner decided #121's fork on 2026-11-24: fix the agent, one rule for all three packet kinds. Everything else live is in the 2026-11-23 entry below.
- **Waiting on the owner:** nothing from this issue.
- **Next:** #121 (owns `scan.py`, `test_pacing.py`, spec §5.6) and #21 are the other work in flight. With #120 merged, the known dependence on a late read thread listed in the 2026-11-21 entry is gone.

## 2026-11-23 — Claude Code (Fable 5.2) — #88 run on the owner's test host: every check that could be run passes; #121 filed
- **Done:** the real-network checks of #88, on a Linux host the owner gave access to, with the owner setting one host to drop ping and then powering it off. No file in the repository changed. The results are [a comment on #88](https://github.com/DeusMaximus/TLG-IPAM/issues/88#issuecomment-7101806047); its boxes are ticked except the two hardware ones, and the issue is closed.
  - Set-up: the agent image and a throwaway server, both built from `main` at `ccd6ceb`, on the same host; the server behind a private CA, so no production data was involved. Nine sweeps (one of a routed /24, eight of the agent's own), each captured with `tcpdump`.
  - Passed: the sweep by the container with `CAP_NET_RAW`; PTR through the subnet's `dns_servers` and through the host's `resolv.conf`; TLS with a private CA, and a refusal without it or with the wrong host name; the agent suite on Python 3.11.18 (897 passed, the dev lock installed there); a host that drops ping found by ARP with its real MAC; a host that left not reported while the kernel still listed it `STALE` (its observation turned not alive, `last_seen_at` kept, its finding gone); the agent host's own MAC; a routed subnet given `icmp`, `rdns` and no ARP.
  - Not run: a parent interface with hardware VLAN offload or a bond (the host's interface is a container veth), and a segment with proxy ARP (none showed). "Complete" was read from the API; nobody looked at the Scanning section in a browser. A certificate from a public CA was not tried.
  - Rate at `MAX_PPS` 200: the agent's own packets never passed 195 in any second. The kernel's ARP during the ICMP sweep of the agent's own subnet (three requests for each silent address) reached 206 a second by itself and 273 with the agent's, the exception spec §5.6 names. On its own subnet the ICMP sweep put 20 to 22 echo requests on the wire, not 254: the kernel sends one only to an address it resolved.
  - Filed, on the owner's go-ahead: #121. In the routed sweep two echo requests left 0.15 ms apart, one of them about 5 ms late. The capture cannot say whether the delay was before the hand-off to the kernel (the agent's side, which the spec bounds) or after it (which it does not).
- **Decisions (the owner's):** an agent session ran #88, which the issue reserved for the owner. Results go on the public issue with no address, MAC or host name of the network. The test set-up was removed after the run. #88 is closed with the two hardware checks marked untested in its body: the only hardware of that kind carries production workloads, and the owner will not experiment on it.
- **State:** green and unchanged: `main` is `a8b58ec` plus hand-off commits (955 server, 897 agent and 118 web tests measured there; CI green). No PR is open and nothing is running. An agent built from `main` has now run on a real network. The test host holds nothing of the run but Docker build cache.
  - Still true from the entry below: ARP is on by default for a new target; since #87 a scan takes longer than before and one that only just fitted its timeout can end incomplete; a stuck `resolv.conf` read leaves later scans that rely on the host's resolvers incomplete until the agent restarts, and no README says so.
  - Greptile's monthly cap ended 2026-11-22. Whether it reviews again is not known until the next PR opens; if it does not, one outside read (Codex, GPT-6.1 Sol) stands in.
- **Waiting on the owner:**
  - #100 (whether a scan in which no DNS server gave a usable answer clears stored names) and #91 (the 2 MiB bound on a bulk import): decisions.
  - #5 and #25 (`owner` label). #25 comes before the rest of M3.
  - Three lines proposed for the worker brief in `.agents/orchestration.md` (how a clean Codex result arrives; a worker never changes git config; a worker never edits `HANDOFF.md`), used in every brief since 2026-11-20 and not yet in the file.
- **Next:**
  - #120: tests only, one test in `agent/tests/test_probe.py`, automatic reviews. Not started; the owner has not given the go-ahead for it.
  - #121: first a test that holds a probe's first step back for more than a slot after its grant, to see whether two hand-offs can come closer than a slot. A briefed round if `agent/src/` changes.
  - #20 is a stretch in M2a. Then M2b (#21 to #24, with #92 and #94 beside #23 and #24; #93 a stretch) and M3 (#26 to #32). #86 and #90 close before #26 exposes `/mcp`; no OAuth grant may be issued before #28's write-scope checks are in force.
