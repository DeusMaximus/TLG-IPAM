"""The agent's client and loop against the server's real check-in and results routes,
in process (acceptance for #13). The prober and resolver are still fakes: no packet
is sent. The server's check-in gap is its real one here (10 s), unlike in the
server's own tests."""

import random
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from app.database import SessionLocal, engine
from app.main import app
from app.models import Base, Observation
from app.services.device_types import seed_defaults
from sqlalchemy import select, update

from conftest import FakeClock, FakeProber, FakeResolver, make_config
from tlg_ipam_agent import loop as agent_loop
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


def make_agent(token, *, prober, resolver=None, interfaces=LINK, **config):
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
        interfaces=lambda: interfaces,
        clock=FakeClock(),
        rng=random.Random(7),
    )


async def run_queued(agent: Agent) -> None:
    while not agent.queue.empty():
        await agent.run_job(agent.queue.get_nowait())


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


async def test_an_arp_job_from_this_release_marks_nothing_dead(owner):
    """`arp` is issued (the agent is on the link) but this release cannot run it: the
    result set is incomplete, so an address that went quiet is not marked dead."""
    agent_row, subnet, target = await setup(owner, methods=("icmp", "arp"))
    prober = FakeProber(alive=["10.0.0.2", "10.0.0.3"])
    agent = make_agent(agent_row["token"], prober=prober)
    await agent.checkin_once()
    await run_queued(agent)
    first = await target_of(owner, target["id"])
    assert first["job_methods"] is None and first["last_scan_complete"] is False
    assert "not run by this agent release: arp" in first["last_scan_error"]

    # 10.0.0.3 goes quiet; a scan is requested and the next job runs
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
