from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Change

# internal columns kept out of audit snapshots
_SNAPSHOT_EXCLUDE = {"ip_int", "start_int", "end_int"}


def snapshot(obj) -> dict:
    out = {}
    for column in obj.__table__.columns:
        if column.name in _SNAPSHOT_EXCLUDE:
            continue
        value = getattr(obj, column.name)
        out[column.name] = value.isoformat() if isinstance(value, datetime) else value
    return out


def record(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_id: str,
    action: str,
    summary: str,
    source: str,
    before: dict | None = None,
    after: dict | None = None,
    actor: str | None = None,
) -> None:
    session.add(
        Change(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            summary=summary,
            before=before,
            after=after,
            source=source,
            actor=actor,
        )
    )


async def list_changes(
    session: AsyncSession, entity_type: str | None = None, limit: int = 50
) -> list[Change]:
    query = select(Change).order_by(Change.ts.desc(), Change.id).limit(limit)
    if entity_type:
        query = query.where(Change.entity_type == entity_type)
    return list((await session.scalars(query)).all())
