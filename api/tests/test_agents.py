"""Agents and agent tokens (#8; v0.2 spec §4.2, §4.3, §5.4).

No route under `/api/v1/agent/` exists yet (check-in is #10), so `require_agent` is
exercised through a probe app that mounts it the way #10 will: a router under that
prefix guarded by the dependency alone. Owner routes and `/mcp` are the real app.
"""

import hashlib
import sqlite3
from pathlib import Path

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastmcp import Client
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import require_agent
from app.config import settings
from app.database import SessionLocal, init_models
from app.errors import ApiError
from app.main import api_error_handler
from app.models import Agent, Token
from app.mcp_server import mcp
from app.schemas import AGENT_NAME_MAX, AgentCreate
from app.services import agents as agents_svc

SCHEMA_BEFORE_AGENTS = Path(__file__).parent / "data" / "schema_before_agents.sql"
MCP_PATHS = ("/mcp", "/mcp/")
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
MCP_ACCEPT = "application/json, text/event-stream"


def _probe_app() -> FastAPI:
    probe = FastAPI()
    probe.add_exception_handler(ApiError, api_error_handler)
    router = APIRouter(prefix="/api/v1/agent")

    @router.post("/checkin")
    async def checkin(agent: Agent = Depends(require_agent)):
        return {"data": {"agent_id": agent.id, "name": agent.name}}

    probe.include_router(router)
    return probe


@pytest.fixture
async def agent_api():
    transport = ASGITransport(app=_probe_app())
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def make_agent(client, name="vlan20-scanner") -> tuple[dict, str]:
    res = await client.post("/api/v1/agents", json={"name": name})
    assert res.status_code == 201, res.text
    data = res.json()["data"]
    return data, data["token"]


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def checkin(agent_api, token: str):
    return await agent_api.post("/api/v1/agent/checkin", headers=bearer(token))


async def token_rows(agent_id: str) -> list[Token]:
    async with SessionLocal() as session:
        query = select(Token).where(Token.agent_id == agent_id).order_by(Token.created_at)
        return list((await session.scalars(query)).all())


def database_text() -> str:
    """Every row of every table, as text: what someone holding the file could read."""
    con = sqlite3.connect(settings.db_path)
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return "\n".join(
            repr(row) for table in tables for row in con.execute(f'SELECT * FROM "{table}"')
        )
    finally:
        con.close()


# --- Create, and the token shown once ---


async def test_create_returns_agent_and_token(client):
    agent, token = await make_agent(client)
    assert agent["name"] == "vlan20-scanner"
    assert agent["state"] == "active"
    assert agent["version"] is agent["hostname"] is agent["last_checkin_at"] is None
    assert token.startswith("tlga_") and len(token) == len("tlga_") + 43
    [listed] = agent["tokens"]
    assert listed["kind"] == "agent"
    assert listed["token_prefix"] == token[:13]
    assert listed["revoked_at"] is listed["last_used_at"] is None
    assert set(listed) == {
        "id", "kind", "token_prefix", "created_at", "last_used_at",
        "expires_at", "used_at", "revoked_at",
    }


async def test_two_agents_get_different_tokens(client):
    _, first = await make_agent(client, "a")
    _, second = await make_agent(client, "b")
    assert first != second


async def test_token_is_stored_as_sha256_and_prefix_only(client):
    agent, token = await make_agent(client)
    [row] = await token_rows(agent["id"])
    assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert row.token_prefix == token[:13]
    dump = database_text()
    assert token not in dump
    assert token[13:] not in dump  # nor the secret part on its own


async def test_plain_token_appears_in_exactly_one_response(client, agent_api):
    """Acceptance: the plain token appears in exactly one response and is not
    recoverable afterwards, by any owner route or from the audit log."""
    agent, token = await make_agent(client)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    aid = agent["id"]
    assert (await checkin(agent_api, token)).status_code == 200

    responses = [
        await client.get("/api/v1/agents"),
        await client.get(f"/api/v1/agents/{aid}"),
        await client.put(f"/api/v1/agents/{aid}", json={"name": "renamed"}),
        await client.put(f"/api/v1/agents/{aid}", json={"enabled": False}),
        await client.put(f"/api/v1/agents/{aid}", json={"enabled": True}),
        await client.post(f"/api/v1/agents/{aid}/revoke"),
        await client.get("/api/v1/changes?limit=200"),
        await client.get("/api/v1/changes?entity_type=token"),
        await client.get("/api/v1/openapi.json"),
    ]
    for res in responses:
        assert res.status_code == 200, res.text
        assert token not in res.text
        assert token[13:] not in res.text
        assert token_hash not in res.text
        assert '"token"' not in res.text.replace('"entity_type":"token"', "")
    assert token not in database_text()


async def test_create_response_is_the_only_one_with_a_token_field(client):
    agent, _ = await make_agent(client)
    got = (await client.get(f"/api/v1/agents/{agent['id']}")).json()["data"]
    assert "token" not in got
    assert set(agent) - set(got) == {"token"}
    assert got["tokens"][0]["token_prefix"] == agent["tokens"][0]["token_prefix"]


# --- The agent auth dependency ---


async def test_agent_token_is_accepted_and_last_used_at_recorded(client, agent_api):
    agent, token = await make_agent(client)
    res = await checkin(agent_api, token)
    assert res.status_code == 200, res.text
    assert res.json()["data"] == {"agent_id": agent["id"], "name": "vlan20-scanner"}
    [row] = await token_rows(agent["id"])
    assert row.last_used_at is not None
    listed = (await client.get(f"/api/v1/agents/{agent['id']}")).json()["data"]["tokens"][0]
    assert listed["last_used_at"] is not None

    first_use = row.last_used_at
    assert (await checkin(agent_api, token)).status_code == 200
    [row] = await token_rows(agent["id"])
    assert row.last_used_at > first_use


async def test_bearer_scheme_is_case_insensitive(client, agent_api):
    _, token = await make_agent(client)
    res = await agent_api.post(
        "/api/v1/agent/checkin", headers={"Authorization": f"bearer {token}"}
    )
    assert res.status_code == 200


async def test_each_agent_token_names_its_own_agent(client, agent_api):
    first, first_token = await make_agent(client, "a")
    second, second_token = await make_agent(client, "b")
    assert (await checkin(agent_api, first_token)).json()["data"]["agent_id"] == first["id"]
    assert (await checkin(agent_api, second_token)).json()["data"]["agent_id"] == second["id"]


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Basic dGVzdC1rZXk="},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer   "},
        {"X-API-Key": "test-key"},
    ],
    ids=["none", "basic", "scheme-only", "blank-token", "owner-x-api-key"],
)
async def test_agent_route_without_bearer_is_401(agent_api, headers):
    res = await agent_api.post("/api/v1/agent/checkin", headers=headers)
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.parametrize(
    "authorization",
    [
        "Bearer test-key",  # the owner key
        "Bearer tlga_not-a-real-token",
        b"Bearer tlga_caf\xe9",
    ],
    ids=["owner-key", "unknown", "non-ascii"],
)
async def test_agent_route_refuses_other_bearers(client, agent_api, authorization):
    await make_agent(client)  # a live token exists, and is not this one
    res = await agent_api.post("/api/v1/agent/checkin", headers={"Authorization": authorization})
    assert res.status_code == 403
    assert res.json()["error"] == {"code": "FORBIDDEN", "message": "Invalid agent token"}


async def test_owner_key_with_valid_agent_bearer_still_needs_the_bearer(client, agent_api):
    # X-API-Key is ignored on agent routes: it neither helps nor replaces the bearer
    _, token = await make_agent(client)
    res = await agent_api.post(
        "/api/v1/agent/checkin", headers={"X-API-Key": "test-key", **bearer("tlga_wrong")}
    )
    assert res.status_code == 403
    res = await agent_api.post(
        "/api/v1/agent/checkin", headers={"X-API-Key": "test-key", **bearer(token)}
    )
    assert res.status_code == 200


async def test_revoked_token_is_refused(client, agent_api):
    agent, token = await make_agent(client)
    assert (await checkin(agent_api, token)).status_code == 200
    res = await client.post(f"/api/v1/agents/{agent['id']}/revoke")
    assert res.status_code == 200, res.text
    assert res.json()["data"]["tokens"][0]["revoked_at"] is not None
    assert res.json()["data"]["state"] == "active"  # the agent is kept

    res = await checkin(agent_api, token)
    assert res.status_code == 403


async def test_disabled_agent_token_is_refused_until_enabled(client, agent_api):
    agent, token = await make_agent(client)
    res = await client.put(f"/api/v1/agents/{agent['id']}", json={"enabled": False})
    assert res.status_code == 200, res.text
    assert res.json()["data"]["state"] == "disabled"
    assert res.json()["data"]["tokens"][0]["revoked_at"] is None  # disabling is not revoking

    assert (await checkin(agent_api, token)).status_code == 403
    [row] = await token_rows(agent["id"])
    assert row.last_used_at is None  # a refused request is not a use

    res = await client.put(f"/api/v1/agents/{agent['id']}", json={"enabled": True})
    assert res.json()["data"]["state"] == "active"
    assert (await checkin(agent_api, token)).status_code == 200


async def test_deleted_agent_token_is_refused(client, agent_api):
    agent, token = await make_agent(client)
    res = await client.delete(f"/api/v1/agents/{agent['id']}")
    assert res.status_code == 204
    assert (await checkin(agent_api, token)).status_code == 403
    assert (await client.get(f"/api/v1/agents/{agent['id']}")).status_code == 404
    assert await token_rows(agent["id"]) == []


async def test_enrol_token_is_refused_on_agent_routes(client, agent_api):
    # Nothing issues an enrol token until M2b (#21); the kind exists in the schema
    # and must never pass as an agent token.
    agent, _ = await make_agent(client)
    plain = "tlge_enrol-token-for-test"
    async with SessionLocal() as session:
        session.add(
            Token(
                kind="enrol",
                agent_id=agent["id"],
                token_hash=agents_svc.hash_token(plain),
                token_prefix=plain[:13],
            )
        )
        await session.commit()
    assert (await checkin(agent_api, plain)).status_code == 403


async def test_agent_token_is_refused_on_owner_routes(client, anon_client):
    _, token = await make_agent(client)
    for path in ("/api/v1/sections", "/api/v1/agents", "/api/v1/changes", "/api/v1/backup"):
        res = await anon_client.get(path, headers={"X-API-Key": token})
        assert res.status_code == 403, path
        res = await anon_client.get(path, headers=bearer(token))
        assert res.status_code == 401, path
    res = await anon_client.post("/api/v1/agents", json={"name": "x"}, headers=bearer(token))
    assert res.status_code == 401


@pytest.mark.parametrize("path", MCP_PATHS)
async def test_agent_token_is_refused_on_mcp(client, anon_client, path):
    _, token = await make_agent(client)
    res = await anon_client.post(
        path, json=INITIALIZE, headers={**bearer(token), "Accept": MCP_ACCEPT}
    )
    assert res.status_code == 401
    assert res.headers["www-authenticate"].startswith('Bearer error="invalid_token"')


# --- Owner REST ---


async def test_list_and_get_agents(client):
    await make_agent(client, "b-agent")
    await make_agent(client, "a-agent")
    res = await client.get("/api/v1/agents")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 2
    assert [a["name"] for a in body["data"]] == ["a-agent", "b-agent"]


async def test_agent_routes_need_the_owner_key(anon_client):
    assert (await anon_client.get("/api/v1/agents")).status_code == 401
    res = await anon_client.post("/api/v1/agents", json={"name": "x"}, headers={"X-API-Key": "no"})
    assert res.status_code == 403


async def test_rename(client):
    agent, _ = await make_agent(client)
    res = await client.put(f"/api/v1/agents/{agent['id']}", json={"name": "  vlan30  "})
    assert res.status_code == 200
    assert res.json()["data"]["name"] == "vlan30"


@pytest.mark.parametrize(
    "name",
    ["", "   ", "x" * (AGENT_NAME_MAX + 1), "line\nbreak", "nul\x00", "esc\x1b[31m"],
    ids=["empty", "blank", "too-long", "newline", "nul", "escape"],
)
async def test_invalid_names_are_422(client, name):
    res = await client.post("/api/v1/agents", json={"name": name})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"
    agent, _ = await make_agent(client)
    res = await client.put(f"/api/v1/agents/{agent['id']}", json={"name": name})
    assert res.status_code == 422


async def test_longest_name_fits_the_audit_actor(client):
    agent, _ = await make_agent(client, "x" * AGENT_NAME_MAX)
    async with SessionLocal() as session:
        row = await session.get(Agent, agent["id"])
    assert len(agents_svc.actor_for(row)) == 80


async def test_duplicate_names_are_409_case_insensitively(client):
    first, _ = await make_agent(client, "Scanner")
    res = await client.post("/api/v1/agents", json={"name": "scanner"})
    assert res.status_code == 409
    other, _ = await make_agent(client, "other")
    res = await client.put(f"/api/v1/agents/{other['id']}", json={"name": "SCANNER"})
    assert res.status_code == 409
    # renaming an agent to a new case of its own name is allowed
    res = await client.put(f"/api/v1/agents/{first['id']}", json={"name": "scanner"})
    assert res.status_code == 200


@pytest.mark.parametrize("field", ["name", "enabled"])
async def test_null_fields_are_422(client, field):
    agent, _ = await make_agent(client)
    res = await client.put(f"/api/v1/agents/{agent['id']}", json={field: None})
    assert res.status_code == 422


async def test_unknown_agent_is_404(client):
    missing = "00000000-0000-0000-0000-000000000000"
    assert (await client.get(f"/api/v1/agents/{missing}")).status_code == 404
    assert (await client.put(f"/api/v1/agents/{missing}", json={"enabled": False})).status_code == 404
    assert (await client.post(f"/api/v1/agents/{missing}/revoke")).status_code == 404
    assert (await client.delete(f"/api/v1/agents/{missing}")).status_code == 404


async def test_revoking_twice_is_409(client):
    agent, _ = await make_agent(client)
    assert (await client.post(f"/api/v1/agents/{agent['id']}/revoke")).status_code == 200
    res = await client.post(f"/api/v1/agents/{agent['id']}/revoke")
    assert res.status_code == 409


async def test_database_allows_one_live_agent_token_per_agent(client):
    agent, _ = await make_agent(client)

    def second(n):
        return Token(kind="agent", agent_id=agent["id"], token_hash=f"h{n}", token_prefix="tlga_x")

    async with SessionLocal() as session:
        session.add(second(1))
        with pytest.raises(IntegrityError):
            await session.commit()

    assert (await client.post(f"/api/v1/agents/{agent['id']}/revoke")).status_code == 200
    async with SessionLocal() as session:
        session.add(second(2))
        await session.commit()  # the first is revoked, so a new live one is allowed
        session.add(second(3))
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_mcp_has_no_agent_or_token_tool_and_its_audit_view_has_no_secret(client):
    _, token = await make_agent(client)
    async with Client(mcp) as mcp_client:
        names = {t.name for t in await mcp_client.list_tools()}
        listed = (await mcp_client.call_tool("list_changes", {"entity_type": "token"})).data
    assert not [n for n in names if "agent" in n or "token" in n]
    [entry] = listed["changes"]
    assert entry["action"] == "create"
    assert token not in repr(listed)
    assert agents_svc.hash_token(token) not in repr(listed)


# --- Audit ---


async def changes_of(client, entity_type=None) -> list[dict]:
    url = "/api/v1/changes?limit=200" + (f"&entity_type={entity_type}" if entity_type else "")
    return list(reversed((await client.get(url)).json()["data"]))  # oldest first


async def test_create_logs_the_agent_and_its_token(client):
    agent, token = await make_agent(client)
    rows = await changes_of(client)
    assert [(c["entity_type"], c["action"]) for c in rows] == [
        ("agent", "create"),
        ("token", "create"),
    ]
    agent_row, token_row = rows
    assert agent_row["entity_id"] == agent["id"]
    assert token_row["entity_id"] == agent["tokens"][0]["id"]
    assert token_row["summary"] == f"token {token[:13]}… issued to agent vlan20-scanner"
    assert token_row["after"]["agent_id"] == agent["id"]
    assert "token_hash" not in token_row["after"]
    assert {c["source"] for c in rows} == {"api"}
    assert {c["actor"] for c in rows} == {None}


async def test_revoke_is_logged(client):
    agent, token = await make_agent(client)
    await client.post(
        f"/api/v1/agents/{agent['id']}/revoke", headers={"X-TLG-Client": "ui"}
    )
    revoke = (await changes_of(client))[-1]
    assert (revoke["entity_type"], revoke["action"]) == ("token", "revoke")
    assert revoke["entity_id"] == agent["tokens"][0]["id"]
    assert revoke["source"] == "ui"
    assert revoke["actor"] is None
    assert revoke["before"]["revoked_at"] is None
    assert revoke["after"]["revoked_at"] is not None
    assert "token_hash" not in revoke["before"] and "token_hash" not in revoke["after"]
    assert revoke["summary"] == f"token {token[:13]}… of agent vlan20-scanner revoked"


async def test_delete_logs_the_revoke_and_the_delete(client):
    agent, _ = await make_agent(client)
    await client.delete(f"/api/v1/agents/{agent['id']}")
    rows = (await changes_of(client))[2:]
    assert [(c["entity_type"], c["action"]) for c in rows] == [
        ("token", "revoke"),
        ("agent", "delete"),
    ]
    assert rows[0]["summary"].endswith("revoked (agent deleted)")
    assert rows[1]["before"]["name"] == "vlan20-scanner"


async def test_delete_after_revoke_logs_no_second_revoke(client):
    agent, _ = await make_agent(client)
    await client.post(f"/api/v1/agents/{agent['id']}/revoke")
    await client.delete(f"/api/v1/agents/{agent['id']}")
    rows = await changes_of(client)
    assert [(c["entity_type"], c["action"]) for c in rows] == [
        ("agent", "create"),
        ("token", "create"),
        ("token", "revoke"),
        ("agent", "delete"),
    ]


async def test_updates_are_logged_and_no_ops_are_not(client):
    agent, _ = await make_agent(client)
    aid = agent["id"]
    await client.put(f"/api/v1/agents/{aid}", json={"enabled": False})
    await client.put(f"/api/v1/agents/{aid}", json={"enabled": False})  # already disabled
    await client.put(f"/api/v1/agents/{aid}", json={"name": "vlan20-scanner"})  # unchanged
    await client.put(f"/api/v1/agents/{aid}", json={"name": "vlan30", "enabled": True})
    rows = (await changes_of(client, "agent"))[1:]
    assert [c["summary"] for c in rows] == [
        "agent vlan20-scanner disabled",
        "agent vlan30 renamed from vlan20-scanner, enabled",
    ]
    assert [(c["before"]["state"], c["after"]["state"]) for c in rows] == [
        ("active", "disabled"),
        ("disabled", "active"),
    ]


# --- Start-up on an existing database ---


async def test_database_from_main_gains_the_agents_and_tokens_tables(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(SCHEMA_BEFORE_AGENTS.read_text())
    old.commit()
    assert {"agents", "tokens"}.isdisjoint(
        r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table'")
    )
    old.close()

    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    try:
        await init_models(engine)
        await init_models(engine)  # a second start-up creates nothing twice
        async with engine.connect() as conn:
            indexes = {
                r[0]: r[1]
                for r in (
                    await conn.exec_driver_sql(
                        "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='tokens'"
                    )
                ).all()
            }
        live_index = indexes["uq_tokens_live_agent_token"]
        assert "WHERE kind = 'agent' AND revoked_at IS NULL" in live_index

        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            agent, token = await agents_svc.create_agent(session, AgentCreate(name="a"), "api")
            assert await agents_svc.authenticate(session, token) is not None
    finally:
        await engine.dispose()
