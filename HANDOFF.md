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

## 2026-11-16 — Claude Code (Fable 5.2), orchestrator — session end: fifteen PRs merged, no work in flight, next wave named
- **Done:** fifteen PRs squash-merged on the owner's go-ahead, each with its worker's entry in this log or the archive (`grep -n '^## ' .agents/handoff/2026-11.md`): #55, #50, #10, #51, #16, #61, #18, #11, #12, #59, #73, #15, #13, #19, #17.
  - `main` is at `2e25e32` plus hand-off commits. CI is green on `2e25e32`. Measured on `main` after the last merge: 687 server tests, 85 web tests, lint and build pass. The agent suite was 374 at `de13ee9`; nothing under `agent/` has changed since.
  - What M2a now ships: agents and tokens, scan targets, check-in and job leasing, results ingest, last-seen from observations, the scan agent (ICMP and reverse DNS), findings with accept and dismiss, the Agents page, subnet scanning and findings in the UI, and the MCP tools `list_scan_findings` and `request_scan`.
  - Issues filed this session: #59, #60, #61, #65, #68, #73, #75, #78, #79. #59, #61 and #73 are closed.
  - `.agents/orchestration.md` changed (`a7f96d6`): a briefed round starts once CI is green and the first automatic pass is answered; after a GO at the current head a Greptile re-review is optional; a head that moves after a GO gets a short round on the new commits, or the orchestrator's own check when they carry no hand-written code.
- **Decisions (the owner's):**
  - An incomplete result set never clears a stored PTR name (`services/results`, PR #70). On a subnet whose agent sits on its own link, scans stay incomplete until #14 ships ARP, so names there are set or replaced and not cleared.
  - Hostnames stay in the change summaries of MCP host writes; #79 qualifies the invariant's wording to say so.
  - No OAuth grant may be issued before #28's write-scope checks are in force (noted on #26 and #28).
- **State:** green. No PR is open, no worker is running, no review is running. Open in M2a, all ready (every dependency closed): #14, #60, #65, #68, #75, #78, #79, and #20 (stretch). 44 worktrees of merged branches and finished reviews remain under the T3 worktree directory on this machine; none was removed.
- **Waiting on the owner:**
  - The go-ahead for the next wave below.
  - The five checks on the scan agent that need a real network (listed in PR #70's body): a real VLAN sweep, PTR lookups against the owner's DNS, TLS against the deployed certificate, the packet rate measured on the wire, and Python 3.11. No agent session may run them.
  - #5 and #25, which carry the `owner` label.
  - Three optional follow-ups the #13 worker raised, not filed: a warning in the Add agent dialog when the UI is open over plain HTTP; a resolver allowlist on the agent's host; pinning the agent's base image by digest.
  - Greptile re-reviews are still retriggered by hand. Its MCP server's tools, `trigger_code_review` among them, were listed in the orchestrator's session at the end of this day but have not been tried: try one on the next PR that needs a re-review before asking the owner.
- **Next:**
  - Wave 1, four lanes with no file in common: #14 (ARP sweep, `agent/`, briefed round), #60 (request body cap: `main.py`, `routers/agent.py`, briefed round), #65 (datetime offsets in responses), #79 (`mcp_server.py`, `services/findings.py`, briefed round).
  - Wave 2, once those merge: #78 (same `agent/` files as #14, briefed round), then #75 and #68, which both edit `schemas.py` as #65 does; #68 also touches `web/`. Give each brief its files, and have the second to merge take `main` in with a merge commit.
  - #20 (forward-DNS check) last; it is a stretch issue.
  - After M2a: M2b (#21–#24), then M3 (#26–#32). #21 carries notes from the review of #59; #26 and #28 carry the ordering note above.

## 2026-11-16 — Claude Code (Opus 5.5) — #17: MCP scan tools (PR #76)
- **Done:** [PR #76](https://github.com/DeusMaximus/TLG-IPAM/pull/76) closes #17, head `e9ca0b8` (= reviewed `4ed7465` + merge of `main` at `de13ee9`). CI green (5 checks). Codex clean on `e9ca0b8` and on `4ed7465`; Greptile 5/5 on `4ed7465` (not re-run on the merge, by the orchestrator's call). Briefed round (Codex GPT-6.1 Sol): GO at `4ed7465`, three P3s for a follow-up issue. Tests: 687 on the head (677 on `main` measured + 10).
  - `list_scan_findings(subnet_id?, type?)` wraps new `findings.list_named`: one subnet's findings or every subnet's (by subnet name, then address), each the REST finding unchanged plus `subnet_name`, `subnet_cidr`, `host_name`, `host_fqdn`; `dynamic` left out; returns `{findings, total, note}` with `findings.NETWORK_REPORTED_NOTE`. `type` is the `FindingType` enum in the schema.
  - `request_scan(subnet_id)` wraps `scans.request_scan` with source `mcp`; returns `scans.scan_request_out` (`subnet_id`, `scan_requested_at`, `next_request_after`), never the target. A cooldown refusal is a tool error naming the time; `scans.next_request_after` rounds up to the second (REST 429 too).
  - `hosts.fqdn_of` factored out of `_host_dict`. `test_scans.py`'s MCP scan-config guard allows exactly these two tool names; its argument check is unchanged.
  - Docs: brief §4.7 line, §5.2 rows + closing line, §5.4 examples, §10 (18 tools, acceptance line); spec §5.8 four MCP bullets; `AGENTS.md` MCP-tools bullet and one phrase in the wire-contract bullet.
  - Merge of #70: one conflict, `AGENTS.md`; resolved as `main`'s file plus this branch's two edits. `api/` is unchanged from `4ed7465` apart from `main`'s changes.
  - **Merged:** `2e25e32`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #17 closed. The issue asked for automatic reviews only; the orchestrator added the briefed round because one tool hands network-reported text to a model and the other lets one ask for packets. The merge commit `e9ca0b8` carries no hand-written code, so the orchestrator checked it in place of a second round: a re-merge shows `AGENTS.md` as the only hand-resolved file, its wire-contract bullet differs from `main`'s by the one phrase, and every other file's diff against `main` is the reviewed one. It re-measured 687 on the head and on `main` after the merge. Greptile did not see `e9ca0b8`, which is optional after a GO. Follow-ups on the owner's say-so: #79 (M2a, `briefed-review`) for a test that `request_scan` changes only the request time, paging for `list_scan_findings`, and wording that says an MCP host write's summary may name a hostname an assistant copied from a finding (the owner chose to keep hostnames in those summaries); and a note on #26 and #28 that no grant may be issued before #28's write-scope checks are in force.
- **Decisions:** nine in the PR. Main ones: no `require_scopes` yet (#28 does every write tool at once; FastMCP hides a scoped tool from the in-process test client); network values marked as data, not withheld; `dynamic` left out with no argument to include them; the result is the request, not the target.
- **State:** green; no schema change; nothing under `web/`; nothing half-finished.
- **Waiting on the owner:** the merge (approved once rebased, now done). Filing the follow-up issue for the round's three P3s. Whether to also file one for the change summary that names a PTR hostname an assistant copies into `create_host`/`update_host`.
- **Next:** #28 adds `ipam:write` to every write tool, `request_scan` included, and picks a test path for scoped tools. #19 may show `next_request_after` beside Scan now. #60 is unblocked now that #70 has merged.

## 2026-11-16 — Claude Code (Opus 5.5) — #19: findings panel, badge, and last-seen (PR #77)
- **Done:** [PR #77](https://github.com/DeusMaximus/TLG-IPAM/pull/77) closes #19, head `1873ee8`, against `main` at `9f47b28`; commits `bbcf065`, `a631d58`, `1873ee8`. Frontend and the brief only (§6.1, §6.2, my §7 lines). CI green. Codex: P1 and P2 on `bbcf065`, both fixed; clean on `1873ee8`. Greptile: 4/5 on `bbcf065`, three P2s, all fixed and answered; not re-reviewed since.
  - `FindingsPanel.jsx` (new): count badge, and a panel grouped by type with accept and dismiss per row and confirmed "Accept all N" per type. Accept sends the key with its `fingerprint`, at most 1,000 keys a request.
  - The per-record answer stays on screen until closed: each skipped or error record with the server's reason. A 4xx-refused request reads "not sent"; any other failure reads "unknown", and the view reloads.
  - A proposal that sets a domain override says so before it is accepted. "Show dynamic" shows only while pool scanning is on. Once shown, the panel stays for that subnet.
  - `SubnetView.jsx`: shows findings for the current subnet only. It holds the scan-target fetch (`useScanTargets`, moved out of `ScanSettings`). A new `last_scan_at` reloads hosts and findings; an accept reloads hosts, subnet and tree.
  - Failed reloads of the findings, the host list and the scan target each say so.
  - `HostTable`/`HostDrawer`: last seen read as UTC (was `new Date()`), with its source. "not scanned" only for a subnet with no scan target.
  - 85 tests (51 + 34). Every hand mutation in the PR's table goes red. Hand run against a real server on `bbcf065`: a real skipped accept showed `skipped, changed`.
  - **Merged:** `8087594`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #19 closed. Before the merge Greptile re-reviewed `1873ee8` after the owner's retrigger: 5/5, all three findings fixed. The issue asked for automatic reviews only, so no briefed round ran. The orchestrator re-measured lint, 85 tests and the build on the head and on `main` after the merge, and confirmed that hiding a skipped record's reason turns two tests red. The hand run against a real backend is the worker's claim alone. The "not scanned" gap below went into #68 on the owner's say-so: the server is to report whether a host is inside the scan scope, so the browser never works with addresses.
- **Decisions:** ten under Deliberate calls. After Codex's P1, the UI does no address arithmetic and does not compare hosts with the scope ranges. Bulk accept is per type; there is no bulk dismiss.
- **State:** green, nothing half-finished, no schema change, nothing under `api/` changed. Not built: "not scanned" for hosts outside a scanned subnet's ranges, because `ScanTargetOut.scope` carries address strings only.
- **Waiting on the owner:** retrigger Greptile on `1873ee8`; the merge. Whether to file the backend follow-up below.
- **Next:**
  - Backend follow-up: `start_int`/`end_int` on each `scope` range, or per-host scope membership on host responses. Then `utils/findings.scopeChecker` can answer `out` for every host outside scope.
  - #68: `ScanSettings` now takes `targetsRes` from `SubnetView`, and shows a failed reload of the target.
  - #17 and #13: whichever merges after this rebases on its brief §7 lines.

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
