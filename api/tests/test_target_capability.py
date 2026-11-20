"""What a scan target's agent can run for it, and the scope's integer bounds (#68;
v0.2 spec §5.2, §5.8).

`runnable_methods` on the scan-target answer is `jobs.job_methods` applied to the
interfaces the agent last reported: computed on read, by the rule check-in issues jobs
by, and never stored. The scope's ranges carry their bounds as integers so the UI can
place a host by its `ip_int` without parsing an address.
"""

import inspect
import ipaddress

from sqlalchemy import func, select

from app import mcp_server
from app.database import SessionLocal
from app.models import Change, ScanTarget
from app.services import jobs

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


async def test_an_agent_on_the_link_can_run_every_method_but_fdns(client, agent_api):
    agent, token = await make_agent(client)
    subnet = await lab(client)
    target = await make_target(
        client, subnet["id"], agent["id"], methods=["icmp", "arp", "rdns", "fdns"]
    )
    [job] = await jobs_for(agent_api, token)
    answer = await target_answer(client, target["id"])
    # the issued job's methods and the answer's agree: one rule
    assert answer["runnable_methods"] == job["methods"] == ["icmp", "arp", "rdns"]


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
    assert "target_out" not in source
    assert "runnable_methods" not in source
