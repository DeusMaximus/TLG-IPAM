"""Scan results ingest, `POST /api/v1/agent/results` (v0.2 spec §5.4, §5.7).

A result set is for one job, and is accepted only from an agent that is still
`active`, presenting an agent token that is still unrevoked, both read under SQLite's
write lock before anything is written (`_credential_live`): authentication read them
before the request waited, and a disable, revoke, delete or re-enrol exchange may have
committed since. Otherwise it is `403` and changes nothing. It is accepted only while
that job is current on both sides of the lease: the target's `current_job_id` and the reporting agent's own. A
superseded job, a target that is gone, and a second result set for the same job are
refused whole (`409 JOB_NOT_CURRENT`) and change nothing. Accepting one clears both
copies of the lease in one transaction, each as a compare-and-set on this job, so two
result sets racing for one job cannot both pass.

The rest is judged per record: an address outside the subnet's scan scope at ingest,
a MAC from a job the server did not issue `arp`, and the like are listed in
`rejected` without affecting the valid records. What the job could collect is read
from the methods the server issued (`job_methods`), never from the agent.

Which addresses are marked dead and which stored names are cleared follow the rule of
the protocol the result set was sent under (`ResultsRequest.protocol`, #100). From
protocol 2 the liveness sweep's completion (`liveness_complete`) decides the first, and
each record's own lookup (`rdns_no_name`) the second. A protocol-1 result set, from an
agent that carried a job leased before the server was upgraded, is judged by
`complete` for both, as it was when it was sent.

Ingest writes observations and the target's last-scan columns. It never writes
`hosts` (spec §7.2, invariant 3) and writes nothing to the change log: an agent
reports every few minutes, and nothing here is owner data. Agent text is sanitised
at ingest by the wire contract (`agent_protocol.rdns_name`, `scan_error`) and never
reaches a change summary (invariant 8).
"""

import ipaddress
import json
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError
from sqlalchemy import delete, exists, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent_protocol import Observation as ObservationRecord
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
from ..errors import ApiError, database_unavailable, internal_error, validation_error
from ..ipnet import parse_cidr
from ..models import Agent, Observation, ScanTarget, Subnet, Token
from . import scans
from .agents import record_token_use, token_in_use, token_refused
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


async def _credential_live(session: AsyncSession, agent: Agent) -> bool:
    """Whether the agent is still `active` and the token that authenticated this
    request still unrevoked, as committed. The caller holds the write lock."""
    token_live = exists().where(Token.id == token_in_use(session), Token.revoked_at.is_(None))
    found = await session.scalar(
        select(Agent.id).where(Agent.id == agent.id, Agent.state == "active", token_live)
    )
    return found is not None


async def _release(
    session: AsyncSession, agent: Agent, target: ScanTarget, report: ResultsRequest
) -> None:
    """Clear both copies of the lease and record the scan on the target. Each update
    applies only while the copy still names this job, so a second result set for it,
    or one racing this, is refused; nothing is written then.

    First, under the write lock, the request's credential is checked again: a result
    set authenticated before the owner disabled, revoked or deleted the agent, or
    before a re-enrol exchange revoked its token, is refused `403` with nothing
    written, as check-in's claim refuses a check-in (`jobs._claim`)."""
    await scans.take_write_lock(session)
    if not await _credential_live(session, agent):
        await session.rollback()
        raise token_refused()
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


def _sets_name(report: ResultsRequest, record: ObservationRecord) -> bool:
    """Whether a record from a job issued `rdns` writes its `rdns_name` (a name, or
    None to clear the stored one), by the rule of the result set's protocol.

    A name always sets it. Protocol 2: no name clears it only when the record says its
    lookup was answered "no name" (`rdns_no_name`); a lookup that got no usable answer,
    was cut off or did not run leaves it, and so does a name dropped by sanitising.
    Protocol 1, which could not tell those apart: no name (or one dropped by
    sanitising) clears it only in a complete result set, since an incomplete one may
    report an address whose lookup never ran."""
    if record.rdns_name is not None:
        return True
    if report.protocol == 1:
        return report.complete
    return record.rdns_no_name


async def ingest(session: AsyncSession, agent: Agent, body: object) -> dict:
    """Handle `POST /api/v1/agent/results` for an authenticated agent.

    One transaction: the lease is released, the observations written and the token's
    use recorded together. If the database refuses a write (locked past its busy
    timeout, say), it is rolled back and the request is refused with
    `503 UNAVAILABLE`: nothing is stored, both lease copies still name the job, and the
    same result set may be sent again. A failure of the rollback itself is not caught
    and is a bare 500.
    """
    try:
        report = ResultsRequest.model_validate(body)
    except ValidationError as exc:
        raise validation_error(_validation_message(exc)) from None
    try:
        return await _accept(session, agent, report)
    except OperationalError as exc:
        await session.rollback()
        raise database_unavailable("results ingest", exc) from exc


async def _accept(session: AsyncSession, agent: Agent, report: ResultsRequest) -> dict:
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
    await record_token_use(session, _now())
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
        elif reason is None and "rdns" not in methods and (
            raw.get("rdns_name") is not None or record.rdns_no_name
        ):
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
        if "rdns" in methods and _sets_name(report, record):
            row.rdns_name = record.rdns_name
        accepted += 1

    # Only a sweep that finished, with a method that can tell, says an address is down.
    # An interrupted or timed-out job only adds what it saw. From protocol 2 that is
    # the liveness sweep's own completion: what the PTR lookups did has no part in it.
    if report.marks_dead() and methods & scans.LIVENESS_METHODS:
        for ip_int, row in existing.items():
            if ip_int not in named and scans.in_scope(scope, ip_int):
                row.alive = False

    # spec §5.4: unseen for 90 days, in any subnet
    await session.execute(
        delete(Observation)
        .where(Observation.last_seen_at < now - RETENTION)
        .execution_options(synchronize_session="fetch")
    )
    # The answer is built, and encoded once as the response will be, before the commit,
    # so that neither step can fail for a stored result set: an agent seeing an error
    # for one could not report the job again (a retry is JOB_NOT_CURRENT). This does not
    # cover rendering and sending the response, which come after the commit. Should it
    # fail, nothing is stored and the agent gets a 500 in the REST error shape.
    try:
        answer = ResultsResponse(accepted=accepted, rejected=rejected).model_dump(mode="json")
        json.dumps({"data": answer}, ensure_ascii=False).encode("utf-8")
    except (ValueError, TypeError) as exc:  # UnicodeEncodeError is a ValueError
        await session.rollback()
        raise internal_error("results ingest: encoding the answer", exc) from exc
    await session.commit()
    return answer
