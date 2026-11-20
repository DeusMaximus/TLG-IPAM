import pytest
from sqlalchemy import update

from app.database import SessionLocal
from app.models import Subnet
from app.schemas import SUBNET_DNS_SERVERS_MAX

from .conftest import PADDED_CIDR, make_host, make_section, make_subnet, store_cidr_as


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


# Spellings of 10.0.0.0/24 that `ipaddress.ip_network` takes (#97): a dotted netmask, a
# host mask, a prefix padded with zeroes, and one padded as the review of PR #95 did.
OTHER_SPELLINGS = [
    "10.0.0.0/255.255.255.0",
    "10.0.0.0/0.0.0.255",
    "10.0.0.0/0024",
    "10.0.0.0/" + "0" * 4298 + "24",
]


async def _newest_change(client) -> dict:
    return (await client.get("/api/v1/changes")).json()["data"][0]


@pytest.mark.parametrize("typed", OTHER_SPELLINGS, ids=["netmask", "hostmask", "padded", "long"])
async def test_a_cidr_is_stored_in_its_canonical_spelling(client, typed):
    """The owner may type any spelling `ip_network` takes; the subnet is stored, shown
    and logged as the one canonical spelling (#97)."""
    section = await make_section(client)
    sub = await make_subnet(client, section["id"], typed)
    assert sub["cidr"] == "10.0.0.0/24"
    got = (await client.get(f"/api/v1/subnets/{sub['id']}")).json()["data"]
    assert got["cidr"] == "10.0.0.0/24"
    change = await _newest_change(client)
    assert change["summary"] == "subnet lab (10.0.0.0/24) created"
    assert change["after"]["cidr"] == "10.0.0.0/24"


async def test_an_update_stores_the_canonical_spelling(client):
    section = await make_section(client)
    sub = await make_subnet(client, section["id"], "10.0.0.0/24")
    res = await client.put(f"/api/v1/subnets/{sub['id']}", json={"cidr": "10.0.0.0/255.255.254.0"})
    assert res.status_code == 200, res.text
    assert res.json()["data"]["cidr"] == "10.0.0.0/23"
    change = await _newest_change(client)
    assert change["summary"] == "subnet lab (10.0.0.0/23) updated"
    assert (change["before"]["cidr"], change["after"]["cidr"]) == ("10.0.0.0/24", "10.0.0.0/23")
    # a refusal names the network as it is spelled canonically, not as typed
    await make_host(client, sub["id"], "10.0.1.10")
    res = await client.put(f"/api/v1/subnets/{sub['id']}", json={"cidr": "10.0.0.0/0024"})
    assert res.status_code == 422
    assert res.json()["error"]["message"].startswith("Cannot change CIDR to 10.0.0.0/24:")


@pytest.mark.parametrize("typed", OTHER_SPELLINGS, ids=["netmask", "hostmask", "padded", "long"])
async def test_the_same_network_in_another_spelling_is_a_duplicate(client, typed):
    """Within a section a second spelling of a subnet's network is refused as the
    duplicate it is, on create and on update; across sections the same network is
    allowed (brief §3.2), and is stored in the one spelling there too."""
    home = await make_section(client, "Home")
    lab = await make_subnet(client, home["id"], "10.0.0.0/24")
    res = await client.post(
        "/api/v1/subnets", json={"section_id": home["id"], "cidr": typed, "name": "again"}
    )
    assert res.status_code == 409, res.text
    message = res.json()["error"]["message"]
    assert message == "Subnet 10.0.0.0/24 overlaps lab (10.0.0.0/24) in the same section"
    other = await make_subnet(client, home["id"], "10.0.1.0/24", name="other")
    res = await client.put(f"/api/v1/subnets/{other['id']}", json={"cidr": typed})
    assert res.status_code == 409, res.text

    away = await make_section(client, "Away")
    assert (await make_subnet(client, away["id"], typed, name="vpn view"))["cidr"] == lab["cidr"]


async def test_a_subnet_stored_with_another_spelling_is_a_duplicate_of_its_network(client):
    """A subnet stored before #97 in another spelling is compared as its network."""
    section = await make_section(client)
    legacy = await make_subnet(client, section["id"], "10.0.0.0/24", name="legacy")
    await store_cidr_as(legacy["id"], "10.0.0.0/255.255.255.0")
    res = await client.post(
        "/api/v1/subnets", json={"section_id": section["id"], "cidr": "10.0.0.0/24", "name": "new"}
    )
    assert res.status_code == 409
    assert res.json()["error"]["message"] == (
        "Subnet 10.0.0.0/24 overlaps legacy (10.0.0.0/24) in the same section"
    )


async def test_a_subnet_stored_with_another_spelling_is_read_and_edited(client):
    """A subnet stored before #97 in a spelling the service no longer stores is read as
    stored, and an edit is not refused: one that leaves `cidr` out keeps the spelling;
    one that resends it, as the UI's form resends every field, stores the canonical
    spelling, which is the same network and so meets every CIDR-change check (hosts,
    reserved ranges, a scan target's prefix) without moving anything."""
    section = await make_section(client)
    sub = await make_subnet(client, section["id"], "192.168.240.0/20", gateway="192.168.240.1")
    await make_host(client, sub["id"], "192.168.255.254")
    res = await client.post(
        f"/api/v1/subnets/{sub['id']}/reserved",
        json={"start_ip": "192.168.240.10", "end_ip": "192.168.240.20", "scan_excluded": True},
    )
    assert res.status_code == 201
    agent = (await client.post("/api/v1/agents", json={"name": "scanner"})).json()["data"]
    res = await client.post(
        "/api/v1/scan-targets",
        json={"subnet_id": sub["id"], "agent_id": agent["id"], "interval_minutes": 60},
    )
    assert res.status_code == 201, res.text
    await store_cidr_as(sub["id"], PADDED_CIDR)

    got = (await client.get(f"/api/v1/subnets/{sub['id']}")).json()["data"]
    assert got["cidr"] == PADDED_CIDR
    assert (got["usable"], got["used"]) == (4094, 1)

    res = await client.put(f"/api/v1/subnets/{sub['id']}", json={"name": "renamed"})
    assert res.status_code == 200, res.text
    assert res.json()["data"]["cidr"] == PADDED_CIDR
    # the summary names the network canonically; the snapshots hold what is stored
    change = await _newest_change(client)
    assert change["summary"] == "subnet renamed (192.168.240.0/20) updated"
    assert change["before"]["cidr"] == change["after"]["cidr"] == PADDED_CIDR

    form = {k: got[k] for k in ("cidr", "vlan_id", "domain", "gateway", "dns_servers",
                                "dhcp_start", "dhcp_end", "notes")}
    res = await client.put(
        f"/api/v1/subnets/{sub['id']}", json={**form, "name": "renamed", "notes": "edited"}
    )
    assert res.status_code == 200, res.text
    assert res.json()["data"]["cidr"] == "192.168.240.0/20"
    assert res.json()["data"]["reserved_ranges"][0]["scan_excluded"] is True
    change = await _newest_change(client)
    assert change["summary"] == "subnet renamed (192.168.240.0/20) updated"
    assert (change["before"]["cidr"], change["after"]["cidr"]) == (PADDED_CIDR, "192.168.240.0/20")


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
