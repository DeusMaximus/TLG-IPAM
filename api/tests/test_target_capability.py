"""What a scan target's agent can run for it, and the scope's integer bounds (#68;
v0.2 spec §5.2, §5.8).

`runnable_methods` on the scan-target answer is `jobs.job_methods` applied to the
interfaces the agent last reported: computed on read, by the rule check-in issues jobs
by, and never stored. The scope's ranges carry their bounds as integers so the UI can
place a host by its `ip_int` without parsing an address.
"""

import inspect
import ipaddress
import re
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, func, select

from app import mcp_server
from app.database import SessionLocal
from app.models import Change, ScanTarget
from app.services import jobs, scans

from .test_checkin import (  # noqa: F401 (agent_api is a fixture)
    ROUTED,
    agent_api,
    agent_row,
    checkin,
    jobs_for,
    lab,
    make_agent,
    make_target,
)


async def target_answer(client, target_id) -> dict:
    res = await client.get(f"/api/v1/scan-targets/{target_id}")
    assert res.status_code == 200, res.text
    one = res.json()["data"]
    listed = (await client.get("/api/v1/scan-targets")).json()["data"]
    # the list answer is built the same way
    assert [t for t in listed if t["id"] == target_id] == [one]
    return one


async def target_columns(target_id) -> dict:
    async with SessionLocal() as session:
        target = await session.get(ScanTarget, target_id)
        return {c.name: getattr(target, c.name) for c in ScanTarget.__table__.columns}


async def scan_target_changes() -> int:
    async with SessionLocal() as session:
        query = select(func.count()).select_from(Change).where(Change.entity_type == "scan_target")
        return await session.scalar(query)


async def test_arp_is_unavailable_from_a_routed_agent_with_no_job_out(client, agent_api):
    agent, token = await make_agent(client)
    subnet = await lab(client)
    # on request only, so the check-in issues no job: the answer does not need one
    target = await make_target(client, subnet["id"], agent["id"], interval_minutes=None)
    assert await jobs_for(agent_api, token, **ROUTED) == []

    answer = await target_answer(client, target["id"])
    assert answer["methods"] == ["icmp", "arp", "rdns"]
    assert answer["runnable_methods"] == ["icmp", "rdns"]
    assert answer["current_job_id"] is None and answer["job_methods"] is None


async def test_an_arp_only_target_cannot_be_scanned_by_a_routed_agent(client, agent_api):
    agent, token = await make_agent(client)
    subnet = await lab(client)
    # due (an interval, never scanned): check-in still issues nothing, for the same reason
    target = await make_target(client, subnet["id"], agent["id"], methods=["arp", "rdns"])
    assert await jobs_for(agent_api, token, **ROUTED) == []

    answer = await target_answer(client, target["id"])
    assert answer["runnable_methods"] == []
    assert answer["current_job_id"] is None


async def test_an_agent_on_the_link_can_run_every_method(client, agent_api):
    agent, token = await make_agent(client)
    subnet = await lab(client)
    target = await make_target(
        client, subnet["id"], agent["id"], methods=["icmp", "arp", "rdns", "fdns"]
    )
    [job] = await jobs_for(agent_api, token)
    answer = await target_answer(client, target["id"])
    # the issued job's methods and the answer's agree: one rule
    assert answer["runnable_methods"] == job["methods"] == ["icmp", "arp", "rdns", "fdns"]


async def test_not_known_without_an_agent_or_before_its_first_check_in(client, agent_api):
    subnet = await lab(client)
    unassigned = await make_target(client, subnet["id"], None)
    assert (await target_answer(client, unassigned["id"]))["runnable_methods"] is None

    agent, token = await make_agent(client, name="never")
    other = await lab(client, "10.0.1.0/24", name="other")
    waiting = await make_target(client, other["id"], agent["id"], methods=["arp"])
    assert (await target_answer(client, waiting["id"]))["runnable_methods"] is None

    # a refused check-in reports no interfaces, so it is still not known
    assert (await checkin(agent_api, token, protocol=99)).status_code == 409
    assert (await agent_row(agent["id"])).last_checkin_at is not None
    assert (await target_answer(client, waiting["id"]))["runnable_methods"] is None


async def test_the_next_check_in_changes_the_answer_without_writing_the_target(
    client, agent_api
):
    agent, token = await make_agent(client)
    subnet = await lab(client)
    target = await make_target(
        client, subnet["id"], agent["id"], methods=["arp", "rdns"], interval_minutes=None
    )
    entries = await scan_target_changes()

    assert await jobs_for(agent_api, token, **ROUTED) == []
    row = await target_columns(target["id"])
    assert (await target_answer(client, target["id"]))["runnable_methods"] == []

    # the agent now reports an interface on the subnet's link
    assert await jobs_for(agent_api, token) == []
    assert (await target_answer(client, target["id"]))["runnable_methods"] == ["arp", "rdns"]

    # and off it again
    assert await jobs_for(agent_api, token, interfaces=[]) == []
    assert (await target_answer(client, target["id"]))["runnable_methods"] == []

    assert await target_columns(target["id"]) == row
    assert await scan_target_changes() == entries


async def test_a_disabled_agent_is_judged_by_the_interfaces_it_last_reported(client, agent_api):
    agent, token = await make_agent(client)
    subnet = await lab(client)
    target = await make_target(client, subnet["id"], agent["id"], interval_minutes=None)
    assert await jobs_for(agent_api, token, **ROUTED) == []
    res = await client.put(f"/api/v1/agents/{agent['id']}", json={"enabled": False})
    assert res.status_code == 200, res.text
    assert (await target_answer(client, target["id"]))["runnable_methods"] == ["icmp", "rdns"]


async def test_the_answer_is_what_job_methods_returns(client, agent_api, monkeypatch):
    """No second copy of the rule: the answer is whatever check-in's function says."""
    agent, token = await make_agent(client)
    subnet = await lab(client)
    target = await make_target(client, subnet["id"], agent["id"], interval_minutes=None)
    assert await jobs_for(agent_api, token) == []
    seen = []

    def fake(t, s, interfaces):
        seen.append((t.id, s.id, interfaces))
        return ["rdns"]

    monkeypatch.setattr(jobs, "job_methods", fake)
    assert (await target_answer(client, target["id"]))["runnable_methods"] == ["rdns"]
    assert seen[0][:2] == (target["id"], subnet["id"])
    assert seen[0][2] == [{"name": "eth0", "cidr": "10.0.0.5/24", "mac": "02:00:00:00:00:05"}]


async def test_scope_ranges_carry_their_bounds_as_integers(client):
    subnet = await lab(client, dhcp_start="10.0.0.100", dhcp_end="10.0.0.199")
    res = await client.post(
        f"/api/v1/subnets/{subnet['id']}/reserved",
        json={"start_ip": "10.0.0.10", "end_ip": "10.0.0.19", "scan_excluded": True},
    )
    assert res.status_code == 201
    target = await make_target(client, subnet["id"], None)
    assert [(r["start"], r["end"]) for r in target["scope"]] == [
        ("10.0.0.1", "10.0.0.9"),
        ("10.0.0.20", "10.0.0.99"),
        ("10.0.0.200", "10.0.0.254"),
    ]
    for r in target["scope"]:
        assert set(r) == {"start", "end", "start_int", "end_int"}
        assert r["start_int"] == int(ipaddress.ip_address(r["start"]))
        assert r["end_int"] == int(ipaddress.ip_address(r["end"]))


async def test_no_mcp_tool_serves_the_scan_target_answer():
    """`runnable_methods` and the scope are on the scan-target answer, which stays off MCP
    (spec §5.8): `request_scan` returns `scans.scan_request_out` instead."""
    source = inspect.getsource(mcp_server)
    assert "target_out" not in source and "targets_out" not in source
    assert "runnable_methods" not in source
    assert "agent_checkin" not in source


# --- The list in a fixed number of statements; how old the evidence is (#101) ---


async def seed_targets(count: int) -> None:
    """`count` subnets, each with a DHCP pool, an excluded range and a target assigned
    to an agent of its own that has reported its interfaces: on the subnet's link for
    even targets, routed for odd ones, and a refused check-in since for every third."""
    from app.models import Agent, ReservedRange, Section, Subnet

    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        section = Section(name="bulk")
        session.add(section)
        await session.flush()
        for i in range(count):
            net = ipaddress.ip_network(f"10.{i // 256}.{i % 256}.0/24")
            base = int(net.network_address)
            subnet = Subnet(
                section_id=section.id,
                cidr=str(net),
                name=f"bulk-{i}",
                dhcp_start=str(net[100]),
                dhcp_end=str(net[199]),
            )
            cidr = f"{net[5]}/24" if i % 2 == 0 else "192.168.50.5/24"
            agent = Agent(
                name=f"bulk-{i}",
                name_key=f"bulk-{i}",
                state="active",
                interfaces=[{"name": "eth0", "cidr": cidr, "mac": None}],
                last_checkin_at=now - timedelta(minutes=i),
                last_error="protocol" if i % 3 == 0 else None,
            )
            session.add_all([subnet, agent])
            await session.flush()
            session.add(
                ReservedRange(
                    subnet_id=subnet.id,
                    start_ip=str(net[10]),
                    end_ip=str(net[19]),
                    start_int=base + 10,
                    end_int=base + 19,
                    scan_excluded=True,
                )
            )
            session.add(
                ScanTarget(
                    subnet_id=subnet.id,
                    agent_id=agent.id,
                    methods=["icmp", "arp", "rdns"],
                    scan_dhcp_pool=i % 4 == 0,
                    interval_minutes=60,
                )
            )
        await session.commit()


@contextmanager
def statements():
    """The SQL statements run inside the block, as text."""
    from app.database import engine

    seen: list[str] = []

    def count(_conn, _cursor, statement, *_):
        seen.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", count)
    try:
        yield seen
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count)


# The list: the targets; then the subnets, the agents, and the scope's three inputs
# (subnet columns, pool setting, excluded ranges), each once for all the targets.
LIST_STATEMENTS = 6


@pytest.mark.parametrize("count", [1, 50, 500])
async def test_the_list_costs_the_same_statements_for_any_number_of_targets(client, count):
    await seed_targets(count)
    with statements() as seen:
        res = await client.get("/api/v1/scan-targets")
    assert res.status_code == 200, res.text
    listed = res.json()["data"]
    assert len(listed) == res.json()["total"] == count
    # one read of the agents, whatever the number of targets
    assert len([s for s in seen if re.search(r"\bFROM agents\b", s)]) == 1
    assert len(seen) == LIST_STATEMENTS, seen
    # and each answer is still worked out for its own target
    for i, answer in enumerate(listed):
        assert answer["runnable_methods"] == (["icmp", "arp", "rdns"] if i % 2 == 0 else ["icmp", "rdns"])
        assert answer["agent_checkin_refused"] is (i % 3 == 0)


async def test_the_list_answer_is_each_targets_own_answer(client, agent_api):
    """The list reads its inputs for every target at once; each answer is the same as the
    target's own (`GET /scan-targets/{id}`), and its scope is the subnet's `scan_scope`."""
    await seed_targets(12)
    listed = (await client.get("/api/v1/scan-targets")).json()["data"]
    for answer in listed:
        res = await client.get(f"/api/v1/scan-targets/{answer['id']}")
        assert res.json()["data"] == answer
    async with SessionLocal() as session:
        for answer in listed:
            scope = await scans.scan_scope(session, answer["subnet_id"])
            assert [(r["start_int"], r["end_int"]) for r in answer["scope"]] == scope
    # the pool is in scope where it is scanned, and the excluded range never is
    pooled = [a for a in listed if a["scan_dhcp_pool"]]
    assert pooled and all(len(a["scope"]) == 2 for a in pooled)
    assert all(len(a["scope"]) == 3 for a in listed if not a["scan_dhcp_pool"])


async def test_the_answer_says_when_the_agent_last_checked_in_and_if_it_was_refused(
    client, agent_api
):
    agent, token = await make_agent(client)
    subnet = await lab(client)
    target = await make_target(client, subnet["id"], agent["id"], interval_minutes=None)

    # nothing reported yet: nothing to date
    answer = await target_answer(client, target["id"])
    assert answer["runnable_methods"] is None
    assert answer["agent_checkin_at"] is None and answer["agent_checkin_refused"] is None

    # an accepted check-in: the interfaces are from that check-in
    assert await jobs_for(agent_api, token, **ROUTED) == []
    accepted = (await agent_row(agent["id"])).last_checkin_at
    answer = await target_answer(client, target["id"])
    assert answer["runnable_methods"] == ["icmp", "rdns"]
    assert answer["agent_checkin_refused"] is False
    assert answer["agent_checkin_at"].endswith("+00:00")
    assert datetime.fromisoformat(answer["agent_checkin_at"]) == accepted.replace(
        tzinfo=timezone.utc
    )
    # the same time the agents route serves
    agents = (await client.get("/api/v1/agents")).json()["data"]
    assert answer["agent_checkin_at"] == agents[0]["last_checkin_at"]

    # a refused one: the time moves, the interfaces stay and are now older than it
    assert (await checkin(agent_api, token, protocol=99)).status_code == 409
    refused = await target_answer(client, target["id"])
    assert refused["runnable_methods"] == ["icmp", "rdns"]
    assert refused["agent_checkin_refused"] is True
    assert refused["agent_checkin_at"] > answer["agent_checkin_at"]

    # a refused body (422) counts the same; the next accepted one clears it
    assert (await checkin(agent_api, token, hostname="x" * 1000)).status_code == 422
    assert (await target_answer(client, target["id"]))["agent_checkin_refused"] is True
    assert await jobs_for(agent_api, token) == []
    again = await target_answer(client, target["id"])
    assert again["runnable_methods"] == ["icmp", "arp", "rdns"]
    assert again["agent_checkin_refused"] is False
    assert again["agent_checkin_at"] > refused["agent_checkin_at"]


async def test_no_agent_means_no_check_in_time(client, agent_api):
    subnet = await lab(client)
    target = await make_target(client, subnet["id"], None)
    answer = await target_answer(client, target["id"])
    assert answer["agent_checkin_at"] is None and answer["agent_checkin_refused"] is None


# --- The list past the SQLite build's parameter limit; scope in chunks (#111) ---


@contextmanager
def statements_with_parameters():
    """The SQL statements run inside the block, with the parameters each bound."""
    from app.database import engine

    seen: list[tuple[str, tuple]] = []

    def count(_conn, _cursor, statement, parameters, *_):
        seen.append((statement, tuple(parameters or ())))

    event.listen(engine.sync_engine, "before_cursor_execute", count)
    try:
        yield seen
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count)


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        # one chunk: the six statements of the list
        (5, LIST_STATEMENTS),
        # two chunks of each: the targets, then two reads each of the subnets, the
        # agents and the scope's three inputs
        (6, 1 + 2 + 2 + 2 * 3),
        (11, 1 + 3 + 3 + 3 * 3),
    ],
)
async def test_the_list_grows_by_a_fixed_number_of_statements_per_chunk(
    client, monkeypatch, count, expected
):
    monkeypatch.setattr(scans, "_IN_BATCH", 5)
    await seed_targets(count)
    with statements_with_parameters() as seen:
        res = await client.get("/api/v1/scan-targets")
    assert res.status_code == 200, res.text
    assert len(seen) == expected, [s for s, _ in seen]
    # no statement binds more ids than a chunk holds
    assert max(len(params) for _, params in seen) <= 5
    listed = res.json()["data"]
    assert len(listed) == count
    for i, answer in enumerate(listed):
        assert answer["runnable_methods"] == (["icmp", "arp", "rdns"] if i % 2 == 0 else ["icmp", "rdns"])
        assert answer["agent_checkin_refused"] is (i % 3 == 0)
        assert len(answer["scope"]) == (2 if answer["scan_dhcp_pool"] else 3)


@asynccontextmanager
async def parameter_limit(limit: int):
    """The app's requests served from their own engine on the same database file, on
    connections that bind at most `limit` parameters per statement: a SQLite build's
    limit, lowered (`sqlite3.Connection.setlimit`), so a test can pass it without
    seeding as many rows as the image's build allows (250,000)."""
    import sqlite3

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.util import await_only

    from app.config import settings
    from app.database import _configure_connection, get_session
    from app.main import app

    other = create_async_engine(f"sqlite+aiosqlite:///{settings.db_path}")

    @event.listens_for(other.sync_engine, "connect")
    def _configure(dbapi_connection, record):
        _configure_connection(dbapi_connection, record)
        driver = dbapi_connection.driver_connection  # aiosqlite, on its own thread
        await_only(
            driver._execute(driver._conn.setlimit, sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, limit)
        )

    sessions = async_sessionmaker(other, expire_on_commit=False)

    async def session():
        async with sessions() as s:
            yield s

    app.dependency_overrides[get_session] = session
    try:
        yield sessions
    finally:
        app.dependency_overrides.pop(get_session, None)
        await other.dispose()


async def test_the_list_answers_for_more_targets_than_one_statement_can_bind(
    client, monkeypatch
):
    """With a build that binds 8 parameters, 20 targets on 20 subnets with 20 agents: in
    one statement per input the list fails; in chunks of 8 it answers, and each answer
    is the target's own (#111)."""
    from sqlalchemy.exc import OperationalError

    await seed_targets(20)
    expected = (await client.get("/api/v1/scan-targets")).json()["data"]
    assert len(expected) == 20

    async with parameter_limit(8):
        # the limit is in force: unchunked, the first read of the subnets passes it
        monkeypatch.setattr(scans, "_IN_BATCH", 500)
        with pytest.raises(OperationalError, match="too many SQL variables"):
            await client.get("/api/v1/scan-targets")

        monkeypatch.setattr(scans, "_IN_BATCH", 8)
        res = await client.get("/api/v1/scan-targets")
        assert res.status_code == 200, res.text
        assert res.json()["data"] == expected


async def seed_scope_shapes() -> list[str]:
    """Subnets whose scopes differ in every input, each in a section of its own (so two
    can hold the same CIDR): the subnet ids, in the order made."""
    from app.models import ReservedRange, Section, Subnet

    shapes = [
        # cidr, pool, target's scan_dhcp_pool (None: no target), excluded, scanned
        ("10.0.0.0/24", (100, 199), True, [(10, 19), (30, 39)], [(15, 35)]),
        ("10.0.0.0/24", (100, 199), False, [(50, 59)], []),
        ("10.0.1.0/24", (100, 199), None, [], []),
        ("10.0.2.0/24", None, True, [(0, 255)], []),
        ("10.0.3.0/30", None, False, [], []),
        ("10.0.3.4/31", None, False, [], []),
        ("10.0.3.6/32", None, True, [], []),
        ("10.0.4.0/24", (100, 199), True, [(10, 19), (20, 29)], [(25, 40)]),
        ("10.0.5.0/24", (100, 199), False, [(150, 210)], []),
        ("10.0.6.0/24", (100, 199), True, [(100, 199)], []),
    ]
    ids = []
    async with SessionLocal() as session:
        for i, (cidr, pool, pool_scanned, excluded, scanned) in enumerate(shapes):
            section = Section(name=f"shape-{i}")
            session.add(section)
            await session.flush()
            net = ipaddress.ip_network(cidr)
            base = int(net.network_address)
            subnet = Subnet(
                section_id=section.id,
                cidr=cidr,
                name=f"shape-{i}",
                dhcp_start=str(net[pool[0]]) if pool else None,
                dhcp_end=str(net[pool[1]]) if pool else None,
            )
            session.add(subnet)
            await session.flush()
            for (lo, hi), excluded_flag in [(r, True) for r in excluded] + [
                (r, False) for r in scanned
            ]:
                session.add(
                    ReservedRange(
                        subnet_id=subnet.id,
                        start_ip=str(net[lo]),
                        end_ip=str(net[hi]),
                        start_int=base + lo,
                        end_int=base + hi,
                        scan_excluded=excluded_flag,
                    )
                )
            if pool_scanned is not None:
                session.add(
                    ScanTarget(
                        subnet_id=subnet.id, methods=["icmp"], scan_dhcp_pool=pool_scanned
                    )
                )
            ids.append(subnet.id)
        await session.commit()
    return ids


@pytest.mark.parametrize("batch", [1, 2, 3, 4, 500])
async def test_each_subnet_gets_its_own_scope_in_any_order_of_ids_and_across_chunks(
    monkeypatch, batch
):
    """`_scopes_as_committed` in chunks gives each subnet the scope of its one-subnet
    case, whatever the chunk size, the order of the ids or their repeats; a deleted
    subnet is left out. Every subnet comes to the end of a chunk under some order and
    size here, the first shape among them: its excluded ranges and its scanned pool
    must both reach its scope (#111)."""
    import random

    ids = await seed_scope_shapes()
    async with SessionLocal() as session:
        own = {i: await scans._scope_as_committed(session, i) for i in ids}
    # the first shape by hand: the pool is scanned, both exclusions hold, and the
    # scanned range over them removes nothing
    base = int(ipaddress.ip_address("10.0.0.0"))
    assert own[ids[0]][1] == [(base + 1, base + 9), (base + 20, base + 29), (base + 40, base + 254)]
    # the same CIDR with the pool not scanned and another exclusion
    assert own[ids[1]][1] == [
        (base + 1, base + 49),
        (base + 60, base + 99),
        (base + 200, base + 254),
    ]

    monkeypatch.setattr(scans, "_IN_BATCH", batch)
    shuffled = ids[:]
    random.Random(111).shuffle(shuffled)
    orders = [
        ids,
        list(reversed(ids)),
        shuffled,
        # repeats, within a chunk and on both sides of a boundary
        [ids[0], ids[0], *ids[1:4], ids[0], *ids[4:], ids[3], ids[9]],
        ["no-such-subnet", *ids[5:], "no-such-subnet", *ids[:5]],
    ]
    async with SessionLocal() as session:
        for order in orders:
            assert await scans._scopes_as_committed(session, order) == own
        assert await scans._scopes_as_committed(session, []) == {}
        assert await scans._scopes_as_committed(session, ["no-such-subnet"]) == {}


async def test_one_subnets_scope_is_read_in_the_same_three_statements(monkeypatch):
    """Check-in, ingest and `drop_out_of_scope` read one subnet's scope: three
    statements, each binding that one id, whatever the chunk size; one for a deleted
    subnet."""
    ids = await seed_scope_shapes()
    for batch in (1, 500):
        monkeypatch.setattr(scans, "_IN_BATCH", batch)
        async with SessionLocal() as session:
            with statements_with_parameters() as seen:
                await scans.scan_scope(session, ids[0])
            assert len(seen) == 3
            assert [params[:1] for _, params in seen] == [(ids[0],)] * 3
            assert re.search(r"FROM subnets\b", seen[0][0])
            assert re.search(r"FROM scan_targets\b", seen[1][0])
            assert re.search(r"FROM reserved_ranges\b", seen[2][0])
            assert all(" IN (?)" in statement for statement, _ in seen)
            with statements_with_parameters() as seen:
                assert await scans.scan_scope(session, "no-such-subnet") == []
            assert len(seen) == 1
