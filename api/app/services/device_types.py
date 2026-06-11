from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import conflict, not_found, validation_error
from ..models import DeviceType, Host
from ..schemas import DeviceTypeCreate, DeviceTypeUpdate
from . import changes

# the default for new hosts — always present, cannot be renamed or deleted
FALLBACK = "other"

DEFAULTS = [
    ("server", "server"),
    ("vm", "box"),
    ("lxc", "container"),
    ("network", "network"),
    ("iot", "cpu"),
    ("desktop", "monitor"),
    ("laptop", "laptop"),
    ("phone", "smartphone"),
    (FALLBACK, "help-circle"),
]


async def seed_defaults(session: AsyncSession) -> None:
    """Populate the starter set on an empty table (first boot / fresh database)."""
    count = await session.scalar(select(func.count(DeviceType.id)))
    if count:
        return
    for name, icon in DEFAULTS:
        session.add(DeviceType(name=name, icon=icon))
    await session.commit()


async def get_device_type(session: AsyncSession, type_id: str) -> DeviceType:
    device_type = await session.get(DeviceType, type_id)
    if device_type is None:
        raise not_found("Device type", type_id)
    return device_type


async def list_with_counts(session: AsyncSession) -> list[tuple[DeviceType, int]]:
    query = (
        select(DeviceType, func.count(Host.id))
        .outerjoin(Host, Host.device_type == DeviceType.name)
        .group_by(DeviceType.id)
        .order_by(DeviceType.name)
    )
    return [(device_type, count) for device_type, count in (await session.execute(query)).all()]


async def _canonical_map(session: AsyncSession) -> dict[str, str]:
    names = (await session.scalars(select(DeviceType.name))).all()
    return {n.lower(): n for n in names}


async def resolve(session: AsyncSession, name: str) -> str:
    """Case-insensitively resolve a device type to its canonical name, or reject."""
    mapping = await _canonical_map(session)
    canonical = mapping.get(name.strip().lower())
    if canonical is None:
        raise validation_error(
            f"Unknown device type {name!r}. Valid types: {', '.join(sorted(mapping.values()))}"
        )
    return canonical


async def create_device_type(
    session: AsyncSession, payload: DeviceTypeCreate, source: str
) -> DeviceType:
    name = payload.name.strip()
    if name.lower() in await _canonical_map(session):
        raise conflict(f"Device type {name} already exists")
    device_type = DeviceType(name=name, icon=payload.icon)
    session.add(device_type)
    await session.flush()
    changes.record(
        session,
        entity_type="device_type",
        entity_id=device_type.id,
        action="create",
        summary=f"device type {name} added",
        source=source,
        after=changes.snapshot(device_type),
    )
    await session.commit()
    return device_type


async def update_device_type(
    session: AsyncSession, type_id: str, payload: DeviceTypeUpdate, source: str
) -> DeviceType:
    device_type = await get_device_type(session, type_id)
    before = changes.snapshot(device_type)
    data = payload.model_dump(exclude_unset=True)
    renamed_from = None
    if data.get("name") and data["name"].strip() != device_type.name:
        if device_type.name == FALLBACK:
            raise validation_error(
                f"{FALLBACK!r} is the fallback type for new hosts and cannot be renamed"
            )
        new_name = data["name"].strip()
        mapping = await _canonical_map(session)
        existing = mapping.get(new_name.lower())
        if existing and existing != device_type.name:
            raise conflict(f"Device type {new_name} already exists")
        renamed_from = device_type.name
        data["name"] = new_name
    for field, value in data.items():
        if value is not None:
            setattr(device_type, field, value)

    summary = f"device type {device_type.name} updated"
    if renamed_from:
        result = await session.execute(
            update(Host).where(Host.device_type == renamed_from).values(device_type=device_type.name)
        )
        summary = (
            f"device type {renamed_from} renamed to {device_type.name} "
            f"({result.rowcount} host(s) updated)"
        )
    changes.record(
        session,
        entity_type="device_type",
        entity_id=device_type.id,
        action="update",
        summary=summary,
        source=source,
        before=before,
        after=changes.snapshot(device_type),
    )
    await session.commit()
    return device_type


async def delete_device_type(session: AsyncSession, type_id: str, source: str) -> None:
    device_type = await get_device_type(session, type_id)
    if device_type.name == FALLBACK:
        raise conflict(f"{FALLBACK!r} is the fallback type for new hosts and cannot be deleted")
    in_use = await session.scalar(
        select(func.count(Host.id)).where(Host.device_type == device_type.name)
    )
    if in_use:
        raise conflict(f"Device type {device_type.name} is used by {in_use} host(s)")
    changes.record(
        session,
        entity_type="device_type",
        entity_id=device_type.id,
        action="delete",
        summary=f"device type {device_type.name} deleted",
        source=source,
        before=changes.snapshot(device_type),
    )
    await session.delete(device_type)
    await session.commit()
