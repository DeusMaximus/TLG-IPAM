from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Change

# internal columns kept out of audit snapshots; the audit log is readable over MCP,
# so a token's hash never goes in it, nor the error text an agent reported with its
# last result set (`scan_targets.last_scan_error`, spec §7.2 invariant 8)
_SNAPSHOT_EXCLUDE = {
    "ip_int",
    "start_int",
    "end_int",
    "name_key",
    "token_hash",
    "last_scan_error",
}

# What an agent reports about itself at check-in: free text from a credential the
# owner does not type, kept in `agent` snapshots. Over MCP the audit log reaches an
# assistant that may hold other tools, so `assistant_view` withholds these values
# there (spec §7.2, invariant 8). Owner REST shows them.
AGENT_REPORTED_FIELDS = ("version", "platform", "hostname", "interfaces")
WITHHELD = "(reported by the agent; withheld over MCP, shown in the web UI)"


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


def assistant_view(change: dict) -> dict:
    """A serialised change as MCP returns it: in `agent` snapshots, whoever wrote the
    entry, each agent-reported value that is set is replaced by WITHHELD. The field
    names stay, so an assistant can still say what changed."""
    if change.get("entity_type") != "agent":
        return change
    out = dict(change)
    for side in ("before", "after"):
        snap = out.get(side)
        if isinstance(snap, dict):
            out[side] = {
                k: WITHHELD if k in AGENT_REPORTED_FIELDS and v is not None else v
                for k, v in snap.items()
            }
    return out
