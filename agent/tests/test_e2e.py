"""The agent's client and loop against the server's real check-in and results routes,
in process (acceptance for #13). The prober and resolver are still fakes: no packet
is sent. The server's check-in gap is its real one here (10 s), unlike in the
server's own tests."""

import asyncio
import ipaddress
import random
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from app.database import SessionLocal, engine
from app.main import app
from app.models import Base, Observation
from app.services.device_types import seed_defaults
from sqlalchemy import select, update

from conftest import FakeArp, FakeClock, FakeProber, FakeResolver, make_config
from tlg_ipam_agent import loop as agent_loop
from tlg_ipam_agent import scan
from tlg_ipam_agent.backoff import Backoff
from tlg_ipam_agent.client import AgentClient
from tlg_ipam_agent.loop import Agent
from tlg_ipam_agent.protocol import (
    CHECKIN_REFUSED_RETRY_S,
    CHECKIN_RETRY_MAX_S,
    CHECKIN_RETRY_MIN_S,
)

LINK = [{"name": "eth0", "cidr": "10.0.0.5/28", "mac": "02:00:00:00:00:05"}]


@pytest.fixture(autouse=True)
async def fresh_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as session:
        await seed_defaults(session)
    yield


@pytest.fixture
async def owner():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": "test-key"},
    ) as client:
        yield client


async def _ok(res, status=200) -> dict:
    assert res.status_code == status, res.text
    return res.json()["data"] if res.content else {}


async def setup(owner, *, cidr="10.0.0.0/28", methods=("icmp", "rdns"), **subnet):
    """An agent, a subnet, and a target assigning the subnet to the agent."""
    agent = await _ok(await owner.post("/api/v1/agents", json={"name": "vlan10"}), 201)
    section = await _ok(await owner.post("/api/v1/sections", json={"name": "Homelab"}), 201)
    made = await _ok(
        await owner.post(
            "/api/v1/subnets",
            json={"section_id": section["id"], "cidr": cidr, "name": "lab", **subnet},
        ),
        201,
    )
    target = await _ok(
        await owner.post(
            "/api/v1/scan-targets",
            json={
                "subnet_id": made["id"],
                "agent_id": agent["id"],
                "interval_minutes": 60,
                "methods": list(methods),
            },
        ),
        201,
    )
    return agent, made, target


def fast_clock(scale: float = 10.0) -> scan.Clock:
    """The real event-loop clock, run `scale` times fast: concurrent waits overlap as
    they do in production (the fake clock adds them up), and a 600-second budget is
    not waited out in full."""
    origin = time.monotonic()
    return scan.Clock(
        monotonic=lambda: 1000.0 + (time.monotonic() - origin) * scale,
        sleep=lambda seconds: asyncio.sleep(max(seconds, 0) / scale),
    )


def make_agent(token, *, prober, resolver=None, arp=None, interfaces=LINK, clock=None, **config):
    http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://test",
        headers={"Authorization": f"Bearer {token}"},
    )
    return Agent(
        make_config(token=token, **config),
        AgentClient(http),
        prober=prober,
        resolver=resolver or FakeResolver(),
        arp_link=arp if arp is not None else FakeArp(),
        interfaces=lambda: interfaces,
        clock=clock or FakeClock(),
        rng=random.Random(7),
    )


async def run_queued(agent: Agent, timeout_s: int | None = None) -> None:
    """Run what the check-in queued. `timeout_s` stands in for a job the deadline cuts
    short, without a test waiting the server's 600 s: the rest of the job is as issued."""
    while not agent.queue.empty():
        raw = agent.queue.get_nowait()
        if timeout_s is not None:
            raw = {**raw, "timeout_s": timeout_s}
        await agent.run_job(raw)


async def observations(subnet_id) -> dict[str, Observation]:
    async with SessionLocal() as session:
        rows = await session.scalars(select(Observation).where(Observation.subnet_id == subnet_id))
        return {o.ip: o for o in rows}


async def target_of(owner, target_id) -> dict:
    return await _ok(await owner.get(f"/api/v1/scan-targets/{target_id}"))


async def past_the_checkin_gap(agent_id) -> None:
    """Move the agent's last check-in back past the server's 10-second gap."""
    from app.models import Agent as AgentRow

    async with SessionLocal() as session:
        await session.execute(
            update(AgentRow)
            .where(AgentRow.id == agent_id)
            .values(last_checkin_at=datetime.now(timezone.utc) - timedelta(seconds=30))
        )
        await session.commit()


async def test_checkin_scan_and_results_against_the_real_routes(owner):
    agent_row, subnet, target = await setup(owner, dns_servers=["10.0.0.1"])
    prober = FakeProber(alive=["10.0.0.2", "10.0.0.9"])
    resolver = FakeResolver({"10.0.0.9": "NAS.home.arpa.", "10.0.0.2": "not a <name>"})
    agent = make_agent(agent_row["token"], prober=prober, resolver=resolver)

    wait = await agent.checkin_once()
    assert wait == 60
    assert agent.queue.qsize() == 1
    leased = await target_of(owner, target["id"])
    assert leased["current_job_id"] is not None

    await run_queued(agent)
    # the subnet's 14 usable addresses, and nothing else
    assert sorted(str(a) for a in prober.sent) == sorted(f"10.0.0.{i}" for i in range(1, 15))
    assert {(str(a), s) for a, s in resolver.asked} == {("10.0.0.2", ("10.0.0.1",)), ("10.0.0.9", ("10.0.0.1",))}

    seen = await observations(subnet["id"])
    assert set(seen) == {"10.0.0.2", "10.0.0.9"}
    assert seen["10.0.0.9"].rdns_name == "nas.home.arpa"
    assert seen["10.0.0.2"].rdns_name is None
    assert all(o.alive and o.mac is None for o in seen.values())

    done = await target_of(owner, target["id"])
    assert done["current_job_id"] is None
    assert done["last_scan_at"] is not None
    assert done["last_scan_complete"] is True
    assert done["last_scan_error"] is None
    shown = await _ok(await owner.get(f"/api/v1/agents/{agent_row['id']}"))
    assert shown["interfaces"] == LINK
    assert shown["current_job_id"] is None


async def test_the_real_rate_limit_is_backed_off_and_authorises_no_scan(owner):
    agent_row, _, target = await setup(owner)
    prober = FakeProber()
    agent = make_agent(agent_row["token"], prober=prober)
    # an accepted check-in first, then the same agent inside the server's 10 s gap
    assert await agent.checkin_once() == 60
    agent.queue.get_nowait()  # set the first job aside, unrun
    wait = await agent.checkin_once()
    assert CHECKIN_RETRY_MIN_S <= wait <= 2 * CHECKIN_RETRY_MIN_S
    assert agent.queue.empty()
    assert prober.sent == []


async def test_a_job_outside_the_allowlist_is_refused_and_the_refusal_reaches_the_server(owner):
    """Acceptance, end to end: no packet, and the server shows why."""
    agent_row, subnet, target = await setup(owner, cidr="203.0.113.0/28")
    prober, resolver = FakeProber(alive=["203.0.113.2"]), FakeResolver()
    agent = make_agent(
        agent_row["token"],
        prober=prober,
        resolver=resolver,
        interfaces=[{"name": "eth0", "cidr": "203.0.113.5/28", "mac": None}],
    )
    await agent.checkin_once()
    assert agent.queue.qsize() == 1
    await run_queued(agent)
    assert prober.sent == [] and resolver.asked == []

    shown = await target_of(owner, target["id"])
    assert shown["current_job_id"] is None  # the refusal cleared the lease
    assert shown["last_scan_complete"] is False
    assert "203.0.113.0/28 is outside ALLOWED_CIDRS" in shown["last_scan_error"]
    assert await observations(subnet["id"]) == {}


async def test_arp_finds_a_host_that_drops_ping_and_a_departed_host_is_marked_dead(owner):
    """The acceptance line of #14, against the real routes with the wire faked: a host
    that answers ARP and not ping is reported alive with its MAC, and once a host has
    left (it answers neither), the next complete scan marks it dead."""
    agent_row, subnet, target = await setup(owner, methods=("icmp", "arp"))
    prober = FakeProber(alive=["10.0.0.2", "10.0.0.3"])
    arp = FakeArp({
        "10.0.0.2": "02:00:00:00:00:02",
        "10.0.0.3": "02:00:00:00:00:03",
        "10.0.0.9": "02:00:00:00:00:09",  # drops ping
    })
    agent = make_agent(agent_row["token"], prober=prober, arp=arp)
    await agent.checkin_once()
    await run_queued(agent)
    first = await target_of(owner, target["id"])
    assert first["last_scan_complete"] is True and first["last_scan_error"] is None
    assert arp.opened == ["eth0"] and arp.closed == 1
    seen = await observations(subnet["id"])
    assert {ip: (o.alive, o.mac) for ip, o in seen.items()} == {
        "10.0.0.2": (True, "02:00:00:00:00:02"),
        "10.0.0.3": (True, "02:00:00:00:00:03"),
        "10.0.0.5": (True, "02:00:00:00:00:05"),  # the agent's own address
        "10.0.0.9": (True, "02:00:00:00:00:09"),
    }

    # 10.0.0.3 leaves the network; a scan is requested and the next job runs
    prober.alive = {a for a in prober.alive if str(a) != "10.0.0.3"}
    del arp.hosts[ipaddress.ip_address("10.0.0.3")]
    await _ok(await owner.post(f"/api/v1/subnets/{subnet['id']}/scan"), 202)
    await past_the_checkin_gap(agent_row["id"])
    await agent.checkin_once()
    await run_queued(agent)
    assert (await target_of(owner, target["id"]))["last_scan_complete"] is True
    seen = await observations(subnet["id"])
    assert {ip for ip, o in seen.items() if o.alive} == {"10.0.0.2", "10.0.0.5", "10.0.0.9"}
    assert seen["10.0.0.3"].alive is False
    # the departed host had its request and its retry, and nothing else was retried
    assert arp.asked("10.0.0.3") == 3  # one in the first scan, two in the second
    assert arp.asked("10.0.0.9") == 2  # once a scan


async def test_an_arp_scan_cut_off_in_its_retry_round_marks_nothing_dead(owner):
    """A scan whose deadline falls while the retries go out is incomplete, so the host
    that left is not marked dead: the silent addresses never had their retry (briefed
    review of PR #84). At 10 a second, the first round's 13 requests and its wait end
    at 2.3 s; the 3 s job is cut off in the retries."""
    agent_row, subnet, target = await setup(owner, methods=("arp",))
    arp = FakeArp({"10.0.0.2": "02:00:00:00:00:02", "10.0.0.3": "02:00:00:00:00:03"})
    agent = make_agent(agent_row["token"], prober=FakeProber(), arp=arp, max_pps=10)
    await agent.checkin_once()
    await run_queued(agent)
    assert (await target_of(owner, target["id"]))["last_scan_complete"] is True
    assert (await observations(subnet["id"]))["10.0.0.3"].alive is True

    del arp.hosts[ipaddress.ip_address("10.0.0.3")]
    arp.requests.clear()
    await _ok(await owner.post(f"/api/v1/subnets/{subnet['id']}/scan"), 202)
    await past_the_checkin_gap(agent_row["id"])
    await agent.checkin_once()
    await run_queued(agent, timeout_s=3)
    shown = await target_of(owner, target["id"])
    assert shown["last_scan_complete"] is False
    assert "during the ARP sweep" in shown["last_scan_error"]
    # the retry round had begun, and had not reached every silent address
    assert arp.asked("10.0.0.1") == 2 and arp.asked("10.0.0.14") == 1
    seen = await observations(subnet["id"])
    assert seen["10.0.0.3"].alive is True and seen["10.0.0.2"].alive is True


async def test_an_arp_job_on_an_interface_without_a_mac_marks_nothing_dead(owner):
    """The server issues `arp` by the reported networks alone; an agent that cannot
    send ARP from that interface reports the scan incomplete, so a quiet address is
    not marked dead."""
    agent_row, subnet, target = await setup(owner, methods=("icmp", "arp"))
    prober = FakeProber(alive=["10.0.0.2", "10.0.0.3"])
    arp = FakeArp()
    no_mac = [{"name": "eth0", "cidr": "10.0.0.5/28", "mac": None}]
    agent = make_agent(agent_row["token"], prober=prober, arp=arp, interfaces=no_mac)
    await agent.checkin_once()
    await run_queued(agent)
    first = await target_of(owner, target["id"])
    assert first["last_scan_complete"] is False
    assert "arp not run" in first["last_scan_error"]
    assert arp.opened == []

    prober.alive = {a for a in prober.alive if str(a) != "10.0.0.3"}
    await _ok(await owner.post(f"/api/v1/subnets/{subnet['id']}/scan"), 202)
    await past_the_checkin_gap(agent_row["id"])
    await agent.checkin_once()
    await run_queued(agent)
    seen = await observations(subnet["id"])
    assert seen["10.0.0.3"].alive is True  # not marked dead by an incomplete scan


async def test_a_complete_icmp_scan_marks_a_quiet_address_dead(owner):
    agent_row, subnet, _ = await setup(owner, methods=("icmp",))
    prober = FakeProber(alive=["10.0.0.2", "10.0.0.3"])
    agent = make_agent(agent_row["token"], prober=prober)
    await agent.checkin_once()
    await run_queued(agent)
    prober.alive = {a for a in prober.alive if str(a) != "10.0.0.3"}
    await _ok(await owner.post(f"/api/v1/subnets/{subnet['id']}/scan"), 202)
    await past_the_checkin_gap(agent_row["id"])
    await agent.checkin_once()
    await run_queued(agent)
    seen = await observations(subnet["id"])
    assert seen["10.0.0.2"].alive is True
    assert seen["10.0.0.3"].alive is False


async def test_a_protocol_mismatch_is_not_retried_in_a_tight_loop(owner, monkeypatch):
    agent_row, _, _ = await setup(owner)
    monkeypatch.setattr(agent_loop, "PROTOCOL_VERSION", 2)
    prober = FakeProber()
    agent = make_agent(agent_row["token"], prober=prober)
    wait = await agent.checkin_once()
    assert 0.9 * CHECKIN_REFUSED_RETRY_S <= wait <= CHECKIN_REFUSED_RETRY_S
    assert agent.queue.empty() and prober.sent == []
    shown = await _ok(await owner.get(f"/api/v1/agents/{agent_row['id']}"))
    assert "Protocol mismatch" in shown["last_error"]


async def test_a_revoked_token_is_not_retried_in_a_tight_loop(owner):
    agent_row, _, _ = await setup(owner)
    await _ok(await owner.post(f"/api/v1/agents/{agent_row['id']}/revoke"))
    prober = FakeProber()
    agent = make_agent(agent_row["token"], prober=prober)
    wait = await agent.checkin_once()
    assert 0.9 * CHECKIN_REFUSED_RETRY_S <= wait <= CHECKIN_REFUSED_RETRY_S
    assert agent.queue.empty() and prober.sent == []


async def test_a_superseded_result_set_is_not_retried(owner):
    agent_row, subnet, target = await setup(owner)
    prober = FakeProber(alive=["10.0.0.2"])
    agent = make_agent(agent_row["token"], prober=prober)
    await agent.checkin_once()
    # the owner deletes the target mid-scan: the job is no longer current
    await _ok(await owner.delete(f"/api/v1/scan-targets/{target['id']}"), 204)
    posted = []
    real = agent.client.results

    async def counting(body, **kwargs):
        answer = await real(body, **kwargs)
        posted.append(answer)
        return answer

    agent.client.results = counting
    await run_queued(agent)
    assert [a.status for a in posted] == [409]
    assert posted[0].code == "JOB_NOT_CURRENT"
    assert await observations(subnet["id"]) == {}


def test_backoff_bounds_are_the_servers():
    backoff = Backoff(random.Random(0))
    assert all(CHECKIN_RETRY_MIN_S <= backoff.next() <= CHECKIN_RETRY_MAX_S for _ in range(50))


def test_the_server_under_test_uses_this_sessions_own_database():
    """An exported DB_PATH never reaches the server's engine, which these tests empty."""
    from conftest import _SESSION_DIR

    assert str(engine.url.database).startswith(_SESSION_DIR)


async def test_names_already_stored_survive_a_scan_at_one_packet_a_second(owner):
    """The consequence the second briefed round measured at ce60198: at MAX_PPS=1 the
    later PTR lookups ran out of time queueing for a slot, the result still said
    complete, and ingest cleared the names they never looked up. Real check-ins and
    results, the real resolver, a DNS responder on loopback."""
    from test_probe import ANSWERS, responder

    from tlg_ipam_agent.probe import DnsResolver

    live = [f"10.0.0.{i}" for i in range(1, 5)]
    for ip in live:
        ANSWERS[f"{ip.split('.')[-1]}.0.0.10.in-addr.arpa."] = f"host{ip.split('.')[-1]}.home.arpa."
    transport, dns_responder, port = await responder()
    try:
        agent_row, subnet, target = await setup(owner, cidr="10.0.0.0/29", dns_servers=["127.0.0.1"])
        agent = make_agent(
            agent_row["token"],
            prober=FakeProber(alive=live),
            resolver=DnsResolver(port=port),
            interfaces=[{"name": "eth0", "cidr": "10.0.0.5/29", "mac": None}],
            max_pps=1,
        )
        for scan_number in (1, 2):
            if scan_number == 2:
                await _ok(await owner.post(f"/api/v1/subnets/{subnet['id']}/scan"), 202)
                await past_the_checkin_gap(agent_row["id"])
            await agent.checkin_once()
            await run_queued(agent)
            seen = await observations(subnet["id"])
            assert {ip: seen[ip].rdns_name for ip in live} == {
                ip: f"host{ip.split('.')[-1]}.home.arpa" for ip in live
            }, f"scan {scan_number}"
            assert (await target_of(owner, target["id"]))["last_scan_complete"] is True
        # every live address asked in each scan
        assert len(dns_responder.arrivals) == 2 * len(live)
    finally:
        transport.close()
        for ip in live:
            ANSWERS.pop(f"{ip.split('.')[-1]}.0.0.10.in-addr.arpa.", None)


# --- names across an interrupted scan (briefed review round 3 of PR #70) ---

LIVE = ["10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4"]
OLD = {ip: f"old{ip.split('.')[-1]}.home.arpa" for ip in LIVE}
NEW = {ip: f"new{ip.split('.')[-1]}.home.arpa" for ip in LIVE}


async def _named_subnet(owner, methods=("icmp", "rdns")):
    """A /29 whose four live addresses have OLD names stored by a complete scan, and an
    agent at MAX_PPS=1 (one packet a second on the fake clock), ready for the next.
    The four answer ARP too, and the agent's interface (10.0.0.5) has a MAC."""
    agent_row, subnet, target = await setup(owner, cidr="10.0.0.0/29", methods=methods)
    resolver = FakeResolver(OLD)
    agent = make_agent(
        agent_row["token"],
        prober=FakeProber(alive=LIVE),
        resolver=resolver,
        arp=FakeArp({ip: f"02:00:00:00:00:0{ip[-1]}" for ip in LIVE}),
        interfaces=[{"name": "eth0", "cidr": "10.0.0.5/29", "mac": "02:00:00:00:00:05"}],
        clock=fast_clock(),
        max_pps=1,
    )
    await agent.checkin_once()
    await run_queued(agent)
    names = {ip: o.rdns_name for ip, o in (await observations(subnet["id"])).items()}
    assert {ip: names[ip] for ip in LIVE} == OLD
    await _ok(await owner.post(f"/api/v1/subnets/{subnet['id']}/scan"), 202)
    await past_the_checkin_gap(agent_row["id"])
    resolver.names = {ipaddress.ip_address(k): v for k, v in NEW.items()}
    resolver.asked.clear()
    return agent, resolver, subnet, target


async def _names(subnet) -> dict:
    return {ip: o.rdns_name for ip, o in (await observations(subnet["id"])).items()}


async def test_a_scan_cut_off_during_the_ptr_lookups_keeps_the_names_it_did_not_ask(owner):
    """Six pings take six seconds at 1 pps; of an 8 s job, the lookups get two slots
    (the clock runs ten times fast: 0.8 s of real time).
    The two asked get their new names; the two never asked keep their old ones."""
    agent, resolver, subnet, target = await _named_subnet(owner)
    await agent.checkin_once()
    await run_queued(agent, timeout_s=8)
    asked = sorted(str(a) for a, _ in resolver.asked)
    assert asked == ["10.0.0.1", "10.0.0.2"]
    assert await _names(subnet) == {
        "10.0.0.1": NEW["10.0.0.1"], "10.0.0.2": NEW["10.0.0.2"],
        "10.0.0.3": OLD["10.0.0.3"], "10.0.0.4": OLD["10.0.0.4"],
    }
    shown = await target_of(owner, target["id"])
    assert shown["last_scan_complete"] is False
    assert all(o.alive for o in (await observations(subnet["id"])).values())


async def test_a_scan_whose_icmp_sweep_uses_the_whole_deadline_keeps_every_name(owner):
    agent, resolver, subnet, target = await _named_subnet(owner)
    await agent.checkin_once()
    await run_queued(agent, timeout_s=4)
    assert resolver.asked == []
    assert await _names(subnet) == OLD
    assert (await target_of(owner, target["id"]))["last_scan_complete"] is False


async def test_a_complete_scan_still_clears_a_name_its_lookup_did_not_return(owner):
    agent, resolver, subnet, target = await _named_subnet(owner)
    del resolver.names[ipaddress.ip_address("10.0.0.3")]
    await agent.checkin_once()
    await run_queued(agent)
    assert len(resolver.asked) == 4
    assert await _names(subnet) == {**NEW, "10.0.0.3": None}
    assert (await target_of(owner, target["id"]))["last_scan_complete"] is True


async def test_on_its_own_link_a_complete_arp_scan_clears_a_name(owner):
    """Deliberate call 17 of PR #70 held while `arp` could not run: a job issued it
    was always incomplete, so names there were only set or replaced. Now the agent
    runs `arp`, a complete scan on its own link clears a name its lookup did not
    return, as it does elsewhere."""
    agent, resolver, subnet, target = await _named_subnet(owner, methods=("icmp", "arp", "rdns"))
    del resolver.names[ipaddress.ip_address("10.0.0.3")]
    await agent.checkin_once()
    await run_queued(agent)
    assert (await target_of(owner, target["id"]))["last_scan_complete"] is True
    # the four live addresses, and the agent's own, found by ARP
    assert sorted(str(a) for a, _ in resolver.asked) == [*LIVE, "10.0.0.5"]
    names = await _names(subnet)
    assert names == {**NEW, "10.0.0.3": None, "10.0.0.5": None}


# --- no server to ask is not a completed lookup (#78) ---


async def past_the_request_cooldown(target_id) -> None:
    """Move the target's last scan request back past the server's cooldown."""
    from app.models import ScanTarget

    async with SessionLocal() as session:
        await session.execute(
            update(ScanTarget)
            .where(ScanTarget.id == target_id)
            .values(scan_requested_at=datetime.now(timezone.utc) - timedelta(hours=1))
        )
        await session.commit()


async def test_with_no_dns_server_anywhere_a_scan_keeps_the_stored_names(owner, tmp_path):
    """Acceptance for #78 through the real routes. The subnet names no DNS server, so
    the agent uses its host's resolv.conf. A complete scan stores the names; with
    resolv.conf emptied (the built image in the briefed review), the next scan sends no
    DNS packet, is incomplete with the fixed error, and the names survive it; with
    resolv.conf fixed, the scan after that, in the same process, looks them up again.
    The real resolver, a DNS responder on loopback."""
    from test_probe import ANSWERS, responder

    from tlg_ipam_agent.probe import NO_DNS_SERVER, DnsResolver

    live = ["10.0.0.1", "10.0.0.2"]
    keys = {ip: f"{ip.split('.')[-1]}.0.0.10.in-addr.arpa." for ip in live}
    resolv = tmp_path / "resolv.conf"
    resolv.write_text("nameserver 127.0.0.1\n")
    transport, dns_responder, port = await responder()
    try:
        agent_row, subnet, target = await setup(owner, cidr="10.0.0.0/29")
        assert subnet["dns_servers"] in (None, [])
        agent = make_agent(
            agent_row["token"],
            prober=FakeProber(alive=live),
            resolver=DnsResolver(port=port, resolv_conf=str(resolv)),
            interfaces=[{"name": "eth0", "cidr": "10.0.0.5/29", "mac": None}],
        )

        async def scan_once(number):
            if number > 1:
                await past_the_request_cooldown(target["id"])
                await _ok(await owner.post(f"/api/v1/subnets/{subnet['id']}/scan"), 202)
                await past_the_checkin_gap(agent_row["id"])
            await agent.checkin_once()
            await run_queued(agent)
            seen = await observations(subnet["id"])
            return {ip: seen[ip].rdns_name for ip in live}, await target_of(owner, target["id"])

        for ip in live:
            ANSWERS[keys[ip]] = f"old{ip[-1]}.home.arpa."
        names, shown = await scan_once(1)
        assert names == {ip: f"old{ip[-1]}.home.arpa" for ip in live}
        assert shown["last_scan_complete"] is True
        assert len(dns_responder.arrivals) == len(live)

        resolv.write_text("")
        for ip in live:
            ANSWERS[keys[ip]] = f"new{ip[-1]}.home.arpa."
        names, shown = await scan_once(2)
        assert names == {ip: f"old{ip[-1]}.home.arpa" for ip in live}
        assert shown["last_scan_complete"] is False
        assert shown["last_scan_error"] == NO_DNS_SERVER
        assert len(dns_responder.arrivals) == len(live)  # no DNS packet in scan 2
        assert all(o.alive for o in (await observations(subnet["id"])).values())

        resolv.write_text("nameserver 127.0.0.1\n")
        names, shown = await scan_once(3)
        assert names == {ip: f"new{ip[-1]}.home.arpa" for ip in live}
        assert shown["last_scan_complete"] is True and shown["last_scan_error"] is None
        assert len(dns_responder.arrivals) == 2 * len(live)
    finally:
        transport.close()
        for key in keys.values():
            ANSWERS.pop(key, None)


async def test_lookups_that_send_nothing_keep_the_stored_names_at_one_packet_a_second(owner):
    """Finding 1 of the briefed round on PR #96, through the real routes. A /30, two
    live addresses, three DNS servers, MAX_PPS=1. The first scan stores names from a
    loopback responder. In the second every send fails (port 0, refused by the kernel
    before anything leaves), and the two lookups share one packet a second, so each
    runs out its own 2 s with nothing sent while the job still has time: that is no
    lookup, the scan is incomplete, and the names survive. The clock runs ten times
    fast."""
    from test_probe import ANSWERS, responder

    from tlg_ipam_agent.probe import DnsResolver, no_query_sent

    live = ["10.0.0.1", "10.0.0.2"]
    keys = {ip: f"{ip[-1]}.0.0.10.in-addr.arpa." for ip in live}
    for ip in live:
        ANSWERS[keys[ip]] = f"saved{ip[-1]}.home.arpa."
    transport, dns_responder, port = await responder()
    try:
        agent_row, subnet, target = await setup(
            owner, cidr="10.0.0.0/30", dns_servers=["127.0.0.1", "127.0.0.2", "127.0.0.3"]
        )
        agent = make_agent(
            agent_row["token"],
            prober=FakeProber(alive=live),
            resolver=DnsResolver(port=port),
            interfaces=[{"name": "eth0", "cidr": "10.0.0.2/30", "mac": None}],
            clock=fast_clock(),
            max_pps=1,
        )
        await agent.checkin_once()
        await run_queued(agent)
        assert await _names(subnet) == {ip: f"saved{ip[-1]}.home.arpa" for ip in live}
        assert (await target_of(owner, target["id"]))["last_scan_complete"] is True
        assert len(dns_responder.arrivals) == len(live)

        await _ok(await owner.post(f"/api/v1/subnets/{subnet['id']}/scan"), 202)
        await past_the_checkin_gap(agent_row["id"])
        agent.resolver = DnsResolver(port=0)
        await agent.checkin_once()
        await run_queued(agent)
        shown = await target_of(owner, target["id"])
        assert shown["last_scan_complete"] is False
        assert shown["last_scan_error"] == no_query_sent("OSError")
        assert await _names(subnet) == {ip: f"saved{ip[-1]}.home.arpa" for ip in live}
        assert len(dns_responder.arrivals) == len(live)
    finally:
        transport.close()
        for key in keys.values():
            ANSWERS.pop(key, None)
