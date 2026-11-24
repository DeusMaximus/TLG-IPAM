"""Answers the agent cannot trust to finish, to fit or to be well formed (findings 2
and 3 of the briefed review of PR #70): every request ends by its deadline, a body is
read into a bounded buffer, a success is acted on only in its route's shape, and no
text the server wrote reaches the log."""

import asyncio
import io
import json
import logging
import time

import httpx
import pytest

from conftest import ENROL_TOKEN, TOKEN, FakeProber, FakeServer, accepted, job, make_agent, refused
from tlg_ipam_agent.client import RESPONSE_MAX, AgentClient, Kind, make_http
from tlg_ipam_agent.loop import ENROL_REDACTED, REDACTED, Redact, log
from tlg_ipam_agent.protocol import CHECKIN_RETRY_MIN_S, LEASE_S

# --- a server that drips its answer, on loopback ---


async def dripping_server():
    """Plain HTTP on 127.0.0.1: declares a large 200 and sends 1 KiB every 10 ms,
    never finishing. Records when each request arrived."""
    arrived = []

    async def handle(reader, writer):
        try:
            await reader.readuntil(b"\r\n\r\n")
            arrived.append(time.monotonic())
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 900000\r\n\r\n")
            writer.write(b'{"data": {"jobs": [')
            while True:
                writer.write(b" " * 1024)
                await writer.drain()
                await asyncio.sleep(0.01)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1], arrived


@pytest.mark.parametrize("route", ["checkin", "results"])
async def test_a_dripping_answer_ends_at_the_request_deadline(route):
    server, port, arrived = await dripping_server()
    async with server:
        async with make_http(f"http://127.0.0.1:{port}", TOKEN) as http:
            client = AgentClient(http, request_timeout=0.5)
            started = time.monotonic()
            answer = await asyncio.wait_for(getattr(client, route)({}), timeout=10)
            took = time.monotonic() - started
    assert answer.kind is Kind.RETRY and answer.status is None
    assert answer.message.startswith("no complete answer")
    assert len(arrived) == 1
    # httpx's 30 s per-chunk read timeout would never fire; the overall deadline does
    assert took < 2.0


class Stalls(httpx.AsyncByteStream):
    """A response body that sends its first chunk and then nothing, ever."""

    async def __aiter__(self):
        yield b'{"data": {"accepted": 1, '
        await asyncio.Event().wait()


async def test_a_results_request_is_cut_off_when_the_lease_runs_out():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, stream=Stalls())

    server = FakeServer()
    agent, clock = make_agent(server)
    agent.client = AgentClient(
        httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x")
    )
    body = {"job_id": "j", "subnet_id": "s"}
    # the job was received so long ago that 0.3 s of its lease is left
    started = time.monotonic()
    await asyncio.wait_for(agent.report(body, received=clock.t - LEASE_S + 0.3), timeout=5)
    assert time.monotonic() - started < 2.0
    # one request, cut off by the lease; the next wait would pass it, so no retry
    assert len(requests) == 1


async def test_no_results_request_starts_after_the_lease():
    server = FakeServer()
    agent, clock = make_agent(server)
    await agent.report({"job_id": "j", "subnet_id": "s"}, received=clock.t - LEASE_S)
    assert server.results_posted == []


async def test_each_results_request_gets_only_the_lease_time_left():
    budgets = []
    server = FakeServer(results=[refused(503, "UNAVAILABLE")])
    agent, clock = make_agent(server)
    real = agent.client.results

    async def recording(body, budget=None):
        budgets.append(budget)
        return await real(body, budget=budget)

    agent.client.results = recording
    received = clock.t
    await agent.report({"job_id": "j", "subnet_id": "s"}, received=received)
    assert len(budgets) >= 3
    assert budgets[0] == pytest.approx(LEASE_S)
    assert all(0 < b <= LEASE_S for b in budgets)
    assert budgets == sorted(budgets, reverse=True)


# --- size ---


def _client(handler, **kwargs):
    return AgentClient(httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x"), **kwargs)


class Endless(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'{"data": {"checkin_interval_s": 60, "jobs": ['
        while True:
            yield b" " * 4096
            await asyncio.sleep(0)


@pytest.mark.parametrize(
    "response",
    [
        lambda: httpx.Response(200, content=b'{"data": {"checkin_interval_s": 60, "jobs": []}}' + b" " * (8 * 1024 * 1024)),
        lambda: httpx.Response(200, stream=Endless()),
        lambda: httpx.Response(200, headers={"content-length": str(RESPONSE_MAX + 1)}, content=b"{}"),
    ],
    ids=["8 MiB", "endless, no length", "declared too large"],
)
async def test_an_answer_over_the_cap_is_not_read_past_it(response):
    answer = await asyncio.wait_for(_client(lambda request: response()).checkin({}), timeout=5)
    assert answer.kind is Kind.RETRY and answer.status == 200
    assert str(RESPONSE_MAX) in answer.message


async def test_an_oversized_refusal_keeps_its_status_but_no_code():
    body = json.dumps({"error": {"code": "FORBIDDEN", "message": "x" * (2 * RESPONSE_MAX)}})
    answer = await _client(lambda request: httpx.Response(403, content=body)).checkin({})
    assert answer.kind is Kind.REFUSED and answer.status == 403 and answer.code is None


# --- shape ---


J = "job"


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"jobs": [J]},
        {"checkin_interval_s": 60},
        {"checkin_interval_s": "60", "jobs": [J]},
        {"checkin_interval_s": True, "jobs": [J]},
        {"checkin_interval_s": 60, "jobs": {"0": J}},
    ],
)
async def test_a_checkin_answer_not_in_its_shape_runs_nothing(data):
    # each case carries a real job where it has a place for one
    data = json.loads(json.dumps(data).replace('"job"', json.dumps(job())))
    server = FakeServer(checkins=[(200, {"data": data})])
    prober = FakeProber()
    agent, _ = make_agent(server, prober=prober)
    wait = await agent.checkin_once()
    assert CHECKIN_RETRY_MIN_S <= wait <= 2 * CHECKIN_RETRY_MIN_S
    assert agent.queue.empty() and prober.sent == []


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"accepted": None, "rejected": []},
        {"accepted": "1\nFORGED\x1b[31m", "rejected": []},
        {"accepted": True, "rejected": []},
        {"accepted": -1, "rejected": []},
        {"accepted": 1, "rejected": "none"},
        {"accepted": 1, "rejected": [{"ip": "10.0.0.1"}]},
        {"accepted": 1, "rejected": [{"ip": 5, "reason": "invalid"}]},
    ],
)
async def test_an_acknowledgment_not_in_its_shape_is_retried_and_never_logged(data, caplog):
    server = FakeServer(
        checkins=[accepted(job(ranges=[{"start": "10.0.0.1", "end": "10.0.0.2"}]))],
        results=[(200, {"data": data}), (200, {"data": {"accepted": 2, "rejected": [{"ip": None, "reason": "invalid"}]}})],
    )
    agent, _ = make_agent(server)
    with caplog.at_level(logging.INFO, logger=log.name):
        await agent.checkin_once()
        await agent.run_job(agent.queue.get_nowait())
    # not delivered on the bad answer: sent again, and acknowledged the second time
    assert len(server.results_posted) == 2
    messages = [r.getMessage() for r in caplog.records]
    assert any("2 accepted, 1 rejected" in m for m in messages)
    assert not any("FORGED" in m or "\x1b" in m or "None accepted" in m for m in messages)


async def test_a_server_echoing_the_token_does_not_get_it_into_the_log(caplog):
    echo = {"error": {"code": TOKEN, "message": f"bad token {TOKEN}\n\x1b[31mFORGED"}}
    server = FakeServer(
        checkins=[(403, echo), accepted(job(ranges=[{"start": "10.0.0.1", "end": "10.0.0.1"}]))],
        results=[(503, echo), (409, echo)],
    )
    agent, _ = make_agent(server)
    with caplog.at_level(logging.DEBUG):
        await agent.checkin_once()
        await agent.checkin_once()
        await agent.run_job(agent.queue.get_nowait())
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "HTTP 403" in text and "HTTP 409" in text
    assert TOKEN not in text
    assert "bad token" not in text and "FORGED" not in text


async def test_an_exceptions_text_is_not_logged():
    def handler(request):
        raise httpx.ConnectError(f"refused by peer saying {TOKEN}")

    answer = await _client(handler).checkin({})
    assert answer.message == "no answer (ConnectError)"


def test_the_redaction_filter_covers_messages_arguments_and_tracebacks():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(Redact(TOKEN))
    logger = logging.getLogger("tlg_ipam_agent.test_redact")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        logger.error("token %s in an argument", TOKEN)
        logger.error(f"token {TOKEN} in the message")
        try:
            raise RuntimeError(f"raised with {TOKEN}")
        except RuntimeError:
            logger.exception("failed")
    finally:
        logger.removeHandler(handler)
    out = stream.getvalue()
    assert TOKEN not in out
    assert out.count(REDACTED) == 3
    assert "RuntimeError" in out


def test_the_cli_redacts_the_token_on_every_handler(monkeypatch):
    from tlg_ipam_agent import cli

    root = logging.getLogger()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    monkeypatch.setattr(root, "handlers", [handler])
    cli.configure_logging(TOKEN)
    cli.configure_logging(TOKEN)  # idempotent
    assert sum(isinstance(f, Redact) for f in handler.filters) == 1
    logging.getLogger("httpx").warning("a library line with %s", TOKEN)
    assert TOKEN not in stream.getvalue() and REDACTED in stream.getvalue()


def test_the_cli_adds_a_token_learnt_later_to_the_same_filter(monkeypatch):
    """The enrolment token is known at start, the agent token only once it is read or
    exchanged (#22): both are redacted, by one filter per handler."""
    from tlg_ipam_agent import cli

    root = logging.getLogger()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    monkeypatch.setattr(root, "handlers", [handler])
    cli.configure_logging(None, ENROL_TOKEN)
    cli.configure_logging(TOKEN)
    assert sum(isinstance(f, Redact) for f in handler.filters) == 1
    logging.getLogger("httpx").warning("both %s and %s", ENROL_TOKEN, TOKEN)
    try:
        raise RuntimeError(f"raised with {ENROL_TOKEN}")
    except RuntimeError:
        logging.getLogger("tlg_ipam_agent.test_redact_later").exception("failed")
    out = stream.getvalue()
    assert ENROL_TOKEN not in out and TOKEN not in out
    assert out.count(ENROL_REDACTED) == 2 and out.count(REDACTED) == 1


def test_every_request_has_a_thirty_second_deadline():
    from tlg_ipam_agent.client import REQUEST_TIMEOUT_S

    assert REQUEST_TIMEOUT_S == 30.0
    assert AgentClient(httpx.AsyncClient()).request_timeout == 30.0


# --- the cap holds in memory, not only in the verdict (briefed round 2, finding 2) ---


async def _serving(head: bytes, body_chunks):
    """Plain HTTP on 127.0.0.1 answering every request with `head` and then each
    chunk of `body_chunks` (an iterable of bytes), then closing. Records the request
    headers it saw."""
    seen = []

    async def handle(reader, writer):
        try:
            seen.append(await reader.readuntil(b"\r\n\r\n"))
            writer.write(head)
            for chunk in body_chunks():
                writer.write(chunk)
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1], seen


async def _peak_during(coro) -> tuple[object, int]:
    import tracemalloc

    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        base = tracemalloc.get_traced_memory()[0]
        result = await coro
        peak = tracemalloc.get_traced_memory()[1] - base
    finally:
        tracemalloc.stop()
    return result, peak


async def test_a_compressed_answer_is_refused_undecoded():
    """32 KiB of gzip that would decode to 32 MiB: the agent asks for identity, refuses
    the encoding, and never holds more than a fraction of the cap."""
    import gzip

    payload = gzip.compress(b'{"data": {"checkin_interval_s": 60, "jobs": [' + b" " * (32 * 1024 * 1024) + b"]}}")
    assert len(payload) < 64 * 1024
    head = (
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Encoding: gzip\r\n"
        + f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
    )
    server, port, seen = await _serving(head, lambda: [payload])
    async with server:
        async with make_http(f"http://127.0.0.1:{port}", TOKEN) as http:
            answer, peak = await _peak_during(AgentClient(http).checkin({}))
    assert peak < RESPONSE_MAX, f"held {peak} bytes"
    assert answer.kind is Kind.RETRY and "content encoding" in answer.message
    assert b"accept-encoding: identity" in seen[0].lower()


async def test_a_plain_answer_far_over_the_cap_never_fills_memory():
    """32 MiB with no declared length, streamed: reads stop at the cap, and what is
    held never passes it by more than one network read."""
    chunk = b" " * 65536
    head = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n"
    server, port, _ = await _serving(head, lambda: (chunk for _ in range(512)))
    async with server:
        async with make_http(f"http://127.0.0.1:{port}", TOKEN) as http:
            answer, peak = await _peak_during(AgentClient(http).checkin({}))
    assert peak < 2 * RESPONSE_MAX, f"held {peak} bytes"
    assert answer.kind is Kind.RETRY and str(RESPONSE_MAX) in answer.message


class Chunks(httpx.AsyncByteStream):
    """Raw chunks allocated before tracing starts, so what is measured is only what
    the agent holds of them."""

    def __init__(self, chunks):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


@pytest.mark.parametrize(
    "sizes",
    [[32 * 1024 * 1024], [600 * 1024, 600 * 1024]],
    ids=["one 32 MiB chunk", "a chunk that would cross the cap"],
)
async def test_a_raw_chunk_is_checked_before_it_is_appended(sizes):
    """The buffer never passes RESPONSE_MAX: a chunk that would take it past is not
    appended (briefed review round 3 of PR #70; real network reads are at most 64 KiB,
    so only a transport that hands over larger chunks shows the order of the check)."""
    chunks = [b"x" * n for n in sizes]
    client = _client(lambda request: httpx.Response(200, stream=Chunks(chunks)))
    answer, peak = await _peak_during(client.checkin({}))
    assert peak < RESPONSE_MAX, f"held {peak} bytes"
    assert answer.kind is Kind.RETRY and str(RESPONSE_MAX) in answer.message
