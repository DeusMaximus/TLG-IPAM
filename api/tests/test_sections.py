from .conftest import make_section, make_subnet


async def test_create_and_list(client):
    await make_section(client, "Home LAN")
    await make_section(client, "IoT")
    res = await client.get("/api/v1/sections")
    body = res.json()
    assert body["total"] == 2
    assert [s["name"] for s in body["data"]] == ["Home LAN", "IoT"]
    assert all(s["subnet_count"] == 0 for s in body["data"])


async def test_subnet_count(client):
    section = await make_section(client)
    await make_subnet(client, section["id"], "10.0.0.0/24")
    await make_subnet(client, section["id"], "10.0.1.0/24", name="lab2")
    res = await client.get("/api/v1/sections")
    assert res.json()["data"][0]["subnet_count"] == 2


async def test_update(client):
    section = await make_section(client)
    res = await client.put(
        f"/api/v1/sections/{section['id']}", json={"colour": "#3b82f6"}
    )
    assert res.status_code == 200
    assert res.json()["data"]["colour"] == "#3b82f6"
    assert res.json()["data"]["name"] == section["name"]


async def test_delete_refused_with_subnets(client):
    section = await make_section(client)
    await make_subnet(client, section["id"])
    res = await client.delete(f"/api/v1/sections/{section['id']}")
    assert res.status_code == 409


async def test_delete_empty_section(client):
    section = await make_section(client)
    res = await client.delete(f"/api/v1/sections/{section['id']}")
    assert res.status_code == 204
    assert (await client.get("/api/v1/sections")).json()["total"] == 0


async def test_missing_section_404(client):
    res = await client.get("/api/v1/subnets?section_id=nope")
    assert res.json()["total"] == 0
    res = await client.put("/api/v1/sections/nope", json={"name": "x"})
    assert res.status_code == 404
