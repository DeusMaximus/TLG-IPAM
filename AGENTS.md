# TLG-IPAM

Homelab IPAM: FastAPI + SQLite backend, React SPA, and an MCP server at `/mcp` so AI assistants (Claude, ChatGPT, or any other MCP client) can query/update it. The full specification — data model, endpoints, MCP tool inventory, UI spec, acceptance criteria — lives in `TLG-IPAM-Project-Brief.md`. **Keep the brief in sync when behaviour changes**; it is the source of truth, not documentation theatre.

## Workflow

v0.2 (scan agents, remote MCP) is specified in `docs/v0.2-spec.md` and tracked as GitHub milestones M0–M3. The spec is the contract for work that has not shipped; the brief describes what `main` does.

- **Session start:** read the newest entry in `HANDOFF.md`. It holds live state: what is merged, in flight, broken, or waiting on the owner.
- **One issue, one branch, one PR.** Branch `<issue>-<slug>` from `main`. The PR closes the issue and carries in the same diff: the tests, the brief update for the behaviour it ships, and the `AGENTS.md` line for any invariant it adds or changes.
- **Green before the PR opens:** the commands CI runs (`.github/workflows/ci.yml`), locally.
- **The PR body is the record.** It opens with the authoring agent, model, and head sha, then **What**, **Deliberate calls**, **Tests**. Read `.agents/review-brief.md` when opening a PR or briefing a reviewer: it holds the body shape and the review template.
- **Merge needs green CI and a GO** from a reviewer in a different model family from the author. The owner merges.
- **When the spec is wrong** (the code or a spike shows it), change `docs/v0.2-spec.md` in the same PR and list the change under Deliberate calls.
- **Session end:** add a `HANDOFF.md` entry using the template in its header. Hand-off entries commit straight to `main`, so the next session sees them without waiting on a PR.

`.agents/README.md` indexes the process material that is not needed on every turn.

## Commands

```sh
# Backend (one-time setup: python3 -m venv api/.venv && api/.venv/bin/pip install -r api/requirements-dev.txt)
cd api && .venv/bin/python -m pytest                  # test suite — run before declaring any backend change done
cd api && API_KEY=dev-key .venv/bin/uvicorn app.main:app --port 8080 --reload
#   add STATIC_DIR=../web/dist to also serve the built SPA (single-origin, like production)

# Frontend
cd web && npm run dev                                  # hot reload, proxies /api → :8080
cd web && npm run build                                # production bundle to web/dist

# Full stack (needs .env with API_KEY)
docker compose up --build
```

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
