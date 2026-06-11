from fastmcp import Client

from app.mcp_server import mcp
from .conftest import make_host, make_section, make_subnet


async def _subnet(client, **extra):
    section = await make_section(client)
    return await make_subnet(client, section["id"], **extra)


async def test_bulk_mixed_batch(client):
    subnet = await _subnet(client, domain="lab.net")
    await make_host(client, subnet["id"], "10.0.0.5", hostname="existing")

    res = await client.post(
        f"/api/v1/subnets/{subnet['id']}/hosts/bulk",
        json={
            "hosts": [
                {"ip": "10.0.0.10", "hostname": "sw-01", "device_type": "network"},
                {"ip": "10.0.0.11", "hostname": "sw-02", "mac": "AA-BB-CC-00-11-22"},
                {"ip": "10.0.0.5", "hostname": "dup-existing"},
                {"ip": "10.0.0.10", "hostname": "dup-in-batch"},
                {"ip": "10.0.99.1", "hostname": "outside"},
                {"ip": "10.0.0.12", "mac": "not-a-mac"},
            ]
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert (data["created"], data["skipped"], data["errors"]) == (2, 2, 2)

    by_index = {r["index"]: r for r in data["results"]}
    assert by_index[0]["status"] == "created" and by_index[0]["host_id"]
    assert by_index[1]["status"] == "created"
    assert by_index[2]["status"] == "skipped" and "duplicate" in by_index[2]["reason"]
    assert by_index[3]["status"] == "skipped"
    assert by_index[4]["status"] == "error" and "not within" in by_index[4]["reason"]
    assert by_index[5]["status"] == "error" and "mac" in by_index[5]["reason"]

    # created records actually exist, with normalised MAC and inherited domain
    hosts = (await client.get(f"/api/v1/hosts?subnet_id={subnet['id']}")).json()["data"]
    assert len(hosts) == 3  # existing + 2 created
    sw02 = next(h for h in hosts if h["hostname"] == "sw-02")
    assert sw02["mac"] == "aa:bb:cc:00:11:22"
    assert sw02["fqdn"] == "sw-02.lab.net"


async def test_bulk_is_single_audit_entry(client):
    subnet = await _subnet(client)
    before = (await client.get("/api/v1/changes")).json()["total"]
    await client.post(
        f"/api/v1/subnets/{subnet['id']}/hosts/bulk",
        json={"hosts": [{"ip": f"10.0.0.{i}"} for i in range(10, 40)]},
    )
    changes = (await client.get("/api/v1/changes")).json()
    assert changes["total"] == before + 1
    newest = changes["data"][0]
    assert "bulk import" in newest["summary"]
    assert "30 created" in newest["summary"]


async def test_bulk_rejects_empty_and_oversized(client):
    subnet = await _subnet(client)
    res = await client.post(f"/api/v1/subnets/{subnet['id']}/hosts/bulk", json={"hosts": []})
    assert res.status_code == 422
    res = await client.post(
        f"/api/v1/subnets/{subnet['id']}/hosts/bulk",
        json={"hosts": [{"ip": "10.0.0.1"}] * 1001},
    )
    assert res.status_code == 422


async def test_bulk_delete(client):
    subnet = await _subnet(client)
    hosts = [await make_host(client, subnet["id"], f"10.0.0.{i}") for i in range(10, 15)]
    ids = [h["id"] for h in hosts[:3]]

    res = await client.post(
        "/api/v1/hosts/bulk-delete", json={"host_ids": ids + ["nonexistent"]}
    )
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["deleted"] == 3
    assert data["not_found"] == ["nonexistent"]

    remaining = (await client.get(f"/api/v1/hosts?subnet_id={subnet['id']}")).json()
    assert remaining["total"] == 2

    change = (await client.get("/api/v1/changes")).json()["data"][0]
    assert "bulk delete" in change["summary"] and "3 host(s)" in change["summary"]
    assert len(change["before"]["hosts"]) == 3


async def test_subnet_force_delete(client):
    subnet = await _subnet(client)
    for i in range(10, 13):
        await make_host(client, subnet["id"], f"10.0.0.{i}")

    # without force: still refused
    res = await client.delete(f"/api/v1/subnets/{subnet['id']}")
    assert res.status_code == 409

    res = await client.delete(f"/api/v1/subnets/{subnet['id']}?force=true")
    assert res.status_code == 204
    assert (await client.get(f"/api/v1/subnets/{subnet['id']}")).status_code == 404
    assert (await client.get("/api/v1/hosts")).json()["total"] == 0

    change = (await client.get("/api/v1/changes")).json()["data"][0]
    assert "deleted along with 3 host(s)" in change["summary"]
    assert len(change["before"]["hosts"]) == 3


async def test_bulk_delete_via_mcp(client):
    subnet = await _subnet(client)
    hosts = [await make_host(client, subnet["id"], f"10.0.0.{i}") for i in range(10, 12)]
    async with Client(mcp) as mcp_client:
        result = (
            await mcp_client.call_tool(
                "bulk_delete_hosts", {"host_ids": [h["id"] for h in hosts]}
            )
        ).data
    assert result["deleted"] == 2
    change = (await client.get("/api/v1/changes")).json()["data"][0]
    assert change["source"] == "mcp"


async def test_bulk_via_mcp(client):
    subnet = await _subnet(client, cidr="10.0.0.0/24")
    async with Client(mcp) as mcp_client:
        result = (
            await mcp_client.call_tool(
                "bulk_create_hosts",
                {
                    "subnet_id": subnet["id"],
                    "hosts": [
                        {"ip": "10.0.0.10", "hostname": "ap-01", "device_type": "network"},
                        {"ip": "10.0.0.11", "hostname": "ap-02", "device_type": "network"},
                    ],
                },
            )
        ).data
    assert result["created"] == 2
    change = (await client.get("/api/v1/changes")).json()["data"][0]
    assert change["source"] == "mcp"
    assert "bulk import" in change["summary"]
