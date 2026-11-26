"""The owner's **Linked clients** (v0.2 spec §6.6, #30): `GET /api/v1/linked-clients`
and `POST /api/v1/linked-clients/{id}/revoke`, against a server with the OAuth server
on (`oauth_flow.oauth_app`), links made through the link window and the consent page.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update
from sqlalchemy.exc import OperationalError

from app.database import SessionLocal
from app.models import Change, OAuthClient, OAuthGrant, OAuthToken
from app.services import linked_clients, oauth

from .oauth_flow import (  # noqa: F401 (web and no_ambient_token are fixtures)
    BASE,
    all_rows,
    database_dump,
    exchange,
    http_client,
    no_ambient_token,
    refresh,
    register,
    rotate,
    running,
    set_token,
    the_grant,
    web,
    writes_app,
)
from .test_link_window import OWNER, consent_with_code, open_window
from .test_oauth import INITIALIZE, invalid_grant

UI = {**OWNER, "X-TLG-Client": "ui"}


async def linked_through_window(web, *, can_write=False, uri_index=0, **metadata):
    """A client linked as the owner links one: a window, a registration, the consent
    page with the code, the code exchanged. The registration and its tokens."""
    window = await open_window(web, **({"can_write": True} if can_write else {}))
    client = await register(web, **metadata)
    uri = client["redirect_uris"][uri_index]
    back = await consent_with_code(web, client, window["code"], redirect_uri=uri)
    res = await exchange(web, client, back.params["code"], redirect_uri=uri)
    assert res.status_code == 200, res.text
    return client, res.json()


async def links(web, headers=OWNER) -> list[dict]:
    res = await web.get("/api/v1/linked-clients", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["data"]


async def revoke(web, grant_id, headers=OWNER):
    return await web.post(f"/api/v1/linked-clients/{grant_id}/revoke", headers=headers)


async def initialise(web, bearer: str):
    async with running():
        return await web.post(
            "/mcp",
            json=INITIALIZE,
            headers={
                "Authorization": f"Bearer {bearer}",
                "Accept": "application/json, text/event-stream",
            },
        )


async def grant_changes() -> list[Change]:
    return [c for c in await all_rows(Change) if c.entity_type == "grant"]


# --- The list ---


async def test_with_no_link_the_list_is_empty(web):
    assert await links(web) == []


async def test_a_link_is_listed_with_its_host_name_access_and_times(web):
    await linked_through_window(web, client_name="Example")
    [link] = await links(web)
    grant = await the_grant()
    assert link == {
        "id": grant.id,
        "client_id": grant.client_id,
        "client_name": "Example",
        "client_registered": True,
        "redirect_host": "client.example.com",
        "can_write": False,
        "writes": False,
        "created_at": link["created_at"],
        "last_used_at": link["last_used_at"],
        "revoked_at": None,
    }
    # a code exchange is a contact (`oauth._touch`), and every time is UTC with its offset
    assert link["created_at"].endswith("+00:00") and link["last_used_at"].endswith("+00:00")


async def test_the_host_is_the_one_the_link_was_authorised_through(web):
    """A client that registered redirect URIs on several hosts (Gemini registers six,
    on three) is listed with the host of the one its link used, not the first."""
    await linked_through_window(
        web,
        uri_index=1,
        redirect_uris=[
            "https://first.example.com/cb",
            "https://second.example.com/cb",
            "https://third.example.com/cb",
        ],
    )
    [link] = await links(web)
    assert link["redirect_host"] == "second.example.com"


async def test_the_host_is_in_its_ascii_form(web):
    await linked_through_window(web, redirect_uris=["https://bücher.example/cb"])
    [link] = await links(web)
    assert link["redirect_host"] == "xn--bcher-kva.example"


async def test_a_client_with_no_kept_name_and_one_whose_registration_is_gone(web):
    await linked_through_window(web, client_name=None)
    [link] = await links(web)
    assert link["client_name"] is None and link["client_registered"] is True
    async with SessionLocal() as session:
        await session.delete(await session.get(OAuthClient, link["client_id"]))
        await session.commit()
    [link] = await links(web)
    assert link["client_name"] is None and link["client_registered"] is False


async def test_a_read_write_link_writes_only_while_remote_writes_are_on(no_ambient_token):
    """The same grant, listed by the server with the setting on and by the one with it
    off (`oauth_flow`: two apps on one database, as a restart that changes it is)."""
    async with http_client(writes_app) as on, http_client() as off:
        await linked_through_window(on, can_write=True)
        [link] = await links(on)
        assert (link["can_write"], link["writes"]) == (True, True)
        [link] = await links(off)
        assert (link["can_write"], link["writes"]) == (True, False)


async def test_unrevoked_links_newest_first_then_the_newest_revoked(web, monkeypatch):
    monkeypatch.setattr(linked_clients, "REVOKED_SHOWN", 2)
    for _ in range(5):
        await linked_through_window(web)
    grants = sorted(await all_rows(OAuthGrant), key=lambda g: g.created_at)
    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        for minutes, grant in zip((3, 1, 2), grants[:3]):
            await session.execute(
                update(OAuthGrant)
                .where(OAuthGrant.id == grant.id)
                .values(revoked_at=now - timedelta(minutes=minutes))
            )
        await session.commit()
    listed = [link["id"] for link in await links(web)]
    # the two unrevoked, newest first; then of the three revoked the two revoked last
    assert listed == [grants[4].id, grants[3].id, grants[1].id, grants[2].id]


async def test_the_list_is_one_statement_whatever_it_holds(web):
    from sqlalchemy import event

    from app.database import engine

    for _ in range(3):
        await linked_through_window(web)
    statements = []

    def count(conn, cursor, statement, *args):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", count)
    try:
        async with SessionLocal() as session:
            assert len(await linked_clients.list_links(session, remote_writes=False)) == 3
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count)
    assert len(statements) == 1


async def test_the_list_is_one_snapshot_whatever_commits_while_it_reads(web, monkeypatch):
    """Round 1 of #195's briefed review, finding 2: a revoke, and a new link, committed
    in other sessions while the list is being read. The answer is the database as one
    statement saw it: the grant once, live, and not the link made after. With two
    statements the revoke landed between them and the grant came back twice, both live."""
    from sqlalchemy.ext.asyncio import AsyncSession

    await linked_through_window(web)
    grant = await the_grant()
    real_execute = AsyncSession.execute
    interleaved = False

    async def execute(self, *args, **kwargs):
        nonlocal interleaved
        result = await real_execute(self, *args, **kwargs)
        if not interleaved:
            interleaved = True
            async with SessionLocal() as other:
                await linked_clients.revoke(other, grant.id, source="api", remote_writes=False)
            async with SessionLocal() as other:
                other.add(OAuthGrant(client_id=grant.client_id, redirect_uri="https://client.example.com/cb"))
                await other.commit()
        return result

    monkeypatch.setattr(AsyncSession, "execute", execute)
    async with SessionLocal() as session:
        listed = await linked_clients.list_links(session, remote_writes=False)
    monkeypatch.undo()
    assert interleaved
    assert [(link["id"], link["revoked_at"]) for link in listed] == [(grant.id, None)]
    # and the next read sees both commits, the revoked grant once
    after = await links(web)
    assert [link["id"] for link in after].count(grant.id) == 1
    assert len(after) == 2 and after[1]["id"] == grant.id and after[1]["revoked_at"]


async def test_without_oauth_the_list_answers_and_there_is_no_mcp_url(client):
    """The session's app is a server without `PUBLIC_BASE_URL`: the tables exist either
    way, so a link made while it was set can still be seen and revoked."""
    res = await client.get("/api/v1/linked-clients")
    assert res.status_code == 200 and res.json() == {"data": []}
    state = (await client.get("/api/v1/link-window")).json()["data"]
    assert state["configured"] is False and state["mcp_url"] is None


async def test_the_window_state_gives_the_url_a_client_is_given(web):
    """`<PUBLIC_BASE_URL>/mcp`, from the setting, never the request's host."""
    opened = await open_window(web)
    assert opened["mcp_url"] == f"{BASE}/mcp"
    res = await web.get("/api/v1/link-window", headers={**OWNER, "Host": "other.example.com"})
    assert res.json()["data"]["mcp_url"] == f"{BASE}/mcp"
    res = await web.delete("/api/v1/link-window", headers=OWNER)
    assert res.json()["data"]["mcp_url"] == f"{BASE}/mcp"


# --- Revoke ---


async def test_revoking_a_client_stops_its_next_request(web, no_ambient_token):
    """The issue's first acceptance line: the access token works on `/mcp`, in a session
    the client holds, the owner revokes the link, and the next request of that same
    session with that same token is refused; so is a new session, and the refresh
    token."""
    client, tokens = await linked_through_window(web, client_name="Example")
    bearer = {
        "Authorization": f"Bearer {tokens['access_token']}",
        "Accept": "application/json, text/event-stream",
    }
    tools = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    async with running():
        res = await web.post("/mcp", json=INITIALIZE, headers=bearer)
        assert res.status_code == 200, res.text
        session = {**bearer, "mcp-session-id": res.headers["mcp-session-id"]}
        initialised = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        assert (await web.post("/mcp", json=initialised, headers=session)).status_code == 202
        res = await web.post("/mcp", json=tools, headers=session)
        assert res.status_code == 200 and "list_sections" in res.text
        res = await revoke(web, (await the_grant()).id, headers=UI)
        assert res.status_code == 200, res.text
        res = await web.post("/mcp", json=tools, headers=session)
        assert res.status_code == 401
        assert res.headers["www-authenticate"].startswith("Bearer")
    assert (await initialise(web, tokens["access_token"])).status_code == 401
    assert invalid_grant(await refresh(web, client, tokens["refresh_token"]))


async def test_revoke_answers_with_the_link_and_deletes_its_tokens(web):
    await linked_through_window(web, client_name="Example")
    grant = await the_grant()
    res = await revoke(web, grant.id)
    assert res.status_code == 200
    link = res.json()["data"]
    assert link["id"] == grant.id and link["revoked_at"].endswith("+00:00")
    assert link["writes"] is False
    assert [t for t in await all_rows(OAuthToken) if t.grant_id == grant.id] == []
    assert (await the_grant()).revoked_at is not None
    # it stays on the list, marked revoked
    [listed] = await links(web)
    assert listed["id"] == grant.id and listed["revoked_at"] == link["revoked_at"]


@pytest.mark.parametrize(("headers", "source"), [(UI, "ui"), (OWNER, "api")])
async def test_a_revoke_is_the_owners_audit_entry(web, headers, source):
    """On the grant's entity, with the owner's source and a null actor (spec §4.3), and
    a summary that names neither the client nor its redirect URI."""
    await linked_through_window(web, client_name="Example")
    grant = await the_grant()
    assert (await revoke(web, grant.id, headers=headers)).status_code == 200
    entry = (await grant_changes())[-1]
    assert (entry.entity_id, entry.action, entry.source, entry.actor) == (
        grant.id,
        "revoke",
        source,
        None,
    )
    assert entry.summary == linked_clients.REVOKE_SUMMARY
    assert "Example" not in entry.summary and "client.example.com" not in entry.summary
    assert entry.before["revoked_at"] is None and entry.after["revoked_at"] is not None


async def test_revoking_a_revoked_link_is_refused_and_writes_nothing(web):
    await linked_through_window(web)
    grant = await the_grant()
    assert (await revoke(web, grant.id)).status_code == 200
    before = database_dump()
    res = await revoke(web, grant.id)
    assert res.status_code == 409 and res.json()["error"]["code"] == "ALREADY_REVOKED"
    assert database_dump() == before


async def test_a_link_the_reuse_rule_revoked_is_already_revoked(web):
    client, first = await linked_through_window(web)
    await rotate(web, client, first["refresh_token"])
    await set_token(
        first["refresh_token"],
        rotated_at=datetime.now(timezone.utc) - oauth.REFRESH_RETRY_WINDOW - timedelta(seconds=1),
    )
    assert invalid_grant(await refresh(web, client, first["refresh_token"]))
    grant = await the_grant()
    res = await revoke(web, grant.id)
    assert res.status_code == 409
    [listed] = await links(web)
    assert listed["revoked_at"] is not None


@pytest.mark.parametrize(
    "grant_id", ["00000000-0000-4000-8000-000000000000", "not-a-grant", "x" * 300]
)
async def test_an_unknown_link_is_not_found(web, grant_id):
    await linked_through_window(web)
    before = database_dump()
    res = await revoke(web, grant_id)
    assert res.status_code == 404 and res.json()["error"]["code"] == "NOT_FOUND"
    assert database_dump() == before


async def test_a_revoke_the_database_refuses_is_503_and_changes_nothing(web, monkeypatch):
    await linked_through_window(web)
    grant = await the_grant()
    before = database_dump()

    async def refused(*args, **kwargs):
        raise OperationalError("UPDATE", {}, Exception("database is locked"))

    monkeypatch.setattr(oauth, "revoke_grant", refused)
    res = await revoke(web, grant.id)
    assert res.status_code == 503 and res.json()["error"]["code"] == "UNAVAILABLE"
    assert database_dump() == before


async def test_revoke_reads_the_grant_under_the_write_lock(web, monkeypatch):
    """The lock comes before the read, so the grant it decides on is the committed one."""
    from app.services import scans

    await linked_through_window(web)
    grant = await the_grant()
    order = []
    real_lock, real_get = scans.take_write_lock, SessionLocal.class_.get

    async def lock(session):
        order.append("lock")
        await real_lock(session)

    async def get(self, model, ident, **kwargs):
        order.append(model.__name__)
        return await real_get(self, model, ident, **kwargs)

    monkeypatch.setattr(scans, "take_write_lock", lock)
    monkeypatch.setattr(SessionLocal.class_, "get", get)
    assert (await revoke(web, grant.id)).status_code == 200
    assert order[:2] == ["lock", "OAuthGrant"]


async def test_revoking_a_link_frees_its_registration_for_the_cap(web):
    """At the cap with every registration linked, a new one is refused until the owner
    revokes a link (spec §6.6): this route is that revoke."""
    client, _ = await linked_through_window(web)
    async with SessionLocal() as session:
        for i in range(oauth.CLIENTS_MAX - 1):
            session.add(
                OAuthClient(
                    client_id=f"00000000-0000-4000-8000-{i:012d}",
                    redirect_uris=["https://client.example.com/cb"],
                    token_endpoint_auth_method="none",
                    grant_types=["authorization_code"],
                    scope="ipam:read",
                )
            )
            session.add(OAuthGrant(client_id=f"00000000-0000-4000-8000-{i:012d}", redirect_uri="https://client.example.com/cb"))
        await session.commit()
    await open_window(web)
    res = await web.post("/mcp/register", json={"redirect_uris": ["https://client.example.com/cb"]})
    assert res.status_code == 403
    grant = next(g for g in await all_rows(OAuthGrant) if g.client_id == client["client_id"])
    assert (await revoke(web, grant.id)).status_code == 200
    res = await web.post("/mcp/register", json={"redirect_uris": ["https://client.example.com/cb"]})
    assert res.status_code == 201


# --- Who may ---


async def test_the_routes_take_the_owner_key_alone(web, no_ambient_token):
    _, tokens = await linked_through_window(web)
    grant = await the_grant()
    for headers in (
        {},
        {"X-API-Key": "wrong"},
        {"X-API-Key": tokens["access_token"]},
        {"Authorization": f"Bearer {tokens['access_token']}"},
    ):
        assert (await web.get("/api/v1/linked-clients", headers=headers)).status_code in (401, 403)
        assert (await revoke(web, grant.id, headers=headers)).status_code in (401, 403)
    assert (await the_grant()).revoked_at is None


async def test_no_mcp_tool_lists_or_revokes_links():
    from app.mcp_server import mcp

    names = {tool.name for tool in await mcp.list_tools()}
    assert not {n for n in names if "link" in n or "grant" in n or "client" in n}
