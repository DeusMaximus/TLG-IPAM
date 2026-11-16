"""One job's sweep: pacing, the timeout, PTR lookups (spec §5.2, §5.6)."""

import ipaddress

import pytest

from conftest import FakeClock, FakeProber, FakeResolver, job
from tlg_ipam_agent import guard, scan
from tlg_ipam_agent.config import parse_allowed
from tlg_ipam_agent.probe import ProbeError
from tlg_ipam_agent.protocol import job_ref, parse_job


def planned(**overrides):
    raw = job(**overrides)
    parsed = parse_job(job_ref(raw), raw)
    return parsed, guard.plan(parsed, parse_allowed(None))


async def sweep(parsed, addresses, *, prober, resolver=None, clock=None, max_pps=200):
    return await scan.run(
        parsed,
        addresses,
        prober=prober,
        resolver=resolver or FakeResolver(),
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


@pytest.mark.parametrize("methods", [["icmp", "arp"], ["icmp", "arp", "rdns"], ["icmp", "fdns"]])
async def test_a_method_this_release_cannot_run_makes_the_result_incomplete(methods):
    """`arp` is #14: a sweep without it must not let the server mark silent addresses
    dead, so the result set says it is incomplete."""
    prober = FakeProber(alive=["10.0.0.3"])
    parsed, addresses = planned(methods=methods)
    result = await sweep(parsed, addresses, prober=prober)
    assert result["complete"] is False
    assert "not run by this agent release" in result["error"]
    assert len(prober.sent) == 254
    assert all("mac" not in o for o in result["observations"])


async def test_an_arp_only_job_sends_nothing():
    prober = FakeProber()
    parsed, addresses = planned(methods=["arp"])
    result = await sweep(parsed, addresses, prober=prober)
    assert prober.sent == []
    assert result["complete"] is False and result["observations"] == []


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
    """Both sends start inside a 1 s job, but the probes take 0.75 s each: the job
    ends after its deadline, so it is not complete (Codex and Greptile on PR #70)."""
    clock = FakeClock()
    prober = FakeProber(alive=["10.0.0.1", "10.0.0.2"], clock=clock, delay=0.75)
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
