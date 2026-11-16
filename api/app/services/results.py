"""Scan results ingest, `POST /api/v1/agent/results` (v0.2 spec §5.4, §5.7).

A result set is for one job. It is accepted only while that job is current on both
sides of the lease: the target's `current_job_id` and the reporting agent's own. A
superseded job, a target that is gone, and a second result set for the same job are
refused whole (`409 JOB_NOT_CURRENT`) and change nothing. Accepting one clears both
copies of the lease in one transaction, each as a compare-and-set on this job, so two
result sets racing for one job cannot both pass.

The rest is judged per record: an address outside the subnet's scan scope at ingest,
a MAC from a job the server did not issue `arp`, and the like are listed in
`rejected` without affecting the valid records. What the job could collect is read
from the methods the server issued (`job_methods`), never from the agent.

Ingest writes observations and the target's last-scan columns. It never writes
`hosts` (spec §7.2, invariant 3) and writes nothing to the change log: an agent
reports every few minutes, and nothing here is owner data. Agent text is sanitised
at ingest by the wire contract (`agent_protocol.rdns_name`, `scan_error`) and never
reaches a change summary (invariant 8).
"""

import ipaddress
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent_protocol import (
    REJECT_DUPLICATE,
    REJECT_MAC_NOT_ISSUED,
    REJECT_OUT_OF_SCOPE,
    REJECT_RDNS_NOT_ISSUED,
    ResultsRequest,
    ResultsResponse,
    address_of,
    echo_ip,
    parse_observation,
)
from ..errors import ApiError, validation_error
from ..ipnet import parse_cidr
from ..models import Agent, Observation, ScanTarget, Subnet
from . import scans
from .scans import utc

# spec §5.4: observations unseen for this long are pruned at ingest
RETENTION = timedelta(days=90)
# the largest duration the `last_scan_duration_ms` column holds
_DURATION_MS_MAX = 2**31 - 1


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _not_current() -> ApiError:
    return ApiError(
        "JOB_NOT_CURRENT",
        "This result set is not for a job this agent holds: the job was superseded or "
        "its target removed, or a result set for it was already accepted. Nothing was "
        "stored; check in for a new job.",
        409,
    )


def _validation_message(exc: ValidationError) -> str:
    # field names and the validators' own messages only: never the rejected value
    first = exc.errors(include_input=False, include_url=False)[0]
    loc = ".".join(str(part) for part in first["loc"])
    detail = f"{loc}: {first['msg']}" if loc else first["msg"]
    return f"Result set refused: {detail}"


def mac_randomised(mac: str) -> bool:
    """The locally-administered bit of the first octet is set: a randomised or
    otherwise made-up address, which says nothing durable about the device."""
    return bool(int(mac[:2], 16) & 0x02)


def _duration_ms(report: ResultsRequest) -> int:
    ms = int((report.finished_at - report.started_at) / timedelta(milliseconds=1))
    return min(ms, _DURATION_MS_MAX)


async def _release(
    session: AsyncSession, agent: Agent, target: ScanTarget, report: ResultsRequest
) -> None:
    """Clear both copies of the lease and record the scan on the target. Each update
    applies only while the copy still names this job, so a second result set for it,
    or one racing this, is refused; nothing is written then."""
    issued = utc(target.job_issued_at) or _now()
    cleared = await session.execute(
        update(ScanTarget)
        .where(ScanTarget.id == target.id, ScanTarget.current_job_id == report.job_id)
        .values(
            current_job_id=None,
            job_issued_at=None,
            job_methods=None,
            # when this job was issued, by the server's clock (spec §5.1): it keeps the
            # attempt time that `jobs._priority` orders by, and a request made while the
            # job ran is still later than it, so still due. `scan_requested_at` stays.
            last_scan_at=issued,
            last_scan_duration_ms=_duration_ms(report),
            last_scan_complete=report.complete,
            last_scan_error=report.error,
        )
        .execution_options(synchronize_session=False)
    )
    if cleared.rowcount != 1:
        await session.rollback()
        raise _not_current()
    released = await session.execute(
        update(Agent)
        .where(Agent.id == agent.id, Agent.current_job_id == report.job_id)
        .values(current_job_id=None, job_issued_at=None)
        .execution_options(synchronize_session=False)
    )
    if released.rowcount != 1:
        await session.rollback()
        raise _not_current()


async def ingest(session: AsyncSession, agent: Agent, body: object) -> dict:
    """Handle `POST /api/v1/agent/results` for an authenticated agent."""
    try:
        report = ResultsRequest.model_validate(body)
    except ValidationError as exc:
        raise validation_error(_validation_message(exc)) from None
    if agent.current_job_id is None or agent.current_job_id != report.job_id:
        raise _not_current()
    target = await session.scalar(
        select(ScanTarget).where(ScanTarget.current_job_id == report.job_id)
    )
    if target is None:
        raise _not_current()
    if target.subnet_id != report.subnet_id:
        raise validation_error("Result set refused: subnet_id is not the job's subnet")
    # what the server issued, read with the lease that `_release` checks is still this
    # job's: liveness, `arp` and so MACs, `rdns` and so names
    methods = set(target.job_methods or ())
    subnet = await session.get(Subnet, target.subnet_id)
    await _release(session, agent, target, report)
    # The reads above ran before this transaction took SQLite's write lock (the driver
    # begins it at the first write), so the owner may have edited the subnet since.
    # Everything below is read under the lock: `scan_scope` reads the scope as
    # committed, and the subnet is read again for its CIDR. No other writer can commit
    # until this one does.
    await session.refresh(subnet)

    now = _now()
    net = parse_cidr(subnet.cidr)
    scope = await scans.scan_scope(session, subnet)
    rows = await session.scalars(select(Observation).where(Observation.subnet_id == subnet.id))
    existing = {row.ip_int: row for row in rows}
    # in-scope addresses this result set named, stored or not: none is marked dead
    named: set[int] = set()
    accepted = 0
    rejected: list[dict] = []

    for raw in report.observations:
        record, reason = parse_observation(raw)
        ip = record.ip if record is not None else address_of(raw)
        if ip is not None:
            addr = ipaddress.ip_address(ip)
            ip_int = int(addr)
            if addr.version != net.version or not scans.in_scope(scope, ip_int):
                reason = REJECT_OUT_OF_SCOPE
            elif ip_int in named:
                reason = REJECT_DUPLICATE
            else:
                # named, even if a bad field rejects the record below
                named.add(ip_int)
        if reason is None and record.mac is not None and "arp" not in methods:
            reason = REJECT_MAC_NOT_ISSUED
        elif reason is None and raw.get("rdns_name") is not None and "rdns" not in methods:
            reason = REJECT_RDNS_NOT_ISSUED
        if reason is not None:
            rejected.append({"ip": echo_ip(raw), "reason": reason})
            continue

        row = existing.get(ip_int)
        if row is None:
            row = Observation(subnet_id=subnet.id, ip=str(addr), ip_int=ip_int, first_seen_at=now)
            session.add(row)
            existing[ip_int] = row
        row.alive = True
        row.last_seen_at = now
        row.agent_id = agent.id
        if record.mac is not None:
            row.mac, row.mac_randomised = record.mac, mac_randomised(record.mac)
        if "rdns" in methods:
            # the latest answer: no name, or one dropped by sanitising, clears it
            row.rdns_name = record.rdns_name
        accepted += 1

    # Only a sweep that finished, with a method that can tell, says an address is down.
    # An interrupted or timed-out job only adds what it saw.
    if report.complete and methods & scans.LIVENESS_METHODS:
        for ip_int, row in existing.items():
            if ip_int not in named and scans.in_scope(scope, ip_int):
                row.alive = False

    # spec §5.4: unseen for 90 days, in any subnet
    await session.execute(
        delete(Observation)
        .where(Observation.last_seen_at < now - RETENTION)
        .execution_options(synchronize_session="fetch")
    )
    await session.commit()
    return ResultsResponse(accepted=accepted, rejected=rejected).model_dump(mode="json")
