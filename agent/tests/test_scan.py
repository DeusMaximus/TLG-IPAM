"""One job's sweep: pacing, the timeout, PTR lookups (spec §5.2, §5.6)."""

import asyncio
import builtins
import ipaddress

import icmplib
import pytest
from app import agent_protocol as server

from conftest import ETH0, FakeArp, FakeClock, FakeProber, FakeResolver, job
from tlg_ipam_agent import guard, probe, scan
from tlg_ipam_agent.config import parse_allowed, parse_allowed_dns
from tlg_ipam_agent.probe import NO_DNS_SERVER, IcmpProber, ProbeError, UnreadReplies, no_query_sent
from tlg_ipam_agent.protocol import (
    INTERFACE_NAME_MAX,
    JOB_TIMEOUT_S,
    OBSERVATIONS_MAX,
    PROTOCOL_VERSION,
    SCAN_ERROR_MAX,
    job_ref,
    parse_job,
    scan_error,
)


def planned(**overrides):
    raw = job(**overrides)
    parsed = parse_job(job_ref(raw), raw)
    return parsed, guard.plan(parsed, parse_allowed(None))


async def sweep(
    parsed, addresses, *, prober, resolver=None, arp=None, interfaces=ETH0, clock=None, max_pps=200, allowed_dns=None
):
    return await scan.run(
        parsed,
        addresses,
        prober=prober,
        resolver=resolver or FakeResolver(),
        arp_link=arp if arp is not None else FakeArp(),
        interfaces=interfaces,
        max_pps=max_pps,
        clock=clock or FakeClock(),
        allowed_dns=allowed_dns,
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
    assert result["complete"] is False and result["liveness_complete"] is False
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
    # an answer with a name is not "no name": the record carries neither, and the
    # server keeps the stored name (#100)
    assert result["observations"] == [{"ip": "10.0.0.3"}]
    assert result["complete"] is True and result["error"] is None


async def test_each_record_says_what_its_own_lookup_found():
    """#100, option 4: a name; `rdns_no_name` for an answer that there is none; neither
    for a lookup no server gave a usable answer to, which leaves the scan complete
    and says so in its error, by counts only. The liveness sweep is whole."""
    prober = FakeProber(alive=["10.0.0.3", "10.0.0.4", "10.0.0.6", "10.0.0.7"])
    resolver = FakeResolver({"10.0.0.3": "a.home.arpa"}, unanswered=["10.0.0.6", "10.0.0.7"])
    parsed, addresses = planned()
    result = await sweep(parsed, addresses, prober=prober, resolver=resolver)
    assert result["observations"] == [
        {"ip": "10.0.0.3", "rdns_name": "a.home.arpa"},
        {"ip": "10.0.0.4", "rdns_no_name": True},
        {"ip": "10.0.0.6"},
        {"ip": "10.0.0.7"},
    ]
    assert result["complete"] is True and result["liveness_complete"] is True
    assert result["error"] == scan.no_answer(2, 4)
    assert len(resolver.asked) == 4


async def test_every_lookup_unanswered_is_still_a_complete_scan():
    """A resolver that answers nothing: every record carries neither, the scan is
    complete, so the server marks the silent addresses dead and keeps every name."""
    live = ["10.0.0.3", "10.0.0.4"]
    prober = FakeProber(alive=live)
    resolver = FakeResolver(unanswered=live)
    parsed, addresses = planned(methods=["icmp", "rdns"])
    result = await sweep(parsed, addresses, prober=prober, resolver=resolver)
    assert result["observations"] == [{"ip": ip} for ip in live]
    assert result["complete"] is True and result["liveness_complete"] is True
    assert result["error"] == scan.no_answer(2, 2)


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
    # `fdns` says nothing about liveness: the sweeps it ran are whole (#100)
    assert result["liveness_complete"] is True
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
    assert result["complete"] is False and result["liveness_complete"] is False
    assert "CAP_NET_RAW" in result["error"]
    # it stops at once rather than failing 254 times
    assert len(prober.sent) <= 2


async def test_result_set_shape(clock):
    prober = FakeProber(alive=["10.0.0.3"], clock=clock)
    parsed, addresses = planned()
    result = await sweep(parsed, addresses, prober=prober, clock=clock)
    assert set(result) == {
        "protocol", "job_id", "subnet_id", "started_at", "finished_at", "complete",
        "liveness_complete", "observations", "fdns", "error",
    }
    assert result["protocol"] == PROTOCOL_VERSION
    assert result["started_at"] <= result["finished_at"]


def test_refusal_result_set(clock):
    raw = job()
    result = scan.refusal(job_ref(raw), "refused: why\nnot", clock)
    assert result["protocol"] == PROTOCOL_VERSION
    assert result["complete"] is False and result["liveness_complete"] is False
    assert result["observations"] == [] and result["fdns"] == []
    assert result["error"] == "refused: why not"


def test_network_and_broadcast_are_skipped():
    _, addresses = planned(ranges=[{"start": "10.0.0.0", "end": "10.0.0.255"}])
    assert ipaddress.ip_address("10.0.0.0") not in addresses
    assert ipaddress.ip_address("10.0.0.255") not in addresses
    assert len(addresses) == 254


class SecondProbeSlow(FakeProber):
    """The first probe takes 0.1 s of the fake clock, the second 0.9 s."""

    async def ping(self, address, timeout):
        self.delay = 0.9 if self.sent else 0.1
        return await super().ping(address, timeout)


async def test_probes_that_run_past_the_deadline_make_the_result_incomplete():
    """Both sends start inside a 1 s job, but the second probe takes 0.9 s: the job
    ends after its deadline, so it is not complete (Codex and Greptile on PR #70).

    The fake clock adds sleeps up, concurrent ones too, in the order the tasks run, so
    where the second send lands depends on whether the first probe's sleep or the
    pacer's runs first: 0.5 s or 0.6 s into the job here, and the second probe ends
    at 1.4 s or 1.5 s either way. (With both probes taking the same time, no one delay
    keeps the second send inside the deadline and the end past it in both orders.)"""
    clock = FakeClock()
    prober = SecondProbeSlow(alive=["10.0.0.1", "10.0.0.2"], clock=clock)
    parsed, addresses = planned(ranges=[{"start": "10.0.0.1", "end": "10.0.0.2"}], methods=["icmp"], timeout_s=1)
    result = await sweep(parsed, addresses, prober=prober, clock=clock, max_pps=2)
    assert len(prober.sent) == 2
    assert result["complete"] is False and result["liveness_complete"] is False
    assert "timed out after 1 s" in result["error"]


async def test_a_ptr_lookup_that_crosses_the_deadline_makes_the_result_incomplete():
    clock = FakeClock()
    prober = FakeProber(alive=["10.0.0.1"], clock=clock)
    resolver = FakeResolver({"10.0.0.1": "a.home.arpa"}, clock=clock, delay=5.0)
    parsed, addresses = planned(ranges=[{"start": "10.0.0.1", "end": "10.0.0.2"}], timeout_s=3)
    result = await sweep(parsed, addresses, prober=prober, resolver=resolver, clock=clock)
    assert len(resolver.asked) == 1
    assert result["complete"] is False
    # the ICMP sweep ended before the deadline: the dead may be marked (#100)
    assert result["liveness_complete"] is True
    assert result["error"] == "timed out after 3 s during the PTR lookups"


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
    assert result["complete"] is False and result["liveness_complete"] is True
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
    assert result["complete"] is False and result["liveness_complete"] is True
    assert result["error"] == no_query_sent("OSError")
    assert [o["ip"] for o in result["observations"]] == live
    assert result["observations"][0] == {"ip": "10.0.0.1", "rdns_name": "h1.home.arpa"}
    # a lookup that failed or was cancelled found nothing either way: neither field
    assert result["observations"][1:] == [{"ip": ip} for ip in live[1:]]
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


# --- ALLOWED_DNS_SERVERS: no server outside it is asked (#93) ---


def dns_nets(value):
    return parse_allowed_dns(value)


async def test_unset_the_jobs_servers_are_asked_as_before():
    """Acceptance 1: with the setting unset every server the job names is asked, in
    its order, and the result set is what it was before #93."""
    live = ["10.0.0.3", "10.0.0.9"]
    parsed, addresses = planned(dns_servers=["192.0.2.1", "198.51.100.1", "10.0.0.53"])
    resolver = FakeResolver({"10.0.0.3": "printer.home.arpa"})
    result = await sweep(parsed, addresses, prober=FakeProber(alive=live), resolver=resolver)
    assert {servers for _, servers in resolver.asked} == {("192.0.2.1", "198.51.100.1", "10.0.0.53")}
    assert result["error"] is None and result["complete"] is True
    assert result["observations"] == [{"ip": "10.0.0.3", "rdns_name": "printer.home.arpa"},
                                      {"ip": "10.0.0.9", "rdns_no_name": True}]


async def test_servers_outside_the_list_are_not_asked_and_the_error_says_how_many():
    """The rest are asked, in the job's order, and their answers decide the records as
    they would were the others silent: the scan is complete, and the error is the
    notice, counts only."""
    live = ["10.0.0.3", "10.0.0.9"]
    parsed, addresses = planned(dns_servers=["192.0.2.1", "10.0.0.53", "198.51.100.1"])
    resolver = FakeResolver({"10.0.0.3": "printer.home.arpa"})
    result = await sweep(
        parsed, addresses, prober=FakeProber(alive=live), resolver=resolver, allowed_dns=dns_nets("10.0.0.0/24")
    )
    assert {servers for _, servers in resolver.asked} == {("10.0.0.53",)}
    assert result["error"] == scan.outside_allowed(2, 3) == (
        "rdns: 2 of the 3 DNS servers to ask are outside ALLOWED_DNS_SERVERS and were not asked"
    )
    assert "192.0.2" not in result["error"] and "198.51" not in result["error"]
    assert result["complete"] is True and result["liveness_complete"] is True
    assert result["observations"] == [{"ip": "10.0.0.3", "rdns_name": "printer.home.arpa"},
                                      {"ip": "10.0.0.9", "rdns_no_name": True}]


@pytest.mark.parametrize("named, said", [
    (["192.0.2.1"], "rdns not run: the 1 DNS server to ask is outside ALLOWED_DNS_SERVERS"),
    (["192.0.2.1", "198.51.100.1", "2001:db8::53"], "rdns not run: all 3 DNS servers to ask are outside ALLOWED_DNS_SERVERS"),
])
async def test_no_server_left_makes_no_lookup_and_clears_no_name(named, said):
    """Acceptance 3: when the list leaves no server, no lookup is made and no query
    sent. The error names the cause; the result set is incomplete and no record says
    there is no name, so the server keeps every stored name (spec §5.7); the liveness
    sweep completed, so the dead are still marked. The host's resolvers are not asked
    in the job's servers' place."""
    live = ["10.0.0.3", "10.0.0.9"]
    parsed, addresses = planned(dns_servers=named)
    resolver = FakeResolver({"10.0.0.3": "printer.home.arpa"}, host=("10.0.0.53",))
    result = await sweep(
        parsed, addresses, prober=FakeProber(alive=live), resolver=resolver, allowed_dns=dns_nets("10.0.0.0/8")
    )
    assert resolver.asked == []
    assert result["error"] == said
    assert result["complete"] is False and result["liveness_complete"] is True
    assert result["observations"] == [{"ip": "10.0.0.3"}, {"ip": "10.0.0.9"}]
    assert not any("rdns_no_name" in o or "rdns_name" in o for o in result["observations"])


async def test_the_list_holds_the_hosts_resolvers_too():
    """A job that names no server asks the host's resolvers, and the list holds them as
    it holds the job's: no DNS packet goes outside it, whoever chose the server."""
    live = ["10.0.0.3"]
    parsed, addresses = planned(dns_servers=[])
    resolver = FakeResolver({"10.0.0.3": "printer.home.arpa"}, host=("192.0.2.53", "10.0.0.53"))
    result = await sweep(
        parsed, addresses, prober=FakeProber(alive=live), resolver=resolver, allowed_dns=dns_nets("10.0.0.53")
    )
    assert {servers for _, servers in resolver.asked} == {("10.0.0.53",)}
    assert result["error"] == scan.outside_allowed(1, 2)
    assert result["complete"] is True
    alone = FakeResolver(host=("192.0.2.53",))
    result = await sweep(
        parsed, addresses, prober=FakeProber(alive=live), resolver=alone, allowed_dns=dns_nets("10.0.0.53")
    )
    assert alone.asked == [] and result["error"] == scan.outside_allowed(1, 1) and result["complete"] is False


async def test_with_no_server_at_all_the_error_is_still_no_dns_server():
    """The list takes nothing from an empty choice: that is #78's error, not #93's."""
    parsed, addresses = planned(dns_servers=[])
    resolver = FakeResolver(host=())
    result = await sweep(
        parsed, addresses, prober=FakeProber(alive=["10.0.0.3"]), resolver=resolver, allowed_dns=dns_nets("10.0.0.53")
    )
    assert result["error"] == NO_DNS_SERVER and result["complete"] is False


async def test_a_job_with_no_live_address_needs_no_allowed_server():
    parsed, addresses = planned(dns_servers=["192.0.2.1"])
    result = await sweep(parsed, addresses, prober=FakeProber(alive=[]), allowed_dns=dns_nets("10.0.0.53"))
    assert result["error"] is None and result["complete"] is True


async def test_the_notice_comes_before_what_the_lookups_met():
    """The notice is said where the servers are chosen, ahead of the lookups' own
    errors, and the refusal counts of the lookups the rest of the servers answered
    follow it."""
    live = ["10.0.0.3", "10.0.0.4"]
    parsed, addresses = planned(dns_servers=["192.0.2.1", "10.0.0.53"])
    resolver = FakeResolver(unread={"10.0.0.3": BOTH}, unanswered=["10.0.0.4"])
    result = await sweep(
        parsed, addresses, prober=FakeProber(alive=live), resolver=resolver, allowed_dns=dns_nets("10.0.0.53")
    )
    assert result["error"] == f"{scan.outside_allowed(1, 2)}; {scan.no_answer(2, 2, 1, 1)}"
    assert result["complete"] is True


# --- the job's error counts the replies the agent refused unread (#164) ---


TOO_LARGE = UnreadReplies(too_large=True, too_many_records=False)
TOO_MANY = UnreadReplies(too_large=False, too_many_records=True)
BOTH = UnreadReplies(too_large=True, too_many_records=True)


async def test_a_job_says_how_many_lookups_met_a_reply_refused_for_size_and_for_its_counts():
    """Acceptance 1: two lookups' only reply was over the size limit, another's over
    the record budget (unequal counts, so each count is held to its own words), a
    fourth had no usable answer for another reason and a fifth was answered. The error
    counts the four without a usable answer, then the refusals by limit, in lookups,
    by count only, and the scan stays complete; the records are what they were, so the
    server keeps those addresses' names."""
    live = ["10.0.0.3", "10.0.0.4", "10.0.0.5", "10.0.0.6", "10.0.0.7"]
    prober = FakeProber(alive=live)
    resolver = FakeResolver(
        {"10.0.0.7": "seven.home.arpa"},
        unanswered=["10.0.0.6"],
        unread={"10.0.0.3": TOO_LARGE, "10.0.0.4": TOO_MANY, "10.0.0.5": TOO_LARGE},
    )
    parsed, addresses = planned()
    result = await sweep(parsed, addresses, prober=prober, resolver=resolver)
    assert result["error"] == (
        "rdns: no DNS server gave a usable answer for 4 of 5 addresses, names kept; the "
        "agent refused a reply over 1232 bytes in 2 lookups, over 1 question or 32 records "
        "in 1 lookup"
    )
    assert result["error"] == scan.no_answer(4, 5, 2, 1)
    assert "10.0." not in result["error"] and "home.arpa" not in result["error"]
    assert result["observations"] == [
        {"ip": "10.0.0.3"}, {"ip": "10.0.0.4"}, {"ip": "10.0.0.5"}, {"ip": "10.0.0.6"},
        {"ip": "10.0.0.7", "rdns_name": "seven.home.arpa"},
    ]
    assert result["complete"] is True and result["liveness_complete"] is True


async def test_a_lookup_whose_servers_sent_one_refusal_of_each_is_in_both_counts():
    live = ["10.0.0.3", "10.0.0.4"]
    resolver = FakeResolver(unread={"10.0.0.3": BOTH, "10.0.0.4": TOO_MANY})
    parsed, addresses = planned()
    result = await sweep(parsed, addresses, prober=FakeProber(alive=live), resolver=resolver)
    assert result["error"] == scan.no_answer(2, 2, 1, 2)
    assert result["error"].endswith("over 1232 bytes in 1 lookup, over 1 question or 32 records in 2 lookups")


@pytest.mark.parametrize(
    "counts, said",
    [
        ((3, 0), "the agent refused a reply over 1232 bytes in 3 lookups"),
        ((0, 1), "the agent refused a reply over 1 question or 32 records in 1 lookup"),
        ((1, 2), "the agent refused a reply over 1232 bytes in 1 lookup, over 1 question or 32 records in 2 lookups"),
    ],
)
def test_only_the_limits_that_refused_a_reply_are_named_and_each_in_lookups(counts, said):
    """Round 1 of the briefed review, finding 3: a count of zero is left out, so it
    never reads as a second problem; each count shown names its unit, lookups, in the
    singular for one; and the header's limits read as more than 1 question or more than
    32 records."""
    assert scan.refused_unread(*counts) == said
    assert scan.no_answer(3, 3, *counts).endswith("addresses, names kept; " + said)
    assert " 0 " not in scan.no_answer(3, 3, *counts) and "for 0" not in scan.no_answer(3, 3, *counts)


@pytest.mark.parametrize("unanswered, asked", [(1, 1), (2, 4), (4096, 4096)])
def test_with_no_refused_reply_the_error_is_what_it_was(unanswered, asked):
    """Acceptance 2: the text before #164, word for word, from the counts alone and
    with both refusal counts zero."""
    before = (
        f"rdns: no DNS server gave a usable answer for {unanswered} of {asked} addresses; "
        "their stored names are kept"
    )
    assert scan.no_answer(unanswered, asked) == before
    assert scan.no_answer(unanswered, asked, 0, 0) == before


async def test_a_job_with_no_refused_reply_reports_what_it_did():
    """Acceptance 2, through a job: lookups without a usable answer that met no
    refused reply give the text before #164; a job whose every lookup was answered
    has no error, whatever a server's reply before the answer was (the resolver returns
    the bare answer when no reply before it was refused, `test_probe.py`'s
    `test_an_answer_with_no_refusal_before_it_is_the_bare_answer`; one after a refusal
    is #170's notice, `test_a_job_whose_every_first_reply_was_refused_and_every_lookup_answered_says_so`)."""
    parsed, addresses = planned()
    result = await sweep(
        parsed, addresses, prober=FakeProber(alive=["10.0.0.3", "10.0.0.4"]),
        resolver=FakeResolver(unanswered=["10.0.0.3"]),
    )
    assert result["error"] == "rdns: no DNS server gave a usable answer for 1 of 2 addresses; their stored names are kept"
    result = await sweep(parsed, addresses, prober=FakeProber(alive=["10.0.0.3"]), resolver=FakeResolver())
    assert result["error"] is None and result["complete"] is True


class DeadlineResolver(FakeResolver):
    """10.0.0.1's lookup returns `first` at once; 10.0.0.2's runs past the job's
    deadline and returns `cut`, as the real resolver does when its time runs out:
    None, or UnreadReplies when an attempt before that was refused."""

    def __init__(self, clock, first, cut):
        super().__init__(clock=clock)
        self.first, self.cut = first, cut

    async def ptr(self, address, servers, slot):
        left = await slot()
        if address == ipaddress.ip_address("10.0.0.1"):
            return self.first
        await self.clock.sleep(left + 1)
        return self.cut


@pytest.mark.parametrize(
    "cut, said",
    [
        (None, "the agent refused a reply over 1232 bytes in 1 lookup"),
        (TOO_MANY, "the agent refused a reply over 1232 bytes in 1 lookup, over 1 question or 32 records in 1 lookup"),
    ],
)
async def test_a_deadline_keeps_the_refusals_of_the_lookups_that_returned(cut, said):
    """Round 1 of the briefed review, finding 1: one lookup returns a refusal at once,
    the other runs past the deadline. The timeout's error stays, the scan is
    incomplete, and the refusals of the lookups that returned follow it; the count of
    lookups without a usable answer stays out, as before #164. A lookup cut off with
    nothing refused adds nothing; one whose earlier attempt was refused says so."""
    clock = FakeClock()
    parsed, addresses = planned(ranges=[{"start": "10.0.0.1", "end": "10.0.0.2"}], timeout_s=1)
    result = await sweep(
        parsed, addresses, prober=FakeProber(alive=["10.0.0.1", "10.0.0.2"]),
        resolver=DeadlineResolver(clock, TOO_LARGE, cut), clock=clock,
    )
    assert result["error"] == f"timed out after 1 s during the PTR lookups; rdns: before the timeout, {said}"
    assert result["error"] == "timed out after 1 s during the PTR lookups; " + scan.cut_short(1, 1 if cut else 0)
    assert result["complete"] is False and "usable answer" not in result["error"]


async def test_a_lookup_the_deadline_cut_off_is_not_counted_as_refused():
    """A lookup cut off with nothing refused, beside one that returned without a
    usable answer and nothing refused: the timeout's error alone, as before #164."""
    clock = FakeClock()
    parsed, addresses = planned(ranges=[{"start": "10.0.0.1", "end": "10.0.0.2"}], timeout_s=1)
    result = await sweep(
        parsed, addresses, prober=FakeProber(alive=["10.0.0.1", "10.0.0.2"]),
        resolver=DeadlineResolver(clock, None, None), clock=clock,
    )
    assert result["error"] == "timed out after 1 s during the PTR lookups"
    assert result["complete"] is False


async def test_refusals_are_counted_beside_a_lookup_that_could_send_nothing():
    """A lookup that sent its query to no server stops the lookups; those that had
    ended before it are still counted, refusals included, after its error."""
    live = ["10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4"]

    class Resolver(FakeResolver):
        async def ptr(self, address, servers, slot):
            if address == ipaddress.ip_address("10.0.0.4"):
                for _ in range(20):
                    await asyncio.sleep(0)  # the lookups before it end first
                raise ProbeError(no_query_sent("OSError"))
            return await super().ptr(address, servers, slot)

    resolver = Resolver(unread={"10.0.0.1": TOO_LARGE, "10.0.0.2": TOO_MANY}, unanswered=["10.0.0.3"])
    parsed, addresses = planned()
    result = await sweep(parsed, addresses, prober=FakeProber(alive=live), resolver=resolver)
    assert result["error"] == f"{no_query_sent('OSError')}; {scan.no_answer(3, 3, 1, 1)}"
    assert result["complete"] is False


def test_the_refusal_counts_are_stored_whole_or_not_at_all():
    """Round 1 of the briefed review, finding 2: across every length of the rest of an
    error, the part that carries the counts is kept whole while the whole fits the
    cap, and is replaced by its text without them (or, after a timeout, left out)
    once it would not; no count is ever stored cut short. Past the point where even
    that would not fit, what is cut is the rest's, as before #164."""
    full, plain = scan.no_answer(3, 3, 1, 2), scan.no_answer(3, 3)
    cut = scan.cut_short(1, 2)
    timeout = "timed out after 1 s during the PTR lookups"
    for length in range(1, SCAN_ERROR_MAX):
        head = "x" * length
        stored = scan.error_text([head, full], (1, plain))
        assert stored == (f"{head}; {full}" if length + 2 + len(full) <= SCAN_ERROR_MAX
                          else scan_error(f"{head}; {plain}"))
        assert server.scan_error(stored) == stored
        stored = scan.error_text([head, timeout, cut], (2, None))
        assert stored == (f"{head}; {timeout}; {cut}" if length + 4 + len(timeout) + len(cut) <= SCAN_ERROR_MAX
                          else scan_error(f"{head}; {timeout}"))
        assert "refused" not in stored or stored.endswith(("lookup", "lookups"))
    assert scan.error_text([]) is None


# the longest name an exception type has that the agent names in an error: the
# builtin OSError and ValueError subclasses (what a socket, the kernel and ipaddress
# raise), and icmplib's socket errors
LONGEST_EXCEPTION = max(
    [c for c in vars(builtins).values() if isinstance(c, type) and issubclass(c, (OSError, ValueError))]
    + [icmplib.SocketPermissionError, icmplib.SocketUnavailableError, ipaddress.AddressValueError],
    key=lambda c: len(c.__name__),
)
LONGEST_INTERFACE = "x" * INTERFACE_NAME_MAX
# the longest spelling of a network str() gives, and one of OBSERVATIONS_MAX addresses
LONGEST_NETWORK = "ffff:ffff:ffff:ffff:ffff:ffff:ffff:f000/116"


async def icmp_errors(monkeypatch) -> list[str]:
    """Every error the ICMP sweep can add, at its longest."""
    texts = []
    for error in (icmplib.SocketPermissionError(False), icmplib.SocketUnavailableError()):
        async def refused(*args, error=error, **kwargs):
            raise error

        monkeypatch.setattr(icmplib, "async_ping", refused)
        with pytest.raises(ProbeError) as raised:
            await IcmpProber().ping(ipaddress.ip_address("127.0.0.1"), 0.1)
        texts.append(str(raised.value))
    return texts + [f"timed out after {JOB_TIMEOUT_S} s during the ICMP sweep"]


async def arp_errors(monkeypatch) -> list[str]:
    """Every error the ARP sweep can add, at its longest: each failure of `PacketLink`,
    made by a stand-in for its socket (none is opened), with the longest interface
    name and exception name, then the sweep's own two."""
    texts = []

    def failing(error):
        def make(*args):
            raise error
        return make

    class Socket:
        def __init__(self, bind=None, send=None):
            self._bind, self._send = bind, send

        def setblocking(self, flag):
            pass

        def bind(self, address):
            if self._bind:
                raise self._bind

        def send(self, frame):
            raise self._send

        def close(self):
            pass

    # not every platform names it (macOS does not); the stand-in takes any value
    monkeypatch.setattr(probe.socket, "AF_PACKET", 17, raising=False)
    for make in (
        failing(PermissionError()),
        failing(LONGEST_EXCEPTION()),
        lambda *args: Socket(bind=LONGEST_EXCEPTION()),
    ):
        monkeypatch.setattr(probe.socket, "socket", make)
        with pytest.raises(ProbeError) as raised:
            probe.PacketLink(LONGEST_INTERFACE)
        texts.append(str(raised.value))
    for error in (BlockingIOError(), LONGEST_EXCEPTION()):
        monkeypatch.setattr(probe.socket, "socket", lambda *args, error=error: Socket(send=error))
        with pytest.raises(ProbeError) as raised:
            await probe.PacketLink(LONGEST_INTERFACE).send(b"")
        texts.append(str(raised.value))
    monkeypatch.setattr(probe.socket, "socket", lambda *args: Socket())
    link = probe.PacketLink(LONGEST_INTERFACE)
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "sock_recvfrom", failing(LONGEST_EXCEPTION()))
    with pytest.raises(ProbeError) as raised:
        await link.receive()
    texts.append(str(raised.value))
    monkeypatch.undo()
    return texts + [
        f"arp not run: no interface of this host with a MAC is on {LONGEST_NETWORK}",
        f"timed out after {JOB_TIMEOUT_S} s during the ARP sweep",
    ]


async def longest_job(
    monkeypatch, exception_name: str, cut_off: bool = False, later: bool = False, narrowed: bool = False
) -> tuple[dict, list[str], int]:
    """A job that carries the longest text of each of its steps at once: what it
    reported, the parts its error is made of, as `scan.run` joins them, and the
    count of lookups it counted. With `cut_off`, the PTR lookups end in the deadline's
    timeout instead of a lookup that could send nothing (finding 1's form). With
    `later`, every other lookup is answered by a later server after a refusal of each
    limit (#170), so both its notice and #164's counts carry four digits. With
    `narrowed`, two of the job's three DNS servers are outside ALLOWED_DNS_SERVERS
    (#93), the longest form of that notice."""
    icmp, arp = await icmp_errors(monkeypatch), await arp_errors(monkeypatch)
    longest_icmp, longest_arp = max(icmp, key=len), max(arp, key=len)
    assert longest_icmp.startswith("ICMP needs CAP_NET_RAW") and longest_arp.startswith("arp not run")
    raw = job(
        cidr=LONGEST_NETWORK,
        ranges=[{"start": "ffff:ffff:ffff:ffff:ffff:ffff:ffff:f000", "end": "ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff"}],
        methods=["icmp", "arp", "rdns", "fdns"],
        dns_servers=["192.0.2.1", "127.0.0.1", "192.0.2.2"] if narrowed else ["127.0.0.1"],
        timeout_s=JOB_TIMEOUT_S,
    )
    parsed = parse_job(job_ref(raw), raw)
    addresses = guard.plan(parsed, parse_allowed(LONGEST_NETWORK))
    allowed_dns = parse_allowed_dns("127.0.0.1") if narrowed else None
    assert len(addresses) == OBSERVATIONS_MAX and str(parsed.network) == LONGEST_NETWORK
    last = addresses[-1]
    # addresses[-2] is the lookup that fails or is cut off; half the rest are answered
    answered_later = set(addresses[:-2][::2]) if later else set()
    clock = FakeClock()

    class Prober(FakeProber):
        async def ping(self, address, timeout):
            if address == last:
                raise ProbeError(longest_icmp)
            return True

    class Resolver(FakeResolver):
        async def ptr(self, address, servers, slot):
            left = await slot()
            if address == addresses[-2]:
                for _ in range(100):
                    await asyncio.sleep(0)  # every lookup before it ends first
                if cut_off:
                    await clock.sleep(left + JOB_TIMEOUT_S)  # past the deadline
                    return None
                raise ProbeError(no_query_sent(exception_name))
            if address in answered_later:
                return probe.AnsweredPastRefusal("h.home.arpa", True, True)
            return BOTH

    result = await sweep(
        parsed, addresses, prober=Prober(), resolver=Resolver(), arp=FakeArp(), interfaces=(),
        clock=clock, max_pps=1000, allowed_dns=allowed_dns,
    )
    counted = OBSERVATIONS_MAX - 2  # the address ICMP failed on is not alive; one lookup failed
    assert len(str(counted)) == len(str(OBSERVATIONS_MAX))
    unanswered = counted - len(answered_later)
    assert len(str(unanswered)) == len(str(len(answered_later) or unanswered)) == len(str(OBSERVATIONS_MAX))
    if cut_off:
        lookups = [f"timed out after {JOB_TIMEOUT_S} s during the PTR lookups", scan.cut_short(unanswered, unanswered)]
    else:
        lookups = [no_query_sent(exception_name), scan.no_answer(unanswered, counted, unanswered, unanswered)]
    if answered_later:
        lookups.append(scan.answered_later(len(answered_later), len(answered_later)))
    if narrowed:
        lookups.insert(0, scan.outside_allowed(2, 3))
    return result, ["not run by this agent release: fdns", longest_icmp, longest_arp, *lookups], counted


async def test_the_longest_error_a_job_can_carry_is_stored_whole(monkeypatch):
    """Acceptance 3. A job's error joins at most one text from each of its steps, in
    order (`scan.run`): the methods this release does not run (`fdns`, the one); the
    ICMP sweep's failure or its timeout; the ARP sweep's failure, its not running or
    its timeout; and the PTR lookups' timeout followed by the refusals of the lookups
    that returned (`scan.cut_short`), `NO_DNS_SERVER`, or a lookup that could send
    nothing followed by the count of lookups without a usable answer and their
    refusals. A timeout of a liveness sweep ends the job's later steps, so the longest
    error takes the longest failure of each step that leaves the next to run. This
    test finds each step's longest text (an exception named by its longest name, an
    interface by the longest the agent reports, the network by the longest spelling,
    every count with as many digits as OBSERVATIONS_MAX), then runs a job that carries
    all of them at once, in both forms the lookups can end in, and holds what it
    reports to the server's rule: within SCAN_ERROR_MAX, and stored as sent, the
    refusal counts whole (495 characters, and 405 after a timeout)."""
    digits = int(str(OBSERVATIONS_MAX))
    longest_lookups = f"{no_query_sent(LONGEST_EXCEPTION.__name__)}; {scan.no_answer(*[digits] * 4)}"
    timed_out = f"timed out after {JOB_TIMEOUT_S} s during the PTR lookups; {scan.cut_short(digits, digits)}"
    assert len(longest_lookups) > max(len(NO_DNS_SERVER), len(timed_out))
    for cut_off, length in ((False, 495), (True, 405)):
        result, parts, _ = await longest_job(monkeypatch, LONGEST_EXCEPTION.__name__, cut_off)
        error = result["error"]
        assert error == "; ".join(parts)
        assert len(error) == length <= server.SCAN_ERROR_MAX == SCAN_ERROR_MAX
        assert server.scan_error(error) == error
        assert "the agent refused a reply over 1232 bytes in 4094 lookups, over 1 question or 32 records in 4094 lookups" in error


async def test_an_error_too_long_for_the_counts_stores_none_of_them(monkeypatch):
    """Finding 2's replay through a job: with a 28-character exception name in place
    of the longest builtin one, the longest error would be 501 characters. It is
    stored with the count of lookups without a usable answer in its words before #164,
    whole, and no refusal count, rather than cut after "in"."""
    result, parts, counted = await longest_job(monkeypatch, "X" * 28)
    assert len("; ".join(parts)) > SCAN_ERROR_MAX
    error = result["error"]
    assert error == "; ".join(parts[:-1] + [scan.no_answer(counted, counted)])
    assert len(error) <= SCAN_ERROR_MAX and server.scan_error(error) == error
    assert "refused" not in error


async def test_the_notice_is_the_first_part_given_up_by_the_longest_error(monkeypatch):
    """#170, acceptance 3: the longest job of #164's test with half its lookups answered
    by a later server after a refusal of each limit, every count at four digits. With
    the notice the whole would pass SCAN_ERROR_MAX, so the notice is left out whole and
    the rest is what #164 stores: 495 characters, and 405 after a timeout, the refusal
    counts of the lookups without a usable answer whole, and stored as sent."""
    for cut_off, length in ((False, 495), (True, 405)):
        result, parts, _ = await longest_job(monkeypatch, LONGEST_EXCEPTION.__name__, cut_off, later=True)
        assert parts[-1].startswith("rdns: a later server answered after")
        assert len("; ".join(parts)) > SCAN_ERROR_MAX
        error = result["error"]
        assert error == "; ".join(parts[:-1])
        assert len(error) == length <= SCAN_ERROR_MAX
        assert server.scan_error(error) == error
        assert "the agent refused a reply over 1232 bytes in 2047 lookups, over 1 question or 32 records in 2047 lookups" in error
        assert "answered after" not in error
        # the answers decided the records, as they would alone
        assert sum(1 for record in result["observations"] if record.get("rdns_name") == "h.home.arpa") == 2047


async def test_the_notice_of_servers_left_out_is_kept_over_the_refusal_counts(monkeypatch):
    """#93: the longest job of #164's test with two of its three DNS servers outside
    ALLOWED_DNS_SERVERS, the longest form of that notice. With it the whole would pass
    SCAN_ERROR_MAX, so #164's refusal counts are left out whole, the count of lookups
    without a usable answer in its words before #164, and the notice kept: it names a
    cause on the agent's own host that no count shows. After a timeout the whole fits,
    and all of it is kept. With #170's notice as well, that goes first, and the rest is
    the same."""
    for later in (False, True):
        result, parts, counted = await longest_job(monkeypatch, LONGEST_EXCEPTION.__name__, later=later, narrowed=True)
        notice = scan.outside_allowed(2, 3)
        assert parts[3] == notice and len("; ".join(parts)) > SCAN_ERROR_MAX
        unanswered = counted - (counted // 2 if later else 0)
        kept = parts[:5] + [scan.no_answer(unanswered, counted)]
        error = result["error"]
        assert error == "; ".join(kept)
        assert len(error) == 494 <= SCAN_ERROR_MAX and server.scan_error(error) == error
        assert "refused" not in error and "answered after" not in error
    result, parts, _ = await longest_job(monkeypatch, LONGEST_EXCEPTION.__name__, cut_off=True, narrowed=True)
    assert result["error"] == "; ".join(parts) and len(result["error"]) == 405 + 2 + len(scan.outside_allowed(2, 3))
    assert server.scan_error(result["error"]) == result["error"]


# --- the lookups a later server answered after a refused reply are said, as a notice (#170) ---


LATER_TOO_LARGE = (True, False)
LATER_TOO_MANY = (False, True)
LATER_BOTH = (True, True)


async def test_a_job_whose_every_first_reply_was_refused_and_every_lookup_answered_says_so():
    """Acceptance 2: every lookup's first reply was refused, for its size or its
    header's counts, and every lookup was answered by a later server, with a name or
    that there is none. The error is the notice alone, by limit, in lookups, counts
    only; the scan is complete, and each record is what the answer alone makes it: the
    same records as a job whose servers answered at once, which has no error."""
    live = ["10.0.0.3", "10.0.0.4", "10.0.0.5", "10.0.0.6"]
    answers = {"10.0.0.3": "three.home.arpa", "10.0.0.4": "four.home.arpa", "10.0.0.5": probe.NO_NAME,
               "10.0.0.6": "six.home.arpa"}
    flags = {"10.0.0.3": LATER_TOO_LARGE, "10.0.0.4": LATER_TOO_LARGE, "10.0.0.5": LATER_TOO_MANY,
             "10.0.0.6": LATER_BOTH}
    later = {a: probe.AnsweredPastRefusal(answers[a], *flags[a]) for a in live}
    parsed, addresses = planned()
    result = await sweep(parsed, addresses, prober=FakeProber(alive=live), resolver=FakeResolver(later=later))
    assert result["error"] == (
        "rdns: a later server answered after the agent refused a reply over 1232 bytes in 3 lookups, "
        "over 1 question or 32 records in 2 lookups"
    )
    assert result["error"] == scan.answered_later(3, 2)
    assert "10.0." not in result["error"] and "home.arpa" not in result["error"]
    assert result["complete"] is True and result["liveness_complete"] is True
    assert result["observations"] == [
        {"ip": "10.0.0.3", "rdns_name": "three.home.arpa"}, {"ip": "10.0.0.4", "rdns_name": "four.home.arpa"},
        {"ip": "10.0.0.5", "rdns_no_name": True}, {"ip": "10.0.0.6", "rdns_name": "six.home.arpa"},
    ]
    # acceptance 2's other half: no refusal, the same answers, and no error, as before
    plain = FakeResolver({a: n for a, n in answers.items() if isinstance(n, str)})
    before = await sweep(parsed, addresses, prober=FakeProber(alive=live), resolver=plain)
    assert before["error"] is None and before["complete"] is True
    assert before["observations"] == result["observations"]


async def test_a_name_a_later_server_answered_is_sanitised_as_any_other():
    """The wrapped answer goes through `rdns_name` as a bare one does: a name that is
    not a hostname is dropped, the record carries neither, and the lookup is still
    one a later server answered."""
    later = {"10.0.0.3": probe.AnsweredPastRefusal("Evil<b>.home.arpa", True, False),
             "10.0.0.4": probe.AnsweredPastRefusal("NAS.Home.Arpa.", False, True)}
    parsed, addresses = planned()
    result = await sweep(
        parsed, addresses, prober=FakeProber(alive=list(later)), resolver=FakeResolver(later=later)
    )
    assert result["observations"] == [{"ip": "10.0.0.3"}, {"ip": "10.0.0.4", "rdns_name": "nas.home.arpa"}]
    assert result["error"] == scan.answered_later(1, 1)


@pytest.mark.parametrize(
    "counts, said",
    [
        ((3, 0), "rdns: a later server answered after the agent refused a reply over 1232 bytes in 3 lookups"),
        ((0, 1), "rdns: a later server answered after the agent refused a reply over 1 question or 32 records in 1 lookup"),
        ((1, 2), "rdns: a later server answered after the agent refused a reply over 1232 bytes in 1 lookup, "
                 "over 1 question or 32 records in 2 lookups"),
    ],
)
def test_the_notice_names_only_the_limits_that_refused_a_reply(counts, said):
    assert scan.answered_later(*counts) == said
    assert " 0 " not in said


async def test_the_notice_follows_the_count_of_lookups_without_a_usable_answer():
    """Lookups of both kinds in one job: one without a usable answer whose reply was
    refused (#164's count), one a later server answered after a refusal, one answered
    at once. #164's part comes first, unchanged, then the notice; each counts only its
    own lookups."""
    live = ["10.0.0.3", "10.0.0.4", "10.0.0.5"]
    resolver = FakeResolver(
        {"10.0.0.5": "five.home.arpa"},
        unread={"10.0.0.3": TOO_LARGE},
        later={"10.0.0.4": probe.AnsweredPastRefusal("four.home.arpa", False, True)},
    )
    parsed, addresses = planned()
    result = await sweep(parsed, addresses, prober=FakeProber(alive=live), resolver=resolver)
    assert result["error"] == f"{scan.no_answer(1, 3, 1, 0)}; {scan.answered_later(0, 1)}"
    assert result["complete"] is True
    assert result["observations"] == [
        {"ip": "10.0.0.3"}, {"ip": "10.0.0.4", "rdns_name": "four.home.arpa"},
        {"ip": "10.0.0.5", "rdns_name": "five.home.arpa"},
    ]


async def test_the_notice_follows_a_timeout_and_counts_the_lookups_that_returned():
    """A lookup a later server answered after a refusal returns at once, the other runs
    past the deadline: the timeout's error, then the notice; nothing of #164's, since
    no lookup that returned went without an answer."""
    clock = FakeClock()
    parsed, addresses = planned(ranges=[{"start": "10.0.0.1", "end": "10.0.0.2"}], timeout_s=1)
    first = probe.AnsweredPastRefusal("one.home.arpa", True, False)
    result = await sweep(
        parsed, addresses, prober=FakeProber(alive=["10.0.0.1", "10.0.0.2"]),
        resolver=DeadlineResolver(clock, first, None), clock=clock,
    )
    assert result["error"] == f"timed out after 1 s during the PTR lookups; {scan.answered_later(1, 0)}"
    assert result["complete"] is False
    assert result["observations"][0] == {"ip": "10.0.0.1", "rdns_name": "one.home.arpa"}


async def test_the_notice_follows_a_lookup_that_could_send_nothing():
    live = ["10.0.0.1", "10.0.0.2"]

    class Resolver(FakeResolver):
        async def ptr(self, address, servers, slot):
            if address == ipaddress.ip_address("10.0.0.2"):
                for _ in range(20):
                    await asyncio.sleep(0)  # the lookup before it ends first
                raise ProbeError(no_query_sent("OSError"))
            return await super().ptr(address, servers, slot)

    resolver = Resolver(later={"10.0.0.1": probe.AnsweredPastRefusal(probe.NO_NAME, True, True)})
    parsed, addresses = planned()
    result = await sweep(parsed, addresses, prober=FakeProber(alive=live), resolver=resolver)
    assert result["error"] == f"{no_query_sent('OSError')}; {scan.answered_later(1, 1)}"
    assert result["complete"] is False
    assert result["observations"] == [{"ip": "10.0.0.1", "rdns_no_name": True}, {"ip": "10.0.0.2"}]


def test_the_notice_is_given_up_whole_before_the_refusal_counts():
    """Acceptance 3, across every length of the rest of an error: the notice is kept
    whole while the whole fits the cap and is left out whole once it would not; only
    once the rest with #164's counts would not fit either are those counts given up,
    as #164 gives them up; no count is ever stored cut short, and what is stored is
    what the server stores."""
    full, plain = scan.no_answer(3, 9, 1, 2), scan.no_answer(3, 9)
    notice = scan.answered_later(4, 5)
    timeout = "timed out after 1 s during the PTR lookups"
    cut = scan.cut_short(1, 2)
    for length in range(1, SCAN_ERROR_MAX):
        head = "x" * length
        stored = scan.error_text([head, full, notice], (2, None), (1, plain))
        if length + 4 + len(full) + len(notice) <= SCAN_ERROR_MAX:
            assert stored == f"{head}; {full}; {notice}"
        elif length + 2 + len(full) <= SCAN_ERROR_MAX:
            assert stored == f"{head}; {full}"
        else:
            assert stored == scan_error(f"{head}; {plain}")
        assert server.scan_error(stored) == stored and len(stored) <= SCAN_ERROR_MAX
        assert "refused" not in stored or stored.endswith(("lookup", "lookups"))
        # the notice alone, and after a timeout's refusals
        stored = scan.error_text([head, notice], (1, None))
        assert stored == (f"{head}; {notice}" if length + 2 + len(notice) <= SCAN_ERROR_MAX else scan_error(head))
        stored = scan.error_text([head, timeout, cut, notice], (3, None), (2, None))
        if length + 6 + len(timeout) + len(cut) + len(notice) <= SCAN_ERROR_MAX:
            assert stored == f"{head}; {timeout}; {cut}; {notice}"
        elif length + 4 + len(timeout) + len(cut) <= SCAN_ERROR_MAX:
            assert stored == f"{head}; {timeout}; {cut}"
        else:
            assert stored == scan_error(f"{head}; {timeout}")
        assert "refused" not in stored or stored.endswith(("lookup", "lookups"))
