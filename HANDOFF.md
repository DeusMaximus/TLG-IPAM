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

## 2026-11-20 — Claude Code (Opus 5.5) — #14: active ARP sweep (PR #84)
- **Done:** [PR #84](https://github.com/DeusMaximus/TLG-IPAM/pull/84) closes #14, head `eeaafca`.
  - First head `347e64f` added the ARP sweep:
    - New `agent/src/tlg_ipam_agent/arp.py`: builds and reads frames, opens no socket.
    - `probe.PacketLink`: the `AF_PACKET` socket, protocol 0 until bound to ARP on one interface.
    - `probe.ArpSession`: takes a reply only from an address it asked; the first reply wins.
    - `scan.py`: one request per address, then one retry per still-silent address after 1 s. It runs after ICMP, on the same pacer and deadline.
    - `arp` is in `SUPPORTED`.
  - The briefed round (Codex, GPT-6.1 Sol) said NO-GO at `347e64f`: two P2s and two P3s. `eeaafca` answers it:
    - The listener yields after every 64 frames.
    - A send never waits for the socket to be writable.
    - A `loop.SCAN_GRACE_S` (30 s) backstop, and `_Pool` cancels its work in flight when cancelled.
    - A retry is skipped if its address is answered while the retry waits for its pacing slot.
    - Tests strengthened, each checked against a mutant (eleven in all).
    - Wording fixed in `AGENTS.md`, spec §5.2 and §5.6, the brief, the README and the PR body.
  - Docs, as of `eeaafca`: spec §5.2 (how the sweep runs; what an ARP reply cannot prove), §5.6 and §5.7, the brief, both READMEs, the `AGENTS.md` agent bullet.
  - Tests: agent 463 (374 on `main`); server 726 at `347e64f`, with no server file changed since.
  - Docker `--internal` runs only: a round trip, a capture of the requests, and a flood run. The scripts are in PR comments.
  - **Merged:** `7014b58`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #14 closed. The second briefed round (Codex, GPT-6.1 Sol) ran after this entry was written: GO at `eeaafca` with two P3s (no yield in a late ARP send loop, filed in #87 with the `Pacer` finding and call 12; stale PR-body wording, corrected). The head then moved to `e4c2189`, a merge of `main` at `1987465` for one `AGENTS.md` conflict with #83: the orchestrator checked it identical to an automatic merge outside `AGENTS.md`, and CI was green and Codex clean on `e4c2189`. The orchestrator re-measured: 463 agent tests, and the Docker `--internal` ARP round trip on `eeaafca`. Acceptance line 2 (a real VLAN) was not verified before the merge, by the owner's decision; it is in #88.
- **Decisions:**
  - The interface's own addresses get no request and are reported alive with its MAC (spec §5.2).
  - The owner's 2026-11-16 decision ("names on the agent's own link are only set or replaced") ends here. A complete scan on the agent's own link now clears a name.
  - A full send queue fails the send instead of waiting (call 14). The backstop drops the stuck scan's observations (call 15).
  - No `PROTOCOL_VERSION`, schema or lock change.
- **State:** CI green on `eeaafca`. Codex clean on `eeaafca` (and on `347e64f`). Greptile not run (monthly cap until 2026-11-22). A short briefed round on the new commits is owed.
- **Waiting on the owner:**
  - Acceptance line 2 on a real VLAN (steps in the PR body).
  - The next briefed round, and the merge.
  - A follow-up issue, which the orchestrator is proposing: `Pacer` after a late wake (finding 3), together with call 12 (the kernel's own ARP is outside `MAX_PPS`).
- **Next:** #78 (same `agent/` files) once this merges; #68 (how the server and UI report "arp not run" and "cannot be scanned").

## 2026-11-20 — Claude Code (Opus 5.5) — #79: request_scan row guard, paged findings, summary invariant (PR #83)
- **Done:** [PR #83](https://github.com/DeusMaximus/TLG-IPAM/pull/83) closes #79, head `138145c`, from `main` at `b481075`.
  - `test_scans.py`: `test_mcp_request_scan_changes_only_the_request_time` compares every column of every table, with a second, untouched target in the fixture. Only the target's `scan_requested_at` and one audit row may change, and that row is checked whole. `test_mcp_request_scan_refused_changes_nothing[cooldown|disabled|unassigned]`.
  - `list_scan_findings(subnet_id?, type?, cursor?)` returns `{next_cursor, returned, note, findings}`.
    - Each page has at most `findings.PAGE_SIZE` (25) findings and at most `findings.PAGE_BUDGET` (45,000) bytes of them as compact UTF-8 JSON. A finding over the budget comes alone.
    - The cursor is an unsigned position (subnet name/CIDR/id, address, type) in base64url UTF-8 JSON, at most `CURSOR_MAX` (2,048). A malformed one is refused, after checking every slot's type, with `findings.CURSOR_REFUSED`; a well-formed made-up one is read as a position.
    - The note is on every page. The cursor and note come before the findings.
    - The docstrings say there is no snapshot, and to start again without a cursor for the current state.
  - Spec §7.2 invariant 8, the `AGENTS.md` bullet, brief §4.7 ("Audit summaries") and the `findings.py` docstring are narrowed as the owner decided. The `create_host`/`update_host` docstrings carry the finding-copy note.
  - Docs: spec §5.8 (signature, paging, the cursor's limits); brief §5.2 rows; the `AGENTS.md` "MCP tools are hand-curated" bullet.
  - 783 server tests (726 + 57). Web and agent untouched. No schema or lock change. `EXPECTED_TOOLS` stays at 18.
  - **Merged:** `1987465`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #79 closed. The second briefed round (Codex, GPT-6.1 Sol) ran after this entry was written: GO at `138145c` with two P3s (the page budget undercounts by one byte; the `AGENTS.md` line omits the single-finding exception). Those, round 1's finding 4 and the missing bound on one finding's size are filed as #86, to close before `/mcp` is exposed. Codex was clean on `138145c`. The orchestrator re-measured: 783 server tests on the head, and its own mutation (an MCP `request_scan` that also sets `interval_minutes`) turned the row guard red.
- **Decisions:**
  - Pages are bounded by count and by size, against Claude Code's documented limits (50,000 characters of a text result, 25,000 tokens).
    - Measured through the tool on `3a4e5d5`, the briefed round's accepted long values page at 43,698 / 42,376 / 45,732 characters at most.
    - A field cap on `domain` was not added: it would need `schemas.py` (#65's) and wouldn't cover existing rows.
  - `total` is replaced by `returned`.
  - The cursor is unsigned; it is checked for shape, not origin.
- **State:**
  - CI green on `138145c`. Codex clean on `138145c`, after two P2s fixed in `2db5db8` (cursor length for Unicode subnet names) and `3a4e5d5` (page size budget). Greptile not run (monthly cap until 2026-11-22).
  - Briefed round (Codex, GPT-6.1 Sol) on `2db5db8`: NO-GO (one P2, three P3s), answered in [the PR comment](https://github.com/DeusMaximus/TLG-IPAM/pull/83#issuecomment-7028646170). Findings 1–3 and the two wording points are fixed in `3a4e5d5`/`138145c`; finding 4 stands for a follow-up.
  - The second briefed round on `138145c` is owed and not started.
- **Waiting on the owner:**
  - The second briefed round on `138145c`, then the merge.
  - Whether to file the briefed round's finding 4 (work per call is not bounded: 4,521 SQL statements and about 17 s for one page with 500 empty subnets between two findings); the orchestrator is proposing it.
  - #65 is also in flight; whichever merges second takes `main` in with a merge commit.
- **Next:**
  - Not tested: a page's token count (no tokenizer; it assumes about 2 characters a token), and a subnet renamed mid-paging.
  - Possible follow-ups, not filed: a length limit on `domain` (needs `schemas.py`); paging for the REST findings listing and the web UI's panel.

## 2026-11-20 — Claude Code (Opus 5.5) — #65: API datetimes carry a UTC offset (PR #82)
- **Done:** [PR #82](https://github.com/DeusMaximus/TLG-IPAM/pull/82) closes #65, head `3914b4c`.
  - `api/app/times.py` (new): `utc()`, moved from `services/scans` (still importable as `scans.utc`, so `hosts.py`/`findings.py` are untouched).
  - `schemas.UtcDatetime` on every datetime field of every response model (`HostOut`, `ChangeOut`, `TokenOut`, `AgentOut`, `ScanTargetOut`): ISO 8601 UTC with `+00:00`, the same string from `model_dump()` and `model_dump(mode="json")`. Request models keep plain `datetime` (their dump goes to the ORM).
  - `changes.snapshot` writes datetimes with the offset; stored audit entries are not rewritten.
  - MCP picks it up from the models; no edit in `mcp_server.py`.
  - `web/src/utils/time.js` (new): `parseServerTime` (moved from `utils/agents.js`) and `formatServerTime`; `ActivityItem.jsx` no longer calls `new Date(change.ts)`.
  - Brief §4.3 paragraph; new `AGENTS.md` bullet "Datetimes in responses are UTC with their offset".
  - **Merged:** `3cbfa63`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #65 closed. A briefed-style round (Codex, GPT-6.1 Sol) ran in place of Greptile after this entry was written: GO at `3914b4c` with two P3s, filed as #85 (re-sending an unchanged `last_seen` moves `updated_at`; the schema-walk test is narrower than its `AGENTS.md` line). Codex was clean on `3914b4c`. The orchestrator re-measured: 732 server and 94 web tests on the head, lint and build, and a running server answering `+00:00` on write and on read for agents, tokens, hosts and changes, and over MCP (`list_changes`, `list_hosts`).
- **Decisions:** offset added at serialisation, not by a SQLAlchemy `TypeDecorator` (that would change what every service compares, and sits in `models.py`); one `+00:00` string, not pydantic's `Z`; `parseServerTime` kept for old offset-less snapshot values. Agent wire contract unchanged, `PROTOCOL_VERSION` not raised.
- **State:** green. CI passed all 5 checks on `3914b4c`; Codex: no findings on `3914b4c` (PR-opened review). Greptile not run (monthly cap until 2026-11-22). Local: server 732 (726 + 6 in `test_datetimes.py`), web 94 (85 − 3 moved + 12), lint and build clean, agent 374 unchanged. No schema change. Nothing half-finished.
- **Waiting on the owner:** the merge of #82.
- **Next:** whichever of #65 / #79 merges second takes `main` in with a merge commit (no file in common; `findings.py` still calls `scans.utc`, which keeps working). #75 and #68 edit `schemas.py` after this: a new datetime field in a response model must be `UtcDatetime` (`test_every_datetime_field_in_the_schemas_is_serialised_with_an_offset` fails otherwise).

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
