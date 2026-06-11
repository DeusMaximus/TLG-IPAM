from fastmcp import Client

from app.mcp_server import mcp
from .conftest import make_section, make_subnet

EXPECTED_TOOLS = {
    "list_sections",
    "list_subnets",
    "get_subnet",
    "get_free_ips",
    "list_hosts",
    "get_host",
    "create_host",
    "bulk_create_hosts",
    "allocate_next_ip",
    "update_host",
    "delete_host",
    "bulk_delete_hosts",
    "list_changes",
    "add_reserved_range",
    "delete_reserved_range",
    "list_device_types",
}


async def test_all_tools_registered():
    async with Client(mcp) as mcp_client:
        tools = await mcp_client.list_tools()
        assert {t.name for t in tools} == EXPECTED_TOOLS
        # descriptions are Claude's documentation — they must exist
        assert all(t.description for t in tools)


async def test_mcp_end_to_end(client):
    section = await make_section(client, "Homelab")
    subnet = await make_subnet(client, section["id"], "10.0.0.0/29", gateway="10.0.0.1")

    async with Client(mcp) as mcp_client:
        sections = (await mcp_client.call_tool("list_sections", {})).data["sections"]
        assert sections[0]["name"] == "Homelab"

        allocated = (
            await mcp_client.call_tool(
                "allocate_next_ip", {"subnet_id": subnet["id"], "hostname": "pbs-01"}
            )
        ).data["host"]
        assert allocated["ip"] == "10.0.0.2"  # .1 is the gateway

        found = (await mcp_client.call_tool("list_hosts", {"q": "pbs"})).data["hosts"]
        assert found[0]["hostname"] == "pbs-01"

        detail = (await mcp_client.call_tool("get_subnet", {"subnet_id": subnet["id"]})).data[
            "subnet"
        ]
        assert detail["used"] == 1

    # MCP writes land in the audit log with source=mcp
    change = (await client.get("/api/v1/changes")).json()["data"][0]
    assert change["source"] == "mcp"
    assert change["entity_type"] == "host"


async def test_mcp_errors_are_meaningful(client):
    section = await make_section(client)
    subnet = await make_subnet(client, section["id"])
    async with Client(mcp) as mcp_client:
        result = await mcp_client.call_tool(
            "create_host",
            {"subnet_id": subnet["id"], "ip": "192.168.99.1"},
            raise_on_error=False,
        )
        assert result.is_error
        assert "not within" in result.content[0].text
