from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import conflict, not_found, validation_error
from ..ipnet import (
    exclusion_intervals,
    interval_size,
    iter_free,
    merge_intervals,
    parse_cidr,
    parse_ip,
    usable_bounds,
)
from ..models import Host, ReservedRange, Subnet
from ..schemas import ReservedRangeCreate, ReservedRangeOut, SubnetCreate, SubnetOut, SubnetUpdate
from . import changes
from .sections import get_section


async def get_subnet(session: AsyncSession, subnet_id: str) -> Subnet:
    subnet = await session.get(Subnet, subnet_id)
    if subnet is None:
        raise not_found("Subnet", subnet_id)
    return subnet


async def list_subnets(session: AsyncSession, section_id: str | None = None) -> list[Subnet]:
    query = select(Subnet).order_by(Subnet.name)
    if section_id:
        query = query.where(Subnet.section_id == section_id)
    return list((await session.scalars(query)).all())


async def _allocated_ints(session: AsyncSession, subnet_id: str) -> set[int]:
    return set((await session.scalars(select(Host.ip_int).where(Host.subnet_id == subnet_id))).all())


async def reserved_ranges(session: AsyncSession, subnet_id: str) -> list[ReservedRange]:
    query = (
        select(ReservedRange)
        .where(ReservedRange.subnet_id == subnet_id)
        .order_by(ReservedRange.start_int)
    )
    return list((await session.scalars(query)).all())


def _stats(subnet: Subnet, allocated: set[int], reserved: list[ReservedRange]) -> dict:
    """used = host records; usable = addresses excl. network/broadcast;
    free = allocatable right now (usable minus gateway, DHCP pool, reserved
    ranges, and allocated)."""
    net = parse_cidr(subnet.cidr)
    first, last = usable_bounds(net)
    usable = last - first + 1
    excluded = exclusion_intervals(subnet, reserved)
    blocked = merge_intervals(
        [*excluded, *((ip, ip) for ip in allocated)], lower=first, upper=last
    )
    free = usable - interval_size(blocked)
    return {"used": len(allocated), "usable": usable, "free": free}


async def subnet_stats(session: AsyncSession, subnet: Subnet) -> dict:
    allocated = await _allocated_ints(session, subnet.id)
    reserved = await reserved_ranges(session, subnet.id)
    return _stats(subnet, allocated, reserved)


async def _grouped_allocated(session: AsyncSession, subnet_ids: list[str]) -> dict[str, set[int]]:
    rows = await session.execute(
        select(Host.subnet_id, Host.ip_int).where(Host.subnet_id.in_(subnet_ids))
    )
    grouped: dict[str, set[int]] = {}
    for subnet_id, ip_int in rows.all():
        grouped.setdefault(subnet_id, set()).add(ip_int)
    return grouped


async def _grouped_reserved(
    session: AsyncSession, subnet_ids: list[str]
) -> dict[str, list[ReservedRange]]:
    rows = await session.scalars(
        select(ReservedRange)
        .where(ReservedRange.subnet_id.in_(subnet_ids))
        .order_by(ReservedRange.start_int)
    )
    grouped: dict[str, list[ReservedRange]] = {}
    for block in rows:
        grouped.setdefault(block.subnet_id, []).append(block)
    return grouped


def _subnet_dict(subnet: Subnet, stats: dict, reserved: list[ReservedRange]) -> dict:
    # build from columns, not the ORM object: the reserved_ranges relationship would
    # otherwise lazy-load (not allowed under the async session)
    columns = {c.name: getattr(subnet, c.name) for c in subnet.__table__.columns}
    ranges = [ReservedRangeOut.model_validate(r).model_dump() for r in reserved]
    return SubnetOut.model_validate(
        {**columns, **stats, "reserved_ranges": ranges}
    ).model_dump(mode="json")


async def subnets_out(session: AsyncSession, subnets: list[Subnet]) -> list[dict]:
    """Serialised subnets with utilisation stats and reserved ranges — the shape
    shared by the REST API and MCP tools. Batches the per-subnet host and
    reserved-range lookups into two queries regardless of subnet count."""
    ids = [s.id for s in subnets]
    allocated = await _grouped_allocated(session, ids) if ids else {}
    reserved = await _grouped_reserved(session, ids) if ids else {}
    return [
        _subnet_dict(
            s,
            _stats(s, allocated.get(s.id, set()), reserved.get(s.id, [])),
            reserved.get(s.id, []),
        )
        for s in subnets
    ]


async def subnet_out(session: AsyncSession, subnet: Subnet) -> dict:
    return (await subnets_out(session, [subnet]))[0]


async def free_ips(session: AsyncSession, subnet_id: str, limit: int = 100) -> list[str]:
    subnet = await get_subnet(session, subnet_id)
    allocated = await _allocated_ints(session, subnet_id)
    reserved = await reserved_ranges(session, subnet_id)
    return iter_free(subnet, allocated, reserved, limit=limit)


async def _validate_subnet(
    session: AsyncSession,
    *,
    section_id: str,
    cidr: str,
    gateway: str | None,
    dhcp_start: str | None,
    dhcp_end: str | None,
    exclude_id: str | None = None,
):
    net = parse_cidr(cidr)
    others = await session.scalars(select(Subnet).where(Subnet.section_id == section_id))
    for other in others:
        if other.id == exclude_id:
            continue
        if net.overlaps(parse_cidr(other.cidr)):
            raise conflict(
                f"Subnet {cidr} overlaps {other.name} ({other.cidr}) in the same section"
            )
    if gateway and parse_ip(gateway) not in net:
        raise validation_error(f"Gateway {gateway} is not within subnet {cidr}")
    if (dhcp_start is None) != (dhcp_end is None):
        raise validation_error("dhcp_start and dhcp_end must both be set or both be empty")
    if dhcp_start and dhcp_end:
        start, end = parse_ip(dhcp_start), parse_ip(dhcp_end)
        if start not in net or end not in net:
            raise validation_error(f"DHCP pool {dhcp_start}-{dhcp_end} is not within subnet {cidr}")
        if int(start) > int(end):
            raise validation_error("dhcp_start must not be greater than dhcp_end")
    return net


async def create_subnet(session: AsyncSession, payload: SubnetCreate, source: str) -> Subnet:
    await get_section(session, payload.section_id)
    await _validate_subnet(
        session,
        section_id=payload.section_id,
        cidr=payload.cidr,
        gateway=payload.gateway,
        dhcp_start=payload.dhcp_start,
        dhcp_end=payload.dhcp_end,
    )
    subnet = Subnet(**payload.model_dump())
    session.add(subnet)
    await session.flush()
    changes.record(
        session,
        entity_type="subnet",
        entity_id=subnet.id,
        action="create",
        summary=f"subnet {subnet.name} ({subnet.cidr}) created",
        source=source,
        after=changes.snapshot(subnet),
    )
    await session.commit()
    return subnet


async def update_subnet(
    session: AsyncSession, subnet_id: str, payload: SubnetUpdate, source: str
) -> Subnet:
    subnet = await get_subnet(session, subnet_id)
    data = payload.model_dump(exclude_unset=True)
    merged = {
        "section_id": data.get("section_id", subnet.section_id),
        "cidr": data.get("cidr", subnet.cidr),
        "gateway": data.get("gateway", subnet.gateway),
        "dhcp_start": data.get("dhcp_start", subnet.dhcp_start),
        "dhcp_end": data.get("dhcp_end", subnet.dhcp_end),
    }
    if "section_id" in data:
        await get_section(session, data["section_id"])
    net = await _validate_subnet(session, **merged, exclude_id=subnet.id)
    if merged["cidr"] != subnet.cidr:
        first, last = usable_bounds(net)
        outside_hosts = await session.scalar(
            select(func.count(Host.id)).where(
                Host.subnet_id == subnet.id,
                (Host.ip_int < first) | (Host.ip_int > last),
            )
        )
        if outside_hosts:
            raise validation_error(
                f"Cannot change CIDR to {merged['cidr']}: "
                f"{outside_hosts} existing host(s) would not be assignable in it"
            )
        outside_ranges = await session.scalar(
            select(func.count(ReservedRange.id)).where(
                ReservedRange.subnet_id == subnet.id,
                (ReservedRange.start_int < int(net.network_address))
                | (ReservedRange.end_int > int(net.broadcast_address)),
            )
        )
        if outside_ranges:
            raise validation_error(
                f"Cannot change CIDR to {merged['cidr']}: "
                f"{outside_ranges} reserved range(s) would fall outside it"
            )
    before = changes.snapshot(subnet)
    for field, value in data.items():
        setattr(subnet, field, value)
    changes.record(
        session,
        entity_type="subnet",
        entity_id=subnet.id,
        action="update",
        summary=f"subnet {subnet.name} ({subnet.cidr}) updated",
        source=source,
        before=before,
        after=changes.snapshot(subnet),
    )
    await session.commit()
    return subnet


async def delete_subnet(
    session: AsyncSession, subnet_id: str, source: str, force: bool = False
) -> None:
    subnet = await get_subnet(session, subnet_id)
    hosts = list(
        (await session.scalars(select(Host).where(Host.subnet_id == subnet_id))).all()
    )
    if hosts and not force:
        raise conflict(f"Subnet {subnet.name} still has {len(hosts)} host(s)")
    before = changes.snapshot(subnet)
    summary = f"subnet {subnet.name} ({subnet.cidr}) deleted"
    if hosts:
        before["hosts"] = [changes.snapshot(h) for h in hosts]
        summary += f" along with {len(hosts)} host(s)"
        for host in hosts:
            await session.delete(host)
    changes.record(
        session,
        entity_type="subnet",
        entity_id=subnet.id,
        action="delete",
        summary=summary,
        source=source,
        before=before,
    )
    for block in await reserved_ranges(session, subnet_id):
        await session.delete(block)
    await session.delete(subnet)
    await session.commit()


async def create_reserved_range(
    session: AsyncSession, subnet_id: str, payload: ReservedRangeCreate, source: str
) -> ReservedRange:
    subnet = await get_subnet(session, subnet_id)
    net = parse_cidr(subnet.cidr)
    start, end = parse_ip(payload.start_ip), parse_ip(payload.end_ip)
    if start not in net or end not in net:
        raise validation_error(
            f"Reserved range {payload.start_ip}-{payload.end_ip} is not within subnet {subnet.cidr}"
        )
    if int(start) > int(end):
        raise validation_error("start_ip must not be greater than end_ip")
    block = ReservedRange(
        subnet_id=subnet_id,
        start_ip=payload.start_ip,
        end_ip=payload.end_ip,
        start_int=int(start),
        end_int=int(end),
        description=payload.description,
    )
    session.add(block)
    await session.flush()
    changes.record(
        session,
        entity_type="subnet",
        entity_id=subnet.id,
        action="update",
        summary=(
            f"reserved range {block.start_ip}-{block.end_ip} added to {subnet.name}"
            + (f" ({block.description})" if block.description else "")
        ),
        source=source,
        after=changes.snapshot(block),
    )
    await session.commit()
    return block


async def delete_reserved_range(
    session: AsyncSession, subnet_id: str, range_id: str, source: str
) -> None:
    subnet = await get_subnet(session, subnet_id)
    block = await session.get(ReservedRange, range_id)
    if block is None or block.subnet_id != subnet_id:
        raise not_found("Reserved range", range_id)
    changes.record(
        session,
        entity_type="subnet",
        entity_id=subnet.id,
        action="update",
        summary=f"reserved range {block.start_ip}-{block.end_ip} removed from {subnet.name}",
        source=source,
        before=changes.snapshot(block),
    )
    await session.delete(block)
    await session.commit()
