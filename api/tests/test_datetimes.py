"""Every datetime the API hands out carries a UTC offset (brief §4.3), whether the row
was just written and is still in memory or was read back from SQLite, which returns
datetimes without their zone (#65)."""

import importlib
import inspect
import pkgutil
import sys
import types
import typing
from datetime import datetime

import pytest
from fastmcp import Client
from pydantic import (
    AwareDatetime,
    BaseModel,
    FutureDatetime,
    NaiveDatetime,
    PastDatetime,
    RootModel,
    TypeAdapter,
    computed_field,
    field_serializer,
)

from app import routers

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
    # pending, with its one token, the enrolment token the create response carries
    created = (await client.post("/api/v1/agents", json={"name": "vlan10"})).json()["data"]
    assert utc_form(created["enrol_token_expires_at"]) == created["tokens"][0]["expires_at"]
    revoked = (await client.post(f"/api/v1/agents/{created['id']}/revoke")).json()["data"]
    read = (await client.get(f"/api/v1/agents/{created['id']}")).json()["data"]
    [listed] = (await client.get("/api/v1/agents")).json()["data"]
    for agent in (revoked, read, listed):
        assert utc_form(agent["created_at"]) == utc_form(created["created_at"])
        [token] = agent["tokens"]
        assert utc_form(token["created_at"]) == utc_form(created["tokens"][0]["created_at"])
        assert utc_form(token["revoked_at"]) == utc_form(revoked["tokens"][0]["revoked_at"])
        assert utc_form(token["expires_at"]) == utc_form(created["enrol_token_expires_at"])


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
# model. A new model is walked as one until it is named here, and
# `test_request_models_lists_only_models_a_request_is_validated_against` refuses a name
# that no request body is validated against.
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


FAILURE = (
    "serialised without an offset: a response model's datetime is `schemas.UtcDatetime`; "
    "a request model (one a request body is validated against, whose datetimes go to the "
    "ORM as they are) is named in REQUEST_MODELS instead"
)


class Unsampled(AssertionError):
    """The walk cannot give a value to a field's type, so it cannot say what it emits."""


# placeholders for the positions of a fixed-length tuple that hold no datetime
FILLERS = {str: "", int: 0, float: 0.0, bool: False, type(None): None}


def _nested_models(annotation):
    """The models an annotation holds, at any depth: `X`, `list[X]`, `X | None`..."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        yield annotation
    for arg in typing.get_args(annotation):
        yield from _nested_models(arg)


def _sample(annotation):
    """A value of the annotation's shape whose datetime is `STORED`, and what each dump
    mode must give for it: `(value, {"json": ..., "python": ...})`; None when the type
    holds no datetime of its own (a nested model's are checked on that model). A
    fixed-length tuple takes a placeholder at each position without one; a position
    this cannot fill is `Unsampled`, never skipped."""
    origin, args = typing.get_origin(annotation), typing.get_args(annotation)
    if annotation in DATETIME_TYPES:
        return STORED, {"json": SERIALISED, "python": SERIALISED}
    if origin is typing.Annotated:
        return _sample(args[0])
    if origin in (list, set, frozenset) or (origin is tuple and args[1:] == (Ellipsis,)):
        inner = _sample(args[0]) if args else None
        if inner is None:
            return None
        value, expected = inner
        make = tuple if origin is tuple else origin
        return make([value]), {
            "json": [expected["json"]],
            "python": make([expected["python"]]),
        }
    if origin is tuple:
        samples = [_sample(arg) for arg in args]
        if not any(samples):
            return None
        values, json, python = [], [], []
        for arg, sample in zip(args, samples):
            if sample is None:
                if typing.get_origin(arg) is typing.Literal:
                    sample = arg.__args__[0], {"json": arg.__args__[0], "python": arg.__args__[0]}
                elif arg in FILLERS:
                    sample = FILLERS[arg], {"json": FILLERS[arg], "python": FILLERS[arg]}
                else:
                    raise Unsampled(f"{annotation}: no value for the position {arg}")
            values.append(sample[0])
            json.append(sample[1]["json"])
            python.append(sample[1]["python"])
        return tuple(values), {"json": json, "python": tuple(python)}
    if origin is dict:
        inner = _sample(args[1]) if len(args) == 2 else None
        if inner is None:
            return None
        return {"k": inner[0]}, {mode: {"k": inner[1][mode]} for mode in ("json", "python")}
    if origin in (typing.Union, types.UnionType):
        return next((s for s in map(_sample, args) if s is not None), None)
    return None


def _dumped(model, instance, name, mode):
    """What a field of the instance emits. A root model dumps as its root value."""
    if issubclass(model, RootModel):
        return instance.model_dump(mode=mode)
    return instance.model_dump(mode=mode, include={name})[name]


def walk(roots) -> tuple[set[str], list[str], set[type[BaseModel]]]:
    """Each datetime field of the roots and of every model their fields reach (nested,
    in a list, a dict or an optional), wherever that model is defined: each is given a
    value as SQLite returns it and dumped in both modes. A computed field has no value
    to give, so its return annotation is dumped through a `TypeAdapter`, which is the
    serialiser pydantic builds for it. Returns the fields checked, those that did not
    serialise with the offset, and the models walked."""
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
                if _dumped(model, instance, name, mode) != expected[mode]:
                    wrong.append(f"{label} ({mode})")
        for name, computed in model.model_computed_fields.items():
            queue.extend(_nested_models(computed.return_type))
            sample = _sample(computed.return_type)
            if sample is None:
                continue
            value, expected = sample
            label = f"{model.__name__}.{name}"
            checked.add(label)
            for mode in ("json", "python"):
                dumped = TypeAdapter(computed.return_type).dump_python(value, mode=mode)
                if dumped != expected[mode]:
                    wrong.append(f"{label} ({mode})")
    return checked, wrong, seen


def test_every_datetime_field_in_the_schemas_is_serialised_with_an_offset():
    """The rule lives in one annotation (`schemas.UtcDatetime`); a field declared with a
    bare `datetime` would be serialised without an offset once read back. The walk
    starts at every response model of `schemas.py` and follows the models they hold.
    A field is given a value as SQLite returns it and must dump with `+00:00` in both
    modes.

    The annotation shapes it supports (`_sample`): a datetime type (pydantic's
    constrained ones included), `Annotated[...]` of one, and `list`, `set`, `frozenset`,
    variadic and fixed-length `tuple`, `dict` with `str` keys, `X | None` and unions of
    those; a model in any of them; a root model; a computed field's return annotation.
    Anything else yields no checked field, and the field passes.

    What it does not see, so a change there needs a test of its own (the first two
    limits are held by tests that fail when the walk improves, so they get flipped):
    - A serializer on the model that holds a computed field (`field_serializer`,
      `model_serializer`): the return annotation is dumped by itself, without it.
      `test_the_walk_does_not_see_a_serializer_on_the_model_holding_a_computed_field`.
    - A union is checked on its first branch that holds a datetime:
      `list[UtcDatetime] | tuple[datetime, ...]` passes.
      `test_the_walk_checks_only_the_first_branch_of_a_union`.
    - Shapes it has no branch for: `Sequence`, `Mapping`, a `TypedDict`, a dataclass, a
      `NamedTuple`, an enum holding a datetime. A `dict` is sampled with the key `"k"`,
      so a key type other than `str` is sampled with a key it does not accept.
    - A field typed `dict` or `Any` (a change's snapshots): `changes.snapshot` gives
      those their offset, `test_a_change_timestamp_and_its_snapshots_carry_an_offset`
      checks it.
    - A property that returns a value its return annotation does not describe.
    - A fixed-length tuple that mixes a datetime with a position that is not a `str`,
      `int`, `float`, `bool`, `None` or `Literal`: the walk raises `Unsampled`, the one
      shape it refuses, not skips.
    - What a route or tool returns. The walk starts at the models `schemas.py` defines.
      `test_no_route_or_tool_serves_a_model_outside_the_walks_roots` pins what the
      routers under `app.routers` and the MCP tools declare, and no more: it does not
      show where an answer's fields come from.
    - A response model that is also a request model. A response model listed in
      `REQUEST_MODELS` is refused by name only; see `request_models_unaccounted_for`."""
    defined = _models_defined_in(schemas)
    assert REQUEST_MODELS <= defined.keys(), REQUEST_MODELS - defined.keys()
    checked, wrong, walked = walk(response_models(schemas))
    assert wrong == [], f"{FAILURE}: {wrong}"
    # the walk reached the fields it is meant to check, inherited and nested ones too
    assert {
        "HostOut.last_seen",
        "ChangeOut.ts",
        "AgentOut.created_at",
        "AgentEnrolment.job_issued_at",
        "AgentEnrolment.enrol_token_expires_at",
        "TokenOut.revoked_at",
        "ScanTargetOut.last_scan_at",
    } <= checked
    # A model from `agent_protocol` that an owner response holds is part of that
    # response and is walked: `ScopeRangeOut` inherits `ScanRange`'s fields. The agent
    # routes' own answers are not owner responses and are not walked.
    assert schemas.ScopeRangeOut in walked


# --- What `REQUEST_MODELS` and the walk's roots rest on ---

# The two request models a service validates one by one, out of a body of raw items
# (`BulkImportRequest.hosts: list[dict]`, `FindingKeysRequest.findings: list[Any]`), so
# that a bad record is reported without failing the batch: no route names them. They are
# an allowance with no check behind it: nothing here shows a service still validates them
# or that they are not also used as responses.
ITEM_MODELS = {"BulkHostItem", "FindingKey"}


def _router_modules():
    return [
        importlib.import_module(f"{routers.__name__}.{module.name}")
        for module in pkgutil.iter_modules(routers.__path__)
    ]


def request_models_unaccounted_for(listed: set[str]) -> set[str]:
    """Names in `listed` that no request body is validated against: not a model a route
    takes as its body (nor one that model holds, nor one it inherits from) and not one
    of `ITEM_MODELS`. It compares names, not models: a request model of another module
    with the same name as a listed response model would exempt it."""
    queue = [
        param.field_info.annotation
        for module in _router_modules()
        for route in module.router.routes
        for param in route.dependant.body_params
    ]
    reached = set()
    while queue:
        for model in _nested_models(queue.pop()):
            if model.__name__ in reached:
                continue
            reached.add(model.__name__)
            queue.extend(base for base in model.__bases__ if issubclass(base, BaseModel))
            queue.extend(field.annotation for field in model.model_fields.values())
    assert {"HostCreate", "_HostFields", "SubnetUpdate"} <= reached, reached  # it found them
    return listed - reached - ITEM_MODELS


def test_request_models_lists_only_models_a_request_is_validated_against():
    """A response model named in `REQUEST_MODELS` by mistake is not walked, so a bare
    datetime in it would pass."""
    assert request_models_unaccounted_for(REQUEST_MODELS) == set()
    # a response model is refused when it is named
    assert request_models_unaccounted_for({"HostOut", "HostCreate"}) == {"HostOut"}
    assert request_models_unaccounted_for(set(ITEM_MODELS)) == set()


async def test_no_route_or_tool_serves_a_model_outside_the_walks_roots(client):
    """A pin on declarations, not a proof of where an answer's fields come from. The
    routers under `app.routers` declare no `response_model` (each returns its own
    `{"data": ...}` envelope; FastAPI infers one from a model return annotation too),
    and every MCP tool advertises the output schema of a bare `dict`. A router route or
    a tool that declared a model defined elsewhere would not be walked until it is made
    a root, so it fails here. Not seen: a tool annotated `-> dict` that returns another
    model's `model_dump()`, a route with `response_model=None` and a model return
    annotation, a route added with `app.add_api_route` outside the routers, a subapp."""
    declared = [
        f"{module.__name__} {route.path}: {route.response_model}"
        for module in _router_modules()
        for route in module.router.routes
        if route.response_model is not None
    ]
    assert declared == [], f"serves a model directly: {declared}"
    async with Client(mcp) as mcp_client:
        tools = await mcp_client.list_tools()
    assert tools
    a_dict = {"additionalProperties": True, "type": "object"}
    typed = [t.name for t in tools if t.output_schema != a_dict]
    assert typed == [], f"returns something other than a dict: {typed}"


# Models the walk is tried on: what a mistake in `schemas.py` would look like (#85, #105).


class _Nested(BaseModel):
    at: datetime


class _Aware(BaseModel):  # as `agent_protocol` declares its datetimes
    at: AwareDatetime | None = None


class _NestedRight(BaseModel):
    at: schemas.UtcDatetime


class ReviewResponse(BaseModel):  # a response model whose name does not end in `Out`
    at: datetime


class ReviewRequest(BaseModel):  # a request model nobody has named in REQUEST_MODELS yet
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


class _TuplesRight(BaseModel):
    first: tuple[schemas.UtcDatetime, str]
    last: tuple[str, schemas.UtcDatetime]
    middle: tuple[int, schemas.UtcDatetime | None, typing.Literal["a"]]
    many: tuple[schemas.UtcDatetime, ...]
    unique: set[schemas.UtcDatetime]
    frozen: frozenset[schemas.UtcDatetime]


class _TuplesWrong(BaseModel):
    last: tuple[str, datetime]
    many: tuple[datetime, ...]
    unique: set[datetime]


class _TupleOfModel(BaseModel):
    mixed: tuple[_NestedRight, schemas.UtcDatetime]


def test_the_walk_samples_every_position_of_a_tuple_and_refuses_one_it_cannot_fill():
    checked, wrong, _ = walk([_TuplesRight])
    assert wrong == []
    assert checked == {f"_TuplesRight.{name}" for name in _TuplesRight.model_fields}
    checked, wrong, _ = walk([_TuplesWrong])
    assert sorted(wrong) == [
        "_TuplesWrong.last (json)",
        "_TuplesWrong.last (python)",
        "_TuplesWrong.many (json)",
        "_TuplesWrong.many (python)",
        "_TuplesWrong.unique (json)",
        "_TuplesWrong.unique (python)",
    ]
    with pytest.raises(Unsampled, match="_NestedRight"):
        walk([_TupleOfModel])


class _ComputedBare(BaseModel):
    @computed_field
    @property
    def at(self) -> datetime:
        return datetime(2026, 1, 2)


class _ComputedRight(BaseModel):
    @computed_field
    @property
    def at(self) -> schemas.UtcDatetime:
        return datetime(2026, 1, 2)


class _ComputedHolder(BaseModel):
    @computed_field
    @property
    def inside(self) -> list[_Nested]:
        return []


def test_the_walk_checks_what_a_computed_field_emits():
    # the computed field of the review: without an offset
    assert _ComputedBare().model_dump(mode="json") == {"at": "2026-01-02T00:00:00"}
    assert _ComputedRight().model_dump(mode="json") == {"at": "2026-01-02T00:00:00+00:00"}
    checked, wrong, _ = walk([_ComputedBare, _ComputedRight])
    assert checked == {"_ComputedBare.at", "_ComputedRight.at"}
    assert sorted(wrong) == ["_ComputedBare.at (json)", "_ComputedBare.at (python)"]
    # and a model that a computed field returns is walked
    checked, wrong, walked = walk([_ComputedHolder])
    assert _Nested in walked and "_Nested.at" in checked
    assert sorted(wrong) == ["_Nested.at (json)", "_Nested.at (python)"]


class _RootBare(RootModel[datetime]):
    pass


class _RootRight(RootModel[schemas.UtcDatetime]):
    pass


class _RootListRight(RootModel[list[schemas.UtcDatetime]]):
    pass


class _RootHolder(BaseModel):
    times: _RootRight
    bare: _RootBare


def test_the_walk_checks_what_a_root_model_emits():
    checked, wrong, _ = walk([_RootBare, _RootRight, _RootListRight])
    assert checked == {"_RootBare.root", "_RootRight.root", "_RootListRight.root"}
    assert sorted(wrong) == ["_RootBare.root (json)", "_RootBare.root (python)"]
    # one a response holds is walked as well
    checked, wrong, _ = walk([_RootHolder])
    assert checked == {"_RootRight.root", "_RootBare.root"}
    assert len(wrong) == 2


def test_the_failure_says_where_a_request_model_goes():
    """A model of `schemas.py` nobody has named in `REQUEST_MODELS` is walked as a
    response model, and its plain `datetime` fails. Its author may have meant a request
    model, so the failure names both ways out."""
    assert ReviewRequest not in response_models(schemas)
    assert response_models(sys.modules[__name__]).count(ReviewRequest) == 1
    _, wrong, _ = walk([ReviewRequest])
    assert wrong == ["ReviewRequest.at (json)", "ReviewRequest.at (python)"]
    message = f"{FAILURE}: {wrong}"
    assert "schemas.UtcDatetime" in message and "REQUEST_MODELS" in message


class _SerialisedByTheModel(BaseModel):
    @computed_field
    @property
    def at(self) -> schemas.UtcDatetime:
        return datetime(2026, 1, 2)

    @field_serializer("at")
    def _at(self, value) -> str:
        return value.isoformat()


def test_the_walk_does_not_see_a_serializer_on_the_model_holding_a_computed_field():
    """A known limit, held so that a better walk flips this test: a computed field's
    return annotation is dumped by itself (`TypeAdapter`), without a `field_serializer`
    or `model_serializer` of the model that holds it. The annotation here is right and
    the model's own serializer takes the offset away."""
    checked, wrong, _ = walk([_SerialisedByTheModel])
    assert checked == {"_SerialisedByTheModel.at"}
    assert wrong == []  # the walk passes it
    assert _SerialisedByTheModel().model_dump(mode="json") == {"at": "2026-01-02T00:00:00"}


class _UnionOfTwoShapes(BaseModel):
    at: list[schemas.UtcDatetime] | tuple[datetime, ...]


def test_the_walk_checks_only_the_first_branch_of_a_union():
    """A known limit, held so that a better walk flips this test: a union is sampled on
    its first branch that holds a datetime, here the right one, and the bare branch
    behind it is never given a value."""
    checked, wrong, _ = walk([_UnionOfTwoShapes])
    assert checked == {"_UnionOfTwoShapes.at"}
    assert wrong == []  # the walk passes it
    held = _UnionOfTwoShapes(at=(datetime(2026, 1, 2),))
    assert held.model_dump(mode="json") == {"at": ["2026-01-02T00:00:00"]}
