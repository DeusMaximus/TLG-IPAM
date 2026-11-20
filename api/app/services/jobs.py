"""Agent check-in, scheduling and job leases (v0.2 spec §5.1, §5.2, §5.7).

The server is the scheduler. At each check-in it stores what the agent reports and
hands back at most one due job. Issuing a job leases its target: `current_job_id`
and `job_issued_at` are set, and the target is not issued again until the lease is
cleared by an accepted result (`services/results`) or expires.

The same two columns on the agent record the job it was issued. An agent is busy, and
gets no new job, while that lease is live. It is kept on the agent so that the owner
reassigning or deleting the target mid-scan does not free an agent still scanning.

A target is due when it is enabled, assigned to the agent, not under a live lease,
and either
- a scan was requested (`scan_requested_at`) after its last scan started, or
- it has an interval and has never been scanned, or its last scan started at least
  `interval_minutes` ago.

`last_scan_at` is when the last completed job was issued, by the server's clock
(written by results ingest, `services/results`). Until a result clears a lease, the
target stays due, so an expired lease makes it due again.

Check-in's own writes are not audit-logged, as `tokens.last_used_at` is not: an
agent checks in every minute. A check-in that changes what the agent reports, or
its `last_error`, is logged once as an `agent` update with `source="agent"` and
`actor="agent:<name>"` (spec §4.3); the summary names the fields, never their values,
and MCP withholds the values from the snapshots (`changes.assistant_view`). Those
entries are capped per agent (AGENT_AUDIT_KEEP), and check-ins are limited to one
per MIN_CHECKIN_GAP. Issuing a lease is scheduling state and is not logged.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import delete, exists, or_, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from ..agent_protocol import (
    CHECKIN_INTERVAL_S,
    DNS_SERVERS_MAX,
    JOB_TIMEOUT_S,
    PROTOCOL_VERSION,
    CheckinRequest,
    CheckinResponse,
    Job,
)
from ..errors import ApiError, database_unavailable, validation_error
from ..ipnet import canonical_cidr, on_link, parse_cidr
from ..models import Agent, Change, ScanTarget, Subnet, Token
from . import agents, changes, scans
from .scans import utc

# spec §5.1: the per-job timeout plus a margin
LEASE = timedelta(minutes=15)
# the fields an agent reports at check-in
REPORTED_FIELDS = changes.AGENT_REPORTED_FIELDS
_AGENT_LEASE = ("current_job_id", "job_issued_at")
# At most one check-in per agent in this time (the agent's own interval is 60 s). A
# refused check-in writes nothing, the token's use included (it is recorded only by a
# check-in that takes its slot), so a token used at request rate drives reads only,
# not writes or audit entries.
DEFAULT_CHECKIN_GAP = timedelta(seconds=10)
MIN_CHECKIN_GAP = DEFAULT_CHECKIN_GAP
# the newest audit entries an agent's own check-ins keep (`_prune_agent_audit`)
AGENT_AUDIT_KEEP = 100
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


def runnable_methods(
    target: ScanTarget, subnet: Subnet, agent: Agent | None
) -> list[str] | None:
    """What a job for the target would run on its agent now, for the scan-target answer
    (`scans.target_out`): `job_methods` on the interfaces the agent reported at its last
    accepted check-in, so the answer and check-in apply one rule. `[]` when the target
    cannot be scanned by this agent. None when that is not known: no agent assigned, or
    one that has never reported its interfaces (every accepted check-in reports them).

    Read-only and computed on read: the agent's next check-in changes the answer
    without a write to the target. The agent's state is not consulted; a disabled
    agent is judged by the interfaces it last reported."""
    if agent is None or agent.interfaces is None:
        return None
    return job_methods(target, subnet, agent.interfaces)


def last_attempt(target: ScanTarget) -> datetime | None:
    """When a job for the target last started: its last completed scan, or a later job
    that is still running or never reported back."""
    times = [t for t in (utc(target.last_scan_at), utc(target.job_issued_at)) if t]
    return max(times) if times else None


def fresh_request(target: ScanTarget) -> bool:
    """A requested scan that no job has been issued for since the request."""
    asked, issued = utc(target.scan_requested_at), utc(target.job_issued_at)
    return requested(target) and (issued is None or asked > issued)


def _priority(target: ScanTarget):
    # A request no job has been tried for yet goes first. Everything else, retries of
    # a request included, goes by when it was last tried, oldest (or never) first: a
    # target whose jobs keep timing out moves to the back each time instead of
    # starving the agent's other targets.
    never = datetime.min.replace(tzinfo=timezone.utc)
    return (not fresh_request(target), last_attempt(target) or never, utc(target.created_at))


async def _lease(
    session: AsyncSession, target: ScanTarget, agent: Agent, methods: list[str], now: datetime
) -> str | None:
    """Lease the target to a new job. The update only applies if the lease is still the
    one this check-in read, so two check-ins can never both issue the same target.
    The lease keeps the methods issued: results ingest judges a result by what
    the server sent (liveness, `arp` and so MACs), never by what the agent claims."""
    job_id = str(uuid4())
    result = await session.execute(
        update(ScanTarget)
        .where(
            ScanTarget.id == target.id,
            ScanTarget.current_job_id.is_not_distinct_from(target.current_job_id),
        )
        .values(current_job_id=job_id, job_issued_at=now, job_methods=methods)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        return None
    target.current_job_id, target.job_issued_at, target.job_methods = job_id, now, methods
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
        job_id = await _lease(session, target, agent, methods, now)
        if job_id is None:
            continue
        return Job(
            job_id=job_id,
            subnet_id=subnet.id,
            # one spelling, whatever the subnet row holds: a subnet stored before #97
            # may hold one longer than the agent takes (spec §5.7)
            cidr=canonical_cidr(subnet.cidr),
            ranges=ranges,
            methods=methods,
            # what the agent asks, and no more: a subnet may list more, or hold a
            # longer list stored before the owner API bounded it (spec §5.7)
            dns_servers=list(subnet.dns_servers or [])[:DNS_SERVERS_MAX],
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


def _rate_limited() -> ApiError:
    gap = int(MIN_CHECKIN_GAP.total_seconds())
    return ApiError(
        "RATE_LIMITED",
        f"An agent may check in at most once every {gap} seconds; check in again in "
        f"{CHECKIN_INTERVAL_S} seconds",
        429,
    )


async def _claim(session: AsyncSession, agent: Agent, now: datetime) -> None:
    """Take this agent's check-in slot, or refuse before anything is written: 429 inside
    the rate limit, 403 when the owner disabled or deleted the agent, or revoked the
    token, after it authenticated.

    This is a compare-and-set on `last_checkin_at` that also requires the agent to be
    `active` and the token unrevoked, so racing check-ins of one agent cannot both pass
    and an owner's disable takes effect from the moment it commits. It is the
    check-in's first write, so SQLite's one writer serialises everything after it until
    the commit: the token's use is recorded here, in the same transaction, and the
    agent row is then read again whole, so what the check-in stores and logs starts
    from the row as committed, never from the one loaded at authentication.

    The check against the row loaded at authentication comes first and needs no write
    statement at all; that row can only be older than the database's, never newer, so
    the compare-and-set remains the guard for check-ins that race.

    That order is the contract (spec §5.7): a loaded row inside the gap is 429 whatever
    the owner committed since, and only a check-in the loaded row lets through reaches
    the compare-and-set, which answers 403 when the owner's change has landed (even if
    another check-in also took the slot) and 429 when only the slot is gone."""
    last = utc(agent.last_checkin_at)
    if last is not None and last > now - MIN_CHECKIN_GAP:
        raise _rate_limited()
    token_id = agents.token_in_use(session)
    token_live = exists().where(Token.id == token_id, Token.revoked_at.is_(None))
    result = await session.execute(
        update(Agent)
        .where(
            Agent.id == agent.id,
            Agent.state == "active",
            token_live,
            or_(Agent.last_checkin_at.is_(None), Agent.last_checkin_at <= now - MIN_CHECKIN_GAP),
        )
        .values(last_checkin_at=now)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        # which condition failed, read under the write lock this statement took
        accepted = await session.scalar(
            select(Agent.id).where(Agent.id == agent.id, Agent.state == "active", token_live)
        )
        await session.rollback()
        raise _rate_limited() if accepted else agents.token_refused()
    await agents.record_token_use(session, now)
    await session.refresh(agent)
    set_committed_value(agent, "last_checkin_at", now)


async def _prune_agent_audit(session: AsyncSession, agent: Agent) -> None:
    """Keep the agent's own audit entries (`source="agent"`) to the newest
    AGENT_AUDIT_KEEP. A report alternating between values must not grow the log
    without bound; owner entries are never touched."""
    mine = (Change.source == "agent", Change.entity_type == "agent", Change.entity_id == agent.id)
    keep = (
        select(Change.id)
        .where(*mine)
        .order_by(Change.ts.desc(), Change.id.desc())
        .limit(AGENT_AUDIT_KEEP)
    )
    await session.execute(
        delete(Change)
        .where(*mine, Change.id.not_in(keep))
        .execution_options(synchronize_session=False)
    )


async def _store(session: AsyncSession, agent: Agent, fields: dict) -> None:
    """Write the check-in to the agent. Logs one entry if a reported field or
    `last_error` changed, then prunes the agent's own entries; does not commit."""
    before = changes.snapshot(agent)
    changed = [name for name, value in fields.items() if getattr(agent, name) != value]
    for name, value in fields.items():
        setattr(agent, name, value)
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
    await session.flush()
    await _prune_agent_audit(session, agent)


async def checkin(session: AsyncSession, agent: Agent, body: dict) -> dict:
    """Handle `POST /api/v1/agent/checkin` for an authenticated agent.

    A check-in inside MIN_CHECKIN_GAP of the last is refused (429) before anything is
    written, and so is one from an agent disabled since it authenticated (403). The
    protocol is then checked before the rest of the body, which another protocol may
    shape differently. A mismatch, or a body that fails validation, is recorded in
    `last_error`, leaves the reported fields as they were, and gets no job.

    Everything the check-in writes, the token's use included, is one transaction. If
    the database refuses a write (locked past its busy timeout, say), it is rolled back
    and the request is refused with `503 UNAVAILABLE`: nothing of it is written, and
    the same check-in may be sent again. A failure of the rollback itself is not
    caught and is a bare 500.
    """
    now = _now()
    try:
        await _claim(session, agent, now)
        error = _protocol_error(body)
        if error is not None:
            await _store(session, agent, {"last_error": error})
            await session.commit()
            raise ApiError("PROTOCOL_MISMATCH", error, 409)
        try:
            report = CheckinRequest.model_validate(body)
        except ValidationError as exc:
            error = _validation_message(exc)
            await _store(session, agent, {"last_error": error})
            await session.commit()
            raise validation_error(error) from None

        data = report.model_dump()
        reported = {f: data[f] for f in REPORTED_FIELDS}
        await _store(session, agent, {**reported, "last_error": None})
        job = await _next_job(session, agent, now)
        await session.commit()
    except OperationalError as exc:
        await session.rollback()
        raise database_unavailable("check-in", exc) from exc
    response = CheckinResponse(checkin_interval_s=CHECKIN_INTERVAL_S, jobs=[job] if job else [])
    return response.model_dump(mode="json")
