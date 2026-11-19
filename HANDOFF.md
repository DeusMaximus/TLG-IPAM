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

## 2026-11-19 — Claude Code (Fable 5.2), orchestrator — #60 merged after a two-model trial; no work in flight
- **Done:** a one-off trial the owner authorised: #60 (request-body cap) implemented twice, at once, from `main` at `8b14013`, by two workers given the same brief. No other issue ran.
  - **Lane A, Claude Opus 5.5:** PR #80, merged as `2526705` on the owner's go-ahead; #60 closed. Its entry is below.
  - **Lane B, Tidepool Alpha (through OpenCode, runtime mode `auto`):** [PR #81](https://github.com/DeusMaximus/TLG-IPAM/pull/81), closed unmerged as the comparison lane, last head `e3c7827`, four commits. Codex raised a P2 on each of its first two heads (non-body methods uncapped; every streamed message retained), both fixed. Briefed round (Codex, GPT-6.1 Sol): NO-GO at `2b1e7e2`: the PR called a 1.12 MB sample "the largest the UI sends" while `notes` and `domain` are unbounded, and a valid 2.35 MB import that `main` served got 413. The worker answered by raising the cap to 4 MiB; the owner stopped the lane there, before a second round.
  - What the trial showed, for whoever seats a model next. Lane A: PR open in 17 minutes, handed back in 33, one briefed round to GO; its first head had the only real cap bypass (found by the orchestrator's probe and by Codex). Lane B: PR open in 35 minutes, handed back in 68; its counts matched every re-measurement and its first commit handled the framing case lane A missed, but it wrote a `[user]` identity into the repository's shared git config (removed; its commits are not on `main`), edited `HANDOFF.md` and asked to commit it on `main` (stopped at the approval prompt), and twice waited on Codex with a shell loop that could not end. One provider error (a 500 on a streamed response, recovered by itself), no rate limit, no refusal.
  - Both remote branches are deleted. Greptile reviewed neither PR: its monthly cap is reached until 2026-11-22, so its MCP `trigger_code_review` is still untried.
- **Decisions (the owner's):** #80 merges; the trial was called once lane A had a GO and lane B a NO-GO. The roster in `.agents/orchestration.md` is unchanged.
- **State:** green. `main` is `2526705` plus this commit; CI is green on `2526705`. Measured on `main` after the merge: 726 server tests. `agent/` and `web/` are unchanged since the 2026-11-16 entry (374 and 85). No PR is open, no worker and no review is running.
  - A clean Codex result is an issue comment ("Didn't find any major issues", naming the commit); only a result with findings is a review object. A worker that polls `pulls/<n>/reviews` never sees a clean one.
- **Waiting on the owner:**
  - Whether to file three follow-ups from #80, none filed: (1) a `Content-Length` padded with thousands of zeroes answers 500 with a traceback before authentication under httptools, and one with trailing whitespace misses the unread refusal (`body_limit._declared_length`); (2) the owner-key line in `AGENTS.md` still says every `/mcp` refusal is an RFC 6750 401, and a 413 is not; (3) the cap bounds one body, not aggregate memory or upload duration for unauthenticated chunked bodies, which matters before `/mcp` is internet-facing (#26).
  - Whether the new 2 MiB bound on a bulk import is acceptable as shipped. An import with long `notes` that `main` served before #80 is now 413 with the limit in the message (the round measured one at 2.22 MB). A follow-up could batch in the UI or bound `notes` and `domain` per field.
  - Two lines proposed for the worker brief in `.agents/orchestration.md`, not added: how a clean Codex result arrives, and that a worker never changes git config.
  - Everything the 2026-11-16 orchestrator entry listed: the five real-network checks on the scan agent (PR #70's body), #5 and #25 (`owner` label), and the three optional follow-ups from #13.
- **Next:** the rest of wave 1 from the 2026-11-16 entry: #14 (ARP sweep, `agent/`, briefed round), #65 (datetime offsets in responses), #79 (`mcp_server.py`, `services/findings.py`, briefed round); no file in common. Wave 2 once those merge: #78 (same `agent/` files as #14, briefed round), then #75 and #68, which both edit `schemas.py` as #65 does. #20 (stretch) last; it must measure its largest `fdns` result set against `body_limit.BODY_MAX`. Then M2b (#21–#24) and M3 (#26–#32); the OAuth routes sit under the cap as mounted.

## 2026-11-19 — Claude Code (Opus 5.5) — #60: one request-body cap (PR #80)
- **Done:** [PR #80](https://github.com/DeusMaximus/TLG-IPAM/pull/80) closes #60, head `ee8ef97`.
  - New `api/app/body_limit.py`: `BODY_MAX` (2 MiB), the only definition, and `BodyLimitMiddleware`, added last in `main.py` so it wraps the whole app.
  - A declared `Content-Length` over the cap is `413 PAYLOAD_TOO_LARGE`, refused unread. A declared length is trusted only on a request with no `Transfer-Encoding`; any other body is counted and refused at the chunk that crosses the cap. The refusal sends `Connection: close`.
  - It covers every route, `/mcp` included. `routers/agent.py` `_capped_json` and `agent_protocol.RESULTS_BODY_MAX` are removed.
  - Docs: brief §4.3, §4.6, §5.1, §7 and §10; spec §5.7 and §6.6; an `AGENTS.md` invariant line.
  - Tests: 726 server (687 + 39), 374 agent, 85 web; lint and build pass. No schema or lock change.
  - **Merged:** `2526705`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #60 closed. The briefed round (Codex, GPT-6.1 Sol) ran after this entry was written: GO at `ee8ef97`, with two P3s the owner has not yet decided to file (a zero-padded `Content-Length` that answers 500 under httptools, and the `AGENTS.md` line that still calls every `/mcp` refusal an RFC 6750 401). Codex was clean on `ee8ef97`; Greptile did not review this PR (monthly cap). The orchestrator re-measured: 726 server tests on the head and on `main` after the merge, and raw-socket probes against a running uvicorn on both HTTP parsers, which found the `h11` bypass on the first head and pass on `ee8ef97`. #60 was also implemented by a second worker as a comparison (PR #81, closed unmerged); the orchestrator's entry above records it.
- **Decisions:**
  - 2 MiB on every route, measured: a full result set is 1,302,386 B; the UI's 1,000-row import with fields at their maximum is 785,571 B (ASCII) to 1,145,571 B (4-byte owner).
  - `/mcp` is capped: its largest call, `bulk_create_hosts` with 1,000 rows, fits.
  - A chunked body is buffered (at most the cap) before auth, so an over-cap one never reaches auth. This changes spec §5.7 and is listed under Deliberate calls.
  - No `PROTOCOL_VERSION` bump.
- **State:** CI green on `ee8ef97`. Codex: one P1 on `09a69a4` (`Content-Length` beside `Transfer-Encoding` under h11), the same as the orchestrator's P2. Fixed in `ee8ef97` and answered on both; Codex clean on `ee8ef97`. Greptile not run (monthly cap until 2026-11-22). The briefed round is owed and not yet run.
- **Waiting on the owner:** the briefed round and the merge.
- **Next:**
  - #20 must measure its largest `fdns` result set against the cap: the schema allows 4,096 × 32 addresses, which would not fit.
  - `notes` and `domain` have no per-field length limit, and the body cap is now their only bound. A per-field limit is a possible follow-up, not filed.
  - #26 and #29 (the OAuth routes) sit under the cap automatically.

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
