"""`last_seen` on host responses, derived on read from the address's observation
(v0.2 spec §5.4; issue #12). Scan ingest never writes `hosts` (spec §7.2, invariant 3),
so these tests write observations directly and read hosts through REST and MCP."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from fastmcp import Client
from sqlalchemy import event, select

from app.database import SessionLocal, engine
from app.ipnet import parse_ip
from app.mcp_server import mcp
from app.models import Host, Observation
from app.services import hosts as hosts_svc
from app.services.scans import utc

from .conftest import make_host, make_section, make_subnet

SCAN = datetime(2026, 11, 10, 11, 0, tzinfo=timezone.utc)
EARLIER = SCAN - timedelta(days=3)
LATER = SCAN + timedelta(days=2)


async def _subnet(client, cidr="10.0.0.0/24", name="lab"):
    section = await make_section(client, name=f"section {name}")
    return await make_subnet(client, section["id"], cidr=cidr, name=name)


async def observe(subnet_id, ip, seen=SCAN, alive=True):
    addr = int(parse_ip(ip))
    async with SessionLocal() as session:
        session.add(
            Observation(
                subnet_id=subnet_id,
                ip=ip,
                ip_int=addr,
                alive=alive,
                first_seen_at=seen - timedelta(days=30),
                last_seen_at=seen,
            )
        )
        await session.commit()


def at(value: str | None) -> datetime | None:
    """A response datetime as an instant: read-back values carry no offset (#65)."""
    return None if value is None else utc(datetime.fromisoformat(value))


async def get(client, host_id) -> dict:
    res = await client.get(f"/api/v1/hosts/{host_id}")
    assert res.status_code == 200, res.text
    return res.json()["data"]


# --- Acceptance: a host with only an observation ---


async def test_host_with_only_an_observation_shows_the_scan_time(client):
    subnet = await _subnet(client)
    host = await make_host(client, subnet["id"], "10.0.0.10", hostname="nas")
    assert (host["last_seen"], host["last_seen_source"]) == (None, None)
    await observe(subnet["id"], "10.0.0.10")

    got = await get(client, host["id"])
    assert at(got["last_seen"]) == SCAN and got["last_seen_source"] == "scan"
    listed = (await client.get("/api/v1/hosts")).json()["data"]
    assert [(at(h["last_seen"]), h["last_seen_source"]) for h in listed] == [(SCAN, "scan")]
    # derived on read: the stored column is still empty
    async with SessionLocal() as session:
        assert (await session.get(Host, host["id"])).last_seen is None


async def test_the_time_comes_from_last_seen_at_not_from_alive(client):
    """A complete sweep that missed the address sets `alive=false` but keeps when it last
    answered; that time is still when the host was last seen."""
    subnet = await _subnet(client)
    host = await make_host(client, subnet["id"], "10.0.0.10")
    await observe(subnet["id"], "10.0.0.10", alive=False)
    got = await get(client, host["id"])
    assert at(got["last_seen"]) == SCAN and got["last_seen_source"] == "scan"


# --- Acceptance: clearing the manual value leaves the scan value ---


async def test_clearing_a_manual_value_over_rest_leaves_the_scan_value(client):
    subnet = await _subnet(client)
    host = await make_host(client, subnet["id"], "10.0.0.10", last_seen=LATER.isoformat())
    await observe(subnet["id"], "10.0.0.10")
    got = await get(client, host["id"])
    assert at(got["last_seen"]) == LATER and got["last_seen_source"] == "manual"

    res = await client.put(f"/api/v1/hosts/{host['id']}", json={"last_seen": None})
    assert res.status_code == 200, res.text
    updated = res.json()["data"]
    assert at(updated["last_seen"]) == SCAN and updated["last_seen_source"] == "scan"
    got = await get(client, host["id"])
    assert at(got["last_seen"]) == SCAN and got["last_seen_source"] == "scan"
    async with SessionLocal() as session:
        assert (await session.get(Host, host["id"])).last_seen is None


async def test_clearing_a_manual_value_over_mcp_leaves_the_scan_value(client):
    subnet = await _subnet(client)
    host = await make_host(client, subnet["id"], "10.0.0.10", last_seen=LATER.isoformat())
    await observe(subnet["id"], "10.0.0.10")
    async with Client(mcp) as mcp_client:
        before = (await mcp_client.call_tool("get_host", {"host_id": host["id"]})).data["host"]
        assert before["last_seen_source"] == "manual"
        after = (
            await mcp_client.call_tool(
                "update_host", {"host_id": host["id"], "clear_fields": ["last_seen"]}
            )
        ).data["host"]
        assert at(after["last_seen"]) == SCAN and after["last_seen_source"] == "scan"
        listed = (await mcp_client.call_tool("list_hosts", {})).data["hosts"]
        assert [h["last_seen_source"] for h in listed] == ["scan"]


async def test_clearing_without_an_observation_leaves_nothing(client):
    subnet = await _subnet(client)
    host = await make_host(client, subnet["id"], "10.0.0.10", last_seen=SCAN.isoformat())
    assert host["last_seen_source"] == "manual"
    res = await client.put(f"/api/v1/hosts/{host['id']}", json={"last_seen": None})
    assert (res.json()["data"]["last_seen"], res.json()["data"]["last_seen_source"]) == (
        None,
        None,
    )


# --- The later of the two wins ---


async def test_the_later_value_wins_and_names_its_source(client):
    subnet = await _subnet(client)
    manual_later = await make_host(
        client, subnet["id"], "10.0.0.10", last_seen=LATER.isoformat()
    )
    scan_later = await make_host(
        client, subnet["id"], "10.0.0.11", last_seen=EARLIER.isoformat()
    )
    tie = await make_host(client, subnet["id"], "10.0.0.12", last_seen=SCAN.isoformat())
    manual_only = await make_host(
        client, subnet["id"], "10.0.0.13", last_seen=EARLIER.isoformat()
    )
    never = await make_host(client, subnet["id"], "10.0.0.14")
    for ip in ("10.0.0.10", "10.0.0.11", "10.0.0.12"):
        await observe(subnet["id"], ip)

    listed = {h["id"]: h for h in (await client.get("/api/v1/hosts")).json()["data"]}
    seen = {i: (at(h["last_seen"]), h["last_seen_source"]) for i, h in listed.items()}
    assert seen[manual_later["id"]] == (LATER, "manual")
    assert seen[scan_later["id"]] == (SCAN, "scan")
    assert seen[tie["id"]] == (SCAN, "scan")  # a tie is reported as the scan
    assert seen[manual_only["id"]] == (EARLIER, "manual")
    assert seen[never["id"]] == (None, None)
    # the single-host read agrees with the list
    for host_id, expected in seen.items():
        got = await get(client, host_id)
        assert (at(got["last_seen"]), got["last_seen_source"]) == expected


async def test_a_manual_value_with_an_offset_is_compared_as_an_instant(client):
    """12:30+02:00 is 10:30 UTC, half an hour before the scan. SQLite drops the zone and
    keeps the wall clock, so the value is converted to UTC before it is stored; otherwise
    it would read back as 12:30 UTC and wrongly beat the scan."""
    subnet = await _subnet(client)
    await observe(subnet["id"], "10.0.0.10")
    offset = datetime(2026, 11, 10, 12, 30, tzinfo=timezone(timedelta(hours=2)))
    host = await make_host(client, subnet["id"], "10.0.0.10", last_seen=offset.isoformat())
    assert at(host["last_seen"]) == SCAN and host["last_seen_source"] == "scan"
    got = await get(client, host["id"])
    assert at(got["last_seen"]) == SCAN and got["last_seen_source"] == "scan"
    async with SessionLocal() as session:
        stored = (await session.get(Host, host["id"])).last_seen
    assert utc(stored) == offset  # the same instant, stored as UTC

    later = datetime(2026, 11, 10, 14, 30, tzinfo=timezone(timedelta(hours=2)))  # 12:30Z
    res = await client.put(f"/api/v1/hosts/{host['id']}", json={"last_seen": later.isoformat()})
    assert at(res.json()["data"]["last_seen"]) == later
    assert res.json()["data"]["last_seen_source"] == "manual"
    got = await get(client, host["id"])
    assert at(got["last_seen"]) == later and got["last_seen_source"] == "manual"


# --- Repeating the stored value is not a change (#85) ---

# 12:34:56.123456 UTC in each form a client may send it
STORED = "2026-01-02T12:34:56.123456"
SAME_INSTANT = (
    STORED,
    f"{STORED}+00:00",
    f"{STORED}Z",
    "2026-01-02T14:34:56.123456+02:00",
    "2026-01-02T04:34:56.123456-08:00",
)


async def _stored_last_seen(host_id):
    async with SessionLocal() as session:
        return (await session.get(Host, host_id)).last_seen


async def test_repeating_the_stored_last_seen_leaves_updated_at_alone(client):
    """SQLite reads the stored value back without its zone and the request's value is
    aware, so assigning the same instant used to look like a change and move
    `updated_at`. Each PUT is its own request, so the host is read back from SQLite."""
    subnet = await _subnet(client)
    naive = await make_host(client, subnet["id"], "10.0.0.10", last_seen=STORED)
    offset = await make_host(client, subnet["id"], "10.0.0.11", last_seen=SAME_INSTANT[3])
    for host in (naive, offset):
        first = await get(client, host["id"])
        for sent in SAME_INSTANT:
            res = await client.put(f"/api/v1/hosts/{host['id']}", json={"last_seen": sent})
            assert res.status_code == 200, res.text
            assert res.json()["data"]["updated_at"] == first["updated_at"], sent
            got = await get(client, host["id"])
            assert (got["last_seen"], got["updated_at"]) == (
                first["last_seen"],
                first["updated_at"],
            ), sent
        assert await _stored_last_seen(host["id"]) == datetime.fromisoformat(STORED)

    # every PUT still goes to the change log, as before: an update whose before and
    # after are the same
    changes = (await client.get("/api/v1/changes?entity_type=host")).json()["data"]
    updates = [c for c in changes if c["action"] == "update"]
    assert len(updates) == 2 * len(SAME_INSTANT)
    assert all(c["before"] == c["after"] for c in updates)


async def test_resending_every_field_unchanged_leaves_updated_at_alone(client):
    """As the UI's edit form does: each field as stored, `last_seen` null included."""
    subnet = await _subnet(client)
    host = await make_host(client, subnet["id"], "10.0.0.10", hostname="nas", owner="lab")
    first = await get(client, host["id"])
    fields = ("ip", "hostname", "domain", "mac", "device_type", "status", "owner", "notes")
    res = await client.put(
        f"/api/v1/hosts/{host['id']}", json={**{f: first[f] for f in fields}, "last_seen": None}
    )
    assert res.status_code == 200, res.text
    got = await get(client, host["id"])
    assert (got["last_seen"], got["updated_at"]) == (None, first["updated_at"])


async def test_a_put_that_changes_last_seen_moves_updated_at(client):
    subnet = await _subnet(client)
    host = await make_host(client, subnet["id"], "10.0.0.10", last_seen=STORED)
    first = await get(client, host["id"])
    # a microsecond later, sent with an offset, and then without
    for sent, expected in (
        ("2026-01-02T14:34:56.123457+02:00", "2026-01-02T12:34:56.123457"),
        ("2026-01-02T12:34:56.123458", "2026-01-02T12:34:56.123458"),
    ):
        res = await client.put(f"/api/v1/hosts/{host['id']}", json={"last_seen": sent})
        assert res.status_code == 200, res.text
        got = await get(client, host["id"])
        assert at(got["last_seen"]) == utc(datetime.fromisoformat(expected))
        assert at(got["updated_at"]) > at(first["updated_at"])
        assert await _stored_last_seen(host["id"]) == datetime.fromisoformat(expected)
        first = got


# --- Which observation belongs to a host ---


async def test_an_observation_matches_only_the_hosts_own_subnet_and_address(client):
    lab = await _subnet(client, "10.0.0.0/24", "lab")
    # subnets may overlap across sections: the same address, in another subnet
    other = await _subnet(client, "10.0.0.0/24", "other")
    host = await make_host(client, lab["id"], "10.0.0.10")
    await observe(lab["id"], "10.0.0.11")  # a neighbour in the same subnet
    await observe(other["id"], "10.0.0.10")
    got = await get(client, host["id"])
    assert (got["last_seen"], got["last_seen_source"]) == (None, None)


async def test_moving_a_host_follows_the_observation_of_its_new_address(client):
    subnet = await _subnet(client)
    host = await make_host(client, subnet["id"], "10.0.0.10")
    await observe(subnet["id"], "10.0.0.20")
    res = await client.put(f"/api/v1/hosts/{host['id']}", json={"ip": "10.0.0.20"})
    assert res.json()["data"]["last_seen_source"] == "scan"
    res = await client.put(f"/api/v1/hosts/{host['id']}", json={"ip": "10.0.0.30"})
    assert res.json()["data"]["last_seen_source"] is None


async def test_a_large_batch_reads_its_subnets_and_matches_the_same(client, monkeypatch):
    """Past `EXACT_LOOKUP_MAX` hosts the query reads the batch's subnets whole, so it stays
    inside SQLite's limit on bound values; the answer must not change."""
    lab = await _subnet(client, "10.0.0.0/24", "lab")
    other = await _subnet(client, "10.0.0.0/24", "other")  # overlaps, in another section
    seen_host = await make_host(client, lab["id"], "10.0.0.10")
    unseen_host = await make_host(client, other["id"], "10.0.0.11")
    await observe(lab["id"], "10.0.0.10")
    await observe(lab["id"], "10.0.0.11")  # the unseen host's address, in the other subnet
    await observe(other["id"], "10.0.0.12")  # no host here

    exact = {h["id"]: h for h in (await client.get("/api/v1/hosts")).json()["data"]}
    monkeypatch.setattr(hosts_svc, "EXACT_LOOKUP_MAX", 0)
    whole = {h["id"]: h for h in (await client.get("/api/v1/hosts")).json()["data"]}
    assert whole == exact
    assert whole[seen_host["id"]]["last_seen_source"] == "scan"
    assert whole[unseen_host["id"]]["last_seen_source"] is None


# --- Acceptance: listing hosts adds one query, not one per host ---


@contextmanager
def statements():
    seen: list[str] = []

    def count(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", count)
    try:
        yield seen
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count)


async def test_serialising_hosts_takes_two_queries_whatever_the_batch_size(client, monkeypatch):
    """`hosts_out`: one query for subnet domains, one for observations, for 1 host or 40,
    on the exact path and on the whole-subnet path."""
    lab = await _subnet(client, "10.0.0.0/24", "lab")
    other = await _subnet(client, "10.0.1.0/24", "other")
    for n in range(20):
        await make_host(client, lab["id"], f"10.0.0.{n + 10}")
        await make_host(client, other["id"], f"10.0.1.{n + 10}")
        await observe(lab["id"], f"10.0.0.{n + 10}")

    async with SessionLocal() as session:
        hosts = list((await session.scalars(select(Host).order_by(Host.ip_int))).all())
        for batch in (hosts[:1], hosts):
            for limit in (hosts_svc.EXACT_LOOKUP_MAX, 0):
                monkeypatch.setattr(hosts_svc, "EXACT_LOOKUP_MAX", limit)
                with statements() as seen:
                    out = await hosts_svc.hosts_out(session, batch)
                assert len(seen) == 2, (len(batch), limit, seen)
                assert [h["last_seen_source"] for h in out[:20]] == ["scan"] * min(len(batch), 20)
        with statements() as seen:
            assert await hosts_svc.hosts_out(session, []) == []
        assert seen == []


async def test_listing_hosts_over_rest_costs_the_same_for_one_host_or_forty(client):
    subnet = await _subnet(client)
    await make_host(client, subnet["id"], "10.0.0.10")
    await observe(subnet["id"], "10.0.0.10")
    with statements() as one:
        assert len((await client.get("/api/v1/hosts")).json()["data"]) == 1
    for n in range(39):
        await make_host(client, subnet["id"], f"10.0.0.{n + 11}")
        await observe(subnet["id"], f"10.0.0.{n + 11}")
    with statements() as forty:
        assert len((await client.get("/api/v1/hosts")).json()["data"]) == 40
    assert len(forty) == len(one)
    assert sum("FROM observations" in s for s in forty) == 1
