"""Agent check-in, scheduling and job leases (v0.2 spec §5.1, §5.2, §5.7).

The server is the scheduler. At each check-in it stores what the agent reports and
hands back at most one due job. Issuing a job leases its target: `current_job_id`
and `job_issued_at` are set, and the target is not issued again until the lease is
cleared by an accepted result (#11) or expires.

The same two columns on the agent record the job it was issued. An agent is busy, and
gets no new job, while that lease is live. It is kept on the agent so that the owner
reassigning or deleting the target mid-scan does not free an agent still scanning.

A target is due when it is enabled, assigned to the agent, not under a live lease,
and either
- a scan was requested (`scan_requested_at`) after its last scan started, or
- it has an interval and has never been scanned, or its last scan started at least
  `interval_minutes` ago.

`last_scan_at` is when the last completed job was issued, by the server's clock
(written by results ingest, #11). Until a result clears a lease, the target stays
due, so an expired lease makes it due again.

Check-in's own writes are not audit-logged, as `tokens.last_used_at` is not: an
agent checks in every minute. A check-in that changes what the agent reports, or
its `last_error`, is logged once as an `agent` update with `source="agent"` and
`actor="agent:<name>"` (spec §4.3); the summary names the fields, never their values.
Issuing a lease is scheduling state and is not logged.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent_protocol import (
    CHECKIN_INTERVAL_S,
    JOB_TIMEOUT_S,
    PROTOCOL_VERSION,
    CheckinRequest,
    CheckinResponse,
    Job,
)
from ..errors import ApiError, validation_error
from ..ipnet import on_link, parse_cidr
from ..models import Agent, ScanTarget, Subnet
from . import agents, changes, scans
from .scans import utc

# spec §5.1: the per-job timeout plus a margin
LEASE = timedelta(minutes=15)
# the fields an agent reports at check-in
REPORTED_FIELDS = ("version", "platform", "hostname", "interfaces")
# the protocol value quoted back in an error, when it is a small integer
_QUOTABLE_PROTOCOL = range(0, 10_000)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- Scheduling ---


def lease_live(target: ScanTarget, now: datetime) -> bool:
    issued = utc(target.job_issued_at)
    return target.current_job_id is not None and issued is not None and now < issued + LEASE


def requested(target: ScanTarget) -> bool:
    """A scan was asked for and no completed scan has started since."""
    asked, last = utc(target.scan_requested_at), utc(target.last_scan_at)
    return asked is not None and (last is None or asked > last)


def interval_due(target: ScanTarget, now: datetime) -> bool:
    if target.interval_minutes is None:
        return False
    last = utc(target.last_scan_at)
    return last is None or now >= last + timedelta(minutes=target.interval_minutes)


def is_due(target: ScanTarget, now: datetime) -> bool:
    if not target.enabled or lease_live(target, now):
        return False
    return requested(target) or interval_due(target, now)


def l2_local(subnet: Subnet, interfaces: list[dict] | None) -> bool:
    """Whether the agent has an interface on the subnet's link, from the interfaces it
    reported at its last check-in. Only then can it send ARP into the subnet."""
    cidrs = [i.get("cidr") for i in interfaces or [] if isinstance(i, dict)]
    return on_link(parse_cidr(subnet.cidr), [c for c in cidrs if isinstance(c, str)])


def job_methods(target: ScanTarget, subnet: Subnet, interfaces: list[dict] | None) -> list[str]:
    """The methods a job for this target runs on this agent, or [] when it cannot run.

    `arp` is dropped when the subnet is not on the agent's link (spec §5.2), and `fdns`
    until the forward-DNS check exists (#20), so no name is ever sent. When no liveness
    method is left, the target cannot be scanned by this agent and no job is issued.
    """
    chosen = set(target.methods or ())
    chosen.discard("fdns")
    if "arp" in chosen and not l2_local(subnet, interfaces):
        chosen.discard("arp")
    if not chosen & scans.LIVENESS_METHODS:
        return []
    return [m for m in scans.SCAN_METHODS if m in chosen]


def _priority(target: ScanTarget):
    # asked-for scans first, then the target whose last scan is oldest (never first)
    never = datetime.min.replace(tzinfo=timezone.utc)
    return (not requested(target), utc(target.last_scan_at) or never, utc(target.created_at))


async def _lease(
    session: AsyncSession, target: ScanTarget, agent: Agent, now: datetime
) -> str | None:
    """Lease the target to a new job. The update only applies if the lease is still the
    one this check-in read, so two check-ins can never both issue the same target."""
    job_id = str(uuid4())
    result = await session.execute(
        update(ScanTarget)
        .where(
            ScanTarget.id == target.id,
            ScanTarget.current_job_id.is_not_distinct_from(target.current_job_id),
        )
        .values(current_job_id=job_id, job_issued_at=now)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        return None
    target.current_job_id, target.job_issued_at = job_id, now
    # the agent's own record: it is busy until this lease is cleared or expires
    agent.current_job_id, agent.job_issued_at = job_id, now
    return job_id


def agent_busy(agent: Agent, now: datetime) -> bool:
    """Whether the agent holds a live lease. Read from the agent itself, so it holds
    whatever has since happened to the target: reassigned, disabled or deleted."""
    issued = utc(agent.job_issued_at)
    return agent.current_job_id is not None and issued is not None and now < issued + LEASE


async def _next_job(session: AsyncSession, agent: Agent, now: datetime) -> Job | None:
    # One job at a time: an agent still holding a live lease is still scanning, so a
    # second job would wait in its queue while its own lease ran out.
    if agent_busy(agent, now):
        return None
    rows = (
        await session.execute(
            select(ScanTarget, Subnet)
            .join(Subnet, ScanTarget.subnet_id == Subnet.id)
            .where(ScanTarget.agent_id == agent.id)
        )
    ).all()
    for target, subnet in sorted(rows, key=lambda row: _priority(row[0])):
        if not is_due(target, now):
            continue
        methods = job_methods(target, subnet, agent.interfaces)
        if not methods:
            continue
        ranges = await scans.scan_scope_ranges(session, subnet)
        if not ranges:
            continue
        job_id = await _lease(session, target, agent, now)
        if job_id is None:
            continue
        return Job(
            job_id=job_id,
            subnet_id=subnet.id,
            cidr=subnet.cidr,
            ranges=ranges,
            methods=methods,
            dns_servers=list(subnet.dns_servers or []),
            timeout_s=JOB_TIMEOUT_S,
            fdns=[],
        )
    return None


# --- Check-in ---


def _protocol_error(body: dict) -> str | None:
    protocol = body.get("protocol")
    if type(protocol) is int and protocol == PROTOCOL_VERSION:
        return None
    if type(protocol) is int and protocol in _QUOTABLE_PROTOCOL:
        spoken = f"protocol {protocol}"
    else:
        spoken = "no protocol this server knows"
    return (
        f"Protocol mismatch: the agent speaks {spoken}, the server speaks protocol "
        f"{PROTOCOL_VERSION}. Install the agent release that matches the server."
    )


def _validation_message(exc: ValidationError) -> str:
    # field names and the validators' own messages only: never the rejected value
    first = exc.errors(include_input=False, include_url=False)[0]
    loc = ".".join(str(part) for part in first["loc"])
    return f"Check-in refused: {loc}: {first['msg']}" if loc else f"Check-in refused: {first['msg']}"


def _store(session: AsyncSession, agent: Agent, now: datetime, fields: dict) -> None:
    """Write the check-in to the agent. Logs one entry if anything but the check-in
    time changed; does not commit."""
    before = changes.snapshot(agent)
    changed = [name for name, value in fields.items() if getattr(agent, name) != value]
    for name, value in fields.items():
        setattr(agent, name, value)
    agent.last_checkin_at = now
    if not changed:
        return
    changes.record(
        session,
        entity_type="agent",
        entity_id=agent.id,
        action="update",
        summary=f"agent {agent.name} check-in changed {', '.join(changed)}",
        source="agent",
        actor=agents.actor_for(agent),
        before=before,
        after=changes.snapshot(agent),
    )


async def checkin(session: AsyncSession, agent: Agent, body: dict) -> dict:
    """Handle `POST /api/v1/agent/checkin` for an authenticated agent.

    The protocol is checked before the rest of the body, which another protocol may
    shape differently. A mismatch, or a body that fails validation, is recorded in
    `last_error`, leaves the reported fields as they were, and gets no job.
    """
    now = _now()
    error = _protocol_error(body)
    if error is not None:
        _store(session, agent, now, {"last_error": error})
        await session.commit()
        raise ApiError("PROTOCOL_MISMATCH", error, 409)
    try:
        report = CheckinRequest.model_validate(body)
    except ValidationError as exc:
        error = _validation_message(exc)
        _store(session, agent, now, {"last_error": error})
        await session.commit()
        raise validation_error(error) from None

    data = report.model_dump()
    _store(session, agent, now, {**{f: data[f] for f in REPORTED_FIELDS}, "last_error": None})
    # Write before reading the leases: SQLite has one writer, so a concurrent check-in
    # waits here until this one commits. The agent was loaded before that, at
    # authentication, so its own lease is read again now.
    await session.flush()
    await session.refresh(agent, ["current_job_id", "job_issued_at"])
    job = await _next_job(session, agent, now)
    await session.commit()
    response = CheckinResponse(checkin_interval_s=CHECKIN_INTERVAL_S, jobs=[job] if job else [])
    return response.model_dump(mode="json")
