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

## 2026-11-13 — Claude Code (Opus 5.5) — #7: changes.actor in the audit log (PR #47)
- **Done:** [PR #47](https://github.com/DeusMaximus/TLG-IPAM/pull/47) closes #7, head `ad466b1`. CI green (backend, frontend, locks). Codex: no findings on `ad466b1`. Greptile: 5/5, no findings on `ad466b1`. Automatic reviews only; no briefed round owed. Merge is the owner's.
  - `Change.actor` (`String(80)`, nullable); `changes.record(..., actor=None)`; `ChangeOut.actor`, so `GET /api/v1/changes` and the `list_changes` tool return it (docstring updated). None of the 17 `record()` call sites passes it: every write records null.
  - `database.init_models(target=engine)` takes an optional engine so a test runs the real start-up path on its own file. `_add_missing_columns` adds the column unchanged.
  - UI: the dashboard activity row is now `web/src/components/ActivityItem.jsx`; it shows `actor` after the source badge when set.
  - Brief §3.6 `actor` row, MCP tool table, dashboard UI line. `AGENTS.md` audit-log invariant: `actor` is null for the owner key; a name a client or agent declares goes in `actor`, never in `summary` (spec §4.3).
  - Tests: backend 101 → 104 (`test_changes.py`: migration from `main`'s frozen schema `api/tests/data/schema_before_actor.sql`; actor returned by REST and MCP; every REST write route and MCP write tool records null, 27 rows). Frontend 3 → 5 (`ActivityItem.test.jsx`). Each test was seen failing with the code broken. Checked by hand: this branch's uvicorn on a database made by `main`'s code: column added, old rows null over REST and over HTTP MCP with a bearer key.
  - **Merged:** `ada7db1`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #7 closed. The orchestrator repeated the old-database check with a database made by `main`'s code. No follow-up issues filed. Live state after the merge: #45 is open as [PR #48](https://github.com/DeusMaximus/TLG-IPAM/pull/48), head `ce9b9f9`, automatic reviews clean, with a GPT-6.1 Sol briefed round running. #8 was started next, in its own thread. #46 and #43 wait behind #8: each edits the brief.
- **Decisions:** no spec change; `ChangeOut.actor` has no default; the migration test uses a frozen `sqlite3 .schema` of `main` at `20f0c53`, not the current models. Full list under Deliberate calls in the PR.
- **State:** schema change, additive only (one nullable column). Nothing half-finished. #45 (`test_auth.py` only) is in flight separately and does not overlap. The local venv must be Python 3.13 to match CI: a plain `python3` here is 3.15 and shows a beartype warning.
- **Waiting on the owner:** merge PR #47.
- **Next:** #8 (agent tokens; first writer of a non-null `actor`, `agent:<name>`; owes a briefed round). #26 writes `grant:<client name>`; #29 sanitises the client name before it reaches `actor`. #43 and #46 wait on brief edits; #25 (`owner`) is unblocked.

## 2026-11-13 — Claude Code (Opus 5.5) — #6: bearer auth on /mcp through FastMCP (PR #44)
- **Done:** [PR #44](https://github.com/DeusMaximus/TLG-IPAM/pull/44) closes #6, head `6c65d78`. CI green (backend, frontend, locks). Codex: no findings on `6c65d78`. Greptile: still pending when the session ended, nothing posted. Briefed round not started; brief at `.dev/6/review-brief-1.md` in the #6 worktree (gitignored). Merge is the owner's.
  - `auth.py`: `is_owner_key()` (`hmac.compare_digest` on UTF-8 bytes) is the only owner-key check. `OwnerKeyVerifier(TokenVerifier)` grants `ipam:read` + `ipam:write`. `APIKeyASGIMiddleware` deleted.
  - `mcp_server.py`: `FastMCP(..., auth=OwnerKeyVerifier())`. `main.py` mounts `mcp_app` unwrapped; `MCPPathRewriteMiddleware` unchanged.
  - `/mcp` with no bearer (including `X-API-Key` only): 401, empty body, `WWW-Authenticate: Bearer`. Wrong bearer: 401 `invalid_token` (RFC 6750 JSON). REST unchanged: 401 missing, 403 wrong, no bearer accepted.
  - README and brief (§4.1, §5.1 with a 401 table, §5.3, layout, §10): `mcp-remote` sends `--header "Authorization: Bearer ${TLG_IPAM_KEY}"`. `AGENTS.md`: one invariant line (owner key only via `is_owner_key`; `/mcp` auth is FastMCP's `auth=`; its 401s are the one response-shape exception).
  - `test_auth.py`: 5 → 26 tests (suite 80 → 101). A real FastMCP HTTP client runs through the whole app on both paths. Measured on a running server with curl and `mcp-remote@0.15.2` (macOS), also with the SPA catch-all live.
  - **Merged:** `86dd8db`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #6 closed. Before the merge, Greptile finished on `6c65d78` with no findings (5/5), and the briefed round by GPT-6.1 Sol returned GO on that head with two P3s, posted on the PR. The orchestrator used its own brief (`.dev/6/review-brief-1.md` in the main checkout), not the worker's. Follow-ups filed from the P3s: #45 (harden the auth tests) and #46 (refuse an empty or placeholder `API_KEY` at startup; `briefed-review`; must land before M3 exposes `/mcp`). Live state after the merge: #7 and #45 were started next, in their own threads. #43, #46 and #8 wait: each edits the brief. #25 (`owner`) is unblocked.
- **Decisions:** FastMCP's 401 shape kept, not wrapped in the REST envelope. No `required_scopes` yet (M3, #28, adds per-tool scope checks). `client_id="owner"`. `auth=` on the `FastMCP` constructor, so in-process `Client(mcp)` tests skip it. Full list under Deliberate calls in the PR.
- **State:** nothing half-finished; no schema or dependency change. The PR is not linked to a T3 thread (the link tool needs a thread id this session did not have).
  - Seen, not changed (same on `main`): with the SPA served, `/.well-known/oauth-*` answer `index.html` 200, which M3 (#26) must take over. With `API_KEY=""`, an empty bearer authenticates on `/mcp`, as an empty `X-API-Key` does on `main`; compose's `${API_KEY:?}` blocks that. Possible follow-up issue.
- **Waiting on the owner:** start the briefed round (different model family from Opus); after Greptile posts, every finding gets answered; merge PR #44. Decide whether to file the empty-`API_KEY` guard.
- **Next:** #7 (`changes.actor`) and #8 (agent tokens; `/api/v1/agent/*` bearer auth; owes a briefed round). #8 should reuse `is_owner_key`'s constant-time pattern and change `test_rest_does_not_accept_bearer` deliberately if agent bearer auth goes on REST paths.
