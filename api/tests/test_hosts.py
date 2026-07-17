from .conftest import make_host, make_section, make_subnet


async def _subnet(client, **extra):
    section = await make_section(client)
    return await make_subnet(client, section["id"], **extra)


async def test_ip_outside_subnet_rejected(client):
    subnet = await _subnet(client)
    res = await client.post(
        "/api/v1/hosts", json={"subnet_id": subnet["id"], "ip": "10.0.1.5"}
    )
    assert res.status_code == 422
    assert "not within" in res.json()["error"]["message"]


async def test_network_and_broadcast_rejected(client):
    subnet = await _subnet(client)
    for ip in ("10.0.0.0", "10.0.0.255"):
        res = await client.post("/api/v1/hosts", json={"subnet_id": subnet["id"], "ip": ip})
        assert res.status_code == 422, ip


async def test_duplicate_ip_rejected(client):
    subnet = await _subnet(client)
    await make_host(client, subnet["id"], "10.0.0.5")
    res = await client.post("/api/v1/hosts", json={"subnet_id": subnet["id"], "ip": "10.0.0.5"})
    assert res.status_code == 409


async def test_mac_normalised(client):
    subnet = await _subnet(client)
    host = await make_host(client, subnet["id"], "10.0.0.5", mac="AA-BB-CC-DD-EE-FF")
    assert host["mac"] == "aa:bb:cc:dd:ee:ff"
    host = await make_host(client, subnet["id"], "10.0.0.6", mac="aabb.ccdd.ee00")
    assert host["mac"] == "aa:bb:cc:dd:ee:00"


async def test_invalid_mac_rejected(client):
    subnet = await _subnet(client)
    res = await client.post(
        "/api/v1/hosts", json={"subnet_id": subnet["id"], "ip": "10.0.0.5", "mac": "zz:zz"}
    )
    assert res.status_code == 422


async def test_search_and_filters(client):
    subnet = await _subnet(client)
    await make_host(client, subnet["id"], "10.0.0.5", hostname="pbs-01", mac="aa:bb:cc:dd:ee:ff")
    await make_host(client, subnet["id"], "10.0.0.6", hostname="pve-01", status="offline")

    by_name = (await client.get("/api/v1/hosts?q=pbs")).json()
    assert by_name["total"] == 1 and by_name["data"][0]["hostname"] == "pbs-01"

    by_ip = (await client.get("/api/v1/hosts?q=10.0.0.6")).json()
    assert by_ip["total"] == 1 and by_ip["data"][0]["hostname"] == "pve-01"

    by_mac = (await client.get("/api/v1/hosts?q=dd:ee")).json()
    assert by_mac["total"] == 1

    by_mac_upper = (await client.get("/api/v1/hosts?q=DD:EE")).json()
    assert by_mac_upper["total"] == 1

    offline = (await client.get("/api/v1/hosts?status=offline")).json()
    assert offline["total"] == 1 and offline["data"][0]["status"] == "offline"


async def test_hosts_sorted_numerically_by_ip(client):
    subnet = await _subnet(client)
    for ip in ("10.0.0.10", "10.0.0.2", "10.0.0.1"):
        await make_host(client, subnet["id"], ip)
    ips = [h["ip"] for h in (await client.get("/api/v1/hosts")).json()["data"]]
    assert ips == ["10.0.0.1", "10.0.0.2", "10.0.0.10"]


async def test_update_and_delete_frees_ip(client):
    subnet = await _subnet(client)
    host = await make_host(client, subnet["id"], "10.0.0.5")
    res = await client.put(f"/api/v1/hosts/{host['id']}", json={"ip": "10.0.0.9", "status": "reserved"})
    assert res.status_code == 200
    assert res.json()["data"]["ip"] == "10.0.0.9"

    res = await client.delete(f"/api/v1/hosts/{host['id']}")
    assert res.status_code == 204
    # the freed IP can be reused
    await make_host(client, subnet["id"], "10.0.0.9")


async def test_required_host_update_fields_reject_explicit_null(client):
    subnet = await _subnet(client)
    host = await make_host(client, subnet["id"], "10.0.0.5")
    for field in ("ip", "device_type", "status"):
        res = await client.put(f"/api/v1/hosts/{host['id']}", json={field: None})
        assert res.status_code == 422, (field, res.text)
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_empty_host_update_rejected(client):
    subnet = await _subnet(client)
    host = await make_host(client, subnet["id"], "10.0.0.5")
    res = await client.put(f"/api/v1/hosts/{host['id']}", json={})
    assert res.status_code == 422
    assert "At least one host field" in res.json()["error"]["message"]
