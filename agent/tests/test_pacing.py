"""The job's packet budget on the wire (spec §5.6, #87): what `scan.Pacer` grants when
its sleeps wake late or early, when every slot is already overdue, and what the
sweeps hand to the kernel between two grants. Fake clocks and fake wires only: no
packet is sent."""

import asyncio
import bisect
import random
import time

import pytest

from conftest import ETH0, FakeArp, FakeClock, FakeProber, FakeResolver, job
from tlg_ipam_agent import guard, scan
from tlg_ipam_agent.config import parse_allowed
from tlg_ipam_agent.protocol import job_ref, parse_job


def planned(**overrides):
    raw = job(**overrides)
    parsed = parse_job(job_ref(raw), raw)
    return parsed, guard.plan(parsed, parse_allowed(None))


async def sweep(parsed, addresses, *, clock, max_pps, prober=None, resolver=None, arp_link=None):
    return await scan.run(
        parsed,
        addresses,
        prober=prober or FakeProber(clock=clock),
        resolver=resolver or FakeResolver(clock=clock),
        arp_link=arp_link if arp_link is not None else FakeArp(clock=clock),
        interfaces=ETH0,
        max_pps=max_pps,
        clock=clock,
    )


def most_in_one_second(times) -> int:
    """The most sends in any half-open window of one second."""
    times = sorted(times)
    return max(bisect.bisect_left(times, t + 1.0 - 1e-9) - i for i, t in enumerate(times))


def shortest_gap(times) -> float:
    times = sorted(times)
    return min(b - a for a, b in zip(times, times[1:]))


class OffClock(FakeClock):
    """A fake clock whose sleeps do not end when asked: `off(n, seconds)` is added to
    the n-th sleep (counted from 1), late when positive, early when negative."""

    def __init__(self, off):
        super().__init__()
        self.off = off
        self.count = 0

    async def _sleep(self, seconds: float) -> None:
        self.count += 1
        await super()._sleep(max(seconds + self.off(self.count, seconds), 0.0))


def first_sleep_late(by: float):
    return lambda n, seconds: by if n == 1 else 0.0


def jittered(pps: int, seed: int = 87):
    """Every sleep off by up to three slots late, or ends at a fraction of what it
    asked: a third late, a sixth early, the rest on time."""
    rng = random.Random(seed)

    def off(n, seconds):
        roll = rng.random()
        if roll < 1 / 3:
            return rng.uniform(0, 3 / pps)
        if roll < 1 / 2:
            return -seconds * rng.uniform(0.1, 0.9)
        return 0.0

    return off


# --- 1. a late wake does not bring the next send sooner ---


async def test_the_arp_sweep_after_a_late_wake_from_the_review_of_pr_84():
    """Round 1 of PR #84, finding 3, as it was measured: an ARP-only job for three
    silent addresses at MAX_PPS=1, the first pacing sleep woken 200 ms late. The
    pacer advanced its next slot before it slept, so it sent at 1000.0, 1001.2 and
    1002.0: two frames in the second from 1001.2. Now every slot follows the one before
    by a second at least, and the late wake moves the ones after it."""
    clock = OffClock(first_sleep_late(0.2))
    link = FakeArp(clock=clock)
    parsed, addresses = planned(methods=["arp"], ranges=[{"start": "10.0.0.1", "end": "10.0.0.3"}])
    result = await sweep(parsed, addresses, clock=clock, max_pps=1, arp_link=link)
    assert result["complete"] is True
    assert link.times == pytest.approx([1000.0, 1001.2, 1002.2, 1003.2, 1004.2, 1005.2])
    assert most_in_one_second(link.times) == 1


@pytest.mark.parametrize("pps", [1, 20, 200, 1000])
@pytest.mark.parametrize("seed", [1, 2, 3])
async def test_no_second_holds_more_than_max_pps_when_sleeps_wake_late_or_early(pps, seed):
    """A job that runs `icmp`, `arp` and `rdns` over 40 addresses, every pacing sleep
    off at random: late by up to three slots, or early. The echo requests, the ARP
    requests and retries, and the PTR queries (16 lookups waiting for slots at once)
    together never put more than MAX_PPS in a second, and no two of them come less
    than a slot apart, across the boundaries between the three."""
    clock = OffClock(jittered(pps, seed))
    alive = [f"10.0.0.{i}" for i in range(1, 41, 3)]
    prober = FakeProber(alive=alive, clock=clock)
    link = FakeArp({f"10.0.0.{i}": f"02:00:00:00:00:{i:02x}" for i in range(2, 41, 4)}, clock=clock)
    resolver = FakeResolver({a: "host.home.arpa" for a in alive}, clock=clock)
    parsed, addresses = planned(
        methods=["icmp", "arp", "rdns"], ranges=[{"start": "10.0.0.1", "end": "10.0.0.40"}],
        dns_servers=["10.0.0.53"],
    )
    result = await sweep(
        parsed, addresses, clock=clock, max_pps=pps, prober=prober, resolver=resolver, arp_link=link,
    )
    assert result["complete"] is True
    found = {o["ip"] for o in result["observations"]}
    # every echo request, every ARP request and retry, a query for every live address
    assert len(prober.times) == 40
    assert len(link.times) == 39 + (39 - 10)
    assert len(resolver.times) == len(found)
    sends = prober.times + link.times + resolver.times
    assert most_in_one_second(sends) <= pps
    assert shortest_gap(sends) >= 1 / pps - 1e-9


async def test_waiters_are_granted_in_turn_a_slot_apart():
    """Sixteen lookups waiting at once, as the PTR pool has them: the slots go in the
    order the waiters came, each a slot after the one before, with every sleep late."""
    clock = OffClock(lambda n, seconds: 0.03)
    pacer = scan.Pacer(10, clock)
    granted: list[tuple[int, float]] = []

    async def waiter(i):
        await pacer.wait()
        granted.append((i, clock.t))

    await asyncio.gather(*(waiter(i) for i in range(16)))
    assert [i for i, _ in granted] == list(range(16))
    times = [t for _, t in granted]
    assert all(b - a >= 0.1 - 1e-9 for a, b in zip(times, times[1:]))


async def test_a_waiter_cancelled_before_its_grant_takes_no_slot():
    clock = FakeClock()
    pacer = scan.Pacer(1, clock)
    await pacer.wait()  # granted at once, at 1000
    cancelled = asyncio.create_task(pacer.wait())
    await asyncio.sleep(0)  # it is sleeping towards 1001
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    await pacer.wait()
    # the next slot, not the one after the cancelled waiter's
    assert clock.t == pytest.approx(1001.0)


class HeldClock(FakeClock):
    """A fake clock whose first sleep is held until `release()`; every later sleep ends
    at once and moves the clock on by what it asked. `started` counts the sleeps that
    have begun. With the first one held, a second sleep that starts is one the pacer
    let begin while the first was still in progress."""

    def __init__(self):
        super().__init__()
        self.started = 0
        self._held = asyncio.Event()

    def release(self):
        self._held.set()

    async def _sleep(self, seconds: float) -> None:
        self.started += 1
        if self.started == 1:
            await self._held.wait()
        else:
            await asyncio.sleep(0)
        self.t += max(seconds, 0)


async def turns(n: int = 20) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


async def test_a_later_waiter_does_not_overtake_the_one_holding_the_turn():
    """The lock `Pacer.wait` serves waiters behind, held to its own test (#107): with
    the first waiter's sleep held, a second waiter starts no sleep of its own. Without
    the lock it does, that sleep (which here ends at once) advances the clock, and the
    second waiter is granted its slot before the first, which is a slot taken twice.
    The additive fake sleeps of the test above keep arrival order without a lock; this clock does
    not."""
    clock = HeldClock()
    pacer = scan.Pacer(1, clock)
    await pacer.wait()  # the first slot, at once, at 1000
    granted: list[int] = []

    async def waiter(i):
        await pacer.wait()
        granted.append(i)

    first = asyncio.create_task(waiter(1))
    await turns(2)  # the first waiter is asleep towards 1001
    second = asyncio.create_task(waiter(2))
    await turns()
    started_while_held = clock.started
    clock.release()
    await asyncio.gather(first, second)
    assert granted == [1, 2], "a later waiter must not overtake the lock holder"
    assert clock.t == pytest.approx(1002.0)  # a slot each, one after the other
    assert started_while_held == 1, "a second waiter must not start a sleep before the holder releases"


async def test_a_queued_waiter_that_is_cancelled_leaves_the_others_in_turn():
    """Four waiters behind a held sleep, the third cancelled while it waits for its
    turn: still no second sleep starts before the holder releases, the cancelled one
    takes no slot, and the others are granted in the order they came, a slot apart."""
    clock = HeldClock()
    pacer = scan.Pacer(1, clock)
    await pacer.wait()
    granted: list[tuple[int, float]] = []

    async def waiter(i):
        await pacer.wait()
        granted.append((i, clock.t))

    tasks = {}
    for i in (1, 2, 3, 4):
        tasks[i] = asyncio.create_task(waiter(i))
        await turns(2)
    await turns()
    started_while_queued = clock.started
    tasks[3].cancel()
    await turns()
    started_after_cancel = clock.started
    clock.release()
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    assert [i for i, _ in granted] == [1, 2, 4], "the surviving waiters must keep the order they queued in"
    assert isinstance(results[2], asyncio.CancelledError)
    assert [t for _, t in granted] == pytest.approx([1001.0, 1002.0, 1003.0])
    assert started_while_queued == 1, "no waiter may start a sleep before the holder releases"
    assert started_after_cancel == 1, "cancelling a queued waiter must not start a sleep either"


async def test_a_sleep_that_ends_early_is_slept_again():
    clock = OffClock(lambda n, seconds: -seconds / 2)
    pacer = scan.Pacer(4, clock)
    times = []
    for _ in range(5):
        await pacer.wait()
        times.append(clock.t)
    assert all(b - a >= 0.25 - 1e-9 for a, b in zip(times, times[1:]))


# --- the echo request goes before the next slot ---


async def test_each_echo_request_is_made_before_the_next_slot_is_granted():
    """A pacer whose every slot is overdue, so its grants come without a sleep: each
    probe's first step, where the echo request is handed to the kernel, still runs
    before the next slot is asked for (since #121 the next probe asks for it, once the
    pool has seen this one start). Without that, the probes started while the pacer
    did not sleep would all send together once the pool next waited."""

    class Overdue:
        grants = 0

        async def wait(self):
            self.grants += 1

    pacer = Overdue()
    seen: list[int] = []

    async def work(item, timeout):
        seen.append(pacer.grants)

    pool = scan._Pool(scan.PING_CONCURRENCY, 1.0, pacer, scan.Clock(), deadline=time.monotonic() + 60)
    assert await pool.run(range(200), work) is True
    assert seen == list(range(1, 201))


# --- 2. a packet handed over late moves the next slot (#121) ---
#
# In a real sweep two echo requests left 0.15 ms apart at MAX_PPS=200, one of them
# about 5 ms late (#121). These fakes put time between a grant and its hand-off, as
# the process not running would (the host's scheduler, a garbage collection, other
# callbacks ahead of the probe): `stall(n)` is how far the clock moves on before the
# n-th packet of that kind is handed over, in the step that hands it over.


class StalledProber(FakeProber):
    def __init__(self, stall, **kwargs):
        super().__init__(**kwargs)
        self.stall, self.count = stall, 0

    async def ping(self, address, timeout):
        self.count += 1
        self.clock.t += self.stall(self.count)
        return await super().ping(address, timeout)


class StalledLink(FakeArp):
    def __init__(self, stall, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stall, self.count = stall, 0

    async def send(self, frame: bytes) -> None:
        self.count += 1
        self.clock.t += self.stall(self.count)
        await super().send(frame)


class StalledResolver(FakeResolver):
    def __init__(self, stall, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stall, self.count = stall, 0

    async def ptr(self, address, servers, slot):
        async def stalled():
            left = await slot()
            self.count += 1
            self.clock.t += self.stall(self.count)
            return left

        return await super().ptr(address, servers, stalled)


def nth(n: int, by: float):
    return lambda count: by if count == n else 0.0


SLOT_200 = 1 / 200


def stalled_sweep(kind: str, stall):
    """The fakes for a job of `kind`, with `stall` on that kind's sends only: what to
    run, and every send of the job, in the order they went."""
    clock = FakeClock()
    alive = [f"10.0.0.{i}" for i in range(1, 7)]
    prober = (StalledProber(stall, alive=alive, clock=clock) if kind == "icmp"
              else FakeProber(alive=alive, clock=clock))
    link = (StalledLink(stall, clock=clock) if kind == "arp" else FakeArp(clock=clock))
    names = {a: "host.home.arpa" for a in alive}
    resolver = (StalledResolver(stall, names, clock=clock) if kind == "rdns"
                else FakeResolver(names, clock=clock))
    methods = {"icmp": ["icmp"], "arp": ["arp"], "rdns": ["icmp", "rdns"]}[kind]
    parsed, addresses = planned(
        methods=methods, ranges=[{"start": "10.0.0.1", "end": "10.0.0.6"}], dns_servers=["10.0.0.53"],
    )

    async def go():
        result = await sweep(
            parsed, addresses, clock=clock, max_pps=200, prober=prober, resolver=resolver, arp_link=link,
        )
        return result, sorted(prober.times + link.times + resolver.times)

    return go


@pytest.mark.parametrize("kind", ["icmp", "arp", "rdns"])
@pytest.mark.parametrize("late", [3 * SLOT_200, 0.4 * SLOT_200])
async def test_a_packet_handed_over_late_moves_the_next_slot(kind, late):
    """The first packet of one kind handed to the kernel `late` after its grant, by
    three slots or by less than one: the next packet still goes a slot or more after
    it, and the ones after that keep their spacing. Before #121 the pacer measured each
    slot from the grant before it, so the next grant was already due, or nearly, and
    the two packets went closer than a slot (0 s apart after a stall of three slots)."""
    result, sends = await stalled_sweep(kind, nth(1, late))()
    assert result["complete"] is True
    assert len(sends) > 3
    assert shortest_gap(sends) >= SLOT_200 - 1e-9


@pytest.mark.parametrize("kind", ["icmp", "arp", "rdns"])
async def test_every_packet_handed_over_late_moves_the_slot_after_it(kind):
    """Every packet of one kind handed over between nothing and two slots after its
    grant, at random: no two sends of the job come less than a slot apart."""
    rng = random.Random(121)
    result, sends = await stalled_sweep(kind, lambda count: rng.uniform(0, 2 * SLOT_200))()
    assert result["complete"] is True
    assert shortest_gap(sends) >= SLOT_200 - 1e-9


@pytest.mark.parametrize("pps", [20, 200, 1000])
@pytest.mark.parametrize("seed", [1, 2, 3])
async def test_no_second_holds_more_than_max_pps_when_hand_offs_and_wakes_are_late(pps, seed):
    """The job of the jittered test above (`icmp`, `arp`, `rdns` over 40 addresses,
    every pacing sleep late or early at random), with every send of all three kinds
    also handed over up to two slots after its grant: no two sends less than a slot
    apart, and no second holding more than MAX_PPS of them."""
    clock = OffClock(jittered(pps, seed))
    rng = random.Random(seed + 121)

    def stall(count):
        return rng.uniform(0, 2 / pps)

    alive = [f"10.0.0.{i}" for i in range(1, 41, 3)]
    prober = StalledProber(stall, alive=alive, clock=clock)
    link = StalledLink(stall, {f"10.0.0.{i}": f"02:00:00:00:00:{i:02x}" for i in range(2, 41, 4)}, clock=clock)
    resolver = StalledResolver(stall, {a: "host.home.arpa" for a in alive}, clock=clock)
    parsed, addresses = planned(
        methods=["icmp", "arp", "rdns"], ranges=[{"start": "10.0.0.1", "end": "10.0.0.40"}],
        dns_servers=["10.0.0.53"],
    )
    result = await sweep(
        parsed, addresses, clock=clock, max_pps=pps, prober=prober, resolver=resolver, arp_link=link,
    )
    assert result["complete"] is True
    assert len(prober.times) == 40 and len(link.times) == 39 + (39 - 10)
    sends = prober.times + link.times + resolver.times
    assert shortest_gap(sends) >= 1 / pps - 1e-9
    assert most_in_one_second(sends) <= pps


# --- 3. a sender that is always late still lets the rest of the agent run ---


class SlowLink(FakeArp):
    """An ARP link whose send never waits, on a loop whose every turn after a send
    takes `cost` seconds of the fake clock (a callback the send leaves behind), so at
    MAX_PPS=1000 every slot is overdue before it is asked for (the review of PR #84,
    round 2: a 2 ms send and `Pacer(1000)`) and the pacer never sleeps. The time passes
    after the slot has ended: time spent inside the send moves the next slot instead
    (section 2). `first_receive` is how many requests had gone when the listener first
    read the link; `pacer_sleeps` counts the sleeps shorter than a slot."""

    def __init__(self, clock, cost=0.002, on_send=None):
        super().__init__(clock=clock)
        self.cost = cost
        self.on_send = on_send
        self.first_receive: int | None = None

    async def send(self, frame: bytes) -> None:
        await super().send(frame)
        asyncio.get_running_loop().call_soon(self._spend)
        if self.on_send is not None:
            self.on_send(len(self.requests))

    def _spend(self) -> None:
        self.clock.t += self.cost

    async def receive(self):
        if self.first_receive is None:
            self.first_receive = len(self.requests)
        return await super().receive()


def pacer_sleeps(clock) -> int:
    # every other sleep of an ARP-only job is a round's wait for replies, in steps of
    # ARP_POLL_S, or what is left of it, which is never under a slot here
    return sum(1 for s in clock.sleeps if s <= 1 / 1000)


async def test_a_ready_task_runs_between_any_two_late_sends():
    """Every slot overdue and every send synchronous: before #87's review, a whole
    round of 253 requests went without the event loop turning, and the listener first
    read the link after all of them; #87 let every other task run after 64 grants in a
    row without a sleep. Since #121 a slot ends in a callback after the step it was
    granted in, and the next grant waits for it, so the loop turns between any two
    grants: a task that is ready, the listener among them, waits for at most one
    send."""
    clock = FakeClock()
    link = SlowLink(clock)
    seen: list[int] = []
    done = False

    async def other():
        while not done:
            seen.append(len(link.requests))
            await asyncio.sleep(0)

    beside = asyncio.create_task(other())
    await asyncio.sleep(0)
    parsed, addresses = planned(methods=["arp"])
    try:
        result = await sweep(parsed, addresses, clock=clock, max_pps=1000, arp_link=link)
    finally:
        done = True
        await beside
    assert result["complete"] is True and len(link.requests) == 2 * 253
    assert pacer_sleeps(clock) == 0, "every slot must be overdue, or this tests nothing"
    assert max(b - a for a, b in zip(seen, seen[1:])) == 1
    assert link.first_receive is not None and link.first_receive <= 1


@pytest.mark.parametrize("due_after", [1, 10, 64, 300])
async def test_a_timer_that_falls_due_runs_before_the_next_late_send(due_after):
    """A timer that falls due while the sends go on (a check-in, the job's deadline,
    a cancellation: anything a timer or a readable socket wakes) is seen at the loop's
    next turn, which comes before the next grant. The task it wakes runs a turn after
    that, behind the waiter the slot's end released: after one more send at most."""
    clock = FakeClock()
    woke = asyncio.Event()
    due_at: list[int] = []

    def on_send(sent):
        if sent == due_after:
            due_at.append(sent)
            asyncio.get_running_loop().call_later(0, woke.set)

    link = SlowLink(clock, on_send=on_send)
    ran_at: list[int] = []

    async def other():
        await woke.wait()
        ran_at.append(len(link.requests))

    beside = asyncio.create_task(other())
    parsed, addresses = planned(methods=["arp"])
    result = await sweep(parsed, addresses, clock=clock, max_pps=1000, arp_link=link)
    await beside
    assert result["complete"] is True
    assert pacer_sleeps(clock) == 0, "every slot must be overdue, or this tests nothing"
    assert due_at == [due_after]
    assert len(ran_at) == 1 and ran_at[0] - due_at[0] <= 1


# --- 4. the pool's start handshake (the review of PR #123) ---


@pytest.mark.parametrize("paced", [False, True])
async def test_an_item_cancelled_before_its_first_step_does_not_hold_the_pool(paced):
    """The pool waits for each item to start before it starts the next. A failing item
    that cancels the rest (`cancel_on_failure`, the PTR lookups') can cancel the newest
    one before its first step has run, so its coroutine never runs: the pool must still
    see that it did not start, and raise the failure, rather than wait for ever (until
    the scan's backstop discards everything the job saw). Found by Codex and Greptile
    on PR #123: item 1 waits on a future, item 2 fails it and item 3 is cancelled
    unstarted."""
    trigger = asyncio.get_running_loop().create_future()
    ran: list[int] = []

    async def work(item, timeout):
        ran.append(item)
        if item == 1:
            await trigger
        elif item == 2:
            trigger.set_exception(scan.ProbeError("no query sent"))
            await asyncio.sleep(0)

    pacer = scan.Pacer(1000, scan.Clock()) if paced else None
    pool = scan._Pool(16, 5.0, pacer, scan.Clock(), deadline=time.monotonic() + 60, cancel_on_failure=True)
    with pytest.raises(scan.ProbeError, match="no query sent"):
        await asyncio.wait_for(pool.run(range(1, 6), work), timeout=2)
    assert ran[:2] == [1, 2] and 4 not in ran and 5 not in ran
