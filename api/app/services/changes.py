from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Change
from ..times import utc

# internal columns kept out of audit snapshots; the audit log is readable over MCP,
# so a token's hash never goes in it (an agent's, an OAuth code's or token's, or an
# OAuth client secret's), nor the error text an agent reported with its last result
# set (`scan_targets.last_scan_error`, spec §7.2 invariant 8)
_SNAPSHOT_EXCLUDE = {
    "ip_int",
    "start_int",
    "end_int",
    "name_key",
    "token_hash",
    "client_secret_hash",
    "last_scan_error",
    # the names a leased job was sent (`scan_targets.job_fdns`): up to a job's worth of
    # FQDNs, scheduling state that is cleared with the lease
    "job_fdns",
}

# What an agent reports about itself at check-in: free text from a credential the
# owner does not type, kept in `agent` snapshots. Over MCP the audit log reaches an
# assistant that may hold other tools, so `assistant_view` withholds these values
# there (spec §7.2, invariant 8). Owner REST shows them.
AGENT_REPORTED_FIELDS = ("version", "platform", "hostname", "interfaces")
WITHHELD = "(reported by the agent; withheld over MCP, shown in the web UI)"
# What an OAuth client chose and a `grant` snapshot keeps: the redirect URI its link
# was approved through, whose path and query are the client's own text (#26). Withheld
# over MCP by the same rule.
CLIENT_SUPPLIED_FIELDS = ("redirect_uri",)
CLIENT_WITHHELD = "(supplied by the client; withheld over MCP, shown in the web UI)"
_WITHHELD_OVER_MCP = {
    "agent": (AGENT_REPORTED_FIELDS, WITHHELD),
    "grant": (CLIENT_SUPPLIED_FIELDS, CLIENT_WITHHELD),
}


def snapshot(obj) -> dict:
    out = {}
    for column in obj.__table__.columns:
        if column.name in _SNAPSHOT_EXCLUDE:
            continue
        value = getattr(obj, column.name)
        # with its offset, as responses give it, whether the object was read back or
        # is still in memory (`times.utc`)
        out[column.name] = utc(value).isoformat() if isinstance(value, datetime) else value
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
    entry, each agent-reported value that is set is replaced by WITHHELD, and in
    `grant` snapshots the client's redirect URI by CLIENT_WITHHELD. The field names
    stay, so an assistant can still say what changed."""
    withheld = _WITHHELD_OVER_MCP.get(change.get("entity_type"))
    if withheld is None:
        return change
    fields, marker = withheld
    out = dict(change)
    for side in ("before", "after"):
        snap = out.get(side)
        if isinstance(snap, dict):
            out[side] = {
                k: marker if k in fields and v is not None else v for k, v in snap.items()
            }
    return out
