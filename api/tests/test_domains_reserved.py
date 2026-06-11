from .conftest import make_host, make_section, make_subnet


async def _subnet(client, **extra):
    section = await make_section(client)
    return await make_subnet(client, section["id"], **extra)


# --- domain suffix ---


async def test_host_inherits_subnet_domain(client):
    subnet = await _subnet(client, domain="lab.home.arpa")
    host = await make_host(client, subnet["id"], "10.0.0.5", hostname="pbs-01")
    assert host["domain"] is None
    assert host["fqdn"] == "pbs-01.lab.home.arpa"


async def test_host_domain_override(client):
    subnet = await _subnet(client, domain="lab.home.arpa")
    host = await make_host(
        client, subnet["id"], "10.0.0.5", hostname="edge-01", domain="dmz.home.arpa"
    )
    assert host["domain"] == "dmz.home.arpa"
    assert host["fqdn"] == "edge-01.dmz.home.arpa"


async def test_clearing_override_reinherits(client):
    subnet = await _subnet(client, domain="lab.home.arpa")
    host = await make_host(
        client, subnet["id"], "10.0.0.5", hostname="h1", domain="other.net"
    )
    res = await client.put(f"/api/v1/hosts/{host['id']}", json={"domain": None})
    assert res.status_code == 200
    assert res.json()["data"]["fqdn"] == "h1.lab.home.arpa"


async def test_subnet_domain_change_propagates_to_inheriting_hosts(client):
    subnet = await _subnet(client, domain="old.net")
    host = await make_host(client, subnet["id"], "10.0.0.5", hostname="h1")
    await client.put(f"/api/v1/subnets/{subnet['id']}", json={"domain": "new.net"})
    res = await client.get(f"/api/v1/hosts/{host['id']}")
    assert res.json()["data"]["fqdn"] == "h1.new.net"


async def test_no_fqdn_without_hostname_or_domain(client):
    subnet = await _subnet(client)  # no domain
    host = await make_host(client, subnet["id"], "10.0.0.5", hostname="h1")
    assert host["fqdn"] is None
    bare = await make_host(client, subnet["id"], "10.0.0.6")  # no hostname
    assert bare["fqdn"] is None


async def test_domain_normalised_and_validated(client):
    subnet = await _subnet(client, domain=".Lab.HOME.Arpa")
    assert subnet["domain"] == "lab.home.arpa"
    section = await make_section(client, "Bad")
    res = await client.post(
        "/api/v1/subnets",
        json={"section_id": section["id"], "cidr": "10.1.0.0/24", "name": "x", "domain": "no spaces!"},
    )
    assert res.status_code == 422


# --- reserved ranges ---


async def test_reserved_range_crud_and_validation(client):
    subnet = await _subnet(client)
    # outside subnet
    res = await client.post(
        f"/api/v1/subnets/{subnet['id']}/reserved",
        json={"start_ip": "10.0.1.10", "end_ip": "10.0.1.20"},
    )
    assert res.status_code == 422
    # start > end
    res = await client.post(
        f"/api/v1/subnets/{subnet['id']}/reserved",
        json={"start_ip": "10.0.0.20", "end_ip": "10.0.0.10"},
    )
    assert res.status_code == 422
    # valid
    res = await client.post(
        f"/api/v1/subnets/{subnet['id']}/reserved",
        json={"start_ip": "10.0.0.10", "end_ip": "10.0.0.20", "description": "network gear"},
    )
    assert res.status_code == 201
    block = res.json()["data"]

    detail = (await client.get(f"/api/v1/subnets/{subnet['id']}")).json()["data"]
    assert detail["reserved_ranges"] == [block]

    res = await client.delete(f"/api/v1/subnets/{subnet['id']}/reserved/{block['id']}")
    assert res.status_code == 204
    detail = (await client.get(f"/api/v1/subnets/{subnet['id']}")).json()["data"]
    assert detail["reserved_ranges"] == []


async def test_allocation_skips_reserved_but_manual_create_allowed(client):
    subnet = await _subnet(client, cidr="10.0.0.0/28")  # usable .1-.14
    await client.post(
        f"/api/v1/subnets/{subnet['id']}/reserved",
        json={"start_ip": "10.0.0.1", "end_ip": "10.0.0.5", "description": "switches"},
    )
    res = await client.post(f"/api/v1/subnets/{subnet['id']}/next-free", json={"hostname": "a"})
    assert res.json()["data"]["ip"] == "10.0.0.6"

    free = (await client.get(f"/api/v1/subnets/{subnet['id']}/free")).json()["data"]
    assert free[0] == "10.0.0.7"
    assert not any(ip.endswith((".1", ".2", ".3", ".4", ".5")) for ip in free)

    # manual creation inside the reserved block is allowed
    host = await make_host(client, subnet["id"], "10.0.0.3", hostname="sw-01")
    assert host["ip"] == "10.0.0.3"


async def test_stats_account_for_reserved(client):
    subnet = await _subnet(client, cidr="10.0.0.0/24")
    assert subnet["free"] == 254
    await client.post(
        f"/api/v1/subnets/{subnet['id']}/reserved",
        json={"start_ip": "10.0.0.10", "end_ip": "10.0.0.19"},
    )
    detail = (await client.get(f"/api/v1/subnets/{subnet['id']}")).json()["data"]
    assert detail["free"] == 244
    assert detail["usable"] == 254
    # a host inside the reserved block counts as used but does not double-subtract
    await make_host(client, subnet["id"], "10.0.0.12")
    detail = (await client.get(f"/api/v1/subnets/{subnet['id']}")).json()["data"]
    assert detail["used"] == 1
    assert detail["free"] == 244


async def test_subnet_delete_cascades_reserved_ranges(client):
    subnet = await _subnet(client)
    await client.post(
        f"/api/v1/subnets/{subnet['id']}/reserved",
        json={"start_ip": "10.0.0.10", "end_ip": "10.0.0.19"},
    )
    res = await client.delete(f"/api/v1/subnets/{subnet['id']}")
    assert res.status_code == 204


async def test_reserved_changes_logged(client):
    subnet = await _subnet(client)
    await client.post(
        f"/api/v1/subnets/{subnet['id']}/reserved",
        json={"start_ip": "10.0.0.10", "end_ip": "10.0.0.19", "description": "switches"},
    )
    change = (await client.get("/api/v1/changes")).json()["data"][0]
    assert "reserved range 10.0.0.10-10.0.0.19 added" in change["summary"]
    assert "switches" in change["summary"]
