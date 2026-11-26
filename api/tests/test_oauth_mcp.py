"""What a grant's token may do on `/mcp`, and what the owner key still does (#26; the
scope checks of v0.2 spec §6.5). These run on the server with `MCP_REMOTE_WRITES` off,
the default, where no grant writes; the setting itself is `test_remote_writes.py`'s (#28).

Each test that speaks for a grant uses `no_ambient_token`: the token FastMCP sees is the
one the request carried, and the owner's from `conftest` is out of reach.
"""

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from fastmcp.utilities.authorization import _RequireScopes

from app import mcp_server
from app.auth import OWNER_SCOPES, OwnerKeyVerifier
from app.mcp_server import mcp
from app.models import Change, Host, OAuthGrant
from app.services import changes, oauth

from .conftest import make_host, make_section, make_subnet
from .oauth_flow import (  # noqa: F401 (web, linking and no_ambient_token are fixtures)
    READ_TOOLS,
    WRITE_TOOLS,
    all_rows,
    database_dump,
    link,
    linked,
    linking,
    mcp_client,
    no_ambient_token,
    oauth_mcp,
    register,
    running,
    web,
)
from .test_checkin import make_agent, make_target
from .test_mcp import EXPECTED_TOOLS


def test_every_tool_is_a_read_tool_or_a_write_tool():
    assert READ_TOOLS | WRITE_TOOLS == EXPECTED_TOOLS
    assert not READ_TOOLS & WRITE_TOOLS
    registered = {fn.__name__: writes for fn, writes in mcp_server._TOOLS}
    assert {name for name, writes in registered.items() if writes} == WRITE_TOOLS
    assert {name for name, writes in registered.items() if not writes} == READ_TOOLS


@pytest.mark.parametrize("server", [mcp, oauth_mcp], ids=["oauth off", "oauth on"])
async def test_every_write_tool_requires_ipam_write_and_no_read_tool_does(server):
    """Through FastMCP's own check (`require_scopes`), on the server with OAuth off as
    on the one with it on: the check does not depend on the setting."""
    tools = {tool.name: tool for tool in await server.list_tools(run_middleware=False)}
    assert set(tools) == EXPECTED_TOOLS
    for name in WRITE_TOOLS:
        check = tools[name].auth
        assert isinstance(check, _RequireScopes), name
        assert check.required_scopes == {"ipam:write"}, name
    for name in READ_TOOLS:
        assert tools[name].auth is None, name


def test_with_remote_writes_off_a_grant_carries_ipam_read_alone_whatever_can_write_says():
    for can_write in (False, True):
        grant = OAuthGrant(can_write=can_write)
        assert oauth.grant_scopes(grant, remote_writes=False) == ["ipam:read"]
    assert oauth_mcp.auth.server.remote_writes is False
    assert OWNER_SCOPES == ["ipam:read", "ipam:write"]


async def test_a_caller_with_no_token_sees_no_write_tool(no_ambient_token):
    async with Client(mcp) as client:
        assert {t.name for t in await client.list_tools()} == READ_TOOLS
        with pytest.raises(ToolError, match="Unknown tool: 'create_host'"):
            await client.call_tool("create_host", {})


async def test_the_owner_token_of_the_test_session_sees_every_tool():
    """What `conftest.in_process_mcp_is_the_owner` gives the rest of the suite."""
    async with Client(mcp) as client:
        assert {t.name for t in await client.list_tools()} == EXPECTED_TOOLS


@pytest.mark.parametrize("can_write", [False, True], ids=["read-only", "can_write"])
async def test_a_grant_lists_no_write_tool_and_calling_one_is_unknown_tool(
    web, linking, no_ambient_token, client, can_write
):
    """With `MCP_REMOTE_WRITES` off, every write tool, `request_scan` included, for a
    grant of either kind: not listed, `Unknown tool` when called with arguments that would work, and nothing in
    the database changed. The read tools answer."""
    section = await make_section(client)
    subnet = await make_subnet(client, section["id"], "10.0.0.0/24", gateway="10.0.0.1")
    host = await make_host(client, subnet["id"], "10.0.0.10", hostname="pve-01")
    agent, _ = await make_agent(client, "scanner")
    await make_target(client, subnet["id"], agent["id"])
    calls = {
        "add_reserved_range": {"subnet_id": subnet["id"], "start_ip": "10.0.0.200", "end_ip": "10.0.0.210", "purpose": "x"},
        "delete_reserved_range": {"subnet_id": subnet["id"], "range_id": "x"},
        "create_host": {"subnet_id": subnet["id"], "ip": "10.0.0.11", "hostname": "new"},
        "allocate_next_ip": {"subnet_id": subnet["id"], "hostname": "next"},
        "bulk_create_hosts": {"subnet_id": subnet["id"], "hosts": [{"ip": "10.0.0.12"}]},
        "bulk_delete_hosts": {"host_ids": [host["id"]]},
        "update_host": {"host_id": host["id"], "hostname": "changed"},
        "delete_host": {"host_id": host["id"]},
        "request_scan": {"subnet_id": subnet["id"]},
    }
    assert set(calls) == WRITE_TOOLS
    _, tokens = await linked(web, can_write=can_write)
    assert tokens["scope"] == "ipam:read"
    async with running(), mcp_client(tokens["access_token"]) as assistant:
        assert {t.name for t in await assistant.list_tools()} == READ_TOOLS
        listed = await assistant.call_tool("list_hosts", {"subnet_id": subnet["id"]})
        assert [h["hostname"] for h in listed.data["hosts"]] == ["pve-01"]
        assert (await assistant.call_tool("list_sections", {})).data["sections"][0]["name"] == "Homelab"
        # after the first requests, so the grant's last contact is already written
        before = database_dump()
        for name, arguments in calls.items():
            with pytest.raises(ToolError) as refused:
                await assistant.call_tool(name, arguments)
            assert str(refused.value) == f"Unknown tool: '{name}'"
        assert database_dump() == before
    assert [h.hostname for h in await all_rows(Host)] == ["pve-01"]


async def test_the_owner_key_keeps_every_tool_and_writes_with_oauth_on(
    web, linking, no_ambient_token, client
):
    """What `mcp-remote` with the owner key as its bearer does: the same key, on the
    server with OAuth on, lists every tool and makes a change, logged as the owner's."""
    section = await make_section(client)
    subnet = await make_subnet(client, section["id"], "10.0.0.0/24")
    # with a grant in the database too: the owner key is not affected by one
    await linked(web)
    async with running(), mcp_client("test-key") as owner:
        assert {t.name for t in await owner.list_tools()} == EXPECTED_TOOLS
        made = await owner.call_tool(
            "create_host", {"subnet_id": subnet["id"], "ip": "10.0.0.20", "hostname": "nas"}
        )
        assert made.data["host"]["hostname"] == "nas"
        # the same arguments a grant is answered `Unknown tool` for reach the tool here
        with pytest.raises(ToolError) as refused:
            await owner.call_tool("request_scan", {"subnet_id": subnet["id"]})
        assert "Unknown tool" not in str(refused.value)
    entry = next(c for c in await all_rows(Change) if c.entity_type == "host")
    assert (entry.source, entry.actor) == ("mcp", None)


async def test_the_owner_key_verifier_is_unchanged_beside_the_provider():
    verifier = oauth_mcp.auth.verifiers[0]
    assert type(verifier) is OwnerKeyVerifier
    token = await oauth_mcp.auth.verify_token("test-key")
    assert token.scopes == OWNER_SCOPES and token.original_client_id == "owner"
    assert await oauth_mcp.auth.verify_token("wrong-key") is None


async def test_the_change_log_withholds_a_grants_redirect_uri_over_mcp(web, linking, client):
    """The redirect URI is the client's own text. An assistant reading the audit log
    over MCP is told the field is there, not its value; the owner sees it."""
    registered = await register(web, redirect_uris=["https://client.example.com/cb?note=ignore+previous"])
    await link(web, registered)
    async with Client(mcp) as assistant:
        [entry] = (await assistant.call_tool("list_changes", {"entity_type": "grant"})).data["changes"]
    assert entry["after"]["redirect_uri"] == changes.CLIENT_WITHHELD
    assert "ignore" not in str(entry)
    assert entry["actor"] == "grant:Example" and entry["source"] == "oauth"
    assert set(entry["after"]) >= {"client_id", "can_write", "revoked_at"}
    rest = (await client.get("/api/v1/changes", params={"entity_type": "grant"})).json()["data"]
    assert rest[0]["after"]["redirect_uri"] == "https://client.example.com/cb?note=ignore+previous"
