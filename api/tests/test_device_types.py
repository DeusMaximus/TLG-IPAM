from .conftest import make_host, make_section, make_subnet

DEFAULT_NAMES = {"server", "vm", "lxc", "network", "iot", "desktop", "laptop", "phone", "other"}


async def _subnet(client):
    section = await make_section(client)
    return await make_subnet(client, section["id"])


async def _type_by_name(client, name):
    rows = (await client.get("/api/v1/device-types")).json()["data"]
    return next(t for t in rows if t["name"] == name)


async def test_defaults_seeded(client):
    res = await client.get("/api/v1/device-types")
    body = res.json()
    assert {t["name"] for t in body["data"]} == DEFAULT_NAMES
    assert all(t["icon"] for t in body["data"])


async def test_create_custom_type_and_use_it(client):
    res = await client.post(
        "/api/v1/device-types", json={"name": "Raspberry Pi", "icon": "cpu"}
    )
    assert res.status_code == 201
    subnet = await _subnet(client)
    host = await make_host(client, subnet["id"], "10.0.0.5", device_type="raspberry pi")
    # case-insensitive input resolves to the canonical name
    assert host["device_type"] == "Raspberry Pi"
    assert (await _type_by_name(client, "Raspberry Pi"))["host_count"] == 1


async def test_duplicate_name_rejected_case_insensitive(client):
    res = await client.post("/api/v1/device-types", json={"name": "SERVER"})
    assert res.status_code == 409


async def test_unknown_type_rejected_with_valid_list(client):
    subnet = await _subnet(client)
    res = await client.post(
        "/api/v1/hosts", json={"subnet_id": subnet["id"], "ip": "10.0.0.5", "device_type": "toaster"}
    )
    assert res.status_code == 422
    assert "Valid types" in res.json()["error"]["message"]


async def test_rename_cascades_to_hosts(client):
    subnet = await _subnet(client)
    await make_host(client, subnet["id"], "10.0.0.5", device_type="iot")
    await make_host(client, subnet["id"], "10.0.0.6", device_type="iot")
    iot = await _type_by_name(client, "iot")

    res = await client.put(f"/api/v1/device-types/{iot['id']}", json={"name": "smart-home"})
    assert res.status_code == 200

    hosts = (await client.get("/api/v1/hosts")).json()["data"]
    assert all(h["device_type"] == "smart-home" for h in hosts)
    change = (await client.get("/api/v1/changes")).json()["data"][0]
    assert "renamed to smart-home" in change["summary"]
    assert "2 host(s)" in change["summary"]


async def test_delete_protections(client):
    subnet = await _subnet(client)
    await make_host(client, subnet["id"], "10.0.0.5", device_type="laptop")
    laptop = await _type_by_name(client, "laptop")
    other = await _type_by_name(client, "other")
    vm = await _type_by_name(client, "vm")

    # in use → refused
    assert (await client.delete(f"/api/v1/device-types/{laptop['id']}")).status_code == 409
    # fallback → refused even when unused
    assert (await client.delete(f"/api/v1/device-types/{other['id']}")).status_code == 409
    # unused → fine
    assert (await client.delete(f"/api/v1/device-types/{vm['id']}")).status_code == 204


async def test_fallback_cannot_be_renamed(client):
    other = await _type_by_name(client, "other")
    res = await client.put(f"/api/v1/device-types/{other['id']}", json={"name": "misc"})
    assert res.status_code == 422
    # but its icon can change
    res = await client.put(f"/api/v1/device-types/{other['id']}", json={"icon": "box"})
    assert res.status_code == 200
    assert res.json()["data"]["icon"] == "box"


async def test_required_device_type_update_fields_reject_explicit_null(client):
    server = await _type_by_name(client, "server")
    for field in ("name", "icon"):
        res = await client.put(f"/api/v1/device-types/{server['id']}", json={field: None})
        assert res.status_code == 422, (field, res.text)
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_bulk_import_validates_types_per_row(client):
    subnet = await _subnet(client)
    res = await client.post(
        f"/api/v1/subnets/{subnet['id']}/hosts/bulk",
        json={
            "hosts": [
                {"ip": "10.0.0.10", "device_type": "SERVER"},
                {"ip": "10.0.0.11", "device_type": "toaster"},
            ]
        },
    )
    data = res.json()["data"]
    assert data["created"] == 1 and data["errors"] == 1
    by_index = {r["index"]: r for r in data["results"]}
    assert "unknown device type" in by_index[1]["reason"]
    hosts = (await client.get(f"/api/v1/hosts?subnet_id={subnet['id']}")).json()["data"]
    assert hosts[0]["device_type"] == "server"
