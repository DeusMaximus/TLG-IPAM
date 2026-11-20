"""Every datetime the API hands out carries a UTC offset (brief §4.3), whether the row
was just written and is still in memory or was read back from SQLite, which returns
datetimes without their zone (#65)."""

import inspect
import sys
import types
import typing
from datetime import datetime

from fastmcp import Client
from pydantic import AwareDatetime, BaseModel, FutureDatetime, NaiveDatetime, PastDatetime

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


# The request models of `schemas.py`: their datetimes go to the ORM through
# `model_dump()`, so they keep a plain `datetime`. Every other model there is a response
# model. A new model is walked as one until it is named here.
REQUEST_MODELS = {
    "DeviceTypeCreate",
    "DeviceTypeUpdate",
    "SectionCreate",
    "SectionUpdate",
    "_SubnetFields",
    "SubnetCreate",
    "SubnetUpdate",
    "ReservedRangeCreate",
    "ReservedRangeUpdate",
    "_HostFields",
    "HostCreate",
    "HostUpdate",
    "AllocateRequest",
    "BulkHostItem",
    "BulkImportRequest",
    "BulkDeleteRequest",
    "AgentCreate",
    "AgentUpdate",
    "ScanTargetCreate",
    "ScanTargetUpdate",
    "FindingKey",
    "FindingKeysRequest",
}

STORED = datetime(2026, 11, 16, 6, 38, 44, 765279)  # as SQLite returns it: no zone
# pydantic's constrained datetimes: each serialises the value it holds, as `datetime` does
DATETIME_TYPES = (datetime, AwareDatetime, NaiveDatetime, PastDatetime, FutureDatetime)
SERIALISED = "2026-11-16T06:38:44.765279+00:00"


def _models_defined_in(module) -> dict[str, type[BaseModel]]:
    return {
        name: model
        for name, model in inspect.getmembers(module, inspect.isclass)
        if issubclass(model, BaseModel) and model.__module__ == module.__name__
    }


def response_models(module) -> list[type[BaseModel]]:
    """Every model the module defines that is not a request model, whatever its name."""
    return [m for name, m in _models_defined_in(module).items() if name not in REQUEST_MODELS]


def _nested_models(annotation):
    """The models an annotation holds, at any depth: `X`, `list[X]`, `X | None`..."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        yield annotation
    for arg in typing.get_args(annotation):
        yield from _nested_models(arg)


def _sample(annotation):
    """A value of the annotation's shape whose datetime is `STORED`, and the dump it
    must give; None when the field holds no datetime of its own (a nested model's are
    checked on that model)."""
    origin, args = typing.get_origin(annotation), typing.get_args(annotation)
    if annotation in DATETIME_TYPES:
        return STORED, SERIALISED
    if origin is typing.Annotated:
        return _sample(args[0])
    if origin in (list, tuple, set, frozenset):
        inner = _sample(args[0]) if args else None
        return None if inner is None else ([inner[0]], [inner[1]])
    if origin is dict:
        inner = _sample(args[1]) if len(args) == 2 else None
        return None if inner is None else ({"k": inner[0]}, {"k": inner[1]})
    if origin in (typing.Union, types.UnionType):
        return next((s for s in map(_sample, args) if s is not None), None)
    return None


def walk(roots) -> tuple[set[str], list[str], set[type[BaseModel]]]:
    """Each datetime field of the roots and of every model their fields reach (nested,
    in a list, a dict or an optional), wherever that model is defined: each is given a
    value as SQLite returns it and dumped in both modes. Returns the fields checked,
    those that did not serialise with the offset, and the models walked."""
    checked, wrong, seen, queue = set(), [], set(), list(roots)
    while queue:
        model = queue.pop()
        if model in seen:
            continue
        seen.add(model)
        for name, field in model.model_fields.items():
            queue.extend(_nested_models(field.annotation))
            sample = _sample(field.annotation)
            if sample is None:
                continue
            value, expected = sample
            instance = model.model_construct(**{name: value})
            label = f"{model.__name__}.{name}"
            checked.add(label)
            for mode in ("json", "python"):
                if instance.model_dump(mode=mode, include={name})[name] != expected:
                    wrong.append(f"{label} ({mode})")
    return checked, wrong, seen


def test_every_datetime_field_in_the_schemas_is_serialised_with_an_offset():
    """The rule lives in one annotation (`schemas.UtcDatetime`); a field declared with a
    bare `datetime` would be serialised without an offset once read back. The walk
    starts at every response model of `schemas.py` and follows the models they hold.
    Fields typed `dict` or `Any` (a change's snapshots) are outside it: `changes.snapshot`
    gives those their offset, `test_a_change_timestamp_and_its_snapshots_carry_an_offset`
    checks it."""
    defined = _models_defined_in(schemas)
    assert REQUEST_MODELS <= defined.keys(), REQUEST_MODELS - defined.keys()
    checked, wrong, walked = walk(response_models(schemas))
    assert wrong == [], f"serialised without an offset (use schemas.UtcDatetime): {wrong}"
    # the walk reached the fields it is meant to check, inherited and nested ones too
    assert {
        "HostOut.last_seen",
        "ChangeOut.ts",
        "AgentOut.created_at",
        "AgentCreated.job_issued_at",
        "TokenOut.revoked_at",
        "ScanTargetOut.last_scan_at",
    } <= checked
    # A model from `agent_protocol` that an owner response holds is part of that
    # response and is walked: `ScopeRangeOut` inherits `ScanRange`'s fields. The agent
    # routes' own answers are not owner responses and are not walked.
    assert schemas.ScopeRangeOut in walked


# Models the walk is tried on: what a mistake in `schemas.py` would look like (#85).


class _Nested(BaseModel):
    at: datetime


class _Aware(BaseModel):  # as `agent_protocol` declares its datetimes
    at: AwareDatetime | None = None


class _NestedRight(BaseModel):
    at: schemas.UtcDatetime


class ReviewResponse(BaseModel):  # a response model whose name does not end in `Out`
    at: datetime


class _ReviewOut(BaseModel):
    times: list[schemas.UtcDatetime]
    maybe: list[schemas.UtcDatetime] | None
    named: dict[str, schemas.UtcDatetime | None]
    nested: list[_Nested] | None
    right: _NestedRight
    aware: dict[str, list[_Aware]]


def test_the_walk_finds_bare_datetimes_wherever_they_are_and_passes_annotated_ones():
    assert ReviewResponse in response_models(sys.modules[__name__])
    checked, wrong, _ = walk([ReviewResponse, _ReviewOut])
    assert {
        "_ReviewOut.times",
        "_ReviewOut.maybe",
        "_ReviewOut.named",
        "_NestedRight.at",
        "_Nested.at",
        "_Aware.at",
        "ReviewResponse.at",
    } == checked
    assert sorted(wrong) == [
        "ReviewResponse.at (json)",
        "ReviewResponse.at (python)",
        "_Aware.at (json)",
        "_Aware.at (python)",
        "_Nested.at (json)",
        "_Nested.at (python)",
    ]
