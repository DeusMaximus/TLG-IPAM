"""Scan targets and scan scope (v0.2 spec §5.3, §5.4).

The scan scope of a subnet is the set of addresses an agent may send packets to.
`scan_scope` is the only place that works it out: job creation, ingest validation,
findings and the UI all call it (spec §7.2, invariant 4).

Scan targets and scan scope are configured by the owner over REST only. No MCP tool
creates, changes or lists a scan target, and no MCP call can widen the scope
(spec §7.2, invariant 2); `list_changes` still shows their audit entries.

Observations and finding dismissals exist only inside the scope. Every write that can
narrow it calls `drop_out_of_scope` in its own transaction (spec §5.3, "leaving scope
deletes data").

This module reads subnets and reserved ranges straight from the models rather than
through `services/subnets`, which imports it.
"""

from bisect import bisect_right
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, exists, false, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from ..errors import ApiError, conflict, not_found, validation_error
from ..ipnet import (
    canonical_cidr,
    format_intervals,
    parse_cidr,
    parse_ip,
    subtract_intervals,
    usable_bounds,
)
from ..models import Agent, FindingDismissal, Observation, ReservedRange, ScanTarget, Subnet
from ..schemas import ScanSuggestionOut, ScanTargetCreate, ScanTargetOut, ScanTargetUpdate
from ..times import utc
from . import changes

# spec §5.2; the order here is the order methods are stored in
SCAN_METHODS = ("icmp", "arp", "rdns", "fdns")
# an address is alive if it answers one of these; rdns and fdns say nothing about it
LIVENESS_METHODS = frozenset({"icmp", "arp"})
# spec §5.6: no scan target for a prefix shorter than this
MIN_PREFIX = 20
# spec §5.8, §9: at most one requested scan per subnet in this time, from any caller
REQUEST_COOLDOWN = timedelta(minutes=15)
# ids per DELETE in `drop_out_of_scope`, well inside SQLite's bound-parameter limit
_DELETE_BATCH = 500
# Ids bound in one `IN (…)` read of the scan-target list and the scope's inputs. The
# SQLite build decides how many parameters one statement may bind (999 before 3.32,
# 32,766 by default since, 250,000 in the image), and a statement past it fails, so
# the ids go in chunks of this size, inside the smallest of those. Up to this many
# distinct subnets and agents the list is six statements; past it, a fixed number
# more per chunk.
_IN_BATCH = 500


def _in_batches(ids) -> list[list]:
    """Distinct ids, in the order first given, in chunks of `_IN_BATCH`."""
    distinct = list(dict.fromkeys(ids))
    return [distinct[i : i + _IN_BATCH] for i in range(0, len(distinct), _IN_BATCH)]


# --- Scope ---


def _scope(
    cidr: str,
    pool: tuple[str | None, str | None],
    excluded: list[tuple[int, int]],
    scan_dhcp_pool: bool,
) -> list[tuple[int, int]]:
    """Usable addresses (network and broadcast already out, for /30 and shorter),
    minus the DHCP pool unless it is scanned, minus every excluded reserved range.
    A reserved range that is not excluded removes nothing, so an address in both an
    excluded and a scanned range is excluded."""
    removed = list(excluded)
    dhcp_start, dhcp_end = pool
    if not scan_dhcp_pool and dhcp_start and dhcp_end:
        removed.append((int(parse_ip(dhcp_start)), int(parse_ip(dhcp_end))))
    return subtract_intervals(usable_bounds(parse_cidr(cidr)), removed)


async def _scopes_as_committed(
    session: AsyncSession, subnet_ids: list[str]
) -> dict[str, tuple[str, list[tuple[int, int]]]]:
    """Each subnet's CIDR and scan scope, from the database, by subnet id; a deleted
    subnet is not in the answer, and an id given twice is read once. The only place the
    scope's inputs are read.

    The ids are read in chunks of `_IN_BATCH` (`_in_batches`), each chunk in one
    statement per input for all its subnets, with one bound parameter per id: so the
    number of statements grows by three per chunk, not per subnet, and no statement
    binds more parameters than the SQLite build allows. A chunk reads all three inputs
    of each of its subnets, so no subnet's scope is put together from two chunks. One
    subnet (check-in, ingest, `drop_out_of_scope`) is one chunk.

    Every input is read as columns, never taken from objects the session loaded
    earlier, and no loaded object is refreshed: check-in's lease compare-and-set relies
    on the target as it read it. Pending changes are flushed first (autoflush), so the
    caller's own edit is included. A caller that writes on the strength of the scope
    holds SQLite's write lock before calling, so what it reads is what is committed and
    nothing can change it before it commits (`drop_out_of_scope`, `results.ingest`,
    and check-in after `jobs._claim`).
    """
    scopes: dict[str, tuple[str, list[tuple[int, int]]]] = {}
    for chunk in _in_batches(subnet_ids):
        scopes.update(await _scopes_of_chunk(session, chunk))
    return scopes


async def _scopes_of_chunk(
    session: AsyncSession, subnet_ids: list[str]
) -> dict[str, tuple[str, list[tuple[int, int]]]]:
    """`_scopes_as_committed` for one chunk of distinct ids: the subnets' columns, then,
    if any is found, the pool settings and the excluded ranges of those ids."""
    rows = (
        await session.execute(
            select(Subnet.id, Subnet.cidr, Subnet.dhcp_start, Subnet.dhcp_end).where(
                Subnet.id.in_(subnet_ids)
            )
        )
    ).all()
    if not rows:
        return {}
    pool_scanned = dict(
        (
            await session.execute(
                select(ScanTarget.subnet_id, ScanTarget.scan_dhcp_pool).where(
                    ScanTarget.subnet_id.in_(subnet_ids)
                )
            )
        ).all()
    )
    excluded: dict[str, list[tuple[int, int]]] = {}
    for subnet_id, start, end in (
        await session.execute(
            select(ReservedRange.subnet_id, ReservedRange.start_int, ReservedRange.end_int).where(
                ReservedRange.subnet_id.in_(subnet_ids), ReservedRange.scan_excluded.is_(True)
            )
        )
    ).all():
        excluded.setdefault(subnet_id, []).append((start, end))
    return {
        subnet_id: (
            cidr,
            _scope(
                cidr,
                (dhcp_start, dhcp_end),
                excluded.get(subnet_id, []),
                bool(pool_scanned.get(subnet_id)),
            ),
        )
        for subnet_id, cidr, dhcp_start, dhcp_end in rows
    }


async def _scope_as_committed(
    session: AsyncSession, subnet_id: str
) -> tuple[str, list[tuple[int, int]]] | None:
    """The subnet's CIDR and scan scope, from the database (`_scopes_as_committed`);
    None once it is deleted."""
    return (await _scopes_as_committed(session, [subnet_id])).get(subnet_id)


async def scan_scope(session: AsyncSession, subnet: Subnet | str) -> list[tuple[int, int]]:
    """The subnet's scan scope: merged, inclusive integer address ranges, ascending.
    A subnet without a scan target gets the default scope (DHCP pool not scanned).
    Read as committed (`_scope_as_committed`): `subnet` names the subnet, as the loaded
    subnet or its id, and its loaded attributes are not used. A deleted subnet has an
    empty scope."""
    found = await _scope_as_committed(session, subnet if isinstance(subnet, str) else subnet.id)
    return found[1] if found else []


async def scan_scope_ranges(session: AsyncSession, subnet: Subnet) -> list[dict]:
    """`scan_scope` as `[{start, end}]` address strings, the shape of a job's ranges."""
    found = await _scope_as_committed(session, subnet.id)
    if found is None:
        return []
    cidr, scope = found
    return format_intervals(parse_cidr(cidr), scope)


async def take_write_lock(session: AsyncSession) -> None:
    """Make sure this session's transaction holds SQLite's write lock, which a write
    statement takes even when it touches no row. Until the caller commits, no other
    writer can change what it reads next. A no-op when the lock is already held."""
    await session.execute(
        delete(Observation).where(false()).execution_options(synchronize_session=False)
    )


def in_scope(scope: list[tuple[int, int]], ip_int: int) -> bool:
    """Whether an address, as an integer of the subnet's family, lies in a scope that
    `scan_scope` returned."""
    i = bisect_right(scope, (ip_int, float("inf"))) - 1
    return i >= 0 and scope[i][0] <= ip_int <= scope[i][1]


async def drop_out_of_scope(session: AsyncSession, subnet: Subnet) -> None:
    """Delete the subnet's observations and finding dismissals outside its scan scope as
    it now stands: the "leaving scope" rule (spec §5.3). Every write that can narrow the scope calls this
    in its own transaction, after its change (pool scanning turned off or the target
    deleted, a range excluded, the pool or the CIDR edited). Does not commit.

    The caller read the subnet, and validated its change, before its transaction took
    SQLite's write lock: the driver begins the transaction at the first write, so
    another request may have committed since (a new pool, an ingest in the space it
    freed). So the write lock is taken here, by a statement that changes nothing, even
    when the caller's own change wrote nothing, and `scan_scope` then reads the scope
    as committed. The caller's own change is flushed first and is part of it.

    The rows deleted are not audit-logged; the write that narrowed the scope is.
    Observations are scan state. A dismissal is the owner's, but it only ever hides a
    finding, and an address out of scope raises none (spec §5.5).
    """
    await session.flush()
    await take_write_lock(session)
    scope = await scan_scope(session, subnet)
    # Membership is decided here with `in_scope`, not in SQL: a scope can hold more
    # ranges than SQLite's expression-depth limit allows as OR branches, and a statement
    # that failed would roll back the owner's edit.
    for model in (Observation, FindingDismissal):
        rows = await session.execute(
            select(model.id, model.ip_int).where(model.subnet_id == subnet.id)
        )
        gone = [row_id for row_id, ip_int in rows.all() if not in_scope(scope, ip_int)]
        for i in range(0, len(gone), _DELETE_BATCH):
            await session.execute(
                delete(model)
                .where(model.id.in_(gone[i : i + _DELETE_BATCH]))
                .execution_options(synchronize_session="fetch")
            )


async def delete_scan_data(session: AsyncSession, subnet: Subnet) -> None:
    """Delete every observation and finding dismissal of the subnet, as part of deleting
    it. Does not commit."""
    for model in (Observation, FindingDismissal):
        await session.execute(
            delete(model)
            .where(model.subnet_id == subnet.id)
            .execution_options(synchronize_session="fetch")
        )


# --- Validation ---


def normalise_methods(methods: list[str]) -> list[str]:
    """A subset of the four methods, in canonical order, with icmp or arp among them."""
    unknown = sorted({m for m in methods if m not in SCAN_METHODS})
    if unknown:
        raise validation_error(
            f"Unknown scan method(s) {', '.join(unknown)}: methods are "
            + ", ".join(SCAN_METHODS)
        )
    chosen = set(methods)
    if not chosen & LIVENESS_METHODS:
        raise validation_error(
            "A scan target needs a liveness method: methods must include icmp or arp"
        )
    return [m for m in SCAN_METHODS if m in chosen]


def check_prefix(cidr: str) -> None:
    if parse_cidr(cidr).prefixlen < MIN_PREFIX:
        raise validation_error(
            f"Subnet {cidr} is too large to scan: a scan target needs a /{MIN_PREFIX} "
            "or a longer prefix"
        )


async def _get_subnet(session: AsyncSession, subnet_id: str) -> Subnet:
    subnet = await session.get(Subnet, subnet_id)
    if subnet is None:
        raise not_found("Subnet", subnet_id)
    return subnet


async def _check_agent(session: AsyncSession, agent_id: str | None) -> Agent | None:
    if agent_id is None:
        return None
    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise not_found("Agent", agent_id)
    return agent


# --- Scan targets ---


async def list_targets(session: AsyncSession) -> list[ScanTarget]:
    query = select(ScanTarget).order_by(ScanTarget.created_at, ScanTarget.id)
    return list((await session.scalars(query)).all())


async def get_target(session: AsyncSession, target_id: str) -> ScanTarget:
    target = await session.get(ScanTarget, target_id)
    if target is None:
        raise not_found("Scan target", target_id)
    return target


async def target_for_subnet(session: AsyncSession, subnet_id: str) -> ScanTarget | None:
    return await session.scalar(select(ScanTarget).where(ScanTarget.subnet_id == subnet_id))


async def targets_by_subnet(
    session: AsyncSession, subnet_ids: list[str]
) -> dict[str, ScanTarget]:
    if not subnet_ids:
        return {}
    rows = await session.scalars(select(ScanTarget).where(ScanTarget.subnet_id.in_(subnet_ids)))
    return {t.subnet_id: t for t in rows}


def _scope_out(cidr: str, scope: list[tuple[int, int]]) -> list[dict]:
    """The scope as a scan-target answer carries it: each range's addresses and the same
    bounds as integers (`schemas.ScopeRangeOut`), so the UI can place a host by its
    `ip_int` without parsing an address."""
    ranges = format_intervals(parse_cidr(cidr), scope)
    return [{**r, "start_int": lo, "end_int": hi} for r, (lo, hi) in zip(ranges, scope)]


def _agent_checkin(agent: Agent | None, runnable: list[str] | None) -> dict:
    """How old the evidence for `runnable_methods` is: the agent's last check-in, and
    whether it was refused. Only an accepted check-in writes the interfaces; a refused
    one (409 or 422) writes `last_checkin_at` and `last_error` and leaves them as they
    were, and an accepted one clears `last_error`. So when the last check-in was
    accepted the interfaces are from `last_checkin_at`; when it was refused they are
    from an earlier accepted one, whose time is not stored. Both null when
    `runnable_methods` is."""
    if agent is None or runnable is None:
        return {"agent_checkin_at": None, "agent_checkin_refused": None}
    return {
        "agent_checkin_at": agent.last_checkin_at,
        "agent_checkin_refused": agent.last_error is not None,
    }


async def targets_out(session: AsyncSession, targets: list[ScanTarget]) -> list[dict]:
    """The scan-target answers of REST (no MCP tool returns them), in the order given:
    the columns, the scope, `runnable_methods` (what a job would run on the target's
    agent now) and the agent's last check-in it is judged from.

    The subnets, the agents and the scope's inputs are each read in one statement per
    chunk of `_IN_BATCH` distinct ids (`_in_batches`, `_scopes_as_committed`), not per
    target: up to that many distinct subnets and agents the list costs the same six
    statements for one target as for five hundred, and past it a fixed number more per
    chunk. No statement binds more than `_IN_BATCH` ids, so the SQLite build's
    parameter limit does not bound the number of targets."""
    # jobs imports this module, so it is imported here
    from . import jobs

    subnets = {}
    for chunk in _in_batches(t.subnet_id for t in targets):
        rows = await session.scalars(select(Subnet).where(Subnet.id.in_(chunk)))
        subnets.update((s.id, s) for s in rows)
    agents = {}
    for chunk in _in_batches(t.agent_id for t in targets if t.agent_id):
        rows = await session.scalars(select(Agent).where(Agent.id.in_(chunk)))
        agents.update((a.id, a) for a in rows)
    scopes = await _scopes_as_committed(session, list(subnets))
    answers = []
    for target in targets:
        subnet = subnets.get(target.subnet_id)
        if subnet is None:
            raise not_found("Subnet", target.subnet_id)
        agent = agents.get(target.agent_id) if target.agent_id else None
        columns = {c.name: getattr(target, c.name) for c in target.__table__.columns}
        found = scopes.get(subnet.id)
        runnable = jobs.runnable_methods(target, subnet, agent)
        out = {
            **columns,
            "scope": _scope_out(*found) if found else [],
            "runnable_methods": runnable,
            **_agent_checkin(agent, runnable),
        }
        answers.append(ScanTargetOut.model_validate(out).model_dump(mode="json"))
    return answers


async def target_out(session: AsyncSession, target: ScanTarget) -> dict:
    """One target's answer, built as the list builds each (`targets_out`)."""
    [answer] = await targets_out(session, [target])
    return answer


async def suggestions(session: AsyncSession) -> list[dict]:
    """Subnets an agent could scan, for the Agents page's offer "Scan *subnet* with this
    agent?" (spec §5.6, "Suggest, don't assume"): every pair of an `active` agent and a
    subnet that has no scan target, is large enough to scan (`check_prefix`) and is on
    the link of an interface the agent reported at its last accepted check-in, by the
    rule `arp` is judged by (`jobs.l2_local`). An agent that has never checked in has
    reported no interface, so it is offered nothing until its first check-in.

    Read-only: nothing is stored and nothing is scanned. Accepting an offer is the
    owner's `POST /scan-targets` with the subnet and the agent, and the defaults
    (`schemas.ScanTargetCreate`). REST only, like the scan-target answer: no MCP tool
    returns it.

    Two statements whatever the number of agents and subnets, each reading only the
    columns the answer uses (a subnet's notes have no length bound); ordered by agent
    name, then subnet name, CIDR and id."""
    # jobs imports this module, so it is imported here
    from . import jobs

    agents = (
        await session.execute(
            select(Agent.id, Agent.name, Agent.interfaces)
            .where(Agent.state == "active")
            .order_by(Agent.name, Agent.id)
        )
    ).all()
    linked = [a for a in agents if a.interfaces]
    if not linked:
        return []
    untargeted = ~exists().where(ScanTarget.subnet_id == Subnet.id)
    subnets = (
        await session.execute(
            select(Subnet.id, Subnet.name, Subnet.cidr)
            .where(untargeted)
            .order_by(Subnet.name, Subnet.cidr, Subnet.id)
        )
    ).all()
    scannable = [s for s in subnets if parse_cidr(s.cidr).prefixlen >= MIN_PREFIX]
    return [
        ScanSuggestionOut(
            agent_id=agent.id,
            agent_name=agent.name,
            subnet_id=subnet.id,
            subnet_name=subnet.name,
            subnet_cidr=canonical_cidr(subnet.cidr),
        ).model_dump()
        for agent in linked
        for subnet in scannable
        if jobs.l2_local(subnet, agent.interfaces)
    ]


def _label(subnet: Subnet) -> str:
    """The target in an audit summary, by its subnet's name and CIDR, the CIDR in its
    one spelling whatever a row stored before #97 holds (`ipnet.canonical_cidr`)."""
    return f"scan target of subnet {subnet.name} ({canonical_cidr(subnet.cidr)})"


async def create_target(
    session: AsyncSession, payload: ScanTargetCreate, source: str
) -> ScanTarget:
    subnet = await _get_subnet(session, payload.subnet_id)
    check_prefix(subnet.cidr)
    methods = normalise_methods(payload.methods)
    agent = await _check_agent(session, payload.agent_id)
    taken = f"Subnet {subnet.name} already has a scan target"
    if await target_for_subnet(session, subnet.id) is not None:
        raise conflict(taken)
    target = ScanTarget(**{**payload.model_dump(), "methods": methods})
    session.add(target)
    try:
        await session.flush()
    except IntegrityError:
        # UNIQUE(subnet_id) is the backstop for two requests racing past the check above
        await session.rollback()
        raise conflict(taken) from None
    summary = f"{_label(subnet)} created"
    if agent is not None:
        summary += f", assigned to agent {agent.name}"
    changes.record(
        session,
        entity_type="scan_target",
        entity_id=target.id,
        action="create",
        summary=summary,
        source=source,
        after=changes.snapshot(target),
    )
    await session.commit()
    return target


async def update_target(
    session: AsyncSession, target_id: str, payload: ScanTargetUpdate, source: str
) -> ScanTarget:
    target = await get_target(session, target_id)
    subnet = await _get_subnet(session, target.subnet_id)
    data = payload.model_dump(exclude_unset=True)
    if "methods" in data:
        data["methods"] = normalise_methods(data["methods"])
    if "agent_id" in data:
        await _check_agent(session, data["agent_id"])
    changed = [field for field, value in data.items() if getattr(target, field) != value]
    if not changed:
        return target
    before = changes.snapshot(target)
    for field in changed:
        setattr(target, field, data[field])
    # turning pool scanning off puts the pool out of scope (spec §5.3)
    await drop_out_of_scope(session, subnet)
    changes.record(
        session,
        entity_type="scan_target",
        entity_id=target.id,
        action="update",
        summary=f"{_label(subnet)} updated ({', '.join(changed)})",
        source=source,
        before=before,
        after=changes.snapshot(target),
    )
    await session.commit()
    return target


def _record_delete(session: AsyncSession, target: ScanTarget, subnet: Subnet, source: str, why=""):
    changes.record(
        session,
        entity_type="scan_target",
        entity_id=target.id,
        action="delete",
        summary=f"{_label(subnet)} deleted{why}",
        source=source,
        before=changes.snapshot(target),
    )


async def delete_target(session: AsyncSession, target_id: str, source: str) -> None:
    target = await get_target(session, target_id)
    subnet = await _get_subnet(session, target.subnet_id)
    _record_delete(session, target, subnet, source)
    await session.delete(target)
    # without a target the subnet has the default scope, which leaves the pool out
    await drop_out_of_scope(session, subnet)
    await session.commit()


async def delete_for_subnet(session: AsyncSession, subnet: Subnet, source: str) -> None:
    """Delete the subnet's observations, finding dismissals and scan target, if any, as
    part of deleting the subnet. Does not commit: `subnets.delete_subnet` does, after deleting the subnet
    itself."""
    # first, and whether or not a target is left: they reference the subnet
    await delete_scan_data(session, subnet)
    target = await target_for_subnet(session, subnet.id)
    if target is None:
        return
    _record_delete(session, target, subnet, source, " (subnet deleted)")
    await session.delete(target)
    # the subnet row goes next, and foreign keys are enforced
    await session.flush()


async def unassign_agent(session: AsyncSession, agent: Agent, source: str) -> None:
    """Set `agent_id` to null on the agent's scan targets, as part of deleting it. They
    keep their settings and show as unassigned. Does not commit."""
    rows = await session.execute(
        select(ScanTarget, Subnet)
        .join(Subnet, ScanTarget.subnet_id == Subnet.id)
        .where(ScanTarget.agent_id == agent.id)
        .order_by(Subnet.name)
    )
    for target, subnet in rows.all():
        before = changes.snapshot(target)
        target.agent_id = None
        changes.record(
            session,
            entity_type="scan_target",
            entity_id=target.id,
            action="update",
            summary=f"{_label(subnet)} unassigned (agent {agent.name} deleted)",
            source=source,
            before=before,
            after=changes.snapshot(target),
        )
    # the agent row goes next; do not leave the order to the unit of work
    await session.flush()


# --- Requested scans ---


def next_request_after(asked: datetime) -> str:
    """When the subnet can next be asked for a scan, in ISO-8601 UTC, rounded up to the
    second: a request made at the time given is never inside the cooldown."""
    again = asked + REQUEST_COOLDOWN
    if again.microsecond:
        again = again.replace(microsecond=0) + timedelta(seconds=1)
    return again.isoformat(timespec="seconds")


def _cooldown(subnet: Subnet, asked: datetime) -> ApiError:
    again = next_request_after(asked)
    minutes = int(REQUEST_COOLDOWN.total_seconds() // 60)
    return ApiError(
        "COOLDOWN",
        f"A scan of subnet {subnet.name} was requested less than {minutes} minutes ago; "
        f"request another after {again}",
        429,
    )


async def request_scan(session: AsyncSession, subnet_id: str, source: str) -> ScanTarget:
    """Mark the subnet's scan target due ("Scan now"). The agent picks it up at its
    next check-in (`services/jobs`).

    The cooldown is per subnet and applies to every caller, the UI included: repeated
    scans would turn the latest state into a presence timeline (spec §5.8). A refused
    request changes nothing and does not restart the cooldown.
    """
    subnet = await _get_subnet(session, subnet_id)
    target = await target_for_subnet(session, subnet.id)
    if target is None:
        raise conflict(f"Subnet {subnet.name} has no scan target: set up scanning first")
    if not target.enabled:
        raise conflict(f"Scanning of subnet {subnet.name} is disabled")
    if target.agent_id is None:
        raise conflict(f"Subnet {subnet.name} has no agent assigned to scan it")
    now = datetime.now(timezone.utc)
    cutoff = now - REQUEST_COOLDOWN
    asked = utc(target.scan_requested_at)
    if asked is not None and asked > cutoff:
        raise _cooldown(subnet, asked)
    before = changes.snapshot(target)
    # Conditional, so two racing requests cannot both pass the check above.
    result = await session.execute(
        update(ScanTarget)
        .where(
            ScanTarget.id == target.id,
            or_(ScanTarget.scan_requested_at.is_(None), ScanTarget.scan_requested_at <= cutoff),
        )
        .values(scan_requested_at=now)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        asked = await session.scalar(
            select(ScanTarget.scan_requested_at).where(ScanTarget.id == target.id)
        )
        error = _cooldown(subnet, utc(asked) or now)
        await session.rollback()
        raise error
    set_committed_value(target, "scan_requested_at", now)
    changes.record(
        session,
        entity_type="scan_target",
        entity_id=target.id,
        action="update",
        summary=f"{_label(subnet)}: scan requested",
        source=source,
        before=before,
        after=changes.snapshot(target),
    )
    await session.commit()
    return target


def scan_request_out(target: ScanTarget) -> dict:
    """What `request_scan` reports over MCP: the request and when the next one can be
    made. Not the target itself: its configuration (agent, methods, interval, scope)
    stays off MCP (spec §5.8)."""
    asked = utc(target.scan_requested_at)
    return {
        "subnet_id": target.subnet_id,
        "scan_requested_at": asked.isoformat(timespec="seconds"),
        "next_request_after": next_request_after(asked),
    }
