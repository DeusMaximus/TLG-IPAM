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
    """A pacer whose every slot is overdue, so its grants come without a sleep: the
    pool still runs each probe's first step, where the echo request is handed to the
    kernel, before it asks for the next slot. Without that, the probes started while
    the pacer did not sleep would all send together once the pool next waited."""

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


# --- 3. a sender that is always late still lets the rest of the agent run ---


class SlowLink(FakeArp):
    """An ARP link whose send never waits and takes `cost` seconds of the fake clock,
    so at MAX_PPS=1000 every slot is overdue before it is asked for (the review of PR
    #84, round 2: a 2 ms send and `Pacer(1000)`). `first_receive` is how many requests
    had gone when the listener first read the link."""

    def __init__(self, clock, cost=0.002, on_send=None):
        super().__init__(clock=clock)
        self.cost = cost
        self.on_send = on_send
        self.first_receive: int | None = None

    async def send(self, frame: bytes) -> None:
        await super().send(frame)
        self.clock.t += self.cost
        if self.on_send is not None:
            self.on_send(len(self.requests))

    async def receive(self):
        if self.first_receive is None:
            self.first_receive = len(self.requests)
        return await super().receive()


async def test_a_ready_task_runs_within_a_batch_of_late_sends():
    """Every slot overdue and every send synchronous: before, a whole round of 253
    requests went without the event loop turning, and the listener first read the
    link after all of them. Now the pacer lets every other task run after
    PACE_YIELD_EVERY grants in a row without a sleep: a task that is ready, the
    listener among them, waits for at most that many sends."""
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
    # the bound the spec states, written out: the constant itself could be changed
    assert scan.PACE_YIELD_EVERY == 64
    assert max(b - a for a, b in zip(seen, seen[1:])) == 64
    assert link.first_receive is not None and link.first_receive <= 64


async def test_a_timer_that_falls_due_runs_within_three_batches_of_late_sends():
    """A timer that falls due while the sends go on (a check-in, the job's deadline,
    a cancellation: anything a timer or a readable socket wakes) is seen by the loop
    only once the batch it fell due in ends, and the task it wakes runs a turn of the
    loop after that: fewer than 3 x PACE_YIELD_EVERY sends after it fell due."""
    clock = FakeClock()
    woke = asyncio.Event()
    due_at: list[int] = []

    def on_send(sent):
        if sent == 10:
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
    assert due_at == [10]
    assert ran_at[0] - due_at[0] < 3 * 64
