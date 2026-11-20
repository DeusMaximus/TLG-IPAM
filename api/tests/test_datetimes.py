"""Every datetime the API hands out carries a UTC offset (brief §4.3), whether the row
was just written and is still in memory or was read back from SQLite, which returns
datetimes without their zone (#65)."""

import inspect
import typing
from datetime import datetime

from fastmcp import Client
from pydantic import BaseModel

from app import schemas
from app.mcp_server import mcp

from .conftest import make_host, make_section, make_subnet
from .test_checkin import make_agent, make_target

UTC_SUFFIX = "+00:00"


def utc_form(value: str) -> str:
    """Asserts the value is ISO 8601 in UTC with an explicit offset, and returns it."""
    assert isinstance(value, str), value
    assert value.endswith(UTC_SUFFIX), value
    assert datetime.fromisoformat(value).utcoffset().total_seconds() == 0
    return value


async def test_a_host_datetime_has_one_form_on_write_and_on_read(client):
    section = await make_section(client)
    subnet = await make_subnet(client, section["id"])
    # one sent without an offset (taken as UTC) and one sent with another offset
    naive = await make_host(client, subnet["id"], "10.0.0.5", last_seen="2026-11-16T06:38:44")
    offset = await make_host(
        client, subnet["id"], "10.0.0.6", last_seen="2026-11-16T08:38:44.5+02:00"
    )
    assert utc_form(naive["last_seen"]) == "2026-11-16T06:38:44+00:00"
    assert utc_form(offset["last_seen"]) == "2026-11-16T06:38:44.500000+00:00"
    for written in (naive, offset):
        read = (await client.get(f"/api/v1/hosts/{written['id']}")).json()["data"]
        for field in ("last_seen", "created_at", "updated_at"):
            assert utc_form(read[field]) == utc_form(written[field])
    listed = (await client.get("/api/v1/hosts")).json()["data"]
    assert [h["last_seen"] for h in listed] == [naive["last_seen"], offset["last_seen"]]


async def test_a_change_timestamp_and_its_snapshots_carry_an_offset(client):
    section = await make_section(client)
    subnet = await make_subnet(client, section["id"])
    host = await make_host(client, subnet["id"], "10.0.0.5", hostname="pbs-01")
    # the update's `before` is the row as read back, its `after` the row as written
    res = await client.put(f"/api/v1/hosts/{host['id']}", json={"hostname": "pbs-02"})
    assert res.status_code == 200, res.text
    changes = (await client.get("/api/v1/changes")).json()["data"]
    assert {c["action"] for c in changes} >= {"create", "update"}
    for change in changes:
        utc_form(change["ts"])
    update = next(c for c in changes if c["entity_type"] == "host" and c["action"] == "update")
    assert utc_form(update["before"]["created_at"]) == utc_form(update["after"]["created_at"])
    assert utc_form(update["before"]["created_at"]) == host["created_at"]


async def test_agent_and_token_datetimes_have_one_form_on_write_and_on_read(client):
    created, _ = await make_agent(client)
    revoked = (await client.post(f"/api/v1/agents/{created['id']}/revoke")).json()["data"]
    read = (await client.get(f"/api/v1/agents/{created['id']}")).json()["data"]
    [listed] = (await client.get("/api/v1/agents")).json()["data"]
    for agent in (revoked, read, listed):
        assert utc_form(agent["created_at"]) == utc_form(created["created_at"])
        [token] = agent["tokens"]
        assert utc_form(token["created_at"]) == utc_form(created["tokens"][0]["created_at"])
        assert utc_form(token["revoked_at"]) == utc_form(revoked["tokens"][0]["revoked_at"])


async def test_scan_target_datetimes_have_one_form_on_write_and_on_read(client):
    section = await make_section(client)
    subnet = await make_subnet(client, section["id"])
    agent, _ = await make_agent(client)
    await client.put(f"/api/v1/agents/{agent['id']}", json={"enabled": True})
    target = await make_target(client, subnet["id"], agent["id"])
    asked = await client.post(f"/api/v1/subnets/{subnet['id']}/scan")
    assert asked.status_code == 202, asked.text
    asked = asked.json()["data"]
    read = (await client.get(f"/api/v1/scan-targets/{target['id']}")).json()["data"]
    [listed] = (await client.get("/api/v1/scan-targets")).json()["data"]
    for later in (asked, read, listed):
        assert utc_form(later["created_at"]) == utc_form(target["created_at"])
    assert utc_form(read["scan_requested_at"]) == utc_form(asked["scan_requested_at"])


async def test_datetimes_an_mcp_tool_returns_carry_an_offset(client):
    section = await make_section(client)
    subnet = await make_subnet(client, section["id"])
    host = await make_host(client, subnet["id"], "10.0.0.5", last_seen="2026-11-16T06:38:44")
    async with Client(mcp) as mcp_client:
        got = (await mcp_client.call_tool("get_host", {"host_id": host["id"]})).data["host"]
        listed = (await mcp_client.call_tool("list_hosts", {})).data["hosts"]
        changes = (await mcp_client.call_tool("list_changes", {})).data["changes"]
    for field in ("last_seen", "created_at", "updated_at"):
        assert utc_form(got[field]) == host[field]
        assert utc_form(listed[0][field]) == host[field]
    assert changes
    for change in changes:
        utc_form(change["ts"])
    [created] = [c for c in changes if c["entity_type"] == "host"]
    assert utc_form(created["after"]["created_at"]) == host["created_at"]


def _mentions_datetime(annotation) -> bool:
    return annotation is datetime or any(
        _mentions_datetime(arg) for arg in typing.get_args(annotation)
    )


def test_every_datetime_field_in_the_schemas_is_serialised_with_an_offset():
    """The rule lives in one annotation (`schemas.UtcDatetime`); a field declared with a
    bare `datetime` would be serialised without an offset once read back. Each datetime
    field of each response model in `schemas.py` is given a value as SQLite returns it."""
    stored = datetime(2026, 11, 16, 6, 38, 44, 765279)
    checked = set()
    for _, model in inspect.getmembers(schemas, inspect.isclass):
        if not issubclass(model, BaseModel) or model.__module__ != schemas.__name__:
            continue
        # response models; request models keep plain datetimes for the ORM
        if not any(base.__name__.endswith("Out") for base in model.__mro__):
            continue
        for name, field in model.model_fields.items():
            if not _mentions_datetime(field.annotation):
                continue
            instance = model.model_construct(**{name: stored})
            for mode in ("json", "python"):
                value = instance.model_dump(mode=mode, include={name})[name]
                assert value == "2026-11-16T06:38:44.765279+00:00", (model.__name__, name)
            checked.add(f"{model.__name__}.{name}")
    # the walk reached the fields it is meant to check
    assert {
        "HostOut.last_seen",
        "ChangeOut.ts",
        "AgentOut.created_at",
        "TokenOut.revoked_at",
        "ScanTargetOut.last_scan_at",
    } <= checked
