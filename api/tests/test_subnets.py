from sqlalchemy import update

from app.database import SessionLocal
from app.models import Subnet
from app.schemas import SUBNET_DNS_SERVERS_MAX

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


async def test_cidr_change_rejected_if_host_becomes_network_or_broadcast(client):
    section = await make_section(client)
    subnet = await make_subnet(client, section["id"], "10.0.0.0/23")
    await make_host(client, subnet["id"], "10.0.1.0")

    res = await client.put(f"/api/v1/subnets/{subnet['id']}", json={"cidr": "10.0.1.0/24"})

    assert res.status_code == 422
    assert "would not be assignable" in res.json()["error"]["message"]


async def test_cidr_change_preserves_31_and_32_host_semantics(client):
    section = await make_section(client)
    subnet = await make_subnet(client, section["id"], "10.0.0.0/30")
    await make_host(client, subnet["id"], "10.0.0.1")

    assert (
        await client.put(f"/api/v1/subnets/{subnet['id']}", json={"cidr": "10.0.0.0/31"})
    ).status_code == 200


async def test_cidr_change_rejected_if_reserved_range_falls_outside(client):
    section = await make_section(client)
    subnet = await make_subnet(client, section["id"], "10.0.0.0/23")
    await client.post(
        f"/api/v1/subnets/{subnet['id']}/reserved",
        json={"start_ip": "10.0.0.10", "end_ip": "10.0.0.20"},
    )

    res = await client.put(f"/api/v1/subnets/{subnet['id']}", json={"cidr": "10.0.1.0/24"})

    assert res.status_code == 422
    assert "reserved range(s) would fall outside" in res.json()["error"]["message"]


async def test_required_subnet_update_fields_reject_explicit_null(client):
    section = await make_section(client)
    subnet = await make_subnet(client, section["id"])
    for field in ("section_id", "cidr", "name"):
        res = await client.put(f"/api/v1/subnets/{subnet['id']}", json={field: None})
        assert res.status_code == 422, (field, res.text)
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_delete_refused_with_hosts(client):
    section = await make_section(client)
    subnet = await make_subnet(client, section["id"])
    await make_host(client, subnet["id"], "10.0.0.5")
    res = await client.delete(f"/api/v1/subnets/{subnet['id']}")
    assert res.status_code == 409


# --- DNS servers (#75) ---


def _servers(count: int, third: int = 0) -> list[str]:
    return [f"10.0.{third}.{i + 1}" for i in range(count)]


async def _store_dns_servers(subnet_id: str, servers: list[str]) -> None:
    """A list written before the owner API bounded it, straight to the database."""
    async with SessionLocal() as session:
        await session.execute(update(Subnet).where(Subnet.id == subnet_id).values(dns_servers=servers))
        await session.commit()


async def test_dns_servers_over_the_bound_are_refused_naming_the_limit(client):
    section = await make_section(client)
    base = {"section_id": section["id"], "cidr": "10.0.0.0/24", "name": "x"}
    res = await client.post(
        "/api/v1/subnets", json={**base, "dns_servers": _servers(SUBNET_DNS_SERVERS_MAX + 1)}
    )
    assert res.status_code == 422, res.text
    error = res.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert f"at most {SUBNET_DNS_SERVERS_MAX} DNS servers" in error["message"]
    # nothing was created
    assert (await client.get("/api/v1/subnets")).json()["data"] == []
    sub = await make_subnet(
        client, section["id"], "10.0.0.0/24", dns_servers=_servers(SUBNET_DNS_SERVERS_MAX)
    )
    assert sub["dns_servers"] == _servers(SUBNET_DNS_SERVERS_MAX)
    res = await client.put(
        f"/api/v1/subnets/{sub['id']}",
        json={"dns_servers": _servers(SUBNET_DNS_SERVERS_MAX + 1)},
    )
    assert res.status_code == 422, res.text
    assert f"at most {SUBNET_DNS_SERVERS_MAX} DNS servers" in res.json()["error"]["message"]
    got = (await client.get(f"/api/v1/subnets/{sub['id']}")).json()["data"]
    assert got["dns_servers"] == _servers(SUBNET_DNS_SERVERS_MAX)


async def test_each_dns_server_is_still_checked_as_an_address(client):
    section = await make_section(client)
    res = await client.post(
        "/api/v1/subnets",
        json={"section_id": section["id"], "cidr": "10.0.0.0/24", "name": "x", "dns_servers": ["dns.home.arpa"]},
    )
    assert res.status_code == 422


async def test_a_list_stored_before_the_bound_survives_an_unrelated_edit(client):
    """A subnet stored with a longer list keeps it: an update that leaves dns_servers
    out, or resends it unchanged (as the UI's form does), is applied and keeps the list;
    an update that changes the list must meet the bound."""
    section = await make_section(client)
    sub = await make_subnet(client, section["id"], "10.0.0.0/24")
    legacy = _servers(SUBNET_DNS_SERVERS_MAX + 4)
    await _store_dns_servers(sub["id"], legacy)

    res = await client.put(f"/api/v1/subnets/{sub['id']}", json={"name": "renamed"})
    assert res.status_code == 200, res.text
    assert res.json()["data"]["dns_servers"] == legacy

    res = await client.put(
        f"/api/v1/subnets/{sub['id']}", json={"notes": "edited in the UI", "dns_servers": legacy}
    )
    assert res.status_code == 200, res.text
    assert res.json()["data"]["notes"] == "edited in the UI"
    assert res.json()["data"]["dns_servers"] == legacy

    # a change that is still over the bound, longer or shorter, is refused whole
    for changed in (legacy + ["10.0.1.1"], legacy[:-1], list(reversed(legacy))):
        res = await client.put(
            f"/api/v1/subnets/{sub['id']}", json={"name": "again", "dns_servers": changed}
        )
        assert res.status_code == 422, res.text
        assert f"at most {SUBNET_DNS_SERVERS_MAX} DNS servers" in res.json()["error"]["message"]
    got = (await client.get(f"/api/v1/subnets/{sub['id']}")).json()["data"]
    assert (got["name"], got["dns_servers"]) == ("renamed", legacy)

    # within the bound, or cleared, it is an ordinary edit
    res = await client.put(f"/api/v1/subnets/{sub['id']}", json={"dns_servers": legacy[:2]})
    assert res.status_code == 200 and res.json()["data"]["dns_servers"] == legacy[:2]
    res = await client.put(f"/api/v1/subnets/{sub['id']}", json={"dns_servers": None})
    assert res.status_code == 200 and res.json()["data"]["dns_servers"] is None
