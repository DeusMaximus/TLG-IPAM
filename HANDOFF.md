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

## 2026-11-24 — Claude Code (Fable 5.2), orchestrator — five PRs merged; #22, #90 and #86 in flight; #100 and #91 decided
- **Done:** five PRs squash-merged on the owner's go-ahead, remote branches deleted. Worker entries are below (the #120 entry is in the archive).
  - First wave: #122 (#120) `ed4a7d6`, Sonnet 5.5, tests only. #123 (#121) `d067821`, Opus 5.5: a packet slot ends with the event-loop step it was granted in; one briefed round (Codex, GPT-6.1 Sol), GO with one P3. #124 (#21) `0226262`, Opus 5.5: enrolment tokens, `POST /api/v1/agent/enrol`, re-enrol; two briefed rounds, NO-GO then GO with one P3.
  - Second wave, so far: #127 (#125) `45712d5` and #128 (#126) `ded8622`, both Sonnet 5.5, tests only, the two P3s from those rounds. Automatic reviews only, both clean on the only head of each.
  - Filed on the owner's go-ahead: #125 and #126 (now closed). #92 is narrowed to what #124 left of it: the plain-HTTP warning comes only after the enrolment token has been issued, and should come before.
  - `.agents/orchestration.md` has the three brief lines proposed since 2026-11-20 (`53afb78`).
- **Decisions (the owner's, 2026-11-24):**
  - Go-ahead for both waves. The second ran five workers at once against the file's limit of three; the owner was told and did not hold the two tests-only issues back.
  - #121 was fixed in the agent, not reworded in the spec. #124 merged with its deliberate calls as written, and with the orchestrator's extension of its scope into `services/results` (ingest rechecks the agent's state and its token under the write lock).
  - #100: option 4. The result set reports name lookups separately from liveness; a name is cleared only on an answered "no name"; `PROTOCOL_VERSION` rises. Recorded on the issue.
  - #91: the 2 MiB bound on an import stands and the dialog does not batch. Length limits on write: `domain` 253 characters, `notes` 4,096; a row over one is that row's `error`. The `bulk_create_hosts` docstring says how large a call may be. Recorded on the issue.
  - #5 and #25 (`owner` label): the owner runs them later, after rebuilding the deployment. Do not raise them in every report. #25 still comes before the rest of M3.
- **State:** `main` is `ded8622` plus hand-off commits. CI was green on `0226262`; it was still running on `45712d5` and `ded8622` when this was written. Measured at the two PR heads, which touch disjoint trees: 1020 server and 924 agent tests. Web: 125 tests, unchanged since #124.
  - **In flight**, each in its own worktree cut from `a8534eb`, Opus 5.5, each owing the automatic reviews and a briefed round. No PR was open when this was written.
    - #22 on `22-agent-enrol-install`: owns `agent/` except `scan.py` and `probe.py`, spec §5.6, the README's agent sections.
    - #90 on `90-bound-unauthenticated-bodies`: owns `api/app/body_limit.py`, brief §4.3, the body-cap text of spec §5.7.
    - #86 on `86-bound-findings-call`: owns `services/findings.py`, `mcp_server.py`, `test_mcp.py`, the brief's tool table, spec §7.2. Told that a `domain` limit of its own is 253 characters, so #91 adds `notes` only.
  - Until #22 merges a new agent is enrolled by hand: the Add dialog shows the `tlge_` token and a `curl` for the exchange. Re-enrol is API-only until #23; the README gives the call.
  - The enrol throttle is global and in memory: 10 attempts that did not enrol per 60 s. One bad request every six seconds keeps new enrolments at `429`.
  - Since #121 scans take longer: Sol's single runs of a 254-address `icmp,arp,rdns` job at 200 pps went from 7.2 s to 7.7 s (macOS) and 7.9 s to 8.3 s (Linux container). One that only just fitted its timeout can end incomplete. The pair captured in #88 stays unattributed; the agent can no longer produce one.
  - Still true from earlier entries: ARP is on by default for a new target; a stuck `resolv.conf` read leaves later scans that rely on the host's resolvers incomplete until the agent restarts, and no README says so; a subnet's CIDR stored before #97 keeps its spelling until its next edit.
  - Reviews: Greptile reviews again and takes about five minutes, but only the first head of a PR. Codex's state for a PR is in its summary comment (`codex-review-state:v1`). CodeRabbit skips the repository. Sol's web run needs Node 24, not 26.
  - Sonnet 5.5 on #120, #125 and #126: each PR open within six minutes, counts and mutations matched every re-measurement. Opus 5.5 on #121 and #21: counts matched; #21 took three fix commits before its GO.
  - Nothing wakes the orchestrator when a worker opens its PR: look for open PRs at the start of each turn and watch each one found.
  - Review briefs and reviews of the first wave are in `.dev/121/` and `.dev/21/` in the main checkout.
- **Waiting on the owner:** nothing now. Next will be the merges of #22, #90 and #86 as each comes back with its GO.
- **Next:**
  - Verify the three PRs as they open; brief Sol on each once CI is green and the first automatic findings are answered.
  - #100 after #22 merges (same agent files; Opus 5.5, briefed). #91 after #86 merges (both edit `schemas.py` and `mcp_server.py`; Opus 5.5, automatic reviews).
  - #23 after #22 (its commands must match what #22 ships), then #92 on the same dialog. #24 waits on #22; #94 goes with #24. #93 and #20 are stretches.
  - Then M3 (#26 to #32) after #25. No OAuth grant may be issued before #28's write-scope checks are in force.
  - No real-network test is planned. The owner's permission to send packets on the test host covered 2026-11-23 only: ask again first.

## 2026-11-24 — Claude Code (Sonnet 5.5), worker — #126: ingest credential read is held to the write lock (PR #128)
- **Done:** [PR #128](https://github.com/DeusMaximus/TLG-IPAM/pull/128) (`126-ingest-lock-guard-test`, head `aaac816`) closes #126. One test in `api/tests/test_results.py`, `test_ingest_reads_the_credential_only_while_it_holds_the_write_lock`. It wraps `results._credential_live`, records `_write_locked()` at the read, and asserts `seen == [True]`, so it also fails if the read is moved, doubled or dropped. No file under `api/app/` changed. CI green (backend, agent, frontend, locks). Codex completed on the head with no findings. Greptile reviewed the head with no findings. CodeRabbit skipped. The owner merges.
  - Merged: `ded8622`, squash, on the owner's go-ahead; the remote branch is deleted. CI green, Codex clean and Greptile clean on `aaac816`, the only head. The orchestrator ran 1020 server tests on that head and repeated the mutation: the new test fails on the lock assertion and the four existing cases pass. No follow-up filed. Nothing from this entry is waiting on the owner any more.
- **Decisions:** the issue offered two forms of the test; I took the lock assertion at the read, reusing `_write_locked`, not a revoke landed between read and lease update. The four existing cases already cover the second form's outcome. `seen == [True]` rather than `all(seen)`.
- **State:** green. Measured on this head: 1020 server and 922 agent tests pass (Python 3.13). The mutation (deleting `await scans.take_write_lock(session)` from `results._release`, in a throwaway worktree, never committed) fails only the new test, on the lock assertion; the four existing cases pass on it. The edit and the output are in the PR body. No schema or web change.
- **Waiting on the owner:** merge of #128.
- **Next:** #125 (agent tests) is the other open test-gap issue in this wave. #22, #90 and #86 as in the previous entry.

## 2026-11-24 — Claude Code (Sonnet 5.5), worker — #125: DNS hand-off guard test (PR #127)
- **Done:** [PR #127](https://github.com/DeusMaximus/TLG-IPAM/pull/127) (`125-dns-handoff-guard-test`, head `6559de8`) closes #125. Tests only, plus a comment.
  - `agent/tests/test_dns_send.py`: the DNS counterpart of `test_icmplib_send.py`. It runs the real `DnsResolver.ptr`, the installed dnspython and `QuerySocket` on the real `scan.Pacer`, with 12 concurrent lookups and an always-overdue clock. Only `create_datagram_endpoint` on the running loop is replaced. The stand-in transport records whether the slot of the lookup that sent the datagram is still open (`pacer.ended < granted[address]`), and answers through `datagram_received`. It sends no packet.
  - The control puts an `await` ahead of `dns.asyncquery.udp` and expects every query to reach the transport after its slot has ended.
  - `scan.py`: three comment lines in `Pacer`'s docstring (a `Pacer` belongs to one event loop). No other file changed.
  - Merged: `45712d5`, squash, on the owner's go-ahead; the remote branch is deleted. CI green, Codex clean and Greptile clean on `6559de8`, the only head. The orchestrator ran 924 agent tests on that head (Python 3.13) and repeated the mutation: the new test fails on the open-slot assertion and the control passes. The Python 3.11 count and the blind-check control are the worker's alone. No follow-up filed. Nothing from this entry is waiting on the owner any more.
- **Decisions:**
  - The open slot is judged per lookup, not by a pacer-wide count. A global count reported late sends as open when another lookup's slot was open.
  - The ARP request needs no counterpart: no library sits between its slot and `PacketLink.sendto`. Said in the PR.
- **State:** green. Agent suite: 924 passed on Python 3.13.12 and on 3.11.18 (922 on `main` at `a8534eb`, plus 2). CI is green on `6559de8`. Codex and Greptile both reviewed the head with no findings, and none are open. Mutation (a `sleep(0)` before `dns.asyncquery.udp`): the new test fails on the open-slot assertion and the control passes. Control with a blind check: the control fails. The mutation worktree was removed and never committed. No schema change.
- **Waiting on the owner:** the merge.
- **Next:** nothing from this entry. #22 owns the rest of `agent/`; the one new file in `agent/tests/` and the `Pacer` docstring edit should not conflict with it.

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
