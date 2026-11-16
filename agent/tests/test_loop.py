"""The check-in loop against a fake server and a fake prober (spec §5.1, §5.7)."""

import asyncio

import httpx
import pytest

from conftest import (
    FakeClock,
    FakeProber,
    FakeResolver,
    FakeServer,
    accepted,
    job,
    make_agent,
    refused,
)
from tlg_ipam_agent.protocol import (
    CHECKIN_REFUSED_RETRY_S,
    CHECKIN_RETRY_MAX_S,
    CHECKIN_RETRY_MIN_S,
    LEASE_S,
    PROTOCOL_VERSION,
)


async def run_checkins(agent, server, count: int) -> None:
    """Run the real loop, worker included, until `count` check-ins have been made."""
    stop = asyncio.Event()

    def counted():
        if len(server.checkins_posted) >= count:
            stop.set()

    server.on_checkin = counted
    await asyncio.wait_for(agent.run(stop), timeout=10)


async def drain(agent) -> None:
    """Let the worker finish what is queued."""
    worker = asyncio.create_task(agent.worker())
    try:
        await asyncio.wait_for(agent.queue.join(), timeout=10)
    finally:
        worker.cancel()


# --- refusals ---


async def test_429_then_503_backs_off_within_bounds_and_sends_nothing_until_accepted():
    """Acceptance: against 429 and then 503 the loop waits between attempts, never
    exceeds its bound, and sends no packet until a check-in is accepted."""
    server = FakeServer(
        checkins=[
            refused(429, "RATE_LIMITED"),
            refused(429, "RATE_LIMITED"),
            refused(503, "UNAVAILABLE"),
            refused(503, "UNAVAILABLE"),
            refused(503, "UNAVAILABLE"),
            refused(503, "UNAVAILABLE"),
            refused(503, "UNAVAILABLE"),
            refused(503, "UNAVAILABLE"),
            accepted(job(ranges=[{"start": "10.0.0.1", "end": "10.0.0.4"}])),
            accepted(),
        ]
    )
    prober = FakeProber()
    sent_before_accepted = []

    def checkin():
        sent_before_accepted.append(len(prober.sent))
        if len(server.checkins_posted) >= 10:
            stop.set()

    stop = asyncio.Event()
    server.on_checkin = checkin
    agent, clock = make_agent(server, prober=prober)
    await asyncio.wait_for(agent.run(stop), timeout=10)

    # every attempt up to and including the accepted one went out with no packet sent
    assert sent_before_accepted[:9] == [0] * 9
    # the loop waited before each retry: eight waits for eight refusals
    waits = clock.sleeps[:8]
    assert len(waits) == 8
    for n, wait in enumerate(waits):
        assert CHECKIN_RETRY_MIN_S <= wait <= min(CHECKIN_RETRY_MAX_S, CHECKIN_RETRY_MIN_S * 2 ** (n + 1))
    assert max(waits) <= CHECKIN_RETRY_MAX_S
    # the ceiling was reached and held: retries 5-7 may draw up to the bound
    assert agent.backoff.attempt == 0  # reset by the accepted check-in
    # after acceptance, the job ran: four addresses swept, no more
    assert len(prober.sent) == 4


async def test_backoff_never_exceeds_its_bound_over_a_long_outage():
    server = FakeServer(checkins=[refused(503, "UNAVAILABLE")])
    agent, clock = make_agent(server)
    await run_checkins(agent, server, 60)
    waits = clock.sleeps
    assert len(waits) >= 59
    assert all(CHECKIN_RETRY_MIN_S <= w <= CHECKIN_RETRY_MAX_S for w in waits)
    # jittered, not lock-step: the late waits are not all the same
    assert len({round(w, 3) for w in waits[10:]}) > 10
    assert max(waits) > CHECKIN_RETRY_MAX_S / 2


@pytest.mark.parametrize(
    "answer",
    [
        refused(401, "UNAUTHORIZED"),
        refused(403, "FORBIDDEN"),
        refused(409, "PROTOCOL_MISMATCH"),
        refused(422, "VALIDATION_ERROR"),
        refused(404, "NOT_FOUND"),
    ],
)
async def test_refusals_only_the_owner_can_clear_are_not_retried_in_a_tight_loop(answer):
    server = FakeServer(checkins=[answer])
    prober = FakeProber()
    agent, clock = make_agent(server, prober=prober)
    await run_checkins(agent, server, 4)
    assert len(clock.sleeps) >= 3
    for wait in clock.sleeps[:3]:
        assert 0.9 * CHECKIN_REFUSED_RETRY_S <= wait <= CHECKIN_REFUSED_RETRY_S
    assert prober.sent == []


@pytest.mark.parametrize("status", [401, 403, 409, 422, 429, 500, 503])
async def test_a_refused_checkin_authorises_no_scan_even_if_it_names_a_job(status):
    """Only a 200's jobs are run. A refusal that carries a job-shaped body is ignored."""
    body = {"error": {"code": "X", "message": "no"}, "data": {"jobs": [job()]}, "jobs": [job()]}
    server = FakeServer(checkins=[(status, body)])
    prober = FakeProber(alive=["10.0.0.1"])
    agent, _ = make_agent(server, prober=prober)
    await agent.checkin_once()
    assert agent.queue.empty()
    await drain(agent)
    assert prober.sent == []
    assert server.results_posted == []


async def test_no_answer_is_retried_on_the_backoff():
    server = FakeServer(checkins=[httpx.ConnectError("refused"), accepted()])
    agent, clock = make_agent(server)
    first = await agent.checkin_once()
    assert CHECKIN_RETRY_MIN_S <= first <= 2 * CHECKIN_RETRY_MIN_S
    assert await agent.checkin_once() == 60


async def test_a_200_without_a_data_object_is_retried_and_runs_nothing():
    server = FakeServer(checkins=[(200, {"jobs": [job()]}), accepted()])
    prober = FakeProber()
    agent, _ = make_agent(server, prober=prober)
    wait = await agent.checkin_once()
    assert CHECKIN_RETRY_MIN_S <= wait <= CHECKIN_RETRY_MAX_S
    assert agent.queue.empty()


async def test_accepted_checkin_resets_the_backoff():
    server = FakeServer(
        checkins=[refused(503, "UNAVAILABLE")] * 6 + [accepted(), refused(503, "UNAVAILABLE")]
    )
    agent, _ = make_agent(server)
    for _ in range(6):
        await agent.checkin_once()
    assert agent.backoff.attempt == 6
    assert await agent.checkin_once() == 60
    assert agent.backoff.attempt == 0
    assert await agent.checkin_once() <= 2 * CHECKIN_RETRY_MIN_S


@pytest.mark.parametrize("interval,expected", [(60, 60), (1, 10), (0, 10), (10**9, 3600)])
async def test_server_interval_is_honoured_within_bounds(interval, expected):
    server = FakeServer(checkins=[(200, {"data": {"checkin_interval_s": interval, "jobs": []}})])
    agent, _ = make_agent(server)
    assert await agent.checkin_once() == expected


# --- the check-in body ---


async def test_checkin_reports_the_protocol_and_interfaces():
    server = FakeServer()
    agent, _ = make_agent(server)
    await agent.checkin_once()
    [body] = server.checkins_posted
    assert body["protocol"] == PROTOCOL_VERSION
    assert body["interfaces"] == [{"name": "eth0", "cidr": "10.0.0.5/24", "mac": "02:00:00:00:00:05"}]
    assert body["capabilities"] == ["icmp", "rdns"]
    assert body["version"] and body["hostname"] and body["platform"]


# --- jobs ---


async def test_job_outside_the_allowlist_sends_nothing_and_the_refusal_reaches_the_server():
    """Acceptance: a job outside the allowlist sends no packet, and the refusal is
    reported to the server."""
    outside = job(cidr="203.0.113.0/24", ranges=[{"start": "203.0.113.1", "end": "203.0.113.254"}])
    server = FakeServer(checkins=[accepted(outside)])
    prober = FakeProber(alive=["203.0.113.7"])
    agent, _ = make_agent(server, prober=prober)
    await agent.checkin_once()
    await drain(agent)
    assert prober.sent == []
    [report] = server.results_posted
    assert report["job_id"] == outside["job_id"]
    assert report["subnet_id"] == outside["subnet_id"]
    assert report["complete"] is False
    assert report["observations"] == []
    assert "203.0.113.0/24 is outside ALLOWED_CIDRS" in report["error"]


@pytest.mark.parametrize(
    "overrides",
    [
        # a range outside the job's own subnet
        {"ranges": [{"start": "10.0.1.1", "end": "10.0.1.10"}]},
        # a range that leaves it
        {"ranges": [{"start": "10.0.0.250", "end": "10.0.1.5"}]},
        # a subnet inside the allowlist, a range outside it
        {"ranges": [{"start": "8.8.8.8", "end": "8.8.8.8"}]},
        # a reversed range
        {"ranges": [{"start": "10.0.0.20", "end": "10.0.0.10"}]},
        # the whole of 10/8 is allowed, but no job may hold more than a /20's addresses
        {"cidr": "10.0.0.0/8", "ranges": [{"start": "10.0.0.1", "end": "10.255.255.254"}]},
        {"cidr": "not a network"},
        {"cidr": "10.0.0.1/24"},
        {"ranges": []},
        {"methods": ["rdns"]},
        {"methods": ["icmp", "exec"]},
        {"dns_servers": ["dns.example.com"]},
        {"timeout_s": 0},
    ],
)
async def test_bad_jobs_are_refused_before_any_packet_and_reported(overrides):
    server = FakeServer(checkins=[accepted(job(**overrides))])
    prober = FakeProber(alive=["10.0.0.1", "8.8.8.8"])
    resolver = FakeResolver()
    agent, _ = make_agent(server, prober=prober, resolver=resolver)
    await agent.checkin_once()
    await drain(agent)
    assert prober.sent == []
    assert resolver.asked == []
    [report] = server.results_posted
    assert report["complete"] is False and report["observations"] == []
    assert report["error"].startswith("refused:")


async def test_a_job_with_no_usable_ids_is_dropped_unreported():
    server = FakeServer(checkins=[accepted(job(job_id=None)), accepted("not a job")])
    prober = FakeProber()
    agent, _ = make_agent(server, prober=prober)
    await agent.checkin_once()
    await agent.checkin_once()
    await drain(agent)
    assert prober.sent == [] and server.results_posted == []


async def test_job_runs_and_reports_live_addresses_with_names():
    server = FakeServer(checkins=[accepted(job())])
    prober = FakeProber(alive=["10.0.0.1", "10.0.0.20"])
    resolver = FakeResolver({"10.0.0.20": "NAS.home.arpa."})
    agent, _ = make_agent(server, prober=prober, resolver=resolver)
    await agent.checkin_once()
    await drain(agent)
    assert len(prober.sent) == 254
    [report] = server.results_posted
    assert report["complete"] is True
    assert report["error"] is None
    assert report["observations"] == [
        {"ip": "10.0.0.1"},
        {"ip": "10.0.0.20", "rdns_name": "nas.home.arpa"},
    ]


async def test_jobs_run_one_at_a_time_while_checkins_continue():
    """The loop keeps checking in while a job runs, and a second job waits for the
    first: the prober never sees two jobs' addresses interleaved."""
    first = job(ranges=[{"start": "10.0.0.1", "end": "10.0.0.30"}])
    second = job(
        job_id="6f1c2a8e-0000-4000-8000-000000000003",
        ranges=[{"start": "10.0.0.100", "end": "10.0.0.130"}],
    )
    server = FakeServer(checkins=[accepted(first, interval=10), accepted(second, interval=10), accepted(interval=10)])
    clock = FakeClock()
    # each ping takes 1 s of fake time, so a job lasts several check-in intervals
    prober = FakeProber(clock=clock, delay=1.0)
    agent, _ = make_agent(server, prober=prober, clock=clock, max_pps=1)
    stop = asyncio.Event()
    server.on_checkin = lambda: len(server.results_posted) == 2 and stop.set()
    await asyncio.wait_for(agent.run(stop), timeout=10)
    sent = [str(a) for a in prober.sent]
    # all of the first job, then all of the second
    assert sent == [f"10.0.0.{i}" for i in range(1, 31)] + [f"10.0.0.{i}" for i in range(100, 131)]
    assert [r["job_id"] for r in server.results_posted] == [first["job_id"], second["job_id"]]
    # check-ins went on while the jobs ran: each took 30 s, the interval is 10 s
    assert len(server.checkins_posted) >= 6


# --- reporting ---


async def test_results_are_retried_on_503_and_then_delivered():
    server = FakeServer(
        checkins=[accepted(job(ranges=[{"start": "10.0.0.1", "end": "10.0.0.2"}]))],
        results=[refused(503, "UNAVAILABLE"), refused(500, "INTERNAL"), (200, {"data": {"accepted": 0, "rejected": []}})],
    )
    agent, clock = make_agent(server)
    await agent.checkin_once()
    await drain(agent)
    assert len(server.results_posted) == 3
    assert server.results_posted[0] == server.results_posted[2]
    assert all(CHECKIN_RETRY_MIN_S <= w <= CHECKIN_RETRY_MAX_S for w in clock.sleeps if w >= 1)


@pytest.mark.parametrize("status,code", [(409, "JOB_NOT_CURRENT"), (422, "VALIDATION_ERROR"), (413, "PAYLOAD_TOO_LARGE"), (403, "FORBIDDEN")])
async def test_a_refused_result_set_is_not_retried(status, code):
    server = FakeServer(
        checkins=[accepted(job(ranges=[{"start": "10.0.0.1", "end": "10.0.0.2"}]))],
        results=[refused(status, code)],
    )
    agent, _ = make_agent(server)
    await agent.checkin_once()
    await drain(agent)
    assert len(server.results_posted) == 1


async def test_results_are_given_up_once_the_lease_could_have_run_out():
    server = FakeServer(
        checkins=[accepted(job(ranges=[{"start": "10.0.0.1", "end": "10.0.0.2"}]))],
        results=[refused(503, "UNAVAILABLE")],
    )
    agent, clock = make_agent(server)
    start = clock.t
    await agent.checkin_once()
    await drain(agent)
    assert clock.t - start <= LEASE_S
    assert 3 <= len(server.results_posted) < 40


async def test_a_failure_while_reporting_does_not_stop_the_next_job():
    first = job(ranges=[{"start": "10.0.0.1", "end": "10.0.0.2"}])
    second = job(job_id="6f1c2a8e-0000-4000-8000-000000000004", ranges=[{"start": "10.0.0.3", "end": "10.0.0.4"}])
    server = FakeServer(checkins=[accepted(first, second)])
    prober = FakeProber()
    agent, _ = make_agent(server, prober=prober)
    real = agent.client.results
    calls = []

    async def flaky(body, **kwargs):
        calls.append(body["job_id"])
        if len(calls) == 1:
            raise RuntimeError("bug")
        return await real(body, **kwargs)

    agent.client.results = flaky
    await agent.checkin_once()
    await drain(agent)
    assert calls == [first["job_id"], second["job_id"]]
    assert [str(a) for a in prober.sent] == ["10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4"]
