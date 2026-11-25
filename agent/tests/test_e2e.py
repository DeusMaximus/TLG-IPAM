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
    """An agent, enrolled, a subnet, and a target assigning the subnet to the agent.
    The agent's `token` is the agent token its enrolment token was exchanged for."""
    agent = await _ok(await owner.post("/api/v1/agents", json={"name": "vlan10"}), 201)
    enrolled = await _ok(
        await owner.post(
            "/api/v1/agent/enrol",
            json={},
            headers={"Authorization": f"Bearer {agent['enrol_token']}"},
        )
    )
    agent["token"] = enrolled["agent_token"]
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


@pytest.mark.parametrize("failure", ["unopenable", "cut-off"])
async def test_an_arp_sweep_that_fails_after_a_whole_icmp_sweep_marks_nothing_dead(owner, failure):
    """#100, round 1 of the briefed review, finding 2, through the real routes: the
    ICMP sweep finishes, the ARP sweep does not (its socket cannot be opened, or the
    deadline cuts it off), so `liveness_complete` is false and an address stored by
    an earlier scan, silent in this one, stays alive."""
    from tlg_ipam_agent.probe import ProbeError

    agent_row, subnet, target = await setup(owner, methods=("icmp", "arp"))
    prober = FakeProber(alive=["10.0.0.2", "10.0.0.3"])
    arp = FakeArp({"10.0.0.2": "02:00:00:00:00:02", "10.0.0.3": "02:00:00:00:00:03"})
    agent = make_agent(agent_row["token"], prober=prober, arp=arp, max_pps=10)
    await agent.checkin_once()
    await run_queued(agent)
    assert (await target_of(owner, target["id"]))["last_scan_complete"] is True
    assert (await observations(subnet["id"]))["10.0.0.3"].alive is True

    prober.alive = {ipaddress.ip_address("10.0.0.2")}
    del arp.hosts[ipaddress.ip_address("10.0.0.3")]
    if failure == "unopenable":
        arp.fail_open = ProbeError("ARP socket unavailable (OSError)")
    await _ok(await owner.post(f"/api/v1/subnets/{subnet['id']}/scan"), 202)
    await past_the_checkin_gap(agent_row["id"])
    await agent.checkin_once()
    # at 10 a second the 14 echo requests take 1.4 s; a 2 s job cuts the ARP sweep
    await run_queued(agent, timeout_s=None if failure == "unopenable" else 2)
    shown = await target_of(owner, target["id"])
    assert shown["last_scan_complete"] is False
    assert ("ARP socket unavailable" if failure == "unopenable" else "during the ARP sweep") in shown["last_scan_error"]
    seen = await observations(subnet["id"])
    assert seen["10.0.0.2"].alive is True and seen["10.0.0.3"].alive is True


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
    monkeypatch.setattr(agent_loop, "PROTOCOL_VERSION", agent_loop.PROTOCOL_VERSION + 1)
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


class StalledProber(FakeProber):
    """A FakeProber that, once `stall_s` is set, holds the answer to every sixth ping
    (the last of a /29's sweep) for that much real time: the scheduling delay of a
    loaded machine, made to order. It sends nothing."""

    stall_s = 0.0
    stalled_at: list[int] = []  # the ping (its number among all it was asked) each stall followed

    async def ping(self, address, timeout):
        answer = await super().ping(address, timeout)
        if self.stall_s and len(self.sent) % 6 == 0:
            self.stalled_at = [*self.stalled_at, len(self.sent)]
            await asyncio.sleep(self.stall_s)
        return answer


async def _named_subnet(owner, methods=("icmp", "rdns"), *, clock=None, prober=None):
    """A /29 whose four live addresses have OLD names stored by a complete scan, and an
    agent at MAX_PPS=1 (one packet a second on its clock, the ten-times-fast one unless
    the test passes its own), ready for the next. The four answer ARP too, and the
    agent's interface (10.0.0.5) has a MAC."""
    agent_row, subnet, target = await setup(owner, cidr="10.0.0.0/29", methods=methods)
    resolver = FakeResolver(OLD)
    agent = make_agent(
        agent_row["token"],
        prober=prober or FakeProber(alive=LIVE),
        resolver=resolver,
        arp=FakeArp({ip: f"02:00:00:00:00:0{ip[-1]}" for ip in LIVE}),
        interfaces=[{"name": "eth0", "cidr": "10.0.0.5/29", "mac": "02:00:00:00:00:05"}],
        clock=clock or fast_clock(),
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


@pytest.mark.parametrize("stall_s", [0.0, 0.4], ids=["no-stall", "stalled-after-the-sixth-ping"])
async def test_a_scan_cut_off_during_the_ptr_lookups_keeps_the_names_it_did_not_ask(owner, stall_s):
    """Six pings take six seconds at 1 pps; of an 8 s job, the lookups get two slots.
    The two asked get their new names; the two never asked keep their old ones.

    The clock is the test's: FakeClock moves only when a wait is slept, so which slots
    fall before the deadline is arithmetic (pings at 0..5 s, lookups at 6 s and 7 s, the
    third lookup's slot at 8 s, the deadline, which leaves it no time to ask, and the
    fourth's at 9 s) and no real time is spare or short. The
    test used a clock that runs ten times fast, which left 0.8 s of real time for the
    two lookups to be admitted; a 150 ms scheduling delay after the sixth ping left one
    asked and 250 ms none (#118). `stall_s` is that delay, made to order: the answer
    to the sixth ping is held in real time, and the result must not change. The test
    asserts that the stall happened, once, after this scan's sixth ping (the first scan
    runs without it), so a change to the number of pings, or to which one is held,
    cannot turn the case into a second `no-stall` unnoticed."""
    clock = FakeClock()
    prober = StalledProber(alive=LIVE)
    agent, resolver, subnet, target = await _named_subnet(owner, clock=clock, prober=prober)
    # the next scan is due an hour on: its first packet has a slot at once, not after
    # what is left of the gap since the last packet of the scan before
    clock.t += 3600
    seeded = len(prober.sent)  # the pings of the scan that stored the old names
    prober.stall_s = stall_s
    await agent.checkin_once()
    await run_queued(agent, timeout_s=8)
    # the stall happened, and after the second scan's sixth ping, the last of its sweep
    assert prober.stalled_at == ([seeded + 6] if stall_s else [])
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
    fast.

    What it assumes (#107; it failed once in 31 full runs under load, when the pacer
    could still catch up, and has not since): the first scan's two loopback answers
    arrive within the lookup's own 2 s, which dnspython waits out on the real clock
    whatever the scale, so a stall of the whole process for that long (after the
    first query is sent) ends a lookup without an answer: the first scan is complete,
    with no error, and the expected stored names are missing, which fails this test
    at its first check. The second scan does not depend on
    timing: a lookup whose next slot falls inside its own 2 s sends nothing either
    (port 0 is refused) and goes on to its next server, and every order of the
    slots ends in the same error."""
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


# --- a lookup no server answered usably keeps the name; the dead are still marked (#100) ---


@pytest.mark.parametrize(
    "action, outcome",
    [
        ("silent", "kept"),
        ("servfail", "kept"),
        ("refused", "kept"),
        ("malformed", "kept"),
        ("cname-referral", "kept"),
        ("cname-only", "kept"),
        ("nxdomain", "cleared"),
        ("nodata", "cleared"),
        ("empty", "cleared"),
        ("cname-nodata", "cleared"),
        # #144: records that do not answer the question are no answer
        ("foreign-soa", "kept"),
        ("cname-foreign-soa", "kept"),
        ("cname-origin-soa", "kept"),
        ("foreign-ptr", "kept"),
        ("foreign-ptr-soa", "kept"),
        # and the answers that do are read as before
        ("cname-out-nodata", "cleared"),
        ("cname-chain-nodata", "cleared"),
        ("cname-ptr", "replaced"),
        ("cname-chain-ptr", "replaced"),
    ],
)
async def test_what_the_responder_answers_decides_the_names_and_never_the_dead(owner, action, outcome):
    """Acceptance for #100 (option 4) and #144 through the real routes, the real
    resolver and a DNS responder on loopback. A first scan stores the names of three
    live addresses. In the second, one of them has left, and the responder gives
    `action` to every query: silence, SERVFAIL, REFUSED, malformed bytes, a CNAME
    followed by a delegation (RFC 2317; round 1 of the briefed review, finding 1), a
    CNAME left unresolved, an SOA for a zone that does not hold the name the answer is
    about (an unrelated zone's, or the question's own zone's beside a CNAME whose
    target is outside it) or a PTR record at an unrelated owner (#144) leave the
    stored names in place; NXDOMAIN, or a NOERROR whose authority holds the SOA of the
    zone that holds that name (the CNAME chain's end, when there is one) or that
    carries no record at all, clears them; a PTR record at the chain's end replaces
    them. Either way the scan is complete, its liveness sweep whole, and the departed
    address is marked dead."""
    from test_probe import ANSWERS, responder

    from tlg_ipam_agent.probe import DnsResolver

    live = ["10.0.0.1", "10.0.0.2", "10.0.0.3"]
    keys = {ip: f"{ip[-1]}.0.0.10.in-addr.arpa." for ip in live}
    for ip in live:
        ANSWERS[keys[ip]] = f"kept{ip[-1]}.home.arpa."
    transport, dns_responder, port = await responder()
    try:
        agent_row, subnet, target = await setup(owner, cidr="10.0.0.0/29", dns_servers=["127.0.0.1"])
        prober = FakeProber(alive=live)
        agent = make_agent(
            agent_row["token"],
            prober=prober,
            resolver=DnsResolver(port=port),
            interfaces=[{"name": "eth0", "cidr": "10.0.0.5/29", "mac": None}],
        )
        await agent.checkin_once()
        await run_queued(agent)
        stored = {ip: f"kept{ip[-1]}.home.arpa" for ip in live}
        assert await _names(subnet) == stored
        assert len(dns_responder.arrivals) == 3

        await _ok(await owner.post(f"/api/v1/subnets/{subnet['id']}/scan"), 202)
        await past_the_checkin_gap(agent_row["id"])
        prober.alive = {ipaddress.ip_address(ip) for ip in live[:2]}  # 10.0.0.3 has left
        dns_responder.script = [action]
        await agent.checkin_once()
        await run_queued(agent)

        assert len(dns_responder.arrivals) == 3 + 2  # one query for each live address
        seen = await observations(subnet["id"])
        names = {ip: seen[ip].rdns_name for ip in live[:2]}
        expected = {"kept": stored, "cleared": dict.fromkeys(live), "replaced": dict.fromkeys(live, "nas.home.arpa")}
        assert names == {ip: expected[outcome][ip] for ip in live[:2]}
        assert seen["10.0.0.1"].alive and seen["10.0.0.2"].alive
        assert seen["10.0.0.3"].alive is False
        shown = await target_of(owner, target["id"])
        assert shown["last_scan_complete"] is True
        assert shown["last_scan_error"] == (scan.no_answer(2, 2) if outcome == "kept" else None)
    finally:
        transport.close()
        for key in keys.values():
            ANSWERS.pop(key, None)


async def test_the_owner_is_told_how_many_replies_the_agents_limits_refused(owner):
    """#164 through the real routes, the real resolver and a DNS responder on loopback.
    A first scan stores four names. In the second, the one server answers two
    addresses with a reply over the payload the query advertised, a third with one
    whose header claims a record over the budget, and the fourth with its name. The
    target's error counts both refusals, each by its own count (two and one, so a
    count given the other's words fails here), in lookups, with no reply text, name
    or address, as the agent sent it; the three names stay, the fourth is replaced,
    and the scan is complete."""
    from test_probe import ANSWERS, responder

    from tlg_ipam_agent.probe import DnsResolver

    live = ["10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4"]
    keys = {ip: f"{ip[-1]}.0.0.10.in-addr.arpa." for ip in live}
    for ip in live:
        ANSWERS[keys[ip]] = f"kept{ip[-1]}.home.arpa."
    transport, dns_responder, port = await responder()
    try:
        agent_row, subnet, target = await setup(owner, cidr="10.0.0.0/29", dns_servers=["127.0.0.1"])
        agent = make_agent(
            agent_row["token"],
            prober=FakeProber(alive=live),
            resolver=DnsResolver(port=port),
            interfaces=[{"name": "eth0", "cidr": "10.0.0.5/29", "mac": None}],
        )
        await agent.checkin_once()
        await run_queued(agent)
        stored = {ip: f"kept{ip[-1]}.home.arpa" for ip in live}
        assert await _names(subnet) == stored

        await _ok(await owner.post(f"/api/v1/subnets/{subnet['id']}/scan"), 202)
        await past_the_checkin_gap(agent_row["id"])
        ANSWERS[keys["10.0.0.4"]] = "new4.home.arpa."
        dns_responder.by_name = {
            keys["10.0.0.1"]: "oversize", keys["10.0.0.2"]: "oversize", keys["10.0.0.3"]: "over-budget",
        }
        await agent.checkin_once()
        await run_queued(agent)

        assert len(dns_responder.arrivals) == 4 + 4
        assert await _names(subnet) == {**stored, "10.0.0.4": "new4.home.arpa"}
        shown = await target_of(owner, target["id"])
        assert shown["last_scan_complete"] is True
        assert shown["last_scan_error"] == (
            "rdns: no DNS server gave a usable answer for 3 of 4 addresses, names kept; the "
            "agent refused a reply over 1232 bytes in 2 lookups, over 1 question or 32 records "
            "in 1 lookup"
        )
        assert not any(text in shown["last_scan_error"] for text in ("10.0.0", "in-addr", "home.arpa", "127.0.0.1"))
    finally:
        transport.close()
        for key in keys.values():
            ANSWERS.pop(key, None)


async def test_a_scan_whose_lookups_had_no_server_still_marks_the_departed_dead(owner, tmp_path):
    """#100: a scan with no DNS server to ask is incomplete and keeps every name (#78),
    and its liveness sweep, which completed, still marks the address that left dead."""
    from tlg_ipam_agent.probe import NO_DNS_SERVER

    live = ["10.0.0.1", "10.0.0.2"]
    agent_row, subnet, target = await setup(owner, cidr="10.0.0.0/29")
    prober = FakeProber(alive=live)
    resolver = FakeResolver({ip: f"n{ip[-1]}.home.arpa" for ip in live})
    agent = make_agent(
        agent_row["token"],
        prober=prober,
        resolver=resolver,
        interfaces=[{"name": "eth0", "cidr": "10.0.0.5/29", "mac": None}],
    )
    await agent.checkin_once()
    await run_queued(agent)
    await _ok(await owner.post(f"/api/v1/subnets/{subnet['id']}/scan"), 202)
    await past_the_checkin_gap(agent_row["id"])
    prober.alive = {ipaddress.ip_address("10.0.0.1")}
    resolver.host = ()
    await agent.checkin_once()
    await run_queued(agent)
    seen = await observations(subnet["id"])
    assert {ip: seen[ip].rdns_name for ip in live} == {ip: f"n{ip[-1]}.home.arpa" for ip in live}
    assert seen["10.0.0.1"].alive is True and seen["10.0.0.2"].alive is False
    shown = await target_of(owner, target["id"])
    assert shown["last_scan_complete"] is False and shown["last_scan_error"] == NO_DNS_SERVER


@pytest.mark.parametrize("unusable", ["cname-referral", "cname-foreign-soa", "cname-origin-soa", "foreign-ptr"])
async def test_a_delegated_reverse_zone_keeps_the_name_and_the_next_server_supplies_it(owner, unusable):
    """Round 1 of the briefed review, finding 1, through the real routes. The subnet
    lists its DNS server twice (the owner API takes IPv4 servers only, and loopback has
    one IPv4 address everywhere; `test_probe.py` asks two distinct servers). A first
    scan stores the name. In the second, the first query is answered with a CNAME and
    a delegation, and the lookup goes on to the next server, whose PTR record
    replaces the name; at `dfae349` it stopped there and cleared it. In a third, both
    answer that way, and the name stays. #144 runs the same scans with a first reply
    whose records do not answer the question: a CNAME beside an unrelated zone's SOA,
    or beside the question's own zone's SOA when its target is outside that zone, or a
    PTR record at an unrelated owner. Until #144 the first two cleared the name and
    the third replaced it with the unrelated one, and the next server was not asked."""
    from test_probe import ANSWERS, responder

    from tlg_ipam_agent.probe import DnsResolver

    key = "1.0.0.10.in-addr.arpa."
    ANSWERS[key] = "first.home.arpa."
    transport, dns_responder, port = await responder()
    try:
        agent_row, subnet, target = await setup(owner, cidr="10.0.0.0/29", dns_servers=["127.0.0.1", "127.0.0.1"])
        agent = make_agent(
            agent_row["token"],
            prober=FakeProber(alive=["10.0.0.1"]),
            resolver=DnsResolver(port=port),
            interfaces=[{"name": "eth0", "cidr": "10.0.0.5/29", "mac": None}],
        )

        async def scan_once(number):
            if number > 1:
                await past_the_request_cooldown(target["id"])
                await _ok(await owner.post(f"/api/v1/subnets/{subnet['id']}/scan"), 202)
                await past_the_checkin_gap(agent_row["id"])
            await agent.checkin_once()
            await run_queued(agent)
            return (await _names(subnet))["10.0.0.1"], await target_of(owner, target["id"])

        name, _ = await scan_once(1)
        assert name == "first.home.arpa" and len(dns_responder.arrivals) == 1

        dns_responder.script = [unusable, "answer"]
        ANSWERS[key] = "second.home.arpa."
        name, shown = await scan_once(2)
        assert name == "second.home.arpa" and len(dns_responder.arrivals) == 3
        assert shown["last_scan_complete"] is True and shown["last_scan_error"] is None

        dns_responder.script = [unusable]
        ANSWERS[key] = "third.home.arpa."
        name, shown = await scan_once(3)
        assert name == "second.home.arpa" and len(dns_responder.arrivals) == 5
        assert shown["last_scan_complete"] is True
        assert shown["last_scan_error"] == scan.no_answer(1, 1)
    finally:
        transport.close()
        ANSWERS.pop(key, None)


@pytest.mark.parametrize("raised", [False, True])
@pytest.mark.parametrize("secret_bytes", [32, 33])
async def test_raising_the_protocol_does_not_keep_a_new_token_form_from_spending_an_old_agents_enrolment(
    owner, monkeypatch, raised, secret_bytes
):
    """Why `agent_protocol` says raising PROTOCOL_VERSION is necessary but not enough
    when the issued form changes (the briefed review of #140). The enrolment body
    carries no protocol and the version is checked only at check-in, so a server that
    issues a longer secret answers this release's agent `200` at any protocol: the
    agent refuses the answer as not in the route's shape, and the enrolment token is
    spent. The form must stay what existing agents take until enrolment itself can
    tell them apart."""
    from app.agent_protocol import AGENT_TOKEN_FORM
    from app.models import Token
    from app.services import agents as server_agents
    from app.services import jobs as server_jobs

    from tlg_ipam_agent.client import Kind

    if raised:
        monkeypatch.setattr(server_jobs, "PROTOCOL_VERSION", server_jobs.PROTOCOL_VERSION + 1)
    monkeypatch.setattr(server_agents, "TOKEN_SECRET_BYTES", secret_bytes)
    agent = await _ok(await owner.post("/api/v1/agents", json={"name": "vlan10"}), 201)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://test",
        headers={"Authorization": f"Bearer {agent['enrol_token']}"},
    ) as http:
        answer = await AgentClient(http).enrol({})
        again = await http.post("/api/v1/agent/enrol", json={})
    async with SessionLocal() as session:
        [spent] = (await session.scalars(select(Token).where(Token.kind == "enrol"))).all()
        [issued] = (await session.scalars(select(Token).where(Token.kind == "agent"))).all()
    # either way the exchange took place and the enrolment token is used up
    assert answer.status == 200 and spent.used_at is not None and again.status_code == 403
    if secret_bytes == 32:
        assert answer.kind is Kind.OK and AGENT_TOKEN_FORM.fullmatch(answer.data["agent_token"])
    else:
        assert answer.kind is Kind.RETRY and answer.message == "HTTP 200 answer not in the expected shape"
        assert issued.revoked_at is None  # the server holds a token the agent never stored
