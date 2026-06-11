from .conftest import make_host, make_section, make_subnet


async def test_invalid_cidr_rejected(client):
    section = await make_section(client)
    for bad in ("300.1.1.1/24", "10.0.0.1/24", "not-a-cidr", "10.0.0.0/33"):
        res = await client.post(
            "/api/v1/subnets", json={"section_id": section["id"], "cidr": bad, "name": "x"}
        )
        assert res.status_code == 422, f"{bad} should be rejected: {res.text}"
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_overlap_rejected_within_section(client):
    section = await make_section(client)
    await make_subnet(client, section["id"], "10.0.0.0/16")
    res = await client.post(
        "/api/v1/subnets",
        json={"section_id": section["id"], "cidr": "10.0.5.0/24", "name": "overlap"},
    )
    assert res.status_code == 409


async def test_overlap_allowed_across_sections(client):
    lan = await make_section(client, "LAN")
    vpn = await make_section(client, "VPN")
    await make_subnet(client, lan["id"], "10.0.0.0/24")
    sub = await make_subnet(client, vpn["id"], "10.0.0.0/24", name="vpn view")
    assert sub["cidr"] == "10.0.0.0/24"


async def test_gateway_must_be_inside(client):
    section = await make_section(client)
    res = await client.post(
        "/api/v1/subnets",
        json={"section_id": section["id"], "cidr": "10.0.0.0/24", "name": "x", "gateway": "10.0.1.1"},
    )
    assert res.status_code == 422


async def test_dhcp_pool_validation(client):
    section = await make_section(client)
    base = {"section_id": section["id"], "cidr": "10.0.0.0/24", "name": "x"}
    # one-sided
    res = await client.post("/api/v1/subnets", json={**base, "dhcp_start": "10.0.0.100"})
    assert res.status_code == 422
    # outside subnet
    res = await client.post(
        "/api/v1/subnets", json={**base, "dhcp_start": "10.0.1.100", "dhcp_end": "10.0.1.200"}
    )
    assert res.status_code == 422
    # start > end
    res = await client.post(
        "/api/v1/subnets", json={**base, "dhcp_start": "10.0.0.200", "dhcp_end": "10.0.0.100"}
    )
    assert res.status_code == 422


async def test_vlan_range(client):
    section = await make_section(client)
    res = await client.post(
        "/api/v1/subnets",
        json={"section_id": section["id"], "cidr": "10.0.0.0/24", "name": "x", "vlan_id": 5000},
    )
    assert res.status_code == 422


async def test_utilisation_stats(client):
    section = await make_section(client)
    subnet = await make_subnet(
        client,
        section["id"],
        "10.0.0.0/24",
        gateway="10.0.0.1",
        dhcp_start="10.0.0.100",
        dhcp_end="10.0.0.199",
    )
    assert subnet["usable"] == 254
    # 254 usable - 1 gateway - 100 DHCP pool
    assert subnet["free"] == 153
    await make_host(client, subnet["id"], "10.0.0.10")
    detail = (await client.get(f"/api/v1/subnets/{subnet['id']}")).json()["data"]
    assert detail["used"] == 1
    assert detail["free"] == 152


async def test_cidr_change_rejected_if_hosts_fall_outside(client):
    section = await make_section(client)
    subnet = await make_subnet(client, section["id"], "10.0.0.0/24")
    await make_host(client, subnet["id"], "10.0.0.200")
    res = await client.put(f"/api/v1/subnets/{subnet['id']}", json={"cidr": "10.0.0.0/25"})
    assert res.status_code == 422
    res = await client.put(f"/api/v1/subnets/{subnet['id']}", json={"cidr": "10.0.0.0/23"})
    assert res.status_code == 200


async def test_delete_refused_with_hosts(client):
    section = await make_section(client)
    subnet = await make_subnet(client, section["id"])
    await make_host(client, subnet["id"], "10.0.0.5")
    res = await client.delete(f"/api/v1/subnets/{subnet['id']}")
    assert res.status_code == 409
