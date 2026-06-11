from .conftest import make_host, make_section, make_subnet


async def test_next_free_skips_reserved_space(client):
    section = await make_section(client)
    subnet = await make_subnet(
        client,
        section["id"],
        "10.0.0.0/29",  # usable .1-.6
        gateway="10.0.0.1",
        dhcp_start="10.0.0.2",
        dhcp_end="10.0.0.3",
    )
    res = await client.post(
        f"/api/v1/subnets/{subnet['id']}/next-free", json={"hostname": "a"}
    )
    assert res.status_code == 201
    assert res.json()["data"]["ip"] == "10.0.0.4"

    res = await client.post(
        f"/api/v1/subnets/{subnet['id']}/next-free", json={"hostname": "b"}
    )
    assert res.json()["data"]["ip"] == "10.0.0.5"

    await make_host(client, subnet["id"], "10.0.0.6")
    res = await client.post(
        f"/api/v1/subnets/{subnet['id']}/next-free", json={"hostname": "c"}
    )
    assert res.status_code == 409
    assert "No free IPs" in res.json()["error"]["message"]


async def test_free_list_matches(client):
    section = await make_section(client)
    subnet = await make_subnet(
        client, section["id"], "10.0.0.0/29", gateway="10.0.0.1"
    )
    await make_host(client, subnet["id"], "10.0.0.3")
    res = await client.get(f"/api/v1/subnets/{subnet['id']}/free")
    assert res.json()["data"] == ["10.0.0.2", "10.0.0.4", "10.0.0.5", "10.0.0.6"]


async def test_free_list_limit(client):
    section = await make_section(client)
    subnet = await make_subnet(client, section["id"], "10.0.0.0/24")
    res = await client.get(f"/api/v1/subnets/{subnet['id']}/free?limit=3")
    assert res.json()["data"] == ["10.0.0.1", "10.0.0.2", "10.0.0.3"]


async def test_slash_31_has_two_usable(client):
    section = await make_section(client)
    subnet = await make_subnet(client, section["id"], "10.0.0.0/31")
    assert subnet["usable"] == 2
    for expected in ("10.0.0.0", "10.0.0.1"):
        res = await client.post(
            f"/api/v1/subnets/{subnet['id']}/next-free", json={"hostname": f"h{expected}"}
        )
        assert res.status_code == 201
        assert res.json()["data"]["ip"] == expected
    res = await client.post(
        f"/api/v1/subnets/{subnet['id']}/next-free", json={"hostname": "extra"}
    )
    assert res.status_code == 409


async def test_hostname_required_for_allocation(client):
    section = await make_section(client)
    subnet = await make_subnet(client, section["id"])
    res = await client.post(f"/api/v1/subnets/{subnet['id']}/next-free", json={})
    assert res.status_code == 422
