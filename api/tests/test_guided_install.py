"""What the Agents page's guided install reads from the server (#23; v0.2 spec §5.6, §5.8).

- `update_available` and `protocol_mismatch` on every agent answer: computed on read by
  `agents.agent_out` from the version the agent last reported and the `last_error`
  check-in stored, never stored themselves.
- `GET /scan-targets/suggestions`: the subnets an agent could scan, for "Scan *subnet*
  with this agent?". Read-only, REST only, and two statements whatever the data holds.
"""

import inspect
from contextlib import contextmanager

import pytest
from sqlalchemy import event, func, select, update

from app import mcp_server
from app.config import Settings
from app.database import SessionLocal, engine
from app.models import Agent, Change
from app.services import agents as agents_svc
from app.services import jobs

from .conftest import make_section, make_subnet, store_cidr_as
from .test_checkin import (  # noqa: F401 (agent_api is a fixture)
    ROUTED,
    agent_api,
    checkin,
    jobs_for,
    lab,
    make_agent,
    make_target,
)

# --- Version flags ---


@pytest.mark.parametrize(
    "agent, server, expected",
    [
        ("0.1.0", "0.2.0", True),
        ("0.2.0.dev0", "0.2.0", True),  # a development build is older than its release
        ("0.2.0rc1", "0.2.0", True),
        ("0.2.0", "0.2.0", False),
        ("v0.2.0", "0.2.0", False),  # PEP 440 takes the leading v
        ("0.3.0", "0.2.0", False),  # newer is not older than expected
        ("0.10.0", "0.9.0", False),  # compared as versions, never as strings
        (None, "0.2.0", None),  # never reported
        ("", "0.2.0", None),
        ("not a version", "0.2.0", None),  # agent text that is not a version
        ("0.2.0", "custom-build", None),  # a server version that is not one either
    ],
)
def test_update_available_compares_releases(agent, server, expected):
    assert agents_svc.update_available(agent, server) is expected


def test_update_available_is_judged_against_the_servers_own_version(monkeypatch):
    monkeypatch.setattr(Settings, "version", "0.3.0")
    assert agents_svc.update_available("0.2.0") is True
    monkeypatch.setattr(Settings, "version", "0.2.0")
    assert agents_svc.update_available("0.2.0") is False


async def agent_answer(client, agent_id) -> dict:
    res = await client.get(f"/api/v1/agents/{agent_id}")
    assert res.status_code == 200, res.text
    one = res.json()["data"]
    listed = (await client.get("/api/v1/agents")).json()["data"]
    # the list answer is built the same way
    assert [a for a in listed if a["id"] == agent_id] == [one]
    return one


async def test_an_agent_answer_flags_an_older_release(client, agent_api, monkeypatch):
    monkeypatch.setattr(Settings, "version", "0.3.0")
    agent, token = await make_agent(client)
    # enrolment reports no version here, so nothing is known yet
    answer = await agent_answer(client, agent["id"])
    assert answer["update_available"] is None
    assert answer["protocol_mismatch"] is False

    await jobs_for(agent_api, token, version="0.2.0")
    assert (await agent_answer(client, agent["id"]))["update_available"] is True
    await jobs_for(agent_api, token, version="0.3.0")
    assert (await agent_answer(client, agent["id"]))["update_available"] is False


async def test_create_and_re_enrol_answers_carry_the_flags(client):
    res = await client.post("/api/v1/agents", json={"name": "fresh"})
    assert res.status_code == 201, res.text
    created = res.json()["data"]
    assert created["update_available"] is None and created["protocol_mismatch"] is False
    res = await client.post(f"/api/v1/agents/{created['id']}/re-enrol")
    assert res.status_code == 201, res.text
    assert {"update_available", "protocol_mismatch", "enrol_token"} <= set(res.json()["data"])


async def test_a_protocol_refusal_is_flagged_until_an_accepted_check_in(client, agent_api):
    agent, token = await make_agent(client)
    assert (await checkin(agent_api, token, protocol=2)).status_code == 409
    answer = await agent_answer(client, agent["id"])
    assert answer["protocol_mismatch"] is True
    assert answer["last_error"].startswith(agents_svc.PROTOCOL_MISMATCH_PREFIX)

    # a body refused for another reason is not a protocol mismatch
    assert (await checkin(agent_api, token, hostname="")).status_code == 422
    answer = await agent_answer(client, agent["id"])
    assert answer["last_error"] and answer["protocol_mismatch"] is False

    assert (await checkin(agent_api, token, protocol="1")).status_code == 409
    assert (await agent_answer(client, agent["id"]))["protocol_mismatch"] is True
    await jobs_for(agent_api, token)
    assert (await agent_answer(client, agent["id"]))["protocol_mismatch"] is False


def test_every_protocol_refusal_starts_with_the_prefix_the_flag_reads():
    for body in ({"protocol": 2}, {"protocol": "1"}, {}, {"protocol": 10**30}):
        assert jobs._protocol_error(body).startswith(agents_svc.PROTOCOL_MISMATCH_PREFIX)


# --- Suggestions ---


async def suggestions(client) -> list[dict]:
    res = await client.get("/api/v1/scan-targets/suggestions")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] == len(body["data"])
    return body["data"]


def offer(agent, subnet, cidr=None) -> dict:
    return {
        "agent_id": agent["id"],
        "agent_name": agent["name"],
        "subnet_id": subnet["id"],
        "subnet_name": subnet["name"],
        "subnet_cidr": cidr or subnet["cidr"],
    }


async def test_an_agent_is_offered_the_subnets_on_its_link_after_its_first_check_in(
    client, agent_api
):
    agent, token = await make_agent(client)
    lab_subnet = await lab(client)  # 10.0.0.0/24, on the link of 10.0.0.5/24
    # inside that link too, in another section (subnets of one section never overlap)
    other = await make_section(client, name="Other")
    narrow = await make_subnet(client, other["id"], "10.0.0.128/25", name="inner")
    await lab(client, "10.0.1.0/24", name="elsewhere")  # not on the link

    # before its first check-in it has reported no interface
    assert await suggestions(client) == []
    await jobs_for(agent_api, token)
    assert await suggestions(client) == [offer(agent, narrow), offer(agent, lab_subnet)]


async def test_accepting_is_creating_the_target_with_the_defaults(client, agent_api):
    agent, token = await make_agent(client)
    subnet = await lab(client)
    await jobs_for(agent_api, token)
    [suggested] = await suggestions(client)

    res = await client.post(
        "/api/v1/scan-targets",
        json={"subnet_id": suggested["subnet_id"], "agent_id": suggested["agent_id"]},
    )
    assert res.status_code == 201, res.text
    target = res.json()["data"]
    assert target["methods"] == ["icmp", "arp", "rdns"]
    assert target["scan_dhcp_pool"] is False
    assert target["subnet_id"] == subnet["id"]
    # a subnet with a target is not offered again, to this agent or another
    assert await suggestions(client) == []


async def test_no_offer_from_a_routed_disabled_or_unenrolled_agent(client, agent_api):
    await lab(client)
    routed, routed_token = await make_agent(client, name="routed")
    await jobs_for(agent_api, routed_token, **ROUTED)
    off, off_token = await make_agent(client, name="off")
    await jobs_for(agent_api, off_token)
    res = await client.put(f"/api/v1/agents/{off['id']}", json={"enabled": False})
    assert res.status_code == 200, res.text
    pending = await client.post("/api/v1/agents", json={"name": "pending"})
    assert pending.status_code == 201
    assert await suggestions(client) == []


async def test_no_offer_for_a_subnet_too_large_to_scan(client, agent_api):
    agent, token = await make_agent(client)
    await lab(client, "10.0.0.0/19", name="wide")
    await jobs_for(
        agent_api, token, interfaces=[{"name": "eth0", "cidr": "10.0.0.5/16", "mac": None}]
    )
    assert await suggestions(client) == []
    allowed = await lab(client, "10.1.0.0/20", name="widest-allowed")
    await jobs_for(
        agent_api, token, interfaces=[{"name": "eth0", "cidr": "10.1.0.5/16", "mac": None}]
    )
    assert await suggestions(client) == [offer(agent, allowed)]


async def test_an_offer_names_the_subnet_in_its_one_spelling(client, agent_api):
    agent, token = await make_agent(client)
    subnet = await lab(client)
    await store_cidr_as(subnet["id"], "10.0.0.0/255.255.255.0")
    await jobs_for(agent_api, token)
    assert await suggestions(client) == [offer(agent, subnet, "10.0.0.0/24")]


async def test_a_refused_check_in_keeps_the_interfaces_it_had(client, agent_api):
    agent, token = await make_agent(client)
    subnet = await lab(client)
    await jobs_for(agent_api, token)
    assert (await checkin(agent_api, token, protocol=2)).status_code == 409
    assert await suggestions(client) == [offer(agent, subnet)]


@contextmanager
def statements():
    seen = []

    def count(conn, cursor, statement, *args):
        seen.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", count)
    try:
        yield seen
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count)


@pytest.mark.parametrize("count", [1, 12])
async def test_suggestions_cost_two_statements_and_read_no_notes(client, agent_api, count):
    from app.services import scans

    for i in range(count):
        await lab(client, f"10.0.{i}.0/24", name=f"lab-{i}", notes="x" * 1000)
        agent, token = await make_agent(client, name=f"agent-{i}")
        await jobs_for(
            agent_api, token, interfaces=[{"name": "eth0", "cidr": f"10.0.{i}.5/24", "mac": None}]
        )
    async with SessionLocal() as session:
        with statements() as seen:
            found = await scans.suggestions(session)
    assert len(found) == count
    assert len(seen) == 2
    assert not any("notes" in s for s in seen)


async def test_suggestions_write_nothing(client, agent_api):
    agent, token = await make_agent(client)
    await lab(client)
    await jobs_for(agent_api, token)
    async with SessionLocal() as session:
        before = await session.scalar(select(func.count()).select_from(Change))
    assert len(await suggestions(client)) == 1
    async with SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(Change)) == before


async def test_suggestions_need_the_owner_key(anon_client):
    assert (await anon_client.get("/api/v1/scan-targets/suggestions")).status_code == 401


async def test_an_agent_reporting_no_interface_is_offered_nothing(client, agent_api):
    agent, token = await make_agent(client)
    await lab(client)
    async with SessionLocal() as session:
        await session.execute(update(Agent).where(Agent.id == agent["id"]).values(interfaces=[]))
        await session.commit()
    assert await suggestions(client) == []


def test_no_mcp_tool_serves_suggestions_or_the_flags():
    """Agents and scan targets stay off MCP (spec §7.2, invariant 2)."""
    source = inspect.getsource(mcp_server)
    assert "suggestions" not in source
    assert "agent_out" not in source and "update_available" not in source
