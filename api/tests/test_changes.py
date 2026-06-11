from .conftest import make_host, make_section, make_subnet


async def test_writes_are_logged_with_source(client):
    section = await make_section(client)
    subnet = await make_subnet(client, section["id"])
    host = await make_host(client, subnet["id"], "10.0.0.5", hostname="pbs-01")
    await client.delete(f"/api/v1/hosts/{host['id']}")

    res = await client.get("/api/v1/changes")
    body = res.json()
    assert body["total"] == 4
    newest = body["data"][0]
    assert newest["action"] == "delete"
    assert newest["entity_type"] == "host"
    assert "pbs-01" in newest["summary"]
    assert newest["before"]["ip"] == "10.0.0.5"
    assert all(c["source"] == "api" for c in body["data"])


async def test_ui_source_detected(client):
    res = await client.post(
        "/api/v1/sections", json={"name": "LAN"}, headers={"X-TLG-Client": "ui"}
    )
    assert res.status_code == 201
    change = (await client.get("/api/v1/changes")).json()["data"][0]
    assert change["source"] == "ui"


async def test_update_records_before_and_after(client):
    section = await make_section(client)
    await client.put(f"/api/v1/sections/{section['id']}", json={"name": "Renamed"})
    change = (await client.get("/api/v1/changes")).json()["data"][0]
    assert change["before"]["name"] == "Homelab"
    assert change["after"]["name"] == "Renamed"


async def test_entity_type_filter(client):
    section = await make_section(client)
    await make_subnet(client, section["id"])
    res = await client.get("/api/v1/changes?entity_type=subnet")
    body = res.json()
    assert body["total"] == 1
    assert body["data"][0]["entity_type"] == "subnet"
