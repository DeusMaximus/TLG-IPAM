# TLG-IPAM

Homelab IPAM: FastAPI + SQLite backend, React SPA, and an MCP server at `/mcp` so AI assistants (Claude, ChatGPT, or any other MCP client) can query/update it. The full specification — data model, endpoints, MCP tool inventory, UI spec, acceptance criteria — lives in `TLG-IPAM-Project-Brief.md`. **Keep the brief in sync when behaviour changes**; it is the source of truth, not documentation theatre.

## Workflow

v0.2 (scan agents, remote MCP) is specified in `docs/v0.2-spec.md` and tracked as GitHub milestones M0–M3. The spec is the contract for work that has not shipped; the brief describes what `main` does.

- **Session start:** read the newest entry in `HANDOFF.md`. It holds live state: what is merged, in flight, broken, or waiting on the owner.
- **One issue, one branch, one PR.** Branch `<issue>-<slug>` from `main`. The PR closes the issue and carries in the same diff: the tests, the brief update for the behaviour it ships, and the `AGENTS.md` line for any invariant it adds or changes.
- **Green before the PR opens:** the commands CI runs (`.github/workflows/ci.yml`), locally.
- **The PR body is the record.** It opens with the authoring agent, model, and head sha, then **What**, **Deliberate calls**, **Tests**. `.agents/review-brief.md` holds the body shape; read it when opening a PR.
- **Merge needs green CI and every automatic review finding answered.** Codex and Greptile review each PR when it opens: fix each finding or reply on its thread with the reason it stands. The owner merges.
- **A briefed review round as well** for a PR that touches authentication, tokens, OAuth, agent ingest, or code that sends packets. It needs a GO from a reviewer in a different model family from the author; `.agents/review-brief.md` is how the round is briefed.
- **When the spec is wrong** (the code or a spike shows it), change `docs/v0.2-spec.md` in the same PR and list the change under Deliberate calls.
- **Session end:** add a `HANDOFF.md` entry using the template in its header. Hand-off entries commit straight to `main`, so the next session sees them without waiting on a PR. A session working in a worktree ends its final message with its entry, and the orchestrator commits it.
- **Orchestrating** (handing issues to other sessions and checking their results): read `.agents/orchestration.md` first.

`.agents/README.md` indexes the process material that is not needed on every turn.

## Commands

```sh
# Backend (one-time setup: python3 -m venv api/.venv && api/.venv/bin/pip install --require-hashes -r api/requirements-dev.txt)
cd api && .venv/bin/python -m pytest                  # test suite — run before declaring any backend change done
cd api && API_KEY=dev-key .venv/bin/uvicorn app.main:app --port 8080 --reload
#   add STATIC_DIR=../web/dist to also serve the built SPA (single-origin, like production)

# Frontend
cd web && npm run dev                                  # hot reload, proxies /api → :8080
cd web && npm run build                                # production bundle to web/dist

# Full stack (needs .env with API_KEY)
docker compose up --build
```

### Dependency lock

`api/requirements.txt` (runtime) and `api/requirements-dev.txt` (runtime + test) are **generated lock files**: every package pinned by version and hash, resolved for Python ≥ 3.12 on any platform. The Dockerfile and CI install them with `pip install --require-hashes`. Edit the direct dependencies in `api/requirements.in` / `api/requirements-dev.in` (`fastmcp` stays `>=4.1,<5`), never the lock files, then regenerate **both** from `api/` with [uv](https://docs.astral.sh/uv/):

```sh
uv pip compile requirements.in --universal --python-version 3.12 --generate-hashes --no-header -o requirements.txt
uv pip compile requirements-dev.in --universal --python-version 3.12 --generate-hashes --no-header -c requirements.txt -o requirements-dev.txt
#   append -P <package> to either command to upgrade just that package; -U to upgrade everything
```

Commit the `.in` and lock changes together. The `-c requirements.txt` constraint keeps shared packages at the same version in production and in tests.

## Architecture invariants

- **All business logic lives in `api/app/services/`.** REST routers (`api/app/routers/`) and MCP tools (`api/app/mcp_server.py`) are thin wrappers over the same service functions. Never put validation or logic in a router or tool.
- **Every write is audit-logged** via `services/changes.record()` with the correct `source` (`ui`/`api`/`mcp`). A write path without a change-log entry is a bug.
- **MCP tools are hand-curated**, never auto-generated from OpenAPI. When adding or changing one: the docstring is the connected assistant's runtime documentation (write it for tool selection), update `EXPECTED_TOOLS` in `api/tests/test_mcp.py`, and update the tool table in the brief. MCP may only mutate hosts and reserved ranges — subnet/section deletion stays UI-only by design.
- **Schema changes are additive only** — new nullable columns or new tables. `database._add_missing_columns` auto-migrates existing databases on startup; there is no migration framework, so never rename/drop columns or add non-nullable ones without a default.
- **Response shapes**: `{"data": ...}` on success, `{"error": {"code", "message"}}` on failure. Domain errors raise `ApiError` (`api/app/errors.py`); never return bare HTTPExceptions from services.
- **IP maths goes through `api/app/ipnet.py`** and Python's `ipaddress`. No string parsing, no `/24` assumptions — the codebase is deliberately IPv6-ready even though v1 is IPv4-only.
- **Bulk endpoints report per-record results** (`created`/`skipped`/`error` + reason) and never abort the batch on a bad record.
- Frontend: shared reference data (sections, subnets, device types) comes from `TreeContext` (`web/src/context.jsx`) — call `refreshTree()` after any mutation that affects it. Checkboxes use the `.tlg-check` class; device icons come from the `ICONS` registry in `DeviceIcon.jsx`.

## Gotchas

- `/mcp` must work **without** a trailing slash (`MCPPathRewriteMiddleware` in `main.py`) — `mcp-remote` and most MCP clients request the bare path, and the SPA catch-all would otherwise swallow it. `test_mcp_reachable_without_trailing_slash` guards this.
- Some MCP clients (Claude Desktop, for one) snapshot the tool list at connect time — after server restarts that change tools, fully restart the client app (Cmd+Q, not just closing the window).
- The repo uses placeholder domains (`home.arpa`, `example.com`) in code, docs, and tests. Do not introduce the user's real domains.
