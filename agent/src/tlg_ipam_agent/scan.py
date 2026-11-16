"""Running one job: the ICMP sweep of its addresses, then PTR lookups for the ones that
answered, inside the job's timeout (spec §5.2, §5.6).

Every packet goes to an address `guard.plan` returned, or, for a PTR lookup, to one of
the job's DNS servers. Sends are paced at `MAX_PPS` across the whole job. The result
set is `complete` only when every issued method ran over every address before the
timeout; otherwise it reports what it saw, which the server only adds (spec §5.7).
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .probe import ProbeError, Prober, Resolver
from .protocol import SUPPORTED, Address, Job, JobRef, rdns_name, scan_error

# one echo request per address, and this long for its reply
PING_TIMEOUT_S = 1.0
# echo requests in flight at once; with the timeout, this bounds open raw sockets
PING_CONCURRENCY = 64
RDNS_TIMEOUT_S = 2.0
RDNS_CONCURRENCY = 16


@dataclass
class Clock:
    """Time, injectable: `monotonic` for deadlines and pacing, `sleep` to wait, `now`
    for the timestamps a result set carries."""

    monotonic: Callable[[], float] = time.monotonic
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    now: Callable[[], datetime] = field(default=lambda: datetime.now(timezone.utc))


class Pacer:
    """At most `pps` sends a second over the whole job: each send waits for its slot."""

    def __init__(self, pps: int, clock: Clock):
        self._interval = 1.0 / pps
        self._clock = clock
        self._next: float | None = None

    async def wait(self) -> None:
        now = self._clock.monotonic()
        if self._next is None or self._next < now:
            self._next = now
        delay = self._next - now
        self._next += self._interval
        if delay > 0:
            await self._clock.sleep(delay)


class _Pool:
    """Runs `work(item, timeout)` on each item, at most `limit` at once, each start
    paced, none started after `deadline`, and none given longer than what is left
    before it. Returns whether every item started and finished before the deadline."""

    def __init__(self, limit: int, item_timeout: float, pacer: Pacer, clock: Clock, deadline: float):
        self._sem = asyncio.Semaphore(limit)
        self._item_timeout = item_timeout
        self._pacer, self._clock, self._deadline = pacer, clock, deadline

    async def run(self, items, work) -> bool:
        tasks: list[asyncio.Task] = []
        finished = True
        failed = False

        async def one(item, timeout):
            nonlocal failed
            try:
                await work(item, timeout)
            except BaseException:
                failed = True
                raise
            finally:
                self._sem.release()

        try:
            for item in items:
                # a ProbeError in a running task stops the sweep before the next send
                if failed:
                    break
                await self._sem.acquire()
                await self._pacer.wait()
                if failed:
                    self._sem.release()
                    break
                left = self._deadline - self._clock.monotonic()
                if left <= 0:
                    self._sem.release()
                    finished = False
                    break
                # a probe started near the deadline waits only until the deadline, so
                # the work in flight never carries the job past it
                tasks.append(asyncio.create_task(one(item, min(self._item_timeout, left))))
        finally:
            results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result
        # work that ran into the deadline did not finish: its answer is unknown
        if self._clock.monotonic() >= self._deadline:
            finished = False
        return finished


def refusal(ref: JobRef, reason: str, clock: Clock) -> dict:
    """The result set for a job the agent would not run: nothing scanned, the reason
    as the error, so the server shows it and clears the lease (spec §5.7)."""
    now = clock.now().isoformat()
    return {
        "job_id": ref.job_id,
        "subnet_id": ref.subnet_id,
        "started_at": now,
        "finished_at": now,
        "complete": False,
        "observations": [],
        "fdns": [],
        "error": scan_error(reason),
    }


async def run(
    job: Job,
    addresses: tuple[Address, ...],
    *,
    prober: Prober,
    resolver: Resolver,
    max_pps: int,
    clock: Clock,
) -> dict:
    """Sweep `addresses` (from `guard.plan`) and build the result set."""
    started = clock.now()
    deadline = clock.monotonic() + job.timeout_s
    pacer = Pacer(max_pps, clock)
    errors: list[str] = []
    complete = True

    unsupported = sorted(job.methods - SUPPORTED)
    if unsupported:
        # e.g. `arp` before #14: the rest runs, but a sweep that skipped an issued
        # liveness method must not let the server mark the silent addresses dead
        complete = False
        errors.append(f"not run by this agent release: {', '.join(unsupported)}")

    alive: set[Address] = set()
    timed_out = False
    if "icmp" in job.methods:

        async def ping(address: Address, timeout: float) -> None:
            if await prober.ping(address, timeout):
                alive.add(address)

        try:
            if not await _Pool(PING_CONCURRENCY, PING_TIMEOUT_S, pacer, clock, deadline).run(addresses, ping):
                timed_out = True
                errors.append(f"timed out after {job.timeout_s} s during the ICMP sweep")
        except ProbeError as exc:
            # what answered before the prober failed is still reported
            complete = False
            errors.append(str(exc))

    names: dict[Address, str] = {}
    if "rdns" in job.methods and alive and not timed_out:

        async def lookup(address: Address, timeout: float) -> None:
            name = rdns_name(await resolver.ptr(address, job.dns_servers, timeout))
            if name is not None:
                names[address] = name

        if not await _Pool(RDNS_CONCURRENCY, RDNS_TIMEOUT_S, pacer, clock, deadline).run(sorted(alive), lookup):
            timed_out = True
            errors.append(f"timed out after {job.timeout_s} s during the PTR lookups")
    if timed_out:
        complete = False

    observations = []
    for address in sorted(alive):
        record = {"ip": str(address)}
        if address in names:
            record["rdns_name"] = names[address]
        observations.append(record)
    return {
        "job_id": job.ref.job_id,
        "subnet_id": job.ref.subnet_id,
        "started_at": started.isoformat(),
        "finished_at": max(clock.now(), started).isoformat(),
        "complete": complete,
        "observations": observations,
        "fdns": [],
        "error": scan_error("; ".join(errors)) if errors else None,
    }
