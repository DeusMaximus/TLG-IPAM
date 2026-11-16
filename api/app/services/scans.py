"""Scan targets and scan scope (v0.2 spec §5.3, §5.4).

The scan scope of a subnet is the set of addresses an agent may send packets to.
`scan_scope` is the only place that works it out: job creation, ingest validation,
findings and the UI all call it (spec §7.2, invariant 4).

Scan targets and scan scope are configured by the owner over REST only. No MCP tool
creates, changes or lists a scan target, and no MCP call can widen the scope
(spec §7.2, invariant 2); `list_changes` still shows their audit entries.

This module reads subnets and reserved ranges straight from the models rather than
through `services/subnets`, which imports it.
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import conflict, not_found, validation_error
from ..ipnet import format_intervals, parse_cidr, parse_ip, subtract_intervals, usable_bounds
from ..models import Agent, ReservedRange, ScanTarget, Subnet
from ..schemas import ScanTargetCreate, ScanTargetOut, ScanTargetUpdate
from . import changes

# spec §5.2; the order here is the order methods are stored in
SCAN_METHODS = ("icmp", "arp", "rdns", "fdns")
# an address is alive if it answers one of these; rdns and fdns say nothing about it
LIVENESS_METHODS = frozenset({"icmp", "arp"})
# spec §5.6: no scan target for a prefix shorter than this
MIN_PREFIX = 20


# --- Scope ---


def _scope(
    subnet: Subnet, reserved: list[ReservedRange], scan_dhcp_pool: bool
) -> list[tuple[int, int]]:
    """Usable addresses (network and broadcast already out, for /30 and shorter),
    minus the DHCP pool unless it is scanned, minus every excluded reserved range.
    A reserved range that is not excluded removes nothing, so an address in both an
    excluded and a scanned range is excluded."""
    removed = [(b.start_int, b.end_int) for b in reserved if b.scan_excluded]
    if not scan_dhcp_pool and subnet.dhcp_start and subnet.dhcp_end:
        removed.append((int(parse_ip(subnet.dhcp_start)), int(parse_ip(subnet.dhcp_end))))
    return subtract_intervals(usable_bounds(parse_cidr(subnet.cidr)), removed)


async def scan_scope(session: AsyncSession, subnet: Subnet) -> list[tuple[int, int]]:
    """The subnet's scan scope: merged, inclusive integer address ranges, ascending.
    A subnet without a scan target gets the default scope (DHCP pool not scanned)."""
    target = await target_for_subnet(session, subnet.id)
    reserved = list(
        (
            await session.scalars(
                select(ReservedRange)
                .where(ReservedRange.subnet_id == subnet.id)
                .order_by(ReservedRange.start_int)
            )
        ).all()
    )
    return _scope(subnet, reserved, bool(target and target.scan_dhcp_pool))


async def scan_scope_ranges(session: AsyncSession, subnet: Subnet) -> list[dict]:
    """`scan_scope` as `[{start, end}]` address strings, the shape of a job's ranges."""
    return format_intervals(parse_cidr(subnet.cidr), await scan_scope(session, subnet))


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


async def target_out(session: AsyncSession, target: ScanTarget) -> dict:
    subnet = await _get_subnet(session, target.subnet_id)
    columns = {c.name: getattr(target, c.name) for c in target.__table__.columns}
    scope = await scan_scope_ranges(session, subnet)
    return ScanTargetOut.model_validate({**columns, "scope": scope}).model_dump(mode="json")


def _label(subnet: Subnet) -> str:
    return f"scan target of subnet {subnet.name} ({subnet.cidr})"


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
    # #11: when a change puts addresses out of scope (scan_dhcp_pool turned off), their
    # observations and dismissals are deleted here, in this transaction (spec §5.3).
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
    await session.commit()


async def delete_for_subnet(session: AsyncSession, subnet: Subnet, source: str) -> None:
    """Delete the subnet's scan target, if any, as part of deleting the subnet. Does not
    commit: `subnets.delete_subnet` does, after deleting the subnet itself."""
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
