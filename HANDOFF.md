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

## 2026-11-16 — Claude Code (Opus 5.5) — #9: scan targets and scan scope (PR #53)
- **Done:** [PR #53](https://github.com/DeusMaximus/TLG-IPAM/pull/53) closes #9, head `5c3a7d3`, on `main` at `3ee631b`. CI green. Codex: no findings. Greptile: 5/5, no findings. Automatic reviews only. Merge is the owner's.
  - Table `scan_targets` (one per subnet; `agent_id` nullable FK `ON DELETE SET NULL`; `methods` JSON; `scan_dhcp_pool`; `interval_minutes`; lease and last-scan columns created now, nullable, unwritten until #10/#11). New nullable column `reserved_ranges.scan_excluded`. Additive only.
  - `services/scans.py`: `scan_scope(session, subnet)` → merged inclusive int ranges (usable minus pool unless `scan_dhcp_pool`, minus excluded ranges; exclusion wins); `scan_scope_ranges` → `[{start, end}]`. Also scan-target CRUD, `normalise_methods`, `check_prefix` (/20), `delete_for_subnet`, `unassign_agent`. It reads `Subnet`/`ReservedRange` models directly: `services/subnets` and `services/agents` import it.
  - `ipnet.subtract_intervals`, `ipnet.format_intervals`.
  - Owner REST: CRUD `/api/v1/scan-targets` (responses carry `scope`); `PATCH /api/v1/subnets/{id}/reserved/{range_id}` `{scan_excluded}`; `POST …/reserved` also takes `scan_excluded`.
  - MCP: no new tools or arguments. `delete_reserved_range` refuses an excluded range (service, on `source="mcp"`, 403, message points at the web UI). `get_subnet`/`list_subnets` show `scan_excluded` per range and `scan_dhcp_pool` (null when no target).
  - Subnet delete deletes its target. Agent delete unassigns its targets, one `scan_target` update entry each. CIDR edits below /20 are refused on a scanned subnet. Audit `entity_type="scan_target"`; range exclusion is logged as a `subnet` update.
  - Brief §3.3, §3.6–3.8, new §3.10, §4.2, §5.2, §7, §10. Spec §5.4 (`id` row), §5.7 (route path; POST takes `scan_excluded`). `AGENTS.md`: MCP bullet carries all of invariant 2; new bullet for invariant 4.
  - Tests: backend 186 → 254 (`api/tests/test_scans.py` 56, `test_ipnet.py` +12; frozen schema `api/tests/data/schema_before_scan_targets.sql` from `3ee631b`). Eleven mutations each turned a test red; table in the PR body.
  - **Merged:** `364d0a4`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #9 closed. Both automatic reviews covered `5c3a7d3`, the only commit, with no findings. The orchestrator re-measured 254 at that head and broke two guards (the MCP refusal, the exclusion of reserved ranges), each turning its tests red. #52 merged just before it (entry below); `main` at `364d0a4` runs 254 passed. No follow-up filed. Live state after this merge: PR #54 (#46) is open at `b17fda9`, with a Greptile re-review and the GPT-6.1 Sol briefed round both running (`.dev/46/review-brief-1.md`); no other worker is in flight. With #54 merged onto this `main` in a scratch worktree the suite is 275 and a container refuses `API_KEY=change-me`. The rest of the older orchestrator entry below still holds.
- **Decisions:** 14 under Deliberate calls in the PR. The main ones: route `/reserved/{range_id}`, not `/reserved-ranges/`; the MCP refusal is keyed on `source` in the service; default methods `icmp, arp, rdns`; interval 5–10080 min or null; /20 also enforced on CIDR edits.
- **State:** "leaving scope deletes data" (spec §5.3) is not implemented: there are no observation or dismissal tables yet. The three hook points carry `#11` comments (`scans.update_target`, `subnets.update_reserved_range`, `subnets.update_subnet`). `GET /scan-targets` runs `scan_scope` per target (N+1; fine at homelab scale). Seen, not changed: `pytest -W error` fails `test_bulk.py::test_bulk_is_single_audit_entry` on an unclosed-sqlite `ResourceWarning`, on `main` too; the normal run is clean. Overlap with #46/#43 is only in documents they and this PR each own sections of; whichever merges second rebases.
- **Waiting on the owner:** merge PR #53.
- **Next:** #10 builds jobs from `scans.scan_scope_ranges`, honours `enabled`/`agent_id`/the lease columns, and drops `arp` for a non-L2-local agent. #11 adds observations and fills the three `#11` hooks. #18 builds the UI on `scope` and `PATCH …/reserved/{range_id}`.

## 2026-11-16 — Claude Code (Sonnet 5.5) — #43: pin the container to one uvicorn worker (PR #52)
- **Done:** [PR #52](https://github.com/DeusMaximus/TLG-IPAM/pull/52) closes #43, head `2b95bee`. CI green (backend, frontend, locks). Codex: completed, no findings. Greptile: 5/5, no findings. Automatic reviews only; no briefed round owed. Merge is the owner's.
  - `Dockerfile` `CMD` now passes `--workers 1`. One-worker text updated in `README.md` ("One worker"), the brief (§2.2, §9 row, §10 Operations) and `AGENTS.md`.
  - Measured in containers (output in the PR body). The old `CMD` with `WEB_CONCURRENCY=4` started four processes, and two of three runs on a fresh volume died at startup. With `--workers 1`, `WEB_CONCURRENCY=4` and `UVICORN_WORKERS=4` each leave one process. Without the variable, `/health` is 200 and `/mcp` without a key is 401.
  - **Merged:** `7a5ebbf`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #43 closed. Both automatic reviews covered `2b95bee`, the only commit, with no findings. The orchestrator rebuilt the image from `2b95bee` and re-measured both acceptance lines: one process with `WEB_CONCURRENCY=4`, and `/health` 200 and `/mcp` 401 with and without it. The "two of three runs died" figure for the old `CMD` is the worker's alone. No follow-up filed.
- **Decisions:** `--workers 1` alone is enough, since it wins over the environment, so no entrypoint script or `ENV` unset. The docs say a replaced `CMD` or several replicas still give several processes. No startup guard (not in the issue). Spec §3 unchanged.
- **State:** Docker and docs only, no application code or schema change. Backend suite 186 passed on Python 3.13; web lint, test and build clean. Not tested: `docker compose up`, an upgrade over an existing database, `/health` on the `UVICORN_WORKERS` run. Possible overlap with #9 and #46 is limited to the documents; whichever merges second rebases.
- **Waiting on the owner:** merge PR #52.
- **Next:** nothing owed on #43. M0's remaining item is #5 (owner).

## 2026-11-13 — Claude Code (Fable 5.2) — orchestrator session: M0 and M1 critical path merged; nothing in flight

- **Done:** seven issues taken from ready to merged through `.agents/orchestration.md`, one worker thread each: #4 (PR #42), #35 (#38), #36 (#41), #6 (#44), #7 (#47), #45 (#48), #8 (#49). Each has its own entry below or in `.agents/handoff/2026-11.md`. Follow-ups filed on the owner's go-ahead: #43, #45, #46, #50, #51. A note for the implementer is on #10.
- **Decisions** (owner, 2026-11-13):
  - Two issues may now run together when they edit different sections of the brief, the README or `AGENTS.md`; code files still run one after the other (`.agents/orchestration.md`, step 2). Untried: the next wave is its first use.
  - The automatic reviews do not run again after a fix commit. Comment `@codex review`; the owner retriggers Greptile (`.agents/orchestration.md` → "Verifying a result").
- **State:**
  - `main` at `d735977` plus this entry. Backend suite 186 passed on Python 3.13, no warnings. No open PRs, no worker running.
  - Open in M0: #43, #5 (`owner`). Open in M1: #46 (`briefed-review`; must land before M3), #50 (`stretch`). M2a: #9 is ready; #51 is ready. M2b: #21 is ready. M3: #25 (`owner`) is ready.
  - What this session learned about running the loop is now in `.agents/orchestration.md`: how the orchestrator is woken, the two review rules in the worker brief, where a briefed reviewer's worktree starts, and re-measuring on the PR merged with `main`.
  - Worker worktrees and local branches from this session are still on disk under the T3 worktrees folder. Nothing in them is unmerged.
- **Waiting on the owner:** #5 (redeploy and a deliberate restore; the procedure is `docs/backup-and-restore.md`). #25 (client-compatibility spike). Later: the real-VLAN check in #14, registry permissions for #24, #32.
- **Next:** a wave of three, under the relaxed rule: #9 (scan targets; Opus 5.5), #46 (the `API_KEY` guard; Opus 5.5 and a GPT-6.1 Sol round) and #43 (`--workers 1`; Sonnet 5.5). Then #10, which owes a test of `require_agent` on the real router. #50 and #51 fit beside any wave that leaves `test_auth.py` and `services/agents.py` alone.

## 2026-11-13 — Claude Code (Opus 5.5) — #8: agents and agent tokens (PR #49)
- **Done:** [PR #49](https://github.com/DeusMaximus/TLG-IPAM/pull/49) closes #8, head `f03216f`. CI green on `36bbc62` and `f03216f`. Codex: one P2 on `36bbc62` (case-insensitive name race), fixed in `f03216f` and answered; no findings on `f03216f`. Greptile: 4/5 on `36bbc62` with one P1 (same race), fixed and answered; not yet re-run on `f03216f`. Briefed round owed (`briefed-review`), not started. Merge is the owner's.
  - Tables `agents` (with internal `name_key = name.casefold()`, `UNIQUE`) and `tokens` (additive). Partial unique index `uq_tokens_live_agent_token`: one unrevoked `agent` token per agent.
  - `services/agents.py`: create (agent + `tlga_` token, shown once, stored as SHA-256 + 13-char prefix), rename, enable/disable, revoke (all live tokens; 409 if none), delete (revokes first, then cascades token rows), `authenticate` (hash lookup; unrevoked `agent` kind, `active` agent; sets `last_used_at`), `actor_for` → `agent:<name>` (unused until #10).
  - `auth.require_agent` for `/api/v1/agent/*`: 401 no bearer, 403 "Invalid agent token" for everything else, owner key included. No route mounted there yet; tests use a probe app.
  - Owner REST `/api/v1/agents` (GET, POST, GET/PUT/DELETE `{id}`, POST `{id}/revoke`) behind `require_api_key`.
  - Audit: `agent` create/update/delete; `token` create/revoke; `token_hash` and `name_key` in `_SNAPSHOT_EXCLUDE`. All owner writes: `actor` null.
  - Brief §2.3, §3.6, new §3.8/§3.9, §4.1, §4.2, §7, §10. `AGENTS.md`: agent-token invariant, and spec §7.2 invariant 2 on the MCP bullet. Spec §4.2, §5.4, §5.7 edited.
  - Tests: backend 104 → 153 (`api/tests/test_agents.py`; frozen schema `api/tests/data/schema_before_agents.sql` from `main` at `1dd7bd4`). `test_auth.py` untouched. Each guard was broken in a scratch copy and its test went red.
  - **Merged:** `d735977`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #8 closed. Before the merge, Greptile re-reviewed `f03216f` after the owner's retrigger (5/5, finding fixed), and the briefed round by GPT-6.1 Sol returned GO on that head with two P3s, posted on the PR. Follow-up filed from the P3s: #51 (`briefed-review`, M2a). A comment on #10 carries what that issue owes. With #48 also on `main`, the suite is 186.
- **Decisions:** names 1–74 printable characters, unique case-insensitively in the database; created agents start `active`; `pending` = never held an agent token; disable ≠ revoke; 403, not RFC 6750 401, for a bad agent bearer; `last_used_at` not audit-logged; no token-rotation route (re-enrol is #21). Full list under Deliberate calls in the PR.
- **State:** schema change, additive only. No dependency change. #48 (#45, `test_auth.py`) still open; no overlap expected, rebase if it merges first. Seen, not changed: create responses carry `+00:00` timestamps and read-backs from SQLite do not (same for hosts on `main`).
- **Waiting on the owner:** retrigger Greptile on `f03216f`; start the briefed round (reviewer outside the Claude family); merge PR #49.
- **Next:** #10 (check-in, results, leasing): mount its router with `require_agent` alone, use `agents_svc.actor_for` for `agent:<name>`, test that the real router lacks `require_api_key`, and note that `authenticate` returns the agent without `tokens` loaded. #9: scan targets set `agent_id` to null on agent delete (hook point in `delete_agent`). #21 issues `enrol` tokens into the same table.

## 2026-11-13 — Claude Code (Opus 5.5) — #45: harden the owner-key auth tests (PR #48)
- **Done:** [PR #48](https://github.com/DeusMaximus/TLG-IPAM/pull/48) closes #45, head `ce9b9f9`. CI green (backend, frontend, locks). Codex: no findings. Greptile: 5/5, no findings. Briefed round not started; brief at `.dev/45/review-brief-1.md` in the #45 worktree (gitignored). Merge is the owner's.
  - Only `api/tests/test_auth.py` changes (26 → 59 tests; suite 101 → 134). Nothing under `api/app/`, brief or `AGENTS.md`.
  - Comparison: owner key, a near miss and a wrong key, on REST, `/mcp` and `/mcp/`, must reach `hmac.compare_digest` (`DigestStandIn` replaces `app.auth.hmac`). Forcing the digest's answer flips the outcome. An AST check refuses `==`/`!=`/`in`/`not in` in `is_owner_key`.
  - Session reuse: 18 cases (2 paths × GET/POST/DELETE × no bearer, wrong bearer, `X-API-Key` only) get 401. A `tools/list` with the owner bearer afterwards shows the session was live and survived. Replays go through raw ASGI (`_response_start`): an accepted GET is an endless SSE stream that the httpx transports buffer.
  - Every `/mcp` refusal test enters `running_app()`, with its assertions after the block.
  - Five mutations measured in a throwaway worktree. The equality shortcut fails 7 tests (26 pass on `main`); accept-any fails 23, the wrong-bearer 8 at `assert 200 == 401`; the X-API-Key-as-bearer translation fails 8; session-id-as-credential fails exactly the 18. Output is in the PR body.
  - **Merged:** `a75cb15`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #45 closed. Before the merge, the briefed round by GPT-6.1 Sol returned GO on `ce9b9f9` with one P3, posted on the PR; the orchestrator used its own brief (`.dev/45/review-brief-1.md` in the main checkout). Follow-up filed from the P3: #50 (`stretch`: widen the source guard to callers and helpers). Live state after the merge: `main` has #7 (`ada7db1`) and this, so the suite is 137 tests. #8 is in its own thread and its PR is being verified. #46 and #43 wait behind #8.
- **Decisions:** acceptance line 2 is met by two mutations. Accept-any cannot redden the `X-API-Key`-only tests, because no token reaches the verifier; the translation mutation does. Deliberate call 5; the briefed reviewer may overrule. Session-reuse assertions run after the lifespan block, so failures report at the test line.
- **State:** nothing half-finished; no schema, dependency or app change. Reversed order and three concurrent sessions: 59 passed each. Not covered: HEAD replays, duplicate or whitespace `Authorization` variants (measured on #44), and timing itself.
- **Waiting on the owner:** start the briefed round (a model family other than Opus); merge PR #48.
- **Next:** #46 (refuse an empty or placeholder `API_KEY` at startup; briefed review; before M3). #7 is in flight separately. #8 should reuse `DigestStandIn` and `_response_start` for agent-token tests.
