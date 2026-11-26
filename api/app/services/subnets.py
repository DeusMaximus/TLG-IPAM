from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import ApiError, conflict, not_found, validation_error
from ..ipnet import (
    canonical_cidr,
    exclusion_intervals,
    interval_size,
    iter_free,
    merge_intervals,
    parse_cidr,
    parse_ip,
    usable_bounds,
)
from ..models import Host, ReservedRange, Subnet
from ..schemas import (
    SUBNET_DNS_SERVERS_MAX,
    SUBNET_RESERVED_RANGES_MAX,
    ReservedRangeCreate,
    ReservedRangeOut,
    ReservedRangeUpdate,
    SubnetCreate,
    SubnetOut,
    SubnetUpdate,
)
from . import changes, scans
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


def _subnet_dict(
    subnet: Subnet, stats: dict, reserved: list[ReservedRange], scan_dhcp_pool: bool | None
) -> dict:
    # build from columns, not the ORM object: the reserved_ranges relationship would
    # otherwise lazy-load (not allowed under the async session)
    columns = {c.name: getattr(subnet, c.name) for c in subnet.__table__.columns}
    ranges = [ReservedRangeOut.model_validate(r).model_dump() for r in reserved]
    return SubnetOut.model_validate(
        {**columns, **stats, "reserved_ranges": ranges, "scan_dhcp_pool": scan_dhcp_pool}
    ).model_dump(mode="json")


async def subnets_out(session: AsyncSession, subnets: list[Subnet]) -> list[dict]:
    """Serialised subnets with utilisation stats, reserved ranges (each with
    `scan_excluded`) and `scan_dhcp_pool` — the shape shared by the REST API and MCP
    tools. Batches the per-subnet host, reserved-range and scan-target lookups into
    three queries regardless of subnet count."""
    ids = [s.id for s in subnets]
    allocated = await _grouped_allocated(session, ids) if ids else {}
    reserved = await _grouped_reserved(session, ids) if ids else {}
    targets = await scans.targets_by_subnet(session, ids)
    return [
        _subnet_dict(
            s,
            _stats(s, allocated.get(s.id, set()), reserved.get(s.id, [])),
            reserved.get(s.id, []),
            targets[s.id].scan_dhcp_pool if s.id in targets else None,
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
    """The checks a subnet's fields must pass on create and update, and its network.
    Networks are compared, never strings, so another spelling of a subnet already in
    the section overlaps it as a duplicate; messages name each network in its
    canonical spelling (`ipnet.canonical_cidr`), whatever was typed or is stored."""
    net = parse_cidr(cidr)
    cidr = str(net)
    others = await session.scalars(select(Subnet).where(Subnet.section_id == section_id))
    for other in others:
        if other.id == exclude_id:
            continue
        other_net = parse_cidr(other.cidr)
        if net.overlaps(other_net):
            raise conflict(
                f"Subnet {cidr} overlaps {other.name} ({other_net}) in the same section"
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


def _check_dns_servers(servers: list[str] | None, stored: list | None = None) -> None:
    """A subnet lists at most SUBNET_DNS_SERVERS_MAX DNS servers. An update that resends
    the list the subnet already holds is let through unchanged, so a subnet stored with
    a longer list before the bound existed can still be edited (the UI's form resends
    every field) and loses nothing; changing that list means meeting the bound."""
    if servers is None or len(servers) <= SUBNET_DNS_SERVERS_MAX or servers == stored:
        return
    raise validation_error(
        f"dns_servers: a subnet may list at most {SUBNET_DNS_SERVERS_MAX} DNS servers, "
        f"not {len(servers)}"
    )


async def create_subnet(session: AsyncSession, payload: SubnetCreate, source: str) -> Subnet:
    _check_dns_servers(payload.dns_servers)
    await get_section(session, payload.section_id)
    net = await _validate_subnet(
        session,
        section_id=payload.section_id,
        cidr=payload.cidr,
        gateway=payload.gateway,
        dhcp_start=payload.dhcp_start,
        dhcp_end=payload.dhcp_end,
    )
    # stored in its canonical spelling, whatever was typed (#97)
    subnet = Subnet(**{**payload.model_dump(), "cidr": str(net)})
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
    if "dns_servers" in data:
        _check_dns_servers(data["dns_servers"], subnet.dns_servers)
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
    if "cidr" in data:
        # Stored in its canonical spelling (#97). A subnet stored with another spelling
        # before then keeps it until an update sends `cidr`, as the UI's form does on
        # every edit; that update stores the canonical one, and its entry shows both.
        data["cidr"] = str(net)
    # the network, not the spelling: respelling the stored network changes nothing
    if net != parse_cidr(subnet.cidr):
        first, last = usable_bounds(net)
        outside_hosts = await session.scalar(
            select(func.count(Host.id)).where(
                Host.subnet_id == subnet.id,
                (Host.ip_int < first) | (Host.ip_int > last),
            )
        )
        if outside_hosts:
            raise validation_error(
                f"Cannot change CIDR to {net}: "
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
                f"Cannot change CIDR to {net}: "
                f"{outside_ranges} reserved range(s) would fall outside it"
            )
        if await scans.target_for_subnet(session, subnet.id) is not None:
            scans.check_prefix(str(net))
    before = changes.snapshot(subnet)
    for field, value in data.items():
        setattr(subnet, field, value)
    # a CIDR or DHCP-pool edit can put addresses out of scan scope (spec §5.3)
    await scans.drop_out_of_scope(session, subnet)
    changes.record(
        session,
        entity_type="subnet",
        entity_id=subnet.id,
        action="update",
        summary=f"subnet {subnet.name} ({canonical_cidr(subnet.cidr)}) updated",
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
    summary = f"subnet {subnet.name} ({canonical_cidr(subnet.cidr)}) deleted"
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
    await scans.delete_for_subnet(session, subnet, source)
    await session.delete(subnet)
    await session.commit()


async def create_reserved_range(
    session: AsyncSession,
    subnet_id: str,
    payload: ReservedRangeCreate,
    source: str,
    *,
    actor: str | None = None,
) -> ReservedRange:
    subnet = await get_subnet(session, subnet_id)
    net = parse_cidr(subnet.cidr)
    start, end = parse_ip(payload.start_ip), parse_ip(payload.end_ip)
    if start not in net or end not in net:
        raise validation_error(
            f"Reserved range {payload.start_ip}-{payload.end_ip} is not within subnet {net}"
        )
    if int(start) > int(end):
        raise validation_error("start_ip must not be greater than end_ip")
    # Counted under the write lock, so two adds at once cannot both pass the limit.
    await scans.take_write_lock(session)
    held = await session.scalar(
        select(func.count(ReservedRange.id)).where(ReservedRange.subnet_id == subnet_id)
    )
    if held >= SUBNET_RESERVED_RANGES_MAX:
        await session.rollback()
        raise validation_error(
            f"reserved_ranges: a subnet holds at most {SUBNET_RESERVED_RANGES_MAX} "
            "reserved ranges; delete one before adding another"
        )
    block = ReservedRange(
        subnet_id=subnet_id,
        start_ip=payload.start_ip,
        end_ip=payload.end_ip,
        start_int=int(start),
        end_int=int(end),
        description=payload.description,
        scan_excluded=payload.scan_excluded,
    )
    session.add(block)
    await session.flush()
    if block.scan_excluded:
        await scans.drop_out_of_scope(session, subnet)
    changes.record(
        session,
        entity_type="subnet",
        entity_id=subnet.id,
        action="update",
        summary=(
            f"reserved range {block.start_ip}-{block.end_ip} added to {subnet.name}"
            + (f" ({block.description})" if block.description else "")
            + (", excluded from scanning" if block.scan_excluded else "")
        ),
        source=source,
        actor=actor,
        after=changes.snapshot(block),
    )
    await session.commit()
    return block


async def _get_reserved_range(
    session: AsyncSession, subnet_id: str, range_id: str
) -> tuple[Subnet, ReservedRange]:
    subnet = await get_subnet(session, subnet_id)
    block = await session.get(ReservedRange, range_id)
    if block is None or block.subnet_id != subnet_id:
        raise not_found("Reserved range", range_id)
    return subnet, block


async def update_reserved_range(
    session: AsyncSession, subnet_id: str, range_id: str, payload: ReservedRangeUpdate, source: str
) -> ReservedRange:
    """Set `scan_excluded`, the range's part in the subnet's scan scope (spec §5.3).
    REST only: scan scope is not configurable over MCP."""
    subnet, block = await _get_reserved_range(session, subnet_id, range_id)
    if bool(block.scan_excluded) == payload.scan_excluded:
        return block
    before = changes.snapshot(block)
    block.scan_excluded = payload.scan_excluded
    # excluding a range puts it out of scan scope (spec §5.3)
    await scans.drop_out_of_scope(session, subnet)
    verb = "excluded from scanning" if payload.scan_excluded else "returned to scanning"
    changes.record(
        session,
        entity_type="subnet",
        entity_id=subnet.id,
        action="update",
        summary=f"reserved range {block.start_ip}-{block.end_ip} in {subnet.name} {verb}",
        source=source,
        before=before,
        after=changes.snapshot(block),
    )
    await session.commit()
    return block


async def delete_reserved_range(
    session: AsyncSession, subnet_id: str, range_id: str, source: str, *, actor: str | None = None
) -> None:
    subnet, block = await _get_reserved_range(session, subnet_id, range_id)
    if block.scan_excluded and source == "mcp":
        # deleting an excluded range widens what is scanned, and no MCP call may do that
        # (spec §7.2, invariant 2)
        raise ApiError(
            "FORBIDDEN",
            f"Reserved range {block.start_ip}-{block.end_ip} is excluded from scanning, and "
            "deleting it would widen what is scanned. Scan scope cannot be changed over "
            "MCP: remove this range in the TLG-IPAM web UI instead.",
            403,
        )
    changes.record(
        session,
        entity_type="subnet",
        entity_id=subnet.id,
        action="update",
        summary=f"reserved range {block.start_ip}-{block.end_ip} removed from {subnet.name}",
        source=source,
        actor=actor,
        before=changes.snapshot(block),
    )
    await session.delete(block)
    await session.commit()
