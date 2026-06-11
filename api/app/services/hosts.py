from collections import Counter

from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import ApiError, conflict, not_found, validation_error
from ..ipnet import ip_in_subnet, iter_free, parse_cidr
from ..models import Host, Subnet
from ..schemas import AllocateRequest, BulkHostItem, HostCreate, HostOut, HostUpdate
from . import changes
from . import device_types as device_types_svc
from .subnets import _allocated_ints, get_subnet, reserved_ranges


def _host_dict(host: Host, subnet_domain: str | None) -> dict:
    effective = host.domain or subnet_domain
    fqdn = f"{host.hostname}.{effective}" if host.hostname and effective else None
    return HostOut.model_validate(host).model_copy(update={"fqdn": fqdn}).model_dump(mode="json")


async def _subnet_domains(session: AsyncSession, subnet_ids: set[str]) -> dict[str, str | None]:
    if not subnet_ids:
        return {}
    rows = await session.execute(
        select(Subnet.id, Subnet.domain).where(Subnet.id.in_(subnet_ids))
    )
    return dict(rows.all())


async def hosts_out(session: AsyncSession, hosts: list[Host]) -> list[dict]:
    """Serialised hosts with computed FQDNs — the shape shared by REST and MCP."""
    domains = await _subnet_domains(session, {h.subnet_id for h in hosts})
    return [_host_dict(h, domains.get(h.subnet_id)) for h in hosts]


async def host_out(session: AsyncSession, host: Host) -> dict:
    return (await hosts_out(session, [host]))[0]


async def get_host(session: AsyncSession, host_id: str) -> Host:
    host = await session.get(Host, host_id)
    if host is None:
        raise not_found("Host", host_id)
    return host


async def list_hosts(
    session: AsyncSession,
    subnet_id: str | None = None,
    status: str | None = None,
    q: str | None = None,
) -> list[Host]:
    query = select(Host).order_by(Host.ip_int)
    if subnet_id:
        query = query.where(Host.subnet_id == subnet_id)
    if status:
        query = query.where(Host.status == status)
    if q:
        pattern = f"%{q}%"
        query = query.where(
            or_(Host.hostname.ilike(pattern), Host.ip.like(pattern), Host.mac.like(pattern))
        )
    return list((await session.scalars(query)).all())


def _check_not_reserved(ip_int: int, subnet: Subnet) -> None:
    net = parse_cidr(subnet.cidr)
    if net.prefixlen <= 30 and ip_int in (int(net.network_address), int(net.broadcast_address)):
        raise validation_error(
            f"IP is the {'network' if ip_int == int(net.network_address) else 'broadcast'} "
            f"address of {subnet.cidr} and cannot be assigned to a host"
        )


def _label(host: Host) -> str:
    return host.hostname or host.ip


async def _flush_host(session: AsyncSession, host: Host) -> None:
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise conflict(f"IP {host.ip} is already allocated in this subnet") from None


async def _commit_host(session: AsyncSession, host: Host) -> None:
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise conflict(f"IP {host.ip} is already allocated in this subnet") from None


async def create_host(session: AsyncSession, payload: HostCreate, source: str) -> Host:
    subnet = await get_subnet(session, payload.subnet_id)
    addr = ip_in_subnet(payload.ip, subnet.cidr)
    _check_not_reserved(int(addr), subnet)
    data = payload.model_dump()
    data["device_type"] = await device_types_svc.resolve(session, data["device_type"])
    host = Host(**data, ip_int=int(addr))
    session.add(host)
    await _flush_host(session, host)
    changes.record(
        session,
        entity_type="host",
        entity_id=host.id,
        action="create",
        summary=f"host {_label(host)} created at {host.ip} in {subnet.name}",
        source=source,
        after=changes.snapshot(host),
    )
    await _commit_host(session, host)
    return host


async def update_host(
    session: AsyncSession, host_id: str, payload: HostUpdate, source: str
) -> Host:
    host = await get_host(session, host_id)
    before = changes.snapshot(host)
    data = payload.model_dump(exclude_unset=True)
    if data.get("device_type") is None:
        data.pop("device_type", None)
    else:
        data["device_type"] = await device_types_svc.resolve(session, data["device_type"])
    if "ip" in data and data["ip"] != host.ip:
        subnet = await get_subnet(session, host.subnet_id)
        addr = ip_in_subnet(data["ip"], subnet.cidr)
        _check_not_reserved(int(addr), subnet)
        host.ip_int = int(addr)
    for field, value in data.items():
        setattr(host, field, value)
    changes.record(
        session,
        entity_type="host",
        entity_id=host.id,
        action="update",
        summary=f"host {_label(host)} ({host.ip}) updated",
        source=source,
        before=before,
        after=changes.snapshot(host),
    )
    await _commit_host(session, host)
    return host


async def delete_host(session: AsyncSession, host_id: str, source: str) -> None:
    host = await get_host(session, host_id)
    before = changes.snapshot(host)
    changes.record(
        session,
        entity_type="host",
        entity_id=host.id,
        action="delete",
        summary=f"host {_label(host)} deleted, freeing {host.ip}",
        source=source,
        before=before,
    )
    await session.delete(host)
    await session.commit()


async def bulk_create(
    session: AsyncSession, subnet_id: str, items: list[dict], source: str
) -> dict:
    """Import many hosts in one transaction. Each record succeeds or fails on its
    own — duplicates are skipped and invalid records error, without aborting the
    batch. Returns per-record results plus counts, and writes ONE audit entry."""
    subnet = await get_subnet(session, subnet_id)
    taken = await _allocated_ints(session, subnet_id)
    valid_types = await device_types_svc._canonical_map(session)
    results: list[dict] = []
    created: list[tuple[dict, Host]] = []

    for index, raw in enumerate(items):
        ip = raw.get("ip") if isinstance(raw, dict) else None
        result = {"index": index, "ip": ip}
        try:
            if not isinstance(raw, dict):
                raise validation_error("record must be an object")
            item = BulkHostItem(**raw)
            result["ip"] = item.ip
            canonical_type = valid_types.get(item.device_type.strip().lower())
            if canonical_type is None:
                raise validation_error(
                    f"unknown device type {item.device_type!r}. "
                    f"Valid types: {', '.join(sorted(valid_types.values()))}"
                )
            item.device_type = canonical_type
            addr = ip_in_subnet(item.ip, subnet.cidr)
            _check_not_reserved(int(addr), subnet)
        except ValidationError as exc:
            first = exc.errors()[0]
            field = ".".join(str(p) for p in first["loc"])
            results.append({**result, "status": "error", "reason": f"{field}: {first['msg']}"})
            continue
        except ApiError as exc:
            results.append({**result, "status": "error", "reason": exc.message})
            continue
        if int(addr) in taken:
            results.append({**result, "status": "skipped", "reason": "duplicate IP"})
            continue
        host = Host(subnet_id=subnet_id, ip_int=int(addr), **item.model_dump())
        session.add(host)
        taken.add(int(addr))
        result["status"] = "created"
        results.append(result)
        created.append((result, host))

    if created:
        await session.flush()
        for result, host in created:
            result["host_id"] = host.id

    counts = Counter(r["status"] for r in results)
    summary = (
        f"bulk import into {subnet.name}: {counts.get('created', 0)} created, "
        f"{counts.get('skipped', 0)} skipped, {counts.get('error', 0)} failed"
    )
    changes.record(
        session,
        entity_type="subnet",
        entity_id=subnet.id,
        action="update",
        summary=summary,
        source=source,
        after={"results": results},
    )
    await session.commit()
    return {
        "created": counts.get("created", 0),
        "skipped": counts.get("skipped", 0),
        "errors": counts.get("error", 0),
        "results": results,
    }


async def bulk_delete(session: AsyncSession, host_ids: list[str], source: str) -> dict:
    """Delete many hosts at once. One audit entry per affected subnet, carrying the
    full before-snapshots of everything removed."""
    ids = list(dict.fromkeys(host_ids))
    hosts = list((await session.scalars(select(Host).where(Host.id.in_(ids)))).all())
    found = {h.id for h in hosts}
    not_found = [i for i in ids if i not in found]

    by_subnet: dict[str, list[Host]] = {}
    for host in hosts:
        by_subnet.setdefault(host.subnet_id, []).append(host)

    for subnet_id, group in by_subnet.items():
        subnet = await session.get(Subnet, subnet_id)
        subnet_name = subnet.name if subnet else subnet_id
        changes.record(
            session,
            entity_type="subnet",
            entity_id=subnet_id,
            action="update",
            summary=f"bulk delete from {subnet_name}: {len(group)} host(s) removed",
            source=source,
            before={"hosts": [changes.snapshot(h) for h in group]},
        )
        for host in group:
            await session.delete(host)

    await session.commit()
    return {"deleted": len(hosts), "not_found": not_found}


async def allocate_next(
    session: AsyncSession, subnet_id: str, payload: AllocateRequest, source: str
) -> Host:
    subnet = await get_subnet(session, subnet_id)
    allocated = await _allocated_ints(session, subnet_id)
    reserved = await reserved_ranges(session, subnet_id)
    candidates = iter_free(subnet, allocated, reserved, limit=1)
    if not candidates:
        raise conflict(f"No free IPs available in subnet {subnet.name} ({subnet.cidr})")
    ip = candidates[0]
    addr = ip_in_subnet(ip, subnet.cidr)
    data = payload.model_dump()
    data["device_type"] = await device_types_svc.resolve(session, data["device_type"])
    host = Host(subnet_id=subnet_id, ip=ip, ip_int=int(addr), **data)
    session.add(host)
    await _flush_host(session, host)
    changes.record(
        session,
        entity_type="host",
        entity_id=host.id,
        action="create",
        summary=f"host {_label(host)} allocated next free IP {ip} in {subnet.name}",
        source=source,
        after=changes.snapshot(host),
    )
    await _commit_host(session, host)
    return host
