"""One job's sweep: pacing, the timeout, PTR lookups (spec §5.2, §5.6)."""

import asyncio
import ipaddress

import pytest

from conftest import ETH0, FakeArp, FakeClock, FakeProber, FakeResolver, job
from tlg_ipam_agent import guard, scan
from tlg_ipam_agent.config import parse_allowed
from tlg_ipam_agent.probe import NO_DNS_SERVER, ProbeError, no_query_sent
from tlg_ipam_agent.protocol import job_ref, parse_job


def planned(**overrides):
    raw = job(**overrides)
    parsed = parse_job(job_ref(raw), raw)
    return parsed, guard.plan(parsed, parse_allowed(None))


async def sweep(parsed, addresses, *, prober, resolver=None, arp=None, interfaces=ETH0, clock=None, max_pps=200):
    return await scan.run(
        parsed,
        addresses,
        prober=prober,
        resolver=resolver or FakeResolver(),
        arp_link=arp if arp is not None else FakeArp(),
        interfaces=interfaces,
        max_pps=max_pps,
        clock=clock or FakeClock(),
    )


@pytest.mark.parametrize("pps", [1, 50, 200])
async def test_sends_are_paced_at_max_pps(pps):
    clock = FakeClock()
    prober = FakeProber(clock=clock)
    parsed, addresses = planned()
    await sweep(parsed, addresses, prober=prober, clock=clock, max_pps=pps)
    times = prober.times
    assert len(times) == 254
    # no one-second window holds more than `pps` sends
    for i in range(len(times) - pps):
        assert times[i + pps] - times[i] >= 1.0 - 1e-9


async def test_in_flight_pings_are_bounded():
    clock = FakeClock()
    prober = FakeProber(clock=clock, delay=1.0)
    parsed, addresses = planned()
    await sweep(parsed, addresses, prober=prober, clock=clock, max_pps=1000)
    assert prober.max_in_flight <= scan.PING_CONCURRENCY


async def test_timeout_stops_the_sweep_and_reports_incomplete():
    clock = FakeClock()
    prober = FakeProber(alive=["10.0.0.1", "10.0.0.2", "10.0.0.200"], clock=clock)
    resolver = FakeResolver({"10.0.0.1": "a.home.arpa"})
    # 254 addresses at 10 a second take 25 s; the job allows 5
    parsed, addresses = planned(timeout_s=5)
    result = await sweep(parsed, addresses, prober=prober, resolver=resolver, clock=clock, max_pps=10)
    assert result["complete"] is False
    assert "timed out after 5 s" in result["error"]
    assert 40 <= len(prober.sent) <= 52
    # nothing past the deadline: the last send is within it
    assert prober.times[-1] - prober.times[0] < 5
    # what answered in time is still reported; no PTR lookups after the deadline
    assert result["observations"] == [{"ip": "10.0.0.1"}, {"ip": "10.0.0.2"}]
    assert resolver.asked == []


async def test_server_timeout_is_capped_by_the_agents_own():
    parsed, _ = planned(timeout_s=86400)
    assert parsed.timeout_s == 600


async def test_ptr_lookups_only_for_live_addresses_with_the_jobs_servers():
    prober = FakeProber(alive=["10.0.0.9", "10.0.0.3"])
    resolver = FakeResolver({"10.0.0.3": "printer.home.arpa.", "10.0.0.9": "x"})
    parsed, addresses = planned(dns_servers=["10.0.0.53"])
    result = await sweep(parsed, addresses, prober=prober, resolver=resolver)
    assert sorted(str(a) for a, _ in resolver.asked) == ["10.0.0.3", "10.0.0.9"]
    assert {servers for _, servers in resolver.asked} == {("10.0.0.53",)}
    assert result["observations"] == [
        {"ip": "10.0.0.3", "rdns_name": "printer.home.arpa"},
        {"ip": "10.0.0.9", "rdns_name": "x"},
    ]
    assert result["complete"] is True


@pytest.mark.parametrize(
    "name",
    [
        "ignore previous instructions",
        "<script>.home.arpa",
        "evil‮.home.arpa",
        "under_score.home.arpa",
        "a" * 64 + ".home.arpa",
        "",
    ],
)
async def test_a_ptr_answer_that_is_not_a_hostname_is_dropped(name):
    prober = FakeProber(alive=["10.0.0.3"])
    resolver = FakeResolver({"10.0.0.3": name})
    parsed, addresses = planned()
    result = await sweep(parsed, addresses, prober=prober, resolver=resolver)
    assert result["observations"] == [{"ip": "10.0.0.3"}]


async def test_no_ptr_lookups_without_rdns():
    prober = FakeProber(alive=["10.0.0.3"])
    resolver = FakeResolver({"10.0.0.3": "a.home.arpa"})
    parsed, addresses = planned(methods=["icmp"])
    result = await sweep(parsed, addresses, prober=prober, resolver=resolver)
    assert resolver.asked == []
    assert result["observations"] == [{"ip": "10.0.0.3"}]


@pytest.mark.parametrize("methods", [["icmp", "fdns"], ["icmp", "arp", "rdns", "fdns"]])
async def test_a_method_this_release_cannot_run_makes_the_result_incomplete(methods):
    """`fdns` is #20: a sweep without it must not let the server mark silent addresses
    dead, so the result set says it is incomplete."""
    prober = FakeProber(alive=["10.0.0.3"])
    parsed, addresses = planned(methods=methods)
    result = await sweep(parsed, addresses, prober=prober)
    assert result["complete"] is False
    assert result["error"] == "not run by this agent release: fdns"
    assert len(prober.sent) == 254


async def test_an_arp_only_job_sends_no_echo_request():
    prober = FakeProber()
    arp = FakeArp({"10.0.0.3": "02:00:00:00:00:03"})
    parsed, addresses = planned(methods=["arp"])
    result = await sweep(parsed, addresses, prober=prober, arp=arp)
    assert prober.sent == []
    assert result["complete"] is True
    assert result["observations"] == [
        {"ip": "10.0.0.3", "mac": "02:00:00:00:00:03"},
        {"ip": "10.0.0.5", "mac": "02:00:00:00:00:05"},
    ]


async def test_a_prober_that_cannot_work_stops_the_sweep():
    class NoRawSockets(FakeProber):
        async def ping(self, address, timeout):
            self.sent.append(address)
            raise ProbeError("ICMP needs CAP_NET_RAW")

    prober = NoRawSockets()
    parsed, addresses = planned()
    result = await sweep(parsed, addresses, prober=prober)
    assert result["complete"] is False
    assert "CAP_NET_RAW" in result["error"]
    # it stops at once rather than failing 254 times
    assert len(prober.sent) <= 2


async def test_result_set_shape(clock):
    prober = FakeProber(alive=["10.0.0.3"], clock=clock)
    parsed, addresses = planned()
    result = await sweep(parsed, addresses, prober=prober, clock=clock)
    assert set(result) == {
        "job_id", "subnet_id", "started_at", "finished_at", "complete", "observations", "fdns", "error"
    }
    assert result["started_at"] <= result["finished_at"]


def test_refusal_result_set(clock):
    raw = job()
    result = scan.refusal(job_ref(raw), "refused: why\nnot", clock)
    assert result["complete"] is False
    assert result["observations"] == [] and result["fdns"] == []
    assert result["error"] == "refused: why not"


def test_network_and_broadcast_are_skipped():
    _, addresses = planned(ranges=[{"start": "10.0.0.0", "end": "10.0.0.255"}])
    assert ipaddress.ip_address("10.0.0.0") not in addresses
    assert ipaddress.ip_address("10.0.0.255") not in addresses
    assert len(addresses) == 254


async def test_probes_that_run_past_the_deadline_make_the_result_incomplete():
    """Both sends start inside a 1 s job, but the probes take 0.4 s each: the job
    ends after its deadline, so it is not complete (Codex and Greptile on PR #70).

    The fake clock adds sleeps up, concurrent ones too: the first probe's 0.4 s and
    the pacer's 0.5 s put the second send at 0.9 s, and the second probe ends at 1.3 s.
    (With 0.75 s, the pacer, which looks at the clock again after it sleeps, would put
    the second send past the deadline, and the test would not reach its point.)"""
    clock = FakeClock()
    prober = FakeProber(alive=["10.0.0.1", "10.0.0.2"], clock=clock, delay=0.4)
    parsed, addresses = planned(ranges=[{"start": "10.0.0.1", "end": "10.0.0.2"}], methods=["icmp"], timeout_s=1)
    result = await sweep(parsed, addresses, prober=prober, clock=clock, max_pps=2)
    assert len(prober.sent) == 2
    assert result["complete"] is False
    assert "timed out after 1 s" in result["error"]


async def test_a_ptr_lookup_that_crosses_the_deadline_makes_the_result_incomplete():
    clock = FakeClock()
    prober = FakeProber(alive=["10.0.0.1"], clock=clock)
    resolver = FakeResolver({"10.0.0.1": "a.home.arpa"}, clock=clock, delay=5.0)
    parsed, addresses = planned(ranges=[{"start": "10.0.0.1", "end": "10.0.0.2"}], timeout_s=3)
    result = await sweep(parsed, addresses, prober=prober, resolver=resolver, clock=clock)
    assert len(resolver.asked) == 1
    assert result["complete"] is False
    assert "during the PTR lookups" in result["error"]


async def test_no_probe_is_given_longer_than_the_time_left():
    clock = FakeClock()
    prober = FakeProber(clock=clock)
    resolver = FakeResolver({"10.0.0.1": "a.home.arpa"}, clock=clock)
    # 10 sends a second over a 2 s job: the later sends have under a second left
    parsed, addresses = planned(ranges=[{"start": "10.0.0.1", "end": "10.0.0.30"}], timeout_s=2)
    await sweep(parsed, addresses, prober=prober, resolver=resolver, clock=clock, max_pps=10)
    start = prober.times[0]
    for sent_at, timeout in zip(prober.times, prober.timeouts, strict=True):
        assert 0 < timeout <= scan.PING_TIMEOUT_S
        assert sent_at + timeout <= start + 2 + 1e-9
    assert min(prober.timeouts) < scan.PING_TIMEOUT_S


async def test_a_scan_well_inside_its_timeout_is_complete():
    clock = FakeClock()
    prober = FakeProber(alive=["10.0.0.1"], clock=clock, delay=0.5)
    resolver = FakeResolver({"10.0.0.1": "a.home.arpa"}, clock=clock, delay=0.5)
    parsed, addresses = planned()
    result = await sweep(parsed, addresses, prober=prober, resolver=resolver, clock=clock)
    assert result["complete"] is True and result["error"] is None
    assert set(resolver.timeouts) == {scan.RDNS_TIMEOUT_S}


@pytest.mark.parametrize("pps", [1, 2])
async def test_lookups_waiting_for_a_slot_do_not_use_up_their_own_timeout(pps):
    """At a low MAX_PPS most lookups queue for a packet slot longer than their 2 s
    timeout. That wait is not theirs to lose: every live address is still asked, and
    the result is complete (Codex on ce60198, PR #70)."""
    clock = FakeClock()
    live = [f"10.0.0.{i}" for i in range(1, 9)]
    prober = FakeProber(alive=live, clock=clock)
    resolver = FakeResolver({a: f"h{a.split('.')[-1]}.home.arpa" for a in live}, clock=clock)
    parsed, addresses = planned(ranges=[{"start": "10.0.0.1", "end": "10.0.0.8"}])
    result = await sweep(parsed, addresses, prober=prober, resolver=resolver, clock=clock, max_pps=pps)
    assert sorted(str(a) for a, _ in resolver.asked) == sorted(live)
    assert all("rdns_name" in o for o in result["observations"])
    assert result["complete"] is True
    # and still paced: the queries are a slot apart
    gaps = [b - a for a, b in zip(resolver.times, resolver.times[1:], strict=False)]
    assert all(g >= 1 / pps - 1e-9 for g in gaps)


async def test_a_lookup_still_ends_at_the_jobs_deadline():
    clock = FakeClock()
    live = [f"10.0.0.{i}" for i in range(1, 9)]
    prober = FakeProber(alive=live, clock=clock)
    resolver = FakeResolver({a: "x.home.arpa" for a in live}, clock=clock)
    # 8 pings and 8 queries at 1 a second need 16 s; the job has 12
    parsed, addresses = planned(ranges=[{"start": "10.0.0.1", "end": "10.0.0.8"}], timeout_s=12)
    result = await sweep(parsed, addresses, prober=prober, resolver=resolver, clock=clock, max_pps=1)
    assert len(resolver.asked) < 8
    assert all(t <= 1000 + 12 for t in resolver.times)
    assert result["complete"] is False


# --- no server to ask is not a completed lookup (#78) ---


async def test_no_dns_server_anywhere_makes_the_scan_incomplete_and_asks_nothing():
    prober = FakeProber(alive=["10.0.0.3", "10.0.0.9"])
    resolver = FakeResolver({"10.0.0.3": "printer.home.arpa"}, host=())
    parsed, addresses = planned(dns_servers=[])
    result = await sweep(parsed, addresses, prober=prober, resolver=resolver)
    assert result["complete"] is False
    assert result["error"] == NO_DNS_SERVER
    # the live addresses are still reported, without names
    assert result["observations"] == [{"ip": "10.0.0.3"}, {"ip": "10.0.0.9"}]
    assert resolver.asked == []


async def test_the_jobs_own_server_needs_none_on_the_host():
    prober = FakeProber(alive=["10.0.0.3"])
    resolver = FakeResolver({"10.0.0.3": "printer.home.arpa"}, host=())
    parsed, addresses = planned(dns_servers=["10.0.0.53"])
    result = await sweep(parsed, addresses, prober=prober, resolver=resolver)
    assert result["complete"] is True and result["error"] is None
    assert resolver.asked == [(ipaddress.ip_address("10.0.0.3"), ("10.0.0.53",))]


async def test_the_hosts_servers_are_given_to_every_lookup():
    prober = FakeProber(alive=["10.0.0.3", "10.0.0.9"])
    resolver = FakeResolver(host=("192.0.2.1", "192.0.2.2"))
    parsed, addresses = planned(dns_servers=[])
    result = await sweep(parsed, addresses, prober=prober, resolver=resolver)
    assert result["complete"] is True
    assert {servers for _, servers in resolver.asked} == {("192.0.2.1", "192.0.2.2")}


async def test_with_nothing_alive_there_is_nothing_to_look_up():
    """No live address, no lookup to make: a host with no resolver does not make the
    scan incomplete, and an `icmp`-only job never asks for servers at all."""
    resolver = FakeResolver(host=())
    parsed, addresses = planned(dns_servers=[])
    result = await sweep(parsed, addresses, prober=FakeProber(), resolver=resolver)
    assert result["complete"] is True and result["error"] is None
    parsed, addresses = planned(dns_servers=[], methods=["icmp"])
    result = await sweep(parsed, addresses, prober=FakeProber(alive=["10.0.0.3"]), resolver=resolver)
    assert result["complete"] is True and result["error"] is None


class UnsendableResolver(FakeResolver):
    """Its lookup for `unsendable` could send the query to no server: it fails once a
    lookup for an address in `slow` has started. A `slow` lookup takes 0.3 s of real
    time (so a pool that lets it finish finishes it, and the test fails rather than
    hangs); any other returns at once."""

    def __init__(self, names, unsendable, slow):
        super().__init__(names)
        self.unsendable = ipaddress.ip_address(unsendable)
        self.slow = {ipaddress.ip_address(a) for a in slow}
        self.slow_started = asyncio.Event()
        self.finished: list = []
        self.cancelled: list = []

    async def ptr(self, address, servers, slot):
        await slot()
        try:
            if address == self.unsendable:
                await self.slow_started.wait()
                raise ProbeError(no_query_sent("OSError"))
            if address in self.slow:
                self.slow_started.set()
                await asyncio.sleep(0.3)
        except asyncio.CancelledError:
            self.cancelled.append(address)
            raise
        self.finished.append(address)
        return self.names.get(address)


async def test_a_lookup_that_could_send_nothing_cancels_the_rest_and_the_scan_is_incomplete():
    """10.0.0.1 is looked up before 10.0.0.2 fails; the lookups still in flight then
    are cancelled, none finishes after the failure, and the scan is incomplete with
    the failure's error (call 7)."""
    live = ["10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4"]
    resolver = UnsendableResolver({a: f"h{a[-1]}.home.arpa" for a in live}, "10.0.0.2", slow=live[2:])
    parsed, addresses = planned()
    result = await sweep(parsed, addresses, prober=FakeProber(alive=live), resolver=resolver)
    assert result["complete"] is False
    assert result["error"] == no_query_sent("OSError")
    assert [o["ip"] for o in result["observations"]] == live
    assert result["observations"][0] == {"ip": "10.0.0.1", "rdns_name": "h1.home.arpa"}
    assert all("rdns_name" not in o for o in result["observations"][1:])
    assert resolver.finished == [ipaddress.ip_address("10.0.0.1")]
    assert ipaddress.ip_address("10.0.0.3") in resolver.cancelled


# --- #99: the two pool contracts the review's mutations showed unguarded ---


async def test_a_ping_still_in_flight_when_the_prober_fails_still_answers():
    """`cancel_on_failure` is the PTR lookups' alone. 10.0.0.1's ping is held until
    10.0.0.2's has failed, then answers: the ICMP sweep lets it, and reports it."""
    failed = asyncio.Event()

    class Held:
        async def ping(self, address, timeout):
            if address == ipaddress.ip_address("10.0.0.2"):
                failed.set()
                raise ProbeError("ICMP needs CAP_NET_RAW")
            await failed.wait()
            await asyncio.sleep(0.02)  # the pool has seen the failure by now
            return True

    parsed, addresses = planned(methods=["icmp"], ranges=[{"start": "10.0.0.1", "end": "10.0.0.2"}])
    result = await sweep(parsed, addresses, prober=Held(), max_pps=1000)
    assert result["observations"] == [{"ip": "10.0.0.1"}]
    assert result["complete"] is False and result["error"] == "ICMP needs CAP_NET_RAW"


async def test_a_later_lookups_failure_is_reported_ahead_of_an_earlier_ones_cancellation():
    """10.0.0.1's lookup is still running when 10.0.0.2's could send its query to no
    server, so the first is cancelled, and its CancelledError comes first among the
    pool's results. The pool raises the failure, not the cancellation: scan.run makes
    its result set. A CancelledError escaping instead would end the agent's worker."""
    started = asyncio.Event()

    class Resolver(FakeResolver):
        async def ptr(self, address, servers, slot):
            await slot()
            if address == ipaddress.ip_address("10.0.0.2"):
                await started.wait()
                raise ProbeError(no_query_sent("OSError"))
            started.set()
            await asyncio.sleep(5)  # cancelled long before
            return "h1.home.arpa"

    live = ["10.0.0.1", "10.0.0.2"]
    parsed, addresses = planned(ranges=[{"start": "10.0.0.1", "end": "10.0.0.2"}])
    outcome = (await asyncio.gather(
        sweep(parsed, addresses, prober=FakeProber(alive=live), resolver=Resolver()),
        return_exceptions=True,
    ))[0]
    assert isinstance(outcome, dict), f"scan.run raised {outcome!r} in place of a result set"
    assert outcome["complete"] is False and outcome["error"] == no_query_sent("OSError")
    assert outcome["observations"] == [{"ip": a} for a in live]


async def test_the_fake_resolver_keeps_the_real_ones_rules():
    """`FakeResolver` filters and cuts as `probe.usable_servers` does, and refuses an
    empty list as `DnsResolver.ptr` does, without taking a slot."""
    fake = FakeResolver(host=("https://dns.example.com/dns-query", "192.0.2.1", "192.0.2.2", "192.0.2.3", "192.0.2.4"))
    assert await fake.servers(()) == ("192.0.2.1", "192.0.2.2", "192.0.2.3")
    assert await fake.servers(("not-an-address", "10.0.0.53")) == ("10.0.0.53",)
    taken = []

    async def slot():
        taken.append(1)
        return 2.0

    with pytest.raises(ProbeError) as raised:
        await fake.ptr(ipaddress.ip_address("10.0.0.9"), (), slot)
    assert str(raised.value) == NO_DNS_SERVER
    assert taken == []
