"""The forward-DNS check (v0.2 spec §5.2, §5.4, §5.7; #20): which names a job sends,
and what ingest keeps of the answers.

**What a job sends** (`names_for_job`). A job issued `fdns` names hosts of its subnet
for the agent to resolve, and only hosts whose address is in the job's scan scope
(`scans.scan_scope`, which the caller passes) and whose FQDN (`hosts.fqdn_of`: the
hostname and the host's domain override, else the subnet's domain) is under the
subnet's own domain (`ipnet.within_domain`). That is invariant 5 (spec §7.2): a host
whose FQDN is outside it, by its own domain override or because the subnet has none, is
never sent, so whoever can edit a hostname or a host's domain cannot make the agent
resolve an arbitrary name. The FQDN must also be a DNS hostname in the one form the
wire takes (`agent_protocol.fdns_name`); any other is not sent. A job names at most
`FDNS_JOB_MAX` hosts: those never asked first, then those asked longest ago, so a
subnet with more is covered over several jobs, whether or not the names asked were
answered (Codex on #185).

**What ingest keeps** (`store`). An answer is stored only for a host the job was sent
(the target's `job_fdns`, kept with the lease), under the name that was sent, and only
while the host is still in the subnet and in its scan scope, as ingest reads them under
the write lock. An entry whose addresses are not all IPv4 addresses is not stored; a
second entry for one host is ignored. An empty list is a name a DNS server answered
does not resolve. A name with no entry got no usable answer, and its stored answer
stays. Every name the job sent whose host is still there and in scope has its
`asked_at` set, answered or not, which is what rotates the names. Rows whose host no
longer exists are deleted.

This module reads `hosts` and never writes it, and writes nothing to the change log:
scan ingest's rule (spec §7.2, invariant 3), which `test_results.py` holds by counting
rows. It does not import `changes`.
"""

import ipaddress
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import delete, exists, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent_protocol import FdnsAnswer, fdns_name
from ..ipnet import within_domain
from ..models import FdnsResult, Host, Subnet
from ..times import utc
from .hosts import fqdn_of
from .scans import in_scope

# The most names one job sends, bounded by both requests it shapes:
# - the check-in answer, which the agent reads into at most 1 MiB
#   (`client.RESPONSE_MAX`): a name entry is at most 314 bytes (a 36-character host id
#   and a 253-character FQDN, in JSON), so 1,024 of them are 321,536 bytes beside the
#   rest of the largest answer (`agent/tests/test_answer_limit.py` builds it);
# - the result set, which every body is capped at 2 MiB (`body_limit.BODY_MAX`): an
#   answer entry with 32 of the widest IPv4 addresses is 642 bytes, so with every
#   address of a /20 observed (a MAC and a 253-character name each, about 1.3 MB) and
#   the longest error, 1,024 of them are about 1.94 MB, and 2,048 would be about 2.6 MB,
#   past the cap, refused whole (`test_fdns.py`'s
#   `test_the_largest_result_set_with_answers_fits_the_body_cap`).
# A /20 can hold 4,094 hosts: past this, the ones asked longest ago (or never) go
# first, and the rest wait for the next job.
FDNS_JOB_MAX = 1024
# host ids bound in one statement: inside the 999 parameters of the oldest SQLite
# builds, as `scans._IN_BATCH` is
_IN_BATCH = 500
_NEVER = datetime.min.replace(tzinfo=timezone.utc)


def sendable_fqdn(
    hostname: str | None, host_domain: str | None, subnet_domain: str | None
) -> str | None:
    """The name a job may send for a host, or None: its FQDN (`hosts.fqdn_of`), lowercased,
    when that is under the subnet's domain (`ipnet.within_domain`; invariant 5) and is a
    DNS hostname (`agent_protocol.fdns_name`)."""
    fqdn = fqdn_of(hostname, host_domain, subnet_domain)
    if fqdn is None or not fqdn.isascii():
        return None
    fqdn = fqdn.lower()
    if not within_domain(fqdn, subnet_domain):
        return None
    return fdns_name(fqdn)


async def names_for_job(
    session: AsyncSession, subnet: Subnet, scope: list[tuple[int, int]]
) -> list[tuple[str, str]]:
    """The `(host_id, fqdn)` pairs a job for `subnet` sends, at most FDNS_JOB_MAX, in
    address order. `scope` is the subnet's scan scope as the caller read it for the
    job's ranges. One statement: the subnet's hosts with the time each was last asked
    (`asked_at`), never-asked first, then oldest. Asked, not answered: a name that
    never gets a usable answer must not hold its place ahead of the rest."""
    rows = (
        await session.execute(
            select(Host.id, Host.ip_int, Host.hostname, Host.domain, FdnsResult.asked_at)
            .outerjoin(FdnsResult, FdnsResult.host_id == Host.id)
            .where(Host.subnet_id == subnet.id)
        )
    ).all()
    candidates = []
    for row in rows:
        if not in_scope(scope, row.ip_int):
            continue
        fqdn = sendable_fqdn(row.hostname, row.domain, subnet.domain)
        if fqdn is None:
            continue
        asked = utc(row.asked_at)
        candidates.append((asked is not None, asked or _NEVER, row.ip_int, row.id, fqdn))
    candidates.sort()
    chosen = sorted(candidates[:FDNS_JOB_MAX], key=lambda c: c[2])
    return [(host_id, fqdn) for _, _, _, host_id, fqdn in chosen]


def _addresses(answer: FdnsAnswer) -> list[str] | None:
    """The answer's addresses as stored: IPv4 only, each once, sorted numerically; None
    when any is not an IPv4 address, so the entry is not stored."""
    parsed = set()
    for value in answer.addresses:
        if not isinstance(value, str) or len(value) > 15:
            return None
        try:
            parsed.add(ipaddress.IPv4Address(value))
        except ValueError:
            return None
    return [str(a) for a in sorted(parsed)]


async def store(
    session: AsyncSession,
    subnet_id: str,
    scope: list[tuple[int, int]],
    sent: list | None,
    answers: list[FdnsAnswer],
    now: datetime,
) -> None:
    """Upsert the answers of a result set into `fdns_results` (see the module's
    docstring for which are kept), mark every name the job sent as asked, then delete
    the rows of hosts that no longer exist.
    The caller holds the write lock, and `scope` is the subnet's scope read under it.
    Does not commit."""
    names = {}
    for pair in sent or []:
        if isinstance(pair, list) and len(pair) == 2:
            names[pair[0]] = pair[1]
    kept: dict[str, list[str] | None] = {}
    for answer in answers:
        if answer.host_id not in names or answer.host_id in kept:
            continue
        addresses = _addresses(answer)
        if addresses is not None:
            kept[answer.host_id] = addresses
        else:
            # a malformed entry is not stored, and a later one for the host is not
            # taken in its place
            kept[answer.host_id] = None
    # every host the job sent is marked asked, answered or not
    wanted = list(names)
    present: set[str] = set()
    for i in range(0, len(wanted), _IN_BATCH):
        batch = wanted[i : i + _IN_BATCH]
        rows = await session.execute(
            select(Host.id, Host.subnet_id, Host.ip_int).where(Host.id.in_(batch))
        )
        present.update(
            r.id for r in rows.all() if r.subnet_id == subnet_id and in_scope(scope, r.ip_int)
        )
    answered = [
        {"host_id": h, "fqdn": names[h], "addresses": kept[h], "checked_at": now, "asked_at": now}
        for h in wanted
        if h in present and kept.get(h) is not None
    ]
    # asked and not answered: the stored answer, if any, stays as it is
    unanswered = [
        {"host_id": h, "fqdn": None, "addresses": None, "checked_at": None, "asked_at": now}
        for h in wanted
        if h in present and kept.get(h) is None
    ]
    statement = insert(FdnsResult)
    for rows, columns in (
        (answered, ("fqdn", "addresses", "checked_at", "asked_at")),
        (unanswered, ("asked_at",)),
    ):
        if rows:
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[FdnsResult.host_id],
                    set_={c: getattr(statement.excluded, c) for c in columns},
                ),
                [{"id": str(uuid4()), **row} for row in rows],
            )
    await session.execute(
        delete(FdnsResult)
        .where(~exists().where(Host.id == FdnsResult.host_id))
        .execution_options(synchronize_session=False)
    )
