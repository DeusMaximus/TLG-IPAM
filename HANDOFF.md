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

## 2026-11-13 — Claude Code (Sonnet 5.5) — #4: scheduled backup pull and restore procedure (PR #42)
- **Done:** [PR #42](https://github.com/DeusMaximus/TLG-IPAM/pull/42) closes #4, head `21b7bba`. CI green. Codex: no findings (on `16303b1`). Greptile: three P2s, all fixed and answered; no re-review of `21b7bba` had arrived. CodeRabbit skips this repo (under 10 stars). Merge is the owner's. Docs and one `AGENTS.md` line only; no application code, endpoint or schema change.
  - `docs/backup-and-restore.md` (new):
    - **Pull:** a scheduled pull of `GET /api/v1/backup` from another host. A POSIX sh script writes dated `ipam-<UTC stamp>.db` files and keeps the newest N. Before saving or pruning it requires HTTP 200 (redirects not followed), a non-empty file, `integrity_check = ok` and the `sections`/`subnets`/`hosts` tables. Systemd timer and cron variants; cron mails stderr only.
    - **Restore:** `tlg-ipam-restore restore <snapshot>` and `undo <dir>`, one `set -eu` script. It refuses a bad snapshot before stopping the app, and checks the saved database exists before removing the current one.
  - README ("Backups and restore", "One worker"), the brief (§2.2 one-worker paragraph, §4.2 pointer, §9 row, two §10 lines) and an `AGENTS.md` invariant line for the single worker.
  - Measured on a local `docker compose` instance, from the doc's own code blocks. Pull, restore, undo and cleanup all worked. An empty 302, an empty 200, a foreign database and a wrong key were each rejected with no file left. A missing, empty and corrupt snapshot were each refused with the app still up. Output is in the PR body.
  - **Merged:** `59acad5`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #4 closed. Before the merge, Codex and Greptile both re-reviewed `21b7bba` with no findings (Greptile 5/5), and the orchestrator re-ran pull, restore and undo at that head. Follow-up filed: #43 (pin `--workers 1` in the Dockerfile, M0). Live state after the merge: #35 and #36 are merged (entries below), so M0 has only #43 and #5 (`owner`, now unblocked) left. #6 was started next, in its own thread, with Opus 5.5.
- **Decisions:**
  - The restore moves the old `ipam.db`, `-wal` and `-shm` into `/data/before-restore-<stamp>/` instead of overwriting. After a normal stop the data was in a 168 KB `-wal` beside a 4 KB `ipam.db`.
  - Retention keeps the newest N files, not an age window, and runs only after a successful pull.
  - The integrity check opens the snapshot with `?immutable=1`. The snapshot is in WAL mode, and a plain open left `-wal`/`-shm` litter.
  - The one-worker reasons are the ones true on `main`: stateful MCP sessions (from the code, not reproduced), SQLite's single writer, and uncoordinated per-process startup (measured: two workers on a fresh database sometimes fail with `database is locked`). The spec's M3 linking-state reason is not stated in the brief or README, which describe `main`.
  - Not done: `--workers 1` in the Dockerfile `CMD`. `WEB_CONCURRENCY` would override the default, which is why the docs name it.
  - Orchestrator, with the owner (2026-11-13): the automatic reviews do not re-run after a fix commit. `.agents/orchestration.md` → "Verifying a result" now says how to retrigger them.
- **State:** backend suite 80 passed; frontend lint, 3 tests and build pass. Not run: the systemd timer, the cron line including the mail itself, a pull from another host over a real network.
- **Waiting on the owner:** merge PR #42. #5 (redeploy and a deliberate restore on the deployed instance) is the owner's; its restore step is `docs/backup-and-restore.md`. Decide whether to pin `--workers 1` in the Dockerfile.
- **Next:** #35 and #36 are independent of this. M1 follows: #6, #7, #8; #6 and #8 owe a briefed review round. #6 will edit the MCP auth parts of the README and the brief, which this PR left alone.

## 2026-11-13 — Claude Code (Opus 5.5) — #36: CI checks the locks match their .in files (PR #41)

- **Done:** [PR #41](https://github.com/DeusMaximus/TLG-IPAM/pull/41) closes #36, head `a317b81`. CI green. Codex: no findings. Greptile: 5/5, no findings. Merge is the owner's.
  - New `locks` job in `.github/workflows/ci.yml`. It installs uv 0.13.6 (`astral-sh/setup-uv@v10.3.3`, no cache), runs the two `AGENTS.md` → "Dependency lock" commands in place in `api/`, and fails on any `git diff` of either lock. The failure shows the diff and an annotation naming both regenerate commands and the uv version. It also fails if `AGENTS.md` stops listing those commands word for word.
  - `AGENTS.md` → "Dependency lock" names uv 0.13.6 (`uvx uv@0.13.6 pip compile …`) and what the job checks. One brief line (Acceptance → Testing) mentions the check.
  - **Merged:** `bb15e69`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #36 closed. #35 merged just before it as `8a0c89e` (entry below). Live state after both: #4 is open as [PR #42](https://github.com/DeusMaximus/TLG-IPAM/pull/42), head `21b7bba`; three Greptile findings are fixed and answered, and the new head waits on a Codex re-review (requested) and a Greptile retrigger (the owner's). No follow-up issues filed.
- **Decisions:**
  - The check recompiles in place instead of resolving afresh. uv keeps every pin, and its hashes, that still satisfies the `.in` files, even a yanked one. That is what keeps the check green when upstream releases. Measured, not assumed.
  - No path filter: the job runs on every PR and push and takes about 7s.
  - uv 0.13.6 is the newest release older than two weeks. uv 0.9.30, 0.13.6 and 0.13.8 all reproduce the committed locks byte for byte.
- **State:**
  - Backend 80 passed (the pytest-asyncio warning, #35). Frontend lint, tests and build pass. No schema change; no application code touched.
  - Throwaway drafts #39 and #40 showed the red and green cases on real CI. Both are closed and their branches deleted.
  - Not reproduced: a pinned version deleted from PyPI would fail the job on unrelated PRs, as `--require-hashes` installs would.
  - The check accepts a `.in` change that the current pins already satisfy, and a hash removed from a lock by hand. Both are by design and listed in the PR.
- **Waiting on the owner:** merge PR #41. Bumping uv means changing `ci.yml` and `AGENTS.md` together. Later items are unchanged: #5, #25, the real-VLAN check in #14, registry permissions for #24, #32.
- **Next:** M0 has #4 and #35 in flight, plus #5 (`owner`). Then M1 in order: #6, #7, #8; #6 and #8 owe a briefed review round.

## 2026-11-13 — Claude Code (Sonnet 5.5) — #35: fixture loop scope set (PR #38)
- **Done:** [PR #38](https://github.com/DeusMaximus/TLG-IPAM/pull/38), head `bf0a0fe`, adds `asyncio_default_fixture_loop_scope = function` to `api/pytest.ini`. It is the only file changed. On Python 3.13 the pytest-asyncio deprecation warning is gone and the warning summary is empty. Three concurrent sessions each gave 80 passed. CI green (4/4); Codex and Greptile finished with no findings.
  - **Merged:** `8a0c89e`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #35 closed. No follow-up issues filed. The orchestrator's own three concurrent sessions all passed, and one printed the pytest `rm_rf` warning known since #2.
- **Decisions:** `function`, not `fixture-scope`. Every async fixture in `conftest.py` is function-scoped, so behaviour is unchanged, and it is the default pytest-asyncio's warning says a future version will adopt. The module-level engine needed no change.
- **State:** nothing half-finished; the PR is ready to merge. A venv built on Python 3.15 still shows one beartype `DeprecationWarning` (`AsyncKeyValue`, FastMCP dependency tree). It does not appear on 3.13, which CI uses, and was left alone as a dependency matter. The pytest `rm_rf` warning did not appear in the concurrent runs.
- **Waiting on the owner:** merge PR #38.
- **Next:** #4 (docs, in flight) and #36 (CI lock check, in flight); then M1: #6, #7, #8. #36 may want a Python 3.15 look if the beartype warning matters later.
