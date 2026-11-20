"""Running one job: the ICMP sweep of its addresses, then the ARP sweep, then PTR
lookups for the addresses either found, inside the job's timeout (spec §5.2, §5.6).

Every packet goes to an address `guard.plan` returned, or, for a PTR lookup, to one of
the job's DNS servers (the host's own resolvers when the job names none). Every packet
is paced at `MAX_PPS` across the whole job: each echo request, each ARP request (a
retry included), and each DNS query, a failover to the next server included. The
result set is `complete` only when every issued method ran over every address before
the timeout; otherwise it reports what it saw, which the server only adds (spec §5.7).
A PTR lookup with no server to ask, or whose every attempt failed before its query went
out, did not run; one the deadline stops before its first attempt is the timeout.
"""

import asyncio
import ipaddress
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import arp
from .probe import NO_DNS_SERVER, ArpLink, ArpSession, ProbeError, Prober, Resolver
from .protocol import SUPPORTED, Address, Job, JobRef, Network, rdns_name, scan_error

# one echo request per address, and this long for its reply
PING_TIMEOUT_S = 1.0
# echo requests in flight at once; with the timeout, this bounds open raw sockets
PING_CONCURRENCY = 64
RDNS_TIMEOUT_S = 2.0
RDNS_CONCURRENCY = 16
# spec §5.2: one request and one retry per address. Each round's requests go out paced,
# then the round waits this long for replies; only the addresses still silent get the
# retry round, so every retry comes at least this long after its first request
ARP_ATTEMPTS = 2
ARP_TIMEOUT_S = 1.0
# how often a round's wait looks whether every reply is in
ARP_POLL_S = 0.05


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
    before it. Returns whether every item started and finished before the deadline.

    When a work item raises, no further item is started and the exception is raised
    once the items in flight are done. With `cancel_on_failure` those items are
    cancelled at once instead of being let finish, so they send nothing more."""

    def __init__(
        self,
        limit: int,
        item_timeout: float,
        pacer: Pacer | None,
        clock: Clock,
        deadline: float,
        cancel_on_failure: bool = False,
    ):
        """`pacer` paces each start; None when the work paces its own sends."""
        self._sem = asyncio.Semaphore(limit)
        self._item_timeout = item_timeout
        self._pacer, self._clock, self._deadline = pacer, clock, deadline
        self._cancel_on_failure = cancel_on_failure

    async def run(self, items, work) -> bool:
        tasks: list[asyncio.Task] = []
        finished = True
        failed = False

        async def one(item, timeout):
            nonlocal failed
            try:
                await work(item, timeout)
            except BaseException as exc:
                if self._cancel_on_failure and not failed and isinstance(exc, Exception):
                    # the first failure: the rest of the work in flight goes now
                    this = asyncio.current_task()
                    for task in tasks:
                        if task is not this:
                            task.cancel()
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
                if self._pacer is not None:
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
        except BaseException:
            # cancelled (`loop.SCAN_GRACE_S`, or the agent stopping): the work in
            # flight goes too, so the wait for it below cannot outlast the cancel
            for task in tasks:
                task.cancel()
            raise
        finally:
            results = await asyncio.gather(*tasks, return_exceptions=True)
        # the failure itself, before the cancellations it caused
        for result in results:
            if isinstance(result, Exception):
                raise result
        for result in results:
            if isinstance(result, BaseException):
                raise result
        # work that ran into the deadline did not finish: its answer is unknown
        if self._clock.monotonic() >= self._deadline:
            finished = False
        return finished


@dataclass(frozen=True)
class ArpInterface:
    """Where the ARP sweep runs: an interface, the address and MAC its requests come
    from, and every IPv4 address the interface holds."""

    name: str
    address: ipaddress.IPv4Address
    mac: bytes
    own: frozenset[ipaddress.IPv4Address]


def _ipv4(item: object) -> tuple[str, ipaddress.IPv4Interface, str | None] | None:
    if not isinstance(item, dict):
        return None
    name, cidr, mac = item.get("name"), item.get("cidr"), item.get("mac")
    if not isinstance(name, str) or not name or not isinstance(cidr, str):
        return None
    try:
        interface = ipaddress.ip_interface(cidr)
    except ValueError:
        return None
    if interface.version != 4:
        return None
    return name, interface, mac if isinstance(mac, str) else None


def arp_interface(network: Network, interfaces: Sequence[dict]) -> ArpInterface | None:
    """The first interface (from `interfaces.discover`) whose network contains the
    whole of `network`, the rule the server applies before it issues `arp`
    (`ipnet.on_link`), and that has a MAC to send from. None when there is none."""
    if network.version != 4:
        return None
    found = [f for f in map(_ipv4, interfaces) if f is not None]
    for name, interface, mac in found:
        if mac is None or not network.subnet_of(interface.network):
            continue
        try:
            raw = arp.mac_bytes(mac)
        except ValueError:
            continue
        own = frozenset(i.ip for n, i, _ in found if n == name)
        return ArpInterface(name, interface.ip, raw, own)
    return None


async def _arp_sweep(
    session: ArpSession,
    targets: Sequence[Address],
    pacer: Pacer,
    clock: Clock,
    deadline: float,
) -> bool:
    """Send ARP requests to `targets` through `session`, paced, in up to ARP_ATTEMPTS
    rounds; the replies land in `session.answers`. Returns whether every round ran
    and waited out its replies before the deadline. Raises ProbeError if the socket
    fails, keeping what was answered before it did."""
    listener = asyncio.create_task(session.listen())

    def check() -> None:
        # a socket that cannot be read stops the sweep before the next send
        if listener.done():
            listener.result()

    try:
        for _ in range(ARP_ATTEMPTS):
            pending = [a for a in targets if a not in session.answers]
            if not pending:
                break
            for address in pending:
                if address in session.answers:
                    continue  # a late reply to the first round
                await pacer.wait()
                check()
                if clock.monotonic() >= deadline:
                    return False
                # a reply can land while the slot is awaited: then no retry goes
                if address in session.answers:
                    continue
                await session.request(address)
            check()
            # the round's replies get ARP_TIMEOUT_S after its last request, and less
            # once every one is in; a request sent near the deadline waits only until it
            until = min(clock.monotonic() + ARP_TIMEOUT_S, deadline)
            while any(a not in session.answers for a in pending):
                left = until - clock.monotonic()
                if left <= 0:
                    break
                await clock.sleep(min(ARP_POLL_S, left))
                check()
            if clock.monotonic() >= deadline and any(a not in session.answers for a in pending):
                return False
        return True
    finally:
        listener.cancel()
        await asyncio.gather(listener, return_exceptions=True)


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
    arp_link: Callable[[str], ArpLink] | None = None,
    interfaces: Sequence[dict] = (),
    max_pps: int,
    clock: Clock,
) -> dict:
    """Sweep `addresses` (from `guard.plan`) and build the result set. `arp_link`
    opens the ARP socket on an interface named in `interfaces`, the host's own as
    `interfaces.discover` reads them when the job starts."""
    started = clock.now()
    deadline = clock.monotonic() + job.timeout_s
    pacer = Pacer(max_pps, clock)
    errors: list[str] = []
    complete = True

    unsupported = sorted(job.methods - SUPPORTED)
    if unsupported:
        # e.g. `fdns` before #20: the rest runs, but a sweep that skipped an issued
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

    macs: dict[Address, str] = {}
    if "arp" in job.methods and not timed_out:
        interface = arp_interface(job.network, interfaces)
        if interface is None or arp_link is None:
            complete = False
            errors.append(f"arp not run: no interface of this host with a MAC is on {job.network}")
        else:
            # the kernel answers no ARP request of this host's own: an address the
            # interface holds is reported alive with its MAC, and no frame is sent to it
            for address in addresses:
                if address in interface.own:
                    macs[address] = arp.mac_text(interface.mac)
            targets = [a for a in addresses if a not in interface.own]
            session: ArpSession | None = None
            try:
                link = arp_link(interface.name)
                try:
                    session = ArpSession(link, interface.address, interface.mac, frozenset(targets))
                    if not await _arp_sweep(session, targets, pacer, clock, deadline):
                        timed_out = True
                        errors.append(f"timed out after {job.timeout_s} s during the ARP sweep")
                finally:
                    link.close()
            except ProbeError as exc:
                # what answered before the socket failed is still reported
                complete = False
                errors.append(str(exc))
            if session is not None:
                macs.update(session.answers)
        alive.update(macs)

    names: dict[Address, str] = {}
    servers: tuple[str, ...] = ()
    if "rdns" in job.methods and alive and not timed_out:
        # chosen once for the job: its own servers, else the host's resolvers as they
        # are now. With none, no lookup can be made, and a lookup not made is not one
        # that found no name: the result set is incomplete, so the server keeps the
        # names it has (spec §5.7), and no query is sent
        servers = resolver.servers(job.dns_servers)
        if not servers:
            complete = False
            errors.append(NO_DNS_SERVER)
    if servers:

        async def lookup(address: Address, _timeout: float) -> None:
            end: float | None = None

            async def slot() -> float:
                # one packet from the budget the ICMP sweep drew on, before each query
                nonlocal end
                await pacer.wait()
                now = clock.monotonic()
                if end is None:
                    # the lookup's own time starts once its first query may go, not
                    # while it queues behind other lookups for a slot; the job's
                    # deadline still holds
                    end = min(now + RDNS_TIMEOUT_S, deadline)
                return end - now

            name = rdns_name(await resolver.ptr(address, servers, slot))
            if name is not None:
                names[address] = name

        # unpaced here: the resolver takes a slot for every query it sends. A lookup
        # that could send its query to none of the servers cancels the lookups still
        # running and no more start, so nothing is sent after it
        pool = _Pool(RDNS_CONCURRENCY, RDNS_TIMEOUT_S, None, clock, deadline, cancel_on_failure=True)
        try:
            if not await pool.run(sorted(alive), lookup):
                timed_out = True
                errors.append(f"timed out after {job.timeout_s} s during the PTR lookups")
        except ProbeError as exc:
            # the names the lookups that finished found are still reported
            complete = False
            errors.append(str(exc))
    if timed_out:
        complete = False

    observations = []
    for address in sorted(alive):
        record = {"ip": str(address)}
        if address in macs:
            record["mac"] = macs[address]
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
