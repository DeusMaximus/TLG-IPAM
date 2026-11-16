"""Scan targets and scan scope (#9; v0.2 spec §5.3, §5.4, §5.7, §5.8, §7.2).

Scope is read two ways: through `services/scans.scan_scope` itself, and through the
`scope` a scan target returns over REST, which is the same function.
"""

import sqlite3
from pathlib import Path

import pytest
from fastmcp import Client
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import SessionLocal, init_models
from app.errors import ApiError
from app.mcp_server import mcp
from app.models import ReservedRange, ScanTarget, Subnet
from app.schemas import ScanTargetCreate
from app.services import scans
from app.services import subnets as subnets_svc

from .conftest import make_host, make_section, make_subnet

SCHEMA_BEFORE_SCAN_TARGETS = Path(__file__).parent / "data" / "schema_before_scan_targets.sql"


# --- helpers ---


async def lab(client, cidr="10.0.0.0/24", **extra) -> dict:
    section = await make_section(client)
    return await make_subnet(client, section["id"], cidr, **extra)


async def make_target(client, subnet_id, **extra) -> dict:
    res = await client.post("/api/v1/scan-targets", json={"subnet_id": subnet_id, **extra})
    assert res.status_code == 201, res.text
    return res.json()["data"]


async def make_range(client, subnet_id, start, end, **extra) -> dict:
    res = await client.post(
        f"/api/v1/subnets/{subnet_id}/reserved",
        json={"start_ip": start, "end_ip": end, **extra},
    )
    assert res.status_code == 201, res.text
    return res.json()["data"]


async def set_excluded(client, subnet_id, range_id, excluded: bool):
    return await client.patch(
        f"/api/v1/subnets/{subnet_id}/reserved/{range_id}", json={"scan_excluded": excluded}
    )


async def scope_of(subnet_id) -> list[tuple[str, str]]:
    async with SessionLocal() as session:
        subnet = await session.get(Subnet, subnet_id)
        return [(r["start"], r["end"]) for r in await scans.scan_scope_ranges(session, subnet)]


async def target_scope(client, target_id) -> list[tuple[str, str]]:
    res = await client.get(f"/api/v1/scan-targets/{target_id}")
    assert res.status_code == 200, res.text
    return [(r["start"], r["end"]) for r in res.json()["data"]["scope"]]


async def make_agent(client, name="vlan10") -> dict:
    res = await client.post("/api/v1/agents", json={"name": name})
    assert res.status_code == 201, res.text
    return res.json()["data"]


async def changes_of(client, entity_type) -> list[dict]:
    res = await client.get("/api/v1/changes", params={"entity_type": entity_type, "limit": 200})
    return res.json()["data"]


# --- Scope (acceptance line 1) ---


async def test_network_and_broadcast_are_not_in_scope(client):
    subnet = await lab(client, gateway="10.0.0.1")
    target = await make_target(client, subnet["id"])
    # the gateway is a usable address and is scanned
    assert await target_scope(client, target["id"]) == [("10.0.0.1", "10.0.0.254")]
    assert await scope_of(subnet["id"]) == [("10.0.0.1", "10.0.0.254")]


@pytest.mark.parametrize(
    "cidr, expected",
    [
        ("10.0.0.0/30", [("10.0.0.1", "10.0.0.2")]),
        ("10.0.0.0/31", [("10.0.0.0", "10.0.0.1")]),
        ("10.0.0.7/32", [("10.0.0.7", "10.0.0.7")]),
    ],
)
async def test_network_and_broadcast_follow_the_prefix(client, cidr, expected):
    subnet = await lab(client, cidr)
    assert await scope_of(subnet["id"]) == expected


async def test_dhcp_pool_is_out_of_scope_by_default(client):
    subnet = await lab(client, dhcp_start="10.0.0.100", dhcp_end="10.0.0.199")
    target = await make_target(client, subnet["id"])
    assert target["scan_dhcp_pool"] is False
    expected = [("10.0.0.1", "10.0.0.99"), ("10.0.0.200", "10.0.0.254")]
    assert await target_scope(client, target["id"]) == expected
    assert await scope_of(subnet["id"]) == expected


async def test_subnet_without_a_target_has_the_default_scope(client):
    subnet = await lab(client, dhcp_start="10.0.0.100", dhcp_end="10.0.0.199")
    assert await scope_of(subnet["id"]) == [("10.0.0.1", "10.0.0.99"), ("10.0.0.200", "10.0.0.254")]


async def test_dhcp_pool_is_in_scope_when_enabled(client):
    subnet = await lab(client, dhcp_start="10.0.0.100", dhcp_end="10.0.0.199")
    target = await make_target(client, subnet["id"], scan_dhcp_pool=True)
    assert await target_scope(client, target["id"]) == [("10.0.0.1", "10.0.0.254")]

    res = await client.put(f"/api/v1/scan-targets/{target['id']}", json={"scan_dhcp_pool": False})
    assert res.status_code == 200, res.text
    assert await target_scope(client, target["id"]) == [
        ("10.0.0.1", "10.0.0.99"),
        ("10.0.0.200", "10.0.0.254"),
    ]


async def test_excluded_range_is_out_of_scope(client):
    subnet = await lab(client)
    target = await make_target(client, subnet["id"])
    block = await make_range(client, subnet["id"], "10.0.0.10", "10.0.0.19")
    # a reserved range is scanned until it is excluded
    assert await target_scope(client, target["id"]) == [("10.0.0.1", "10.0.0.254")]

    res = await set_excluded(client, subnet["id"], block["id"], True)
    assert res.status_code == 200, res.text
    assert res.json()["data"]["scan_excluded"] is True
    assert await target_scope(client, target["id"]) == [
        ("10.0.0.1", "10.0.0.9"),
        ("10.0.0.20", "10.0.0.254"),
    ]

    assert (await set_excluded(client, subnet["id"], block["id"], False)).status_code == 200
    assert await target_scope(client, target["id"]) == [("10.0.0.1", "10.0.0.254")]


async def test_range_created_excluded_over_rest_is_out_of_scope_at_once(client):
    subnet = await lab(client)
    block = await make_range(client, subnet["id"], "10.0.0.10", "10.0.0.19", scan_excluded=True)
    assert block["scan_excluded"] is True
    assert await scope_of(subnet["id"]) == [("10.0.0.1", "10.0.0.9"), ("10.0.0.20", "10.0.0.254")]


async def test_address_in_an_excluded_and_a_scanned_range_is_excluded(client):
    subnet = await lab(client)
    scanned = await make_range(client, subnet["id"], "10.0.0.10", "10.0.0.30")
    await make_range(client, subnet["id"], "10.0.0.20", "10.0.0.40", scan_excluded=True)
    assert scanned["scan_excluded"] is False
    # 10.0.0.20-30 is in both: excluded. The rest of the scanned range stays in scope.
    assert await scope_of(subnet["id"]) == [("10.0.0.1", "10.0.0.19"), ("10.0.0.41", "10.0.0.254")]


async def test_excluded_range_inside_a_scanned_pool_is_excluded(client):
    subnet = await lab(client, dhcp_start="10.0.0.100", dhcp_end="10.0.0.199")
    await make_target(client, subnet["id"], scan_dhcp_pool=True)
    await make_range(client, subnet["id"], "10.0.0.150", "10.0.0.160", scan_excluded=True)
    assert await scope_of(subnet["id"]) == [
        ("10.0.0.1", "10.0.0.149"),
        ("10.0.0.161", "10.0.0.254"),
    ]


async def test_excluded_ranges_next_to_the_pool_merge(client):
    subnet = await lab(client, dhcp_start="10.0.0.100", dhcp_end="10.0.0.199")
    await make_range(client, subnet["id"], "10.0.0.200", "10.0.0.254", scan_excluded=True)
    await make_range(client, subnet["id"], "10.0.0.0", "10.0.0.99", scan_excluded=True)
    assert await scope_of(subnet["id"]) == []


async def test_scope_is_integer_intervals(client):
    subnet = await lab(client, dhcp_start="10.0.0.100", dhcp_end="10.0.0.199")
    async with SessionLocal() as session:
        row = await session.get(Subnet, subnet["id"])
        base = 10 << 24
        assert await scans.scan_scope(session, row) == [(base + 1, base + 99), (base + 200, base + 254)]


# --- Validation ---


async def test_prefix_shorter_than_20_is_refused(client):
    section = await make_section(client)
    wide = await make_subnet(client, section["id"], "10.0.0.0/19", name="wide")
    res = await client.post("/api/v1/scan-targets", json={"subnet_id": wide["id"]})
    assert res.status_code == 422
    assert "/20" in res.json()["error"]["message"]

    ok = await make_subnet(client, section["id"], "10.1.0.0/20", name="ok")
    target = await make_target(client, ok["id"])
    assert target["scope"] == [{"start": "10.1.0.1", "end": "10.1.15.254"}]


async def test_widening_a_scanned_subnet_past_20_is_refused(client):
    subnet = await lab(client, "10.0.0.0/20")
    await make_target(client, subnet["id"])
    res = await client.put(f"/api/v1/subnets/{subnet['id']}", json={"cidr": "10.0.0.0/19"})
    assert res.status_code == 422
    assert "/20" in res.json()["error"]["message"]

    # a subnet that is not scanned may be any size
    section = await make_section(client, "other")
    other = await make_subnet(client, section["id"], "10.0.0.0/20")
    res = await client.put(f"/api/v1/subnets/{other['id']}", json={"cidr": "10.0.0.0/19"})
    assert res.status_code == 200, res.text


@pytest.mark.parametrize(
    "methods",
    [[], ["rdns"], ["fdns"], ["rdns", "fdns"], ["icmp", "ping"], ["ICMP"], ["arp", ""]],
)
async def test_invalid_methods_are_422(client, methods):
    subnet = await lab(client)
    res = await client.post(
        "/api/v1/scan-targets", json={"subnet_id": subnet["id"], "methods": methods}
    )
    assert res.status_code == 422, res.text
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_liveness_message_names_icmp_and_arp(client):
    subnet = await lab(client)
    res = await client.post(
        "/api/v1/scan-targets", json={"subnet_id": subnet["id"], "methods": ["rdns"]}
    )
    message = res.json()["error"]["message"]
    assert "icmp" in message and "arp" in message


@pytest.mark.parametrize(
    "methods, stored",
    [
        (["arp"], ["arp"]),
        (["icmp"], ["icmp"]),
        (["fdns", "rdns", "arp", "icmp"], ["icmp", "arp", "rdns", "fdns"]),
        (["rdns", "icmp", "icmp"], ["icmp", "rdns"]),
    ],
)
async def test_methods_are_stored_in_canonical_order(client, methods, stored):
    subnet = await lab(client)
    target = await make_target(client, subnet["id"], methods=methods)
    assert target["methods"] == stored


async def test_update_checks_methods_too(client):
    subnet = await lab(client)
    target = await make_target(client, subnet["id"])
    url = f"/api/v1/scan-targets/{target['id']}"
    assert (await client.put(url, json={"methods": ["rdns"]})).status_code == 422
    assert (await client.put(url, json={"methods": ["telnet", "icmp"]})).status_code == 422
    res = await client.put(url, json={"methods": ["rdns", "arp"]})
    assert res.json()["data"]["methods"] == ["arp", "rdns"]


async def test_defaults(client):
    subnet = await lab(client)
    target = await make_target(client, subnet["id"])
    assert target["methods"] == ["icmp", "arp", "rdns"]
    assert target["enabled"] is True
    assert target["agent_id"] is None
    assert target["scan_dhcp_pool"] is False
    assert target["interval_minutes"] is None
    for field in (
        "scan_requested_at",
        "current_job_id",
        "job_issued_at",
        "last_scan_at",
        "last_scan_duration_ms",
        "last_scan_complete",
    ):
        assert target[field] is None


@pytest.mark.parametrize("minutes, status", [(4, 422), (5, 201), (10080, 201), (10081, 422)])
async def test_interval_bounds(client, minutes, status):
    subnet = await lab(client)
    res = await client.post(
        "/api/v1/scan-targets", json={"subnet_id": subnet["id"], "interval_minutes": minutes}
    )
    assert res.status_code == status, res.text


@pytest.mark.parametrize("field", ["enabled", "methods", "scan_dhcp_pool"])
async def test_null_for_required_fields_is_422(client, field):
    subnet = await lab(client)
    target = await make_target(client, subnet["id"])
    res = await client.put(f"/api/v1/scan-targets/{target['id']}", json={field: None})
    assert res.status_code == 422


async def test_one_target_per_subnet(client):
    subnet = await lab(client)
    await make_target(client, subnet["id"])
    res = await client.post("/api/v1/scan-targets", json={"subnet_id": subnet["id"]})
    assert res.status_code == 409


async def test_database_refuses_a_second_target_for_a_subnet(client, monkeypatch):
    # the race backstop: two requests both past the service's own check
    subnet = await lab(client)
    await make_target(client, subnet["id"])

    async def no_target(session, subnet_id):
        return None

    monkeypatch.setattr(scans, "target_for_subnet", no_target)
    async with SessionLocal() as session:
        with pytest.raises(ApiError) as caught:
            await scans.create_target(session, ScanTargetCreate(subnet_id=subnet["id"]), "api")
    assert caught.value.status == 409


async def test_unknown_subnet_or_agent_is_404(client):
    res = await client.post("/api/v1/scan-targets", json={"subnet_id": "nope"})
    assert res.status_code == 404
    subnet = await lab(client)
    res = await client.post(
        "/api/v1/scan-targets", json={"subnet_id": subnet["id"], "agent_id": "nope"}
    )
    assert res.status_code == 404
    target = await make_target(client, subnet["id"])
    res = await client.put(f"/api/v1/scan-targets/{target['id']}", json={"agent_id": "nope"})
    assert res.status_code == 404
    assert (await client.get("/api/v1/scan-targets/nope")).status_code == 404
    assert (await client.delete("/api/v1/scan-targets/nope")).status_code == 404


async def test_scan_target_routes_need_the_owner_key(anon_client):
    assert (await anon_client.get("/api/v1/scan-targets")).status_code == 401
    res = await anon_client.get("/api/v1/scan-targets", headers={"X-API-Key": "wrong"})
    assert res.status_code == 403
    res = await anon_client.patch(
        "/api/v1/subnets/x/reserved/y", json={"scan_excluded": True}
    )
    assert res.status_code == 401


# --- CRUD and audit ---


async def test_crud(client):
    subnet = await lab(client)
    agent = await make_agent(client)
    target = await make_target(
        client, subnet["id"], agent_id=agent["id"], methods=["icmp"], interval_minutes=60
    )
    assert target["agent_id"] == agent["id"]

    listed = (await client.get("/api/v1/scan-targets")).json()
    assert listed["total"] == 1 and listed["data"][0]["id"] == target["id"]

    res = await client.put(
        f"/api/v1/scan-targets/{target['id']}",
        json={"agent_id": None, "enabled": False, "interval_minutes": None},
    )
    data = res.json()["data"]
    assert (data["agent_id"], data["enabled"], data["interval_minutes"]) == (None, False, None)

    assert (await client.delete(f"/api/v1/scan-targets/{target['id']}")).status_code == 204
    assert (await client.get("/api/v1/scan-targets")).json()["total"] == 0


async def test_scan_target_writes_are_logged(client):
    subnet = await lab(client, name="iot")
    agent = await make_agent(client, "iot-scanner")
    target = await make_target(client, subnet["id"], agent_id=agent["id"])
    url = f"/api/v1/scan-targets/{target['id']}"
    await client.put(url, json={"scan_dhcp_pool": True})
    await client.delete(url, headers={"X-TLG-Client": "ui"})

    rows = await changes_of(client, "scan_target")
    assert [r["action"] for r in rows] == ["delete", "update", "create"]
    delete, update, create = rows
    assert all(r["entity_id"] == target["id"] for r in rows)
    assert all(r["actor"] is None for r in rows)
    assert [r["source"] for r in rows] == ["ui", "api", "api"]
    assert create["summary"] == (
        "scan target of subnet iot (10.0.0.0/24) created, assigned to agent iot-scanner"
    )
    assert update["summary"] == "scan target of subnet iot (10.0.0.0/24) updated (scan_dhcp_pool)"
    assert update["before"]["scan_dhcp_pool"] is False
    assert update["after"]["scan_dhcp_pool"] is True
    assert delete["before"]["id"] == target["id"]


async def test_update_that_changes_nothing_is_not_logged(client):
    subnet = await lab(client)
    target = await make_target(client, subnet["id"])
    res = await client.put(
        f"/api/v1/scan-targets/{target['id']}",
        json={"methods": ["rdns", "icmp", "arp"], "enabled": True, "scan_dhcp_pool": False},
    )
    assert res.status_code == 200
    assert [r["action"] for r in await changes_of(client, "scan_target")] == ["create"]


async def test_reserved_range_scan_exclusion_is_logged(client):
    subnet = await lab(client, name="lab")
    block = await make_range(client, subnet["id"], "10.0.0.10", "10.0.0.19")
    await set_excluded(client, subnet["id"], block["id"], True)
    await set_excluded(client, subnet["id"], block["id"], True)  # no change, no entry
    await set_excluded(client, subnet["id"], block["id"], False)

    rows = [r for r in await changes_of(client, "subnet") if "scanning" in r["summary"]]
    assert [r["summary"] for r in rows] == [
        "reserved range 10.0.0.10-10.0.0.19 in lab returned to scanning",
        "reserved range 10.0.0.10-10.0.0.19 in lab excluded from scanning",
    ]
    assert rows[1]["before"]["scan_excluded"] is False
    assert rows[1]["after"]["scan_excluded"] is True
    assert all(r["entity_id"] == subnet["id"] and r["source"] == "api" for r in rows)


async def test_reserved_range_created_excluded_is_logged_as_such(client):
    subnet = await lab(client, name="lab")
    await make_range(client, subnet["id"], "10.0.0.10", "10.0.0.19", scan_excluded=True)
    create = (await changes_of(client, "subnet"))[0]
    assert create["summary"].endswith(", excluded from scanning")
    assert create["after"]["scan_excluded"] is True


async def test_patch_reserved_range_errors(client):
    subnet = await lab(client)
    other = await make_subnet(client, subnet["section_id"], "10.0.1.0/24", name="other")
    block = await make_range(client, subnet["id"], "10.0.0.10", "10.0.0.19")
    # a range is addressed through its own subnet only
    assert (await set_excluded(client, other["id"], block["id"], True)).status_code == 404
    assert (await set_excluded(client, subnet["id"], "nope", True)).status_code == 404
    url = f"/api/v1/subnets/{subnet['id']}/reserved/{block['id']}"
    assert (await client.patch(url, json={})).status_code == 422
    assert (await client.patch(url, json={"scan_excluded": None})).status_code == 422


# --- Deletion ---


async def test_deleting_a_subnet_deletes_its_scan_target(client):
    subnet = await lab(client)
    target = await make_target(client, subnet["id"])
    assert (await client.delete(f"/api/v1/subnets/{subnet['id']}")).status_code == 204
    assert (await client.get(f"/api/v1/scan-targets/{target['id']}")).status_code == 404
    delete = (await changes_of(client, "scan_target"))[0]
    assert delete["action"] == "delete"
    assert delete["summary"] == "scan target of subnet lab (10.0.0.0/24) deleted (subnet deleted)"


async def test_force_deleting_a_subnet_with_hosts_ranges_and_a_target(client):
    subnet = await lab(client)
    await make_host(client, subnet["id"], "10.0.0.5")
    await make_range(client, subnet["id"], "10.0.0.10", "10.0.0.19", scan_excluded=True)
    await make_target(client, subnet["id"])
    res = await client.delete(f"/api/v1/subnets/{subnet['id']}", params={"force": "true"})
    assert res.status_code == 204, res.text
    async with SessionLocal() as session:
        assert (await session.scalars(select(ScanTarget))).all() == []
        assert (await session.scalars(select(ReservedRange))).all() == []


async def test_deleting_an_agent_unassigns_its_targets(client):
    agent = await make_agent(client, "scanner")
    keep = await make_agent(client, "other")
    section = await make_section(client)
    a = await make_subnet(client, section["id"], "10.0.0.0/24", name="a")
    b = await make_subnet(client, section["id"], "10.0.1.0/24", name="b")
    c = await make_subnet(client, section["id"], "10.0.2.0/24", name="c")
    ta = await make_target(
        client, a["id"], agent_id=agent["id"], methods=["arp"], scan_dhcp_pool=True
    )
    tb = await make_target(client, b["id"], agent_id=agent["id"], interval_minutes=30)
    tc = await make_target(client, c["id"], agent_id=keep["id"])

    assert (await client.delete(f"/api/v1/agents/{agent['id']}")).status_code == 204

    after_a = (await client.get(f"/api/v1/scan-targets/{ta['id']}")).json()["data"]
    after_b = (await client.get(f"/api/v1/scan-targets/{tb['id']}")).json()["data"]
    after_c = (await client.get(f"/api/v1/scan-targets/{tc['id']}")).json()["data"]
    assert after_a["agent_id"] is None and after_b["agent_id"] is None
    assert after_c["agent_id"] == keep["id"]
    # the targets keep their settings
    assert (after_a["methods"], after_a["scan_dhcp_pool"]) == (["arp"], True)
    assert after_b["interval_minutes"] == 30

    unassigned = [r for r in await changes_of(client, "scan_target") if r["action"] == "update"]
    # one transaction: the two entries share a timestamp, so their order is not asserted
    assert sorted(r["summary"] for r in unassigned) == [
        "scan target of subnet a (10.0.0.0/24) unassigned (agent scanner deleted)",
        "scan target of subnet b (10.0.1.0/24) unassigned (agent scanner deleted)",
    ]
    assert all(r["before"]["agent_id"] == agent["id"] for r in unassigned)
    assert all(r["after"]["agent_id"] is None for r in unassigned)


# --- MCP (acceptance line 2, spec §5.3, §5.8, §7.2 invariant 2) ---


async def test_mcp_cannot_delete_an_excluded_range(client):
    subnet = await lab(client)
    block = await make_range(client, subnet["id"], "10.0.0.10", "10.0.0.19", scan_excluded=True)
    before = await changes_of(client, "subnet")

    async with Client(mcp) as mcp_client:
        result = await mcp_client.call_tool(
            "delete_reserved_range",
            {"subnet_id": subnet["id"], "range_id": block["id"]},
            raise_on_error=False,
        )
    assert result.is_error
    message = result.content[0].text
    assert "excluded from scanning" in message
    assert "web UI" in message

    detail = (await client.get(f"/api/v1/subnets/{subnet['id']}")).json()["data"]
    assert [r["id"] for r in detail["reserved_ranges"]] == [block["id"]]
    assert await changes_of(client, "subnet") == before

    # the owner can still remove it over REST, which the UI uses
    url = f"/api/v1/subnets/{subnet['id']}/reserved/{block['id']}"
    assert (await client.delete(url)).status_code == 204


async def test_mcp_can_delete_a_scanned_range(client):
    subnet = await lab(client)
    block = await make_range(client, subnet["id"], "10.0.0.10", "10.0.0.19")
    async with Client(mcp) as mcp_client:
        result = await mcp_client.call_tool(
            "delete_reserved_range", {"subnet_id": subnet["id"], "range_id": block["id"]}
        )
    assert result.data == {"deleted": True}


async def test_mcp_add_reserved_range_always_creates_a_scanned_range(client):
    subnet = await lab(client)
    async with Client(mcp) as mcp_client:
        tool = next(t for t in await mcp_client.list_tools() if t.name == "add_reserved_range")
        assert set(tool.input_schema["properties"]) == {
            "subnet_id",
            "start_ip",
            "end_ip",
            "description",
        }
        created = (
            await mcp_client.call_tool(
                "add_reserved_range",
                {"subnet_id": subnet["id"], "start_ip": "10.0.0.10", "end_ip": "10.0.0.19"},
            )
        ).data["reserved_range"]
        # an argument the tool does not declare is refused, not passed through
        refused = await mcp_client.call_tool(
            "add_reserved_range",
            {
                "subnet_id": subnet["id"],
                "start_ip": "10.0.0.30",
                "end_ip": "10.0.0.39",
                "scan_excluded": True,
            },
            raise_on_error=False,
        )
    assert created["scan_excluded"] is False
    assert refused.is_error
    ranges = (await client.get(f"/api/v1/subnets/{subnet['id']}")).json()["data"][
        "reserved_ranges"
    ]
    assert [(r["start_ip"], r["scan_excluded"]) for r in ranges] == [("10.0.0.10", False)]
    assert await scope_of(subnet["id"]) == [("10.0.0.1", "10.0.0.254")]


async def test_mcp_get_subnet_shows_scan_settings(client):
    subnet = await lab(client, dhcp_start="10.0.0.100", dhcp_end="10.0.0.199")
    await make_range(client, subnet["id"], "10.0.0.10", "10.0.0.19", scan_excluded=True)
    await make_range(client, subnet["id"], "10.0.0.20", "10.0.0.29")

    async def get_subnet():
        async with Client(mcp) as mcp_client:
            result = await mcp_client.call_tool("get_subnet", {"subnet_id": subnet["id"]})
            return result.data["subnet"]

    detail = await get_subnet()
    assert detail["scan_dhcp_pool"] is None  # not set up for scanning
    assert [r["scan_excluded"] for r in detail["reserved_ranges"]] == [True, False]

    target = await make_target(client, subnet["id"])
    assert (await get_subnet())["scan_dhcp_pool"] is False
    await client.put(f"/api/v1/scan-targets/{target['id']}", json={"scan_dhcp_pool": True})
    assert (await get_subnet())["scan_dhcp_pool"] is True

    async with Client(mcp) as mcp_client:
        listed = (await mcp_client.call_tool("list_subnets", {})).data["subnets"]
    assert listed[0]["scan_dhcp_pool"] is True


async def test_no_mcp_tool_reaches_scan_configuration():
    async with Client(mcp) as mcp_client:
        tools = await mcp_client.list_tools()
    for tool in tools:
        assert "scan" not in tool.name
        for arg in tool.input_schema.get("properties", {}):
            assert "scan" not in arg and "agent" not in arg and "method" not in arg, (
                tool.name,
                arg,
            )


async def test_mcp_audit_view_shows_scan_target_entries(client):
    subnet = await lab(client)
    await make_target(client, subnet["id"])
    async with Client(mcp) as mcp_client:
        rows = (
            await mcp_client.call_tool("list_changes", {"entity_type": "scan_target"})
        ).data["changes"]
    assert [r["action"] for r in rows] == ["create"]


# --- Start-up on an existing database (acceptance line 3) ---


async def test_database_from_main_gains_the_column_and_ranges_read_as_scanned(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(SCHEMA_BEFORE_SCAN_TARGETS.read_text())
    columns = {r[1] for r in old.execute("PRAGMA table_info(reserved_ranges)")}
    assert "scan_excluded" not in columns
    tables = {r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "scan_targets" not in tables
    # a subnet with a DHCP pool and a reserved range, written by main's schema
    old.execute("INSERT INTO sections (id, name) VALUES ('sec', 'Homelab')")
    old.execute(
        "INSERT INTO subnets (id, section_id, cidr, name, dhcp_start, dhcp_end) "
        "VALUES ('net', 'sec', '10.0.0.0/24', 'lab', '10.0.0.100', '10.0.0.199')"
    )
    old.execute(
        "INSERT INTO reserved_ranges (id, subnet_id, start_ip, end_ip, start_int, end_int) "
        f"VALUES ('rr', 'net', '10.0.0.10', '10.0.0.19', {(10 << 24) + 10}, {(10 << 24) + 19})"
    )
    old.commit()
    old.close()

    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    try:
        await init_models(engine)
        await init_models(engine)  # a second start-up adds nothing twice
        async with engine.connect() as conn:
            columns = {
                r[1]: r
                for r in (await conn.exec_driver_sql("PRAGMA table_info(reserved_ranges)")).all()
            }
            stored = (
                await conn.exec_driver_sql("SELECT scan_excluded FROM reserved_ranges")
            ).scalar_one()
        assert "scan_excluded" in columns
        assert columns["scan_excluded"][3] == 0  # nullable
        assert stored is None

        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            subnet = await session.get(Subnet, "net")
            out = await subnets_svc.subnet_out(session, subnet)
            assert out["reserved_ranges"][0]["scan_excluded"] is False
            assert out["scan_dhcp_pool"] is None
            # the old range is scanned; the pool is not (the default)
            assert await scans.scan_scope_ranges(session, subnet) == [
                {"start": "10.0.0.1", "end": "10.0.0.99"},
                {"start": "10.0.0.200", "end": "10.0.0.254"},
            ]
            target = await scans.create_target(session, ScanTargetCreate(subnet_id="net"), "api")
            assert target.scan_dhcp_pool is False
    finally:
        await engine.dispose()
