"""`MCP_REMOTE_WRITES` (v0.2 spec §6.5, #28): a grant's token carries `ipam:write` only
while the grant has `can_write` and the setting is on, judged each time a token is
loaded; MCP writes from a grant name it in `actor` (§4.3).

Two apps on one database stand for the setting off and on (`oauth_flow.oauth_app`,
`oauth_flow.writes_app`): a token issued by one is loaded by the other, as it is by the
next process after a restart that changes the setting (`test_oauth_restart.py` shows
that across processes). Each test that speaks for a grant uses `no_ambient_token`.
"""

import re

import pytest
from fastmcp.exceptions import ToolError

from app import mcp_server
from app.auth import OWNER_SCOPES
from app.mcp_server import mcp
from app.models import Change, Host, OAuthGrant
from app.services import oauth

from .conftest import make_host, make_section, make_subnet
from .oauth_flow import (  # noqa: F401 (web, linking and no_ambient_token are fixtures)
    READ_TOOLS,
    WRITE_TOOLS,
    all_rows,
    database_dump,
    exchange,
    http_client,
    linked,
    linking,
    mcp_client,
    no_ambient_token,
    oauth_app,
    register,
    rotate,
    running,
    the_grant,
    web,
    writes_app,
    writes_mcp,
)
from .test_checkin import make_agent, make_target
from .test_link_window import OWNER, consent_with_code, open_window, window_state
from .test_mcp import EXPECTED_TOOLS


@pytest.fixture
async def web_on():
    """The owner's and the clients' HTTP client on the server with the setting on."""
    async with http_client(writes_app) as client:
        yield client


async def tools_of(bearer: str, app) -> set[str]:
    async with running(app), mcp_client(bearer, app=app) as assistant:
        return {t.name for t in await assistant.list_tools()}


async def lab(client) -> dict:
    section = await make_section(client)
    return await make_subnet(client, section["id"], "10.0.0.0/24", gateway="10.0.0.1")


# --- The rule ---


@pytest.mark.parametrize(
    ("can_write", "remote_writes", "scopes"),
    [
        (False, False, ["ipam:read"]),
        (True, False, ["ipam:read"]),
        (False, True, ["ipam:read"]),
        (True, True, ["ipam:read", "ipam:write"]),
    ],
)
def test_a_grant_writes_only_with_can_write_and_the_setting_on(can_write, remote_writes, scopes):
    grant = OAuthGrant(can_write=can_write)
    assert oauth.grant_scopes(grant, remote_writes=remote_writes) == scopes


def test_the_owner_key_carries_both_scopes_whatever_the_setting():
    assert OWNER_SCOPES == ["ipam:read", "ipam:write"]


# --- The setting on ---


async def test_a_read_write_grant_lists_and_calls_the_write_tools_and_is_the_actor(
    web_on, linking, no_ambient_token, client
):
    subnet = await lab(client)
    _, tokens = await linked(web_on, can_write=True)
    assert tokens["scope"] == "ipam:read ipam:write"
    async with running(writes_app), mcp_client(tokens["access_token"], app=writes_app) as assistant:
        assert {t.name for t in await assistant.list_tools()} == EXPECTED_TOOLS
        made = await assistant.call_tool(
            "create_host", {"subnet_id": subnet["id"], "ip": "10.0.0.20", "hostname": "nas"}
        )
        assert made.data["host"]["hostname"] == "nas"
        [entry] = [
            c
            for c in (await assistant.call_tool("list_changes", {"entity_type": "host"})).data["changes"]
            if c["source"] == "mcp"
        ]
    assert entry["actor"] == "grant:Example"
    # the client's name is in `actor` alone, never the summary (spec §7.2, invariant 8)
    assert entry["summary"] == "host nas created at 10.0.0.20 in lab"
    [stored] = [c for c in await all_rows(Change) if c.source == "mcp"]
    assert stored.actor == "grant:Example" and "Example" not in stored.summary


async def test_turning_the_setting_on_does_not_upgrade_a_read_only_grant(
    web_on, linking, no_ambient_token, client
):
    subnet = await lab(client)
    client_info, tokens = await linked(web_on, can_write=False)
    assert tokens["scope"] == "ipam:read"
    async with running(writes_app), mcp_client(tokens["access_token"], app=writes_app) as assistant:
        assert {t.name for t in await assistant.list_tools()} == READ_TOOLS
        before = database_dump()
        for name in ("create_host", "request_scan"):
            with pytest.raises(ToolError, match=f"Unknown tool: '{name}'"):
                await assistant.call_tool(name, {"subnet_id": subnet["id"], "ip": "10.0.0.20"})
        assert database_dump() == before
    # nor does a refresh that asks for both scopes
    again = await rotate(web_on, client_info, tokens["refresh_token"], scope="ipam:read ipam:write")
    assert again["scope"] == "ipam:read"
    assert await tools_of(again["access_token"], writes_app) == READ_TOOLS


async def test_every_write_tool_records_the_grant_as_actor(web_on, linking, no_ambient_token, client):
    """All nine, each with arguments that succeed: every change entry the grant's calls
    write names it, and none names it in its summary."""
    subnet = await lab(client)
    host = await make_host(client, subnet["id"], "10.0.0.10", hostname="pve-01")
    other = await make_host(client, subnet["id"], "10.0.0.11", hostname="pve-02")
    agent, _ = await make_agent(client, "scanner")
    await make_target(client, subnet["id"], agent["id"])
    _, tokens = await linked(web_on, can_write=True)
    called = set()
    async with running(writes_app), mcp_client(tokens["access_token"], app=writes_app) as assistant:

        async def call(name, arguments):
            called.add(name)
            return (await assistant.call_tool(name, arguments)).data

        added = await call(
            "add_reserved_range",
            {"subnet_id": subnet["id"], "start_ip": "10.0.0.200", "end_ip": "10.0.0.210"},
        )
        await call(
            "delete_reserved_range",
            {"subnet_id": subnet["id"], "range_id": added["reserved_range"]["id"]},
        )
        await call("create_host", {"subnet_id": subnet["id"], "ip": "10.0.0.20", "hostname": "nas"})
        await call("allocate_next_ip", {"subnet_id": subnet["id"], "hostname": "next"})
        await call("bulk_create_hosts", {"subnet_id": subnet["id"], "hosts": [{"ip": "10.0.0.30"}]})
        await call("update_host", {"host_id": host["id"], "notes": "rack 2"})
        await call("request_scan", {"subnet_id": subnet["id"]})
        await call("delete_host", {"host_id": host["id"]})
        await call("bulk_delete_hosts", {"host_ids": [other["id"]]})
    assert called == WRITE_TOOLS
    written = [c for c in await all_rows(Change) if c.source == "mcp"]
    assert len(written) == len(WRITE_TOOLS)
    assert {c.actor for c in written} == {"grant:Example"}
    assert not [c.summary for c in written if "Example" in c.summary]


async def test_a_grant_whose_name_was_not_kept_is_named_by_its_id(
    web_on, linking, no_ambient_token, client
):
    """A declared name the server did not keep (`oauth.kept_name`; #29 brings the
    sanitising) leaves no name to record. The write is not the owner's, so `actor` is
    never null for it: it is the grant's id, under a prefix no client name can make."""
    subnet = await lab(client)
    _, tokens = await linked(web_on, can_write=True, client_name=" Example")
    grant = await the_grant()
    async with running(writes_app), mcp_client(tokens["access_token"], app=writes_app) as assistant:
        await assistant.call_tool("create_host", {"subnet_id": subnet["id"], "ip": "10.0.0.20"})
    [entry] = [c for c in await all_rows(Change) if c.source == "mcp"]
    assert entry.actor == f"grant-id:{grant.id}" == oauth.GRANT_ID_ACTOR_PREFIX + grant.id
    assert not entry.actor.startswith("grant:")
    assert len(entry.actor) <= Change.__table__.c.actor.type.length


async def test_the_owner_key_writes_as_the_owner_with_the_setting_on(
    web_on, linking, no_ambient_token, client
):
    subnet = await lab(client)
    await linked(web_on, can_write=True)
    async with running(writes_app), mcp_client("test-key", app=writes_app) as owner:
        assert {t.name for t in await owner.list_tools()} == EXPECTED_TOOLS
        await owner.call_tool("create_host", {"subnet_id": subnet["id"], "ip": "10.0.0.20"})
    [entry] = [c for c in await all_rows(Change) if c.source == "mcp"]
    assert entry.actor is None


async def test_the_in_process_owner_of_the_test_session_writes_with_a_null_actor(client):
    """`conftest.in_process_mcp_is_the_owner`: a token with no grant in its claims."""
    from fastmcp import Client

    subnet = await lab(client)
    async with Client(mcp) as owner:
        await owner.call_tool("create_host", {"subnet_id": subnet["id"], "ip": "10.0.0.20"})
    [entry] = [c for c in await all_rows(Change) if c.source == "mcp"]
    assert entry.actor is None


# --- The setting turned off: a restart, so the same tokens on the other server ---


async def test_a_read_write_grant_loses_write_access_when_the_setting_is_off_with_no_relink(
    web, web_on, linking, no_ambient_token, client
):
    """The grant and its tokens are the ones the setting-on server issued; the server
    with it off loads them as they are. `create_host` and `request_scan` are unknown,
    `list_hosts` works, and a refresh keeps working, with `ipam:read` alone. Turned on
    again, the same grant writes again: `can_write` is the owner's choice, kept."""
    subnet = await lab(client)
    await make_host(client, subnet["id"], "10.0.0.10", hostname="pve-01")
    agent, _ = await make_agent(client, "scanner")
    await make_target(client, subnet["id"], agent["id"])
    client_info, tokens = await linked(web_on, can_write=True)
    assert tokens["scope"] == "ipam:read ipam:write"
    async with running(oauth_app), mcp_client(tokens["access_token"], app=oauth_app) as assistant:
        assert {t.name for t in await assistant.list_tools()} == READ_TOOLS
        listed = await assistant.call_tool("list_hosts", {"subnet_id": subnet["id"]})
        assert [h["hostname"] for h in listed.data["hosts"]] == ["pve-01"]
        before = database_dump()
        for name, arguments in (
            ("create_host", {"subnet_id": subnet["id"], "ip": "10.0.0.20", "hostname": "nas"}),
            ("request_scan", {"subnet_id": subnet["id"]}),
        ):
            with pytest.raises(ToolError) as refused:
                await assistant.call_tool(name, arguments)
            assert str(refused.value) == f"Unknown tool: '{name}'"
        assert database_dump() == before
    assert [h.hostname for h in await all_rows(Host)] == ["pve-01"]
    # a refresh is answered, with what the grant may do now
    off = await rotate(web, client_info, tokens["refresh_token"])
    assert off["scope"] == "ipam:read"
    assert (await the_grant()).can_write is True and (await the_grant()).revoked_at is None
    # and on again: the same grant, the same token, writes again
    assert await tools_of(off["access_token"], writes_app) == EXPECTED_TOOLS


async def test_the_owner_key_keeps_both_scopes_with_the_setting_off(linking, no_ambient_token):
    for app in (oauth_app, writes_app):
        assert await tools_of("test-key", app) == EXPECTED_TOOLS
    for server in (mcp_server.mcp, writes_mcp):
        token = await server.auth.verify_token("test-key")
        assert token.scopes == OWNER_SCOPES and not token.claims.get("grant_id")


# --- The owner's side: the link window ---


async def test_the_window_state_says_whether_a_link_may_write(web, web_on, client):
    """What #30's dialog asks before it offers "Allow this client to make changes":
    in the state the owner reads, open or not, with no window opened to find out."""
    assert (await window_state(web_on))["remote_writes"] is True
    assert (await window_state(web))["remote_writes"] is False
    # without the OAuth server there is no link at all (the session's app)
    state = (await client.get("/api/v1/link-window")).json()["data"]
    assert state["configured"] is False and state["remote_writes"] is False
    opened = await open_window(web_on)
    assert opened["remote_writes"] is True and opened["can_write"] is False
    cancelled = await web_on.delete("/api/v1/link-window", headers=OWNER)
    assert cancelled.json()["data"]["remote_writes"] is True


async def test_with_the_setting_off_a_read_write_window_is_refused(web):
    res = await web.post("/api/v1/link-window", json={"can_write": True}, headers=OWNER)
    assert res.status_code == 409 and res.json()["error"]["code"] == "REMOTE_WRITES_OFF"
    assert (await window_state(web))["open"] is False
    assert [c for c in await all_rows(Change)] == []


async def test_a_read_write_link_through_the_window_and_the_page(web_on, no_ambient_token, client):
    """End to end on the server with the setting on, nothing opened in the window's
    place: a read-write window, the consent page, the code, the exchange, a write."""
    subnet = await lab(client)
    opened = await open_window(web_on, can_write=True)
    assert opened["can_write"] is True
    registered = await register(web_on)
    url = await consent_with_code(web_on, registered, opened["code"])
    assert (await the_grant()).can_write is True
    tokens = (await exchange(web_on, registered, url.params["code"])).json()
    assert tokens["scope"] == "ipam:read ipam:write"
    async with running(writes_app), mcp_client(tokens["access_token"], app=writes_app) as assistant:
        await assistant.call_tool("create_host", {"subnet_id": subnet["id"], "ip": "10.0.0.20"})
    grant_entry, host_entry = [c for c in await all_rows(Change) if c.entity_type in ("grant", "host")]
    assert grant_entry.summary == "read-write grant issued to a linked client"
    assert (host_entry.source, host_entry.actor) == ("mcp", "grant:Example")


# --- What an assistant is told ---


def _descriptions() -> dict[str, str]:
    return {fn.__name__: fn.__doc__ or "" for fn, _ in mcp_server._TOOLS}


def test_a_read_tool_that_names_a_write_tool_says_it_is_absent_on_a_read_only_connection():
    """Spec §6.5: a hidden tool looks missing, so a read tool's description that sends
    the assistant to a write tool says that it may be absent, and what to do then."""
    descriptions = _descriptions()
    naming = {}
    for name in READ_TOOLS:
        text = " ".join(descriptions[name].split())
        named = {tool for tool in WRITE_TOOLS if re.search(rf"\b{tool}\b", text)}
        if named:
            naming[name] = named
            assert "On a read-only connection" in text, name
            assert "absent" in text and "tell the user what you would have" in text, name
    # the two this was written for; another one that names a write tool is held too
    assert set(naming) >= {"list_scan_findings", "list_device_types"}


def test_nothing_tells_an_assistant_to_retry_a_tool_it_cannot_find():
    """The one place `retry` appears is `request_scan`'s own refusal, which says not
    to; the server's instructions say a missing tool is no fault to work around."""
    texts = {**_descriptions(), "instructions": mcp_server.INSTRUCTIONS}
    found = {
        name: re.findall(r"[^.]*\b(?:retry|retrying|try again|reconnect)\b[^.]*", text, re.I)
        for name, text in texts.items()
    }
    found = {name: hits for name, hits in found.items() if hits}
    assert set(found) == {"request_scan"}
    [hit] = found["request_scan"]
    assert "instead of retrying" in " ".join(hit.split())
    instructions = " ".join(mcp_server.INSTRUCTIONS.split())
    assert "read-only" in instructions and "not offered" in instructions
    assert "tell the user what you would have changed" in instructions


def test_the_instructions_reach_a_client():
    assert mcp.instructions == mcp_server.INSTRUCTIONS == writes_mcp.instructions
