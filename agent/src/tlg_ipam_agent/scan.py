"""Running one job: the ICMP sweep of its addresses, then the ARP sweep, then PTR
lookups for the addresses either found, then the forward-DNS check's A lookups for the
names the job sent (#20), inside the job's timeout (spec §5.2, §5.6).

Every packet goes to an address `guard.plan` returned, or, for a DNS lookup, to one of
the job's DNS servers (the host's own resolvers when the job names none) that
`guard.dns_servers` keeps: with `ALLOWED_DNS_SERVERS` set, none outside it (#93), which
the job's error says, and when it leaves none the lookups are not made. Every packet
takes a slot of the agent's one `MAX_PPS` budget (`Pacer`), carried from job to job:
each echo request, each ARP request (a retry included), and each DNS query, a failover
to the next server included, each handed to the kernel in the step its slot is
granted in, and the next slot measured from the end of that step. The result set is
`complete` only when every issued method ran over every address before the timeout;
otherwise it reports what it saw, which the server only adds (spec §5.7). Apart from
that, `liveness_complete` says whether the liveness sweeps (ICMP, ARP) did, which alone
lets the server mark the silent addresses dead, whatever the PTR lookups did (#100).
A PTR lookup with no server to ask, or whose every attempt failed before its query went
out, did not run; one the deadline stops before its first attempt is the timeout. Each
record says what its own lookup found: a name, `rdns_no_name` when a server answered
that there is none, or neither, and only `rdns_no_name` clears a stored name. The
lookups that got no usable answer are counted in the job's error, and of them those
that met a reply the agent refused unread, for its size or its header's counts (#164),
and, as a notice, the lookups a later server answered after such a refusal (#170).
A forward lookup is judged the same way: a name a server answered has an entry in the
result set's `fdns`, its addresses or an empty list when the name does not resolve, and
a name no server gave a usable answer for has none, so the server keeps its stored
answer; the job's error counts them in their own words. The forward lookups run after
the PTR lookups and only when those neither timed out nor stopped for a query that
could not be sent, so no query is handed to a socket after such a failure.
"""

import asyncio
import ipaddress
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import arp, guard
from .probe import (
    DNS_PAYLOAD,
    DNS_QUESTIONS_MAX,
    DNS_RECORDS_MAX,
    NO_NAME,
    AnsweredPastRefusal,
    ArpLink,
    ArpSession,
    ProbeError,
    Prober,
    Resolver,
    UnreadReplies,
    no_dns_server,
)
from .protocol import (
    FDNS_ADDRESSES_MAX,
    LIVENESS,
    PROTOCOL_VERSION,
    SUPPORTED,
    Address,
    Job,
    JobRef,
    Network,
    rdns_name,
    scan_error,
    scan_error_fits,
)

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
    """The agent's packet budget (spec §5.6): `wait` grants one slot to the task that
    awaits it, and that task hands its packet to the kernel in the same step, before
    its next await: an ARP request, an echo request (`_Pool` has the probe take its
    own slot, and icmplib sends before its first await), a DNS query on a socket made
    before the slot was asked for (`probe.QuerySocket`). `loop.Agent` keeps one for all
    its jobs.

    A slot ends when the step it was granted in ends, so after the packet was handed
    over, however long that took (#121): the turn is held until then, and the next
    slot is granted no sooner than 1 / `pps` after the clock read once it has ended. So
    no two packets are handed over less than 1 / `pps` apart, and no window of one
    second holds more than `pps` of them. Nothing is caught up: a sleep or a hand-off
    that comes late moves every later slot with it, and a sleep that wakes early sleeps
    again. Waiters are served one at a time, in the order they came. A waiter cancelled
    before its grant takes no slot.

    The end of a slot is a callback the event loop runs after the granting step, so a
    turn of the loop (its timers and ready sockets among it) comes between any two
    grants, even when every slot is overdue and no send ever waits: the ARP listener,
    the check-ins, the job's deadline and a cancellation are not held back by a sweep.

    A `Pacer` belongs to one event loop. Stopping the loop in the step of a grant and
    closing it discards the callback that ends the slot and leaves the turn held, so a
    later change that restarts the agent in process, or keeps a pacer across event loops,
    has to make a new one."""

    def __init__(self, pps: int, clock: Clock):
        self._interval = 1.0 / pps
        self._clock = clock
        self._last: float | None = None
        self._turn = asyncio.Lock()

    async def wait(self) -> None:
        await self._turn.acquire()
        try:
            while True:
                now = self._clock.monotonic()
                if self._last is None or now >= self._last + self._interval:
                    break
                await self._clock.sleep(self._last + self._interval - now)
            self._last = now
            # runs once the step that holds this grant has ended, its packet handed over
            asyncio.get_running_loop().call_soon(self._ended)
        except BaseException:
            self._turn.release()
            raise

    def _ended(self) -> None:
        self._last = max(self._last, self._clock.monotonic())
        self._turn.release()


class _Pool:
    """Runs `work(item, timeout)` on each item, at most `limit` at once, each start
    paced, none started after `deadline`, and none given longer than what is left
    before it. Returns whether every item started and finished before the deadline.

    Items start one at a time, in order. With a pacer, an item's task takes its own
    slot and runs the work's first step in the step the slot is granted in, so the
    packet that step hands to the kernel (the echo request) goes before the slot ends
    (`Pacer`); the next item is started only after that.

    When a work item raises, no further item is started and the exception is raised
    once the items in flight are done. With `cancel_on_failure` those items are
    cancelled at once instead of being let finish: none hands another packet to a
    socket. A packet a cancelled item had already handed to the kernel is the kernel's;
    none is left with asyncio to send later (`probe.QuerySocket`).

    When an item failed, the failure is what is raised (the first failed item's, in
    the order the items started), ahead of any cancellation among the results, so a
    sibling's CancelledError that the failure caused never leaves the pool in its
    place. The pool's own cancellation still propagates."""

    def __init__(
        self,
        limit: int,
        item_timeout: float,
        pacer: Pacer | None,
        clock: Clock,
        deadline: float,
        cancel_on_failure: bool = False,
    ):
        """`pacer` paces each start, for work that hands its one packet to the kernel
        in its first step (the echo request); None when the work paces its own sends."""
        self._sem = asyncio.Semaphore(limit)
        self._item_timeout = item_timeout
        self._pacer, self._clock, self._deadline = pacer, clock, deadline
        self._cancel_on_failure = cancel_on_failure

    async def run(self, items, work) -> bool:
        tasks: list[asyncio.Task] = []
        finished = True
        failed = False
        loop = asyncio.get_running_loop()

        async def one(item, started: asyncio.Future):
            nonlocal failed, finished
            try:
                if self._pacer is not None:
                    await self._pacer.wait()
                # from the grant to the work's first await, one step: nothing else runs
                left = self._deadline - self._clock.monotonic()
                # a ProbeError in a running task stops the sweep before the next send;
                # `started` is already done only when the pool itself was cancelled
                if failed or started.done():
                    return
                if left <= 0:
                    finished = False
                    return
                started.set_result(True)
                # a probe started near the deadline waits only until the deadline, so
                # the work in flight never carries the job past it
                await work(item, min(self._item_timeout, left))
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
                if not started.done():
                    started.set_result(False)
                self._sem.release()

        try:
            for item in items:
                if failed:
                    break
                await self._sem.acquire()
                started = loop.create_future()
                task = asyncio.create_task(one(item, started))
                # a task cancelled before its first step (a failing lookup cancels the
                # rest) never runs `one`, so its finally never settles `started`; its
                # done callback does, however it ended
                task.add_done_callback(lambda _, s=started: s.done() or s.set_result(False))
                tasks.append(task)
                # the next item waits until this one has its slot and its work's first
                # step has run (an echo request handed to the kernel), or it will not
                # start: so the pacer has one waiter from the pool at a time, and no
                # item starts after the deadline or a failure
                if not await started:
                    break
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


def _lookups(count: int) -> str:
    return f"{count} lookup" if count == 1 else f"{count} lookups"


def refused_unread(too_large: int, too_many_records: int) -> str:
    """What the agent's own limits refused (#164): of a job's lookups, `too_large` met
    a reply from their server longer than DNS_PAYLOAD and `too_many_records` one whose
    header claimed more than DNS_QUESTIONS_MAX question or DNS_RECORDS_MAX records,
    each refused unread (`probe.UnreadReplies`). A count of zero is left out; at least
    one is not zero. Counts and fixed words only: nothing of a reply, a name, an
    address or a server."""
    parts = []
    if too_large:
        parts.append(f"over {DNS_PAYLOAD} bytes in {_lookups(too_large)}")
    if too_many_records:
        parts.append(
            f"over {DNS_QUESTIONS_MAX} question or {DNS_RECORDS_MAX} records in {_lookups(too_many_records)}"
        )
    return "the agent refused a reply " + ", ".join(parts)


def no_answer(unanswered: int, asked: int, too_large: int = 0, too_many_records: int = 0) -> str:
    """The error of a job some of whose PTR lookups no DNS server gave a usable answer
    to: counts only, since the server keeps those addresses' stored names (spec §5.2).

    Of those lookups, `too_large` and `too_many_records` met a reply the agent refused
    unread (`refused_unread`, #164). One lookup can be in both counts, when its servers
    sent one of each. When neither happened the text is what it was before #164, word
    for word. Otherwise the refusals follow it, and "their stored names are kept"
    becomes "names kept", so that the longest error a job can carry still fits the
    server's SCAN_ERROR_MAX whole (`test_scan.py`'s
    `test_the_longest_error_a_job_can_carry_is_stored_whole`); `error_text` leaves the
    refusals out, whole, should it not."""
    if not (too_large or too_many_records):
        return (
            f"rdns: no DNS server gave a usable answer for {unanswered} of {asked} "
            "addresses; their stored names are kept"
        )
    return (
        f"rdns: no DNS server gave a usable answer for {unanswered} of {asked} addresses, "
        f"names kept; {refused_unread(too_large, too_many_records)}"
    )


def fdns_no_answer(unanswered: int, asked: int, too_large: int = 0, too_many_records: int = 0) -> str:
    """`no_answer` for the forward-DNS check's lookups (#20): of the `asked` names a job
    sent, no DNS server gave a usable answer for `unanswered`, so the result set has no
    entry for them and the server keeps their stored answers. The refusals follow as
    they do for the PTR lookups, and `error_text` leaves them out whole should the
    whole not fit."""
    if not (too_large or too_many_records):
        return (
            f"fdns: no DNS server gave a usable answer for {unanswered} of {asked} "
            "names; their stored answers are kept"
        )
    return (
        f"fdns: no DNS server gave a usable answer for {unanswered} of {asked} names, "
        f"answers kept; {refused_unread(too_large, too_many_records)}"
    )


def cut_short(too_large: int, too_many_records: int, what: str = "rdns") -> str:
    """The refusals of a job whose PTR lookups the deadline cut short, after the
    timeout's own error (#164, round 1 of the briefed review, finding 1): counted over
    the lookups that returned, each refusal having ended an attempt before the
    deadline. A lookup the pool cancelled at the deadline returned nothing, and is not
    counted whatever its earlier attempts met. The count of lookups without a usable
    answer stays out of such a job's error, as before #164: the timeout's error already
    says the lookups did not all run."""
    return f"{what}: before the timeout, {refused_unread(too_large, too_many_records)}"


def answered_later(too_large: int, too_many_records: int, what: str = "rdns") -> str:
    """The notice of a job some of whose PTR lookups a later server answered, with a
    name or that there is none, after an earlier attempt of each met a reply from its
    own server that the agent refused unread (#170): `too_large` and
    `too_many_records` count those lookups by limit (`probe.AnsweredPastRefusal`), in
    `refused_unread`'s words. Those lookups had their answer, so nothing was lost but
    a query and a packet of the budget each; this says so, so that a first server
    whose every reply is refused shows before the day the later one stops answering.
    Counts and fixed words only, as #164's: nothing of a reply, a name, an address or
    a server, which of the configured servers it was included."""
    return f"{what}: a later server answered after {refused_unread(too_large, too_many_records)}"


def outside_allowed(outside: int, chosen: int, what: str = "rdns") -> str:
    """What `ALLOWED_DNS_SERVERS` took away (#93): of the `chosen` servers the agent
    would have asked (`Resolver.servers`), `outside` lie outside it and were not asked.
    When that is all of them the lookups were not made, which this says as NO_DNS_SERVER
    does: the result set is then incomplete, so the server keeps every stored name.
    Otherwise the rest were asked, and this is a notice. Counts and fixed words only:
    nothing says which server, whose address the server sent or the host's resolv.conf
    holds, as #164's counts name none."""
    if outside == chosen:
        if chosen == 1:
            return f"{what} not run: the 1 DNS server to ask is outside ALLOWED_DNS_SERVERS"
        return f"{what} not run: all {chosen} DNS servers to ask are outside ALLOWED_DNS_SERVERS"
    verb, passive = ("is", "was") if outside == 1 else ("are", "were")
    return (
        f"{what}: {outside} of the {chosen} DNS servers to ask {verb} outside ALLOWED_DNS_SERVERS "
        f"and {passive} not asked"
    )


# The error of a job whose forward-DNS lookups were not made because its PTR lookups
# stopped with no query sent, which the error says just before it: none is handed to a
# socket after that failure (#20). Short, so that with it the longest error of such a
# job still keeps #93's notice (`test_scan.py`)
FDNS_AFTER_RDNS_STOPPED = "fdns not run"


def lookups_named(rdns: bool, fdns: bool) -> str:
    """Which lookups a job's DNS error is about: `rdns`, `fdns`, or both."""
    return "rdns and fdns" if rdns and fdns else ("fdns" if fdns else "rdns")


def error_text(errors: list[str], *optional: tuple[int, str | None]) -> str | None:
    """A job's error: its parts joined, made printable and capped as the server stores
    it (`scan_error`). Each of `optional` names a part that carries counts, at its
    place in `errors`, with the text that stands there without them (None: nothing),
    in the order they are given up: `scan.run` gives the notices of lookups a later
    server answered first (#170), the forward lookups' before the PTR lookups' (#20),
    then the refusal counts of the lookups without a usable answer (#164), in the same
    order, then the notice of servers `ALLOWED_DNS_SERVERS` left out (#93), which is
    short and says a cause no count does. While the whole would not
    fit SCAN_ERROR_MAX, the next of them is left out whole, never cut short of its
    number (round 1 of #164's briefed review, finding 2); what is left is capped as
    before."""
    if not errors:
        return None
    parts: list[str | None] = list(errors)
    for at, without in optional:
        if scan_error_fits("; ".join(p for p in parts if p is not None)):
            break
        parts[at] = without
    return scan_error("; ".join(p for p in parts if p is not None))


def refusal(ref: JobRef, reason: str, clock: Clock) -> dict:
    """The result set for a job the agent would not run: nothing scanned, the reason
    as the error, so the server shows it and clears the lease (spec §5.7)."""
    now = clock.now().isoformat()
    return {
        "protocol": PROTOCOL_VERSION,
        "job_id": ref.job_id,
        "subnet_id": ref.subnet_id,
        "started_at": now,
        "finished_at": now,
        "complete": False,
        "liveness_complete": False,
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
    pacer: Pacer | None = None,
    allowed_dns: tuple[Network, ...] | None = None,
) -> dict:
    """Sweep `addresses` (from `guard.plan`) and build the result set. `arp_link`
    opens the ARP socket on an interface named in `interfaces`, the host's own as
    `interfaces.discover` reads them when the job starts. `pacer` is the agent's packet
    budget, carried from job to job (`loop.Agent`); without one, the job has its own
    at `max_pps`. `allowed_dns` is `ALLOWED_DNS_SERVERS` (None: unset), which the
    servers the PTR lookups ask must lie in (`guard.dns_servers`)."""
    started = clock.now()
    deadline = clock.monotonic() + job.timeout_s
    if pacer is None:
        pacer = Pacer(max_pps, clock)
    errors: list[str] = []
    complete = True
    # whether every issued liveness method ran over every address before the
    # deadline: what lets the server mark the silent addresses dead
    liveness_complete = True

    unsupported = sorted(job.methods - SUPPORTED)
    if unsupported:
        # none since #20 (this release runs every method the protocol names), kept for
        # a method a later protocol adds: the rest runs, but a sweep that skipped an
        # issued liveness method must not let the server mark the silent addresses dead
        complete = False
        liveness_complete = not (LIVENESS & set(unsupported))
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
            complete = liveness_complete = False
            errors.append(str(exc))

    macs: dict[Address, str] = {}
    if "arp" in job.methods and not timed_out:
        interface = arp_interface(job.network, interfaces)
        if interface is None or arp_link is None:
            complete = liveness_complete = False
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
                complete = liveness_complete = False
                errors.append(str(exc))
            if session is not None:
                macs.update(session.answers)
        alive.update(macs)
    if timed_out:
        # a liveness sweep the deadline cut short, or the ARP sweep it left unrun
        liveness_complete = False

    names: dict[Address, str] = {}
    # the addresses a server answered have no name, and those no server answered
    no_name: set[Address] = set()
    unanswered: set[Address] = set()
    # of those, the ones a server sent a reply the agent refused unread: for its size,
    # for its header's counts (a lookup a later server answered is in neither)
    too_large: set[Address] = set()
    too_many_records: set[Address] = set()
    # the lookups a later server answered after such a refusal, by limit (#170)
    later_too_large: set[Address] = set()
    later_too_many_records: set[Address] = set()
    looked_up = 0  # lookups that returned, whatever they found
    # the parts of `errors` that carry counts, and what stands there without them, by
    # the order they are given up should the error not fit (`error_text`): each
    # lookup's notice, then each lookup's refusal counts, the forward lookups' first
    rdns_notice: tuple[int, str | None] | None = None
    rdns_counts: tuple[int, str | None] | None = None
    fdns_notice: tuple[int, str | None] | None = None
    fdns_counts: tuple[int, str | None] | None = None
    # the notice of servers ALLOWED_DNS_SERVERS left out, while some are still asked
    narrowed: tuple[int, str | None] | None = None
    # which lookups the job needs a DNS server for: PTR for its live addresses, A for
    # the names it was sent (#20), chosen once for both
    wants_rdns = "rdns" in job.methods and bool(alive)
    wants_fdns = bool(job.fdns)
    lookups = lookups_named(wants_rdns, wants_fdns)
    servers: tuple[str, ...] = ()
    if (wants_rdns or wants_fdns) and not timed_out:
        # chosen once for the job: its own servers, else the host's resolvers as they
        # are now. With none, no lookup can be made, and a lookup not made is not one
        # that found no name: the result set is incomplete, so the server keeps the
        # names and answers it has (spec §5.7), and no query is sent
        left = deadline - clock.monotonic()
        try:
            if left <= 0:
                raise TimeoutError
            # the choice (a read of resolv.conf among it) has only the job's time left,
            # waited out on the event loop's clock as the read's own bound is; ending
            # the wait leaves a blocked read's thread, and its lock, as they are
            servers = await asyncio.wait_for(resolver.servers(job.dns_servers), left)
        except TimeoutError:
            timed_out = True
            step = "PTR" if wants_rdns else "forward"
            errors.append(f"timed out after {job.timeout_s} s during the {step} lookups")
        else:
            # the host's limit, before any query: a server outside it is never asked,
            # whether the job named it or resolv.conf lists it (#93)
            chosen = len(servers)
            servers, outside = guard.dns_servers(servers, allowed_dns)
            if not servers:
                # no lookup is made: so the result set is incomplete, and the server
                # keeps the names it has, as with no server at all
                complete = False
                errors.append(outside_allowed(outside, chosen, lookups) if outside else no_dns_server(lookups))
            elif outside:
                # the rest are asked, as they would be were the others silent
                errors.append(outside_allowed(outside, chosen, lookups))
                narrowed = (len(errors) - 1, None)

    def lookup_slot() -> Callable[[], Awaitable[float]]:
        """One lookup's `slot`: a packet from the budget the sweeps drew on, before each
        query, and the seconds the lookup has left."""
        end: float | None = None

        async def slot() -> float:
            nonlocal end
            await pacer.wait()
            now = clock.monotonic()
            if end is None:
                # the lookup's own time starts once its first query may go, not while
                # it queues behind other lookups for a slot; the job's deadline holds
                end = min(now + RDNS_TIMEOUT_S, deadline)
            return end - now

        return slot

    rdns_stopped = False
    if servers and wants_rdns:

        async def lookup(address: Address, _timeout: float) -> None:
            nonlocal looked_up
            answer = await resolver.ptr(address, servers, lookup_slot())
            looked_up += 1
            if isinstance(answer, AnsweredPastRefusal):
                # a later server's answer decides the record as it would alone; that an
                # earlier server's reply was refused is only counted (#170)
                if answer.too_large:
                    later_too_large.add(address)
                if answer.too_many_records:
                    later_too_many_records.add(address)
                answer = answer.answer
            if answer is NO_NAME:
                no_name.add(address)
            elif answer is None or isinstance(answer, UnreadReplies):
                # no usable answer, or none in time: says nothing about a name
                unanswered.add(address)
                if isinstance(answer, UnreadReplies):
                    if answer.too_large:
                        too_large.add(address)
                    if answer.too_many_records:
                        too_many_records.add(address)
            else:
                # a name that is not a hostname is dropped, and is not "no name": the
                # record carries neither, so the stored name stays
                name = rdns_name(answer)
                if name is not None:
                    names[address] = name

        # unpaced here: the resolver takes a slot for every query it sends. A lookup
        # that could send its query to none of the servers cancels the lookups still
        # running and no more start, so no query is handed to a socket after it, and a
        # cancelled lookup's socket is aborted: no query of the job leaves after it
        pool = _Pool(RDNS_CONCURRENCY, RDNS_TIMEOUT_S, None, clock, deadline, cancel_on_failure=True)
        try:
            if not await pool.run(sorted(alive), lookup):
                timed_out = True
                errors.append(f"timed out after {job.timeout_s} s during the PTR lookups")
        except ProbeError as exc:
            # the names the lookups that finished found are still reported
            complete = False
            rdns_stopped = True
            errors.append(str(exc))
        # a lookup the deadline cut off is in the timeout's error, not counted here;
        # the refusals of the lookups that ended in time are (#164)
        if unanswered and not timed_out:
            errors.append(no_answer(len(unanswered), looked_up, len(too_large), len(too_many_records)))
            if too_large or too_many_records:
                rdns_counts = (len(errors) - 1, no_answer(len(unanswered), looked_up))
        elif timed_out and (too_large or too_many_records):
            errors.append(cut_short(len(too_large), len(too_many_records)))
            rdns_counts = (len(errors) - 1, None)
        # the lookups that returned with a later server's answer, whatever ended the
        # rest: last, and the first part given up should the error not fit (#170)
        if later_too_large or later_too_many_records:
            errors.append(answered_later(len(later_too_large), len(later_too_many_records)))
            rdns_notice = (len(errors) - 1, None)

    # The forward-DNS check (#20): the A records of each name the job sent, after the
    # PTR lookups, through the same resolver, servers and budget. A name a server
    # answered has an entry (an empty address list when it does not resolve); one no
    # server gave a usable answer for has none, so the server keeps its stored answer.
    resolved: dict[str, tuple[str, ...]] = {}
    if servers and wants_fdns and not timed_out:
        if rdns_stopped:
            # no query is handed to a socket after a lookup that could send none
            complete = False
            errors.append(FDNS_AFTER_RDNS_STOPPED)
        else:
            f_unanswered: set[str] = set()
            f_too_large: set[str] = set()
            f_too_many_records: set[str] = set()
            f_later_too_large: set[str] = set()
            f_later_too_many_records: set[str] = set()
            f_looked_up = 0

            async def resolve(item: tuple[str, str], _timeout: float) -> None:
                nonlocal f_looked_up
                host_id, fqdn = item
                answer = await resolver.a(fqdn, servers, lookup_slot())
                f_looked_up += 1
                if isinstance(answer, AnsweredPastRefusal):
                    if answer.too_large:
                        f_later_too_large.add(host_id)
                    if answer.too_many_records:
                        f_later_too_many_records.add(host_id)
                    answer = answer.answer
                if answer is NO_NAME:
                    resolved[host_id] = ()
                elif answer is None or isinstance(answer, UnreadReplies):
                    f_unanswered.add(host_id)
                    if isinstance(answer, UnreadReplies):
                        if answer.too_large:
                            f_too_large.add(host_id)
                        if answer.too_many_records:
                            f_too_many_records.add(host_id)
                else:
                    resolved[host_id] = tuple(answer)[:FDNS_ADDRESSES_MAX]

            pool = _Pool(RDNS_CONCURRENCY, RDNS_TIMEOUT_S, None, clock, deadline, cancel_on_failure=True)
            try:
                if not await pool.run(list(job.fdns), resolve):
                    timed_out = True
                    errors.append(f"timed out after {job.timeout_s} s during the forward lookups")
            except ProbeError as exc:
                # the answers the lookups that finished found are still reported
                complete = False
                errors.append(str(exc))
            if f_unanswered and not timed_out:
                errors.append(
                    fdns_no_answer(len(f_unanswered), f_looked_up, len(f_too_large), len(f_too_many_records))
                )
                if f_too_large or f_too_many_records:
                    fdns_counts = (len(errors) - 1, fdns_no_answer(len(f_unanswered), f_looked_up))
            elif timed_out and (f_too_large or f_too_many_records):
                errors.append(cut_short(len(f_too_large), len(f_too_many_records), "fdns"))
                fdns_counts = (len(errors) - 1, None)
            if f_later_too_large or f_later_too_many_records:
                errors.append(answered_later(len(f_later_too_large), len(f_later_too_many_records), "fdns"))
                fdns_notice = (len(errors) - 1, None)
    if timed_out:
        complete = False

    observations = []
    for address in sorted(alive):
        record = {"ip": str(address)}
        if address in macs:
            record["mac"] = macs[address]
        if address in names:
            record["rdns_name"] = names[address]
        elif address in no_name:
            record["rdns_no_name"] = True
        observations.append(record)
    return {
        "protocol": PROTOCOL_VERSION,
        "job_id": job.ref.job_id,
        "subnet_id": job.ref.subnet_id,
        "started_at": started.isoformat(),
        "finished_at": max(clock.now(), started).isoformat(),
        "complete": complete,
        "liveness_complete": liveness_complete,
        "observations": observations,
        "fdns": [
            {"host_id": host_id, "addresses": list(resolved[host_id])}
            for host_id, _ in job.fdns
            if host_id in resolved
        ],
        "error": error_text(
            errors,
            *(
                part
                for part in (fdns_notice, rdns_notice, fdns_counts, rdns_counts, narrowed)
                if part is not None
            ),
        ),
    }
