"""The limits on a request's head and on a connection waiting for one (#132; brief §4.3).

Nothing in the app can see a head that is still arriving, so every test here sends raw
bytes to a real uvicorn on loopback, started as the image starts it (`python -m
app.serve`, httptools through `header_limit.HeaderLimitProtocol`). `image` runs the
limits as shipped; `quick` lowers the time to `QUICK_S` so that tests of what happens
around the deadline take seconds.
"""

import json
import secrets
import socket
import subprocess
import sys
import time

import pytest

from app import serve
from app.header_limit import HEADER_MAX, HEADER_TIMEOUT_S, HeaderLimitProtocol, _head_size

from .test_auth import INITIALIZE, INITIALIZED, MCP_ACCEPT
from .test_body_limit import Served, exchange, raw_post
from .test_startup import API_DIR, START_DEADLINE, _child_env, _image, _read_stderr, _stop, _wait_until_serving

QUICK_S = 2
TOO_LARGE = {
    "error": {
        "code": "HEADERS_TOO_LARGE",
        "message": f"The request line and headers may be at most {HEADER_MAX} bytes",
    }
}


def test_the_limits_are_the_documented_ones():
    assert HEADER_MAX == 64 * 1024
    assert HEADER_TIMEOUT_S == 10
    assert serve.KEEP_ALIVE_S == 5


def test_the_image_command_runs_uvicorn_with_the_limits(monkeypatch):
    calls = []
    monkeypatch.setattr(serve.uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    serve.main([])
    assert calls == [
        (
            ("app.main:app",),
            {
                "host": "0.0.0.0",
                "port": 8080,
                "workers": 1,
                "http": HeaderLimitProtocol,
                "ws": "none",
                "timeout_keep_alive": serve.KEEP_ALIVE_S,
            },
        )
    ]


def test_a_head_measures_what_was_sent_in_the_usual_spelling():
    head = b"POST /api/v1/hosts?x=1 HTTP/1.1\r\nhost: 127.0.0.1\r\nx-api-key: k\r\n\r\n"
    assert _head_size(b"POST", b"/api/v1/hosts?x=1", [(b"host", b"127.0.0.1"), (b"x-api-key", b"k")]) == len(head)


def _started(proc):
    lines, reader = _read_stderr(proc)
    try:
        port = _wait_until_serving(lines, time.monotonic() + START_DEADLINE)
    except BaseException:
        _stop(proc, reader)
        raise
    return lines, reader, port


def _finish(proc, lines, reader):
    _stop(proc, reader)
    log = []
    while (line := lines.get_nowait()) is not None:
        log.append(line)
    assert not any("Traceback" in line or "Exception in ASGI" in line for line in log), "".join(log)


@pytest.fixture(scope="module")
def image(tmp_path_factory):
    key = secrets.token_hex(32)
    proc = _image(tmp_path_factory.mktemp("image"), key)
    lines, reader, port = _started(proc)
    yield Served("httptools", port, key)
    _finish(proc, lines, reader)


@pytest.fixture(scope="module")
def quick(tmp_path_factory):
    """The image's server with the time for a head lowered to `QUICK_S`."""
    key = secrets.token_hex(32)
    code = (
        "import app.header_limit, app.serve\n"
        f"app.header_limit.HEADER_TIMEOUT_S = {QUICK_S}\n"
        "app.serve.main(['--host', '127.0.0.1', '--port', '0'])\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=API_DIR,
        env=_child_env(tmp_path_factory.mktemp("quick"), key),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    lines, reader, port = _started(proc)
    yield Served("httptools", port, key)
    _finish(proc, lines, reader)


def connect(server) -> socket.socket:
    return socket.create_connection(("127.0.0.1", server.port), timeout=10)


def until_closed(sock, within: float) -> tuple[bytes, float]:
    """Every byte the server sends until it closes the connection, and when it did (a
    reset counts as closing), counted from the call; fails if it is still open after
    `within` seconds."""
    started = time.monotonic()
    received = b""
    while True:
        sock.settimeout(max(started + within - time.monotonic(), 0.01))
        try:
            chunk = sock.recv(65536)
        except TimeoutError:
            pytest.fail(f"the connection was still open after {within} s, having sent {received[:200]!r}")
        except ConnectionResetError:
            chunk = b""
        if not chunk:
            return received, time.monotonic() - started
        received += chunk


def still_open(sock, seconds: float) -> bytes:
    """Wait `seconds`, failing if the server closes the connection; what it sent."""
    end = time.monotonic() + seconds
    received = b""
    while (left := end - time.monotonic()) > 0:
        sock.settimeout(left)
        try:
            chunk = sock.recv(65536)
        except TimeoutError:
            break
        assert chunk, f"the server closed the connection, having sent {received[:200]!r}"
        received += chunk
    return received


def one_response(sock) -> bytes:
    """One whole response read off a connection that stays open: its head, then as
    many body bytes as its `content-length` says, however the segments fall."""
    received = b""
    while b"\r\n\r\n" not in received:
        chunk = sock.recv(65536)
        assert chunk, f"the server closed the connection, having sent {received[:200]!r}"
        received += chunk
    head, _, body = received.partition(b"\r\n\r\n")
    fields = dict(line.split(b":", 1) for line in head.split(b"\r\n")[1:])
    length = int({k.strip().lower(): v for k, v in fields.items()}[b"content-length"])
    while len(body) < length:
        chunk = sock.recv(65536)
        assert chunk, "the server closed the connection inside a response"
        body += chunk
    assert len(body) == length, "more than one response arrived"
    return head + b"\r\n\r\n" + body


def status_of(answer: bytes) -> int:
    return int(answer.split(b" ", 2)[1])


def health(server) -> int:
    return exchange(server.port, b"GET /health HTTP/1.1\r\nHost: t\r\nConnection: close\r\n\r\n")[0]


def head_of(size: int) -> bytes:
    """A complete `GET /health` whose head is `size` bytes."""
    start = b"GET /health HTTP/1.1\r\nHost: t\r\nConnection: close\r\nX-Pad: "
    pad = size - len(start) - len(b"\r\n\r\n")
    return start + b"a" * pad + b"\r\n\r\n"


def compact_head_over_the_limit() -> bytes:
    """A complete head that measures one byte over the limit and arrives 1 KiB under it:
    its fields have no space after the colon, which the measure counts. Only the check
    of a complete head can refuse it, since fewer than `HEADER_MAX` bytes arrive (with
    room for the piece a previous request ended in, counted toward this head)."""
    fields = [(f"x-{i}".encode(), b"a") for i in range(1024)]
    pad = HEADER_MAX + 1 - _head_size(b"GET", b"/health", [*fields, (b"x-pad", b"")])
    fields.append((b"x-pad", b"a" * pad))
    assert _head_size(b"GET", b"/health", fields) == HEADER_MAX + 1
    head = b"GET /health HTTP/1.1\r\n" + b"".join(n + b":" + v + b"\r\n" for n, v in fields) + b"\r\n"
    assert len(head) == HEADER_MAX + 1 - len(fields)
    return head


# --- The time (`HEADER_TIMEOUT_S`) ---


def test_on_the_image_a_connection_that_sends_nothing_is_closed_at_the_deadline(image):
    with connect(image) as sock:
        received, after = until_closed(sock, HEADER_TIMEOUT_S + 5)
    assert received == b""  # closed without an answer
    assert HEADER_TIMEOUT_S - 0.5 <= after <= HEADER_TIMEOUT_S + 3, after


def test_an_unfinished_head_sent_a_byte_at_a_time_is_closed_at_the_deadline(quick):
    """Each byte would cancel uvicorn's keep-alive timeout; the time for a head runs on."""
    with connect(quick) as sock:
        started = time.monotonic()
        sock.sendall(b"GET /health HTTP/1.1\r\nHost: t\r\n")
        try:
            while time.monotonic() - started < QUICK_S + 5:
                time.sleep(0.25)
                sock.sendall(b"X")
        except (BrokenPipeError, ConnectionResetError):
            pass
        received, _ = until_closed(sock, 5)
    assert received == b""
    assert QUICK_S - 0.5 <= time.monotonic() - started <= QUICK_S + 5


def test_after_a_response_the_next_head_has_the_same_time(quick):
    with connect(quick) as sock:
        sock.sendall(b"GET /health HTTP/1.1\r\nHost: t\r\n\r\n")
        assert status_of(one_response(sock)) == 200
        answered = time.monotonic()
        trickle = iter(b"GET /health HTTP/1.1\r\nX-Pad: ")
        try:
            while time.monotonic() - answered < QUICK_S + 5:
                sock.sendall(bytes([next(trickle, ord("a"))]))
                time.sleep(0.25)
        except (BrokenPipeError, ConnectionResetError):
            pass
        received, _ = until_closed(sock, 5)
    assert received == b""
    assert QUICK_S - 0.5 <= time.monotonic() - answered <= QUICK_S + 5


def test_a_head_that_completes_in_time_is_served_after_a_slow_start(quick):
    request = b"GET /health HTTP/1.1\r\nHost: t\r\nConnection: close\r\n\r\n"
    with connect(quick) as sock:
        for i in range(0, len(request), 10):
            sock.sendall(request[i : i + 10])
            time.sleep(QUICK_S / 2 / (len(request) / 10))
        received, _ = until_closed(sock, 5)
    assert status_of(received) == 200


def test_a_body_is_timed_by_the_body_deadline_not_the_heads(quick):
    """The head is complete, so the time for it has stopped: a body trickled for longer
    than it is served (the body has `body_limit.BODY_TIMEOUT_S`)."""
    body = json.dumps({"name": "slow"}).encode()
    request = raw_post(
        "/api/v1/sections",
        str(len(body)).encode(),
        f"X-API-Key: {quick.key}\r\nContent-Type: application/json\r\nConnection: close\r\n".encode(),
    )
    with connect(quick) as sock:
        sock.sendall(request)
        started = time.monotonic()
        for i in range(len(body)):
            time.sleep((QUICK_S + 1.5) / len(body))
            sock.sendall(body[i : i + 1])
        assert time.monotonic() - started > QUICK_S + 1
        received, _ = until_closed(sock, 10)
    assert status_of(received) == 201, received[:300]


def mcp_session(server) -> dict:
    """A new MCP session, initialised; the headers a request on it carries."""
    status, headers, answer, _ = exchange(
        server.port,
        raw_post(
            "/mcp",
            str(len(json.dumps(INITIALIZE))).encode(),
            f"Authorization: Bearer {server.key}\r\nAccept: {MCP_ACCEPT}\r\n"
            "Content-Type: application/json\r\nConnection: close\r\n".encode(),
            json.dumps(INITIALIZE).encode(),
        ),
    )
    assert status == 200, answer
    session = {"Authorization": f"Bearer {server.key}", "Mcp-Session-Id": headers["mcp-session-id"]}
    note = json.dumps(INITIALIZED).encode()
    fields = "".join(f"{k}: {v}\r\n" for k, v in session.items())
    status, _, answer, _ = exchange(
        server.port,
        raw_post(
            "/mcp",
            str(len(note)).encode(),
            f"{fields}Accept: {MCP_ACCEPT}\r\nContent-Type: application/json\r\nConnection: close\r\n".encode(),
            note,
        ),
    )
    assert status == 202, answer
    return session


def event_stream(session) -> bytes:
    fields = "".join(f"{k}: {v}\r\n" for k, v in session.items())
    return f"GET /mcp HTTP/1.1\r\nHost: t\r\n{fields}Accept: text/event-stream\r\n\r\n".encode()


def test_an_mcp_event_stream_held_open_is_not_timed(quick):
    """The stream is a response in progress: it stays open past the time for a head,
    and the session's calls are served beside it."""
    session = mcp_session(quick)
    with connect(quick) as stream:
        stream.sendall(event_stream(session))
        head = still_open(stream, 2 * QUICK_S + 1)
        assert head.startswith(b"HTTP/1.1 200"), head[:300]
        assert b"text/event-stream" in head.lower()
        call = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).encode()
        fields = "".join(f"{k}: {v}\r\n" for k, v in session.items())
        status, _, answer, _ = exchange(
            quick.port,
            raw_post(
                "/mcp",
                str(len(call)).encode(),
                f"{fields}Accept: {MCP_ACCEPT}\r\nContent-Type: application/json\r\nConnection: close\r\n".encode(),
                call,
            ),
        )
        assert status == 200, answer
        assert b"list_subnets" in answer
        still_open(stream, 1)


def test_pipelined_requests_are_served_in_turn(quick):
    request = b"GET /health HTTP/1.1\r\nHost: t\r\n\r\n"
    with connect(quick) as sock:
        sock.sendall(request + request.replace(b"\r\n\r\n", b"\r\nConnection: close\r\n\r\n"))
        received, _ = until_closed(sock, 5)
    assert received.count(b"HTTP/1.1 200 OK") == 2


# --- The size (`HEADER_MAX`) ---


def test_on_the_image_an_unfinished_head_past_the_limit_is_refused(image):
    """Exactly one byte more than the limit, and no end to the head: the server has read
    all of it when it answers, so its close sends no reset that could drop the answer."""
    start = b"GET /health HTTP/1.1\r\nHost: t\r\nX-Pad: "
    with connect(image) as sock:
        sock.sendall(start + b"a" * (HEADER_MAX + 1 - len(start)))
        received, after = until_closed(sock, 5)
    assert after < 3  # at once, not at the deadline
    head, _, body = received.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 431 ")
    assert b"connection: close" in head.lower()
    assert b"content-type: application/json" in head.lower()
    assert json.loads(body) == TOO_LARGE
    assert health(image) == 200


def test_an_unfinished_head_at_the_limit_waits_for_the_deadline(quick):
    start = b"GET /health HTTP/1.1\r\nHost: t\r\nX-Pad: "
    with connect(quick) as sock:
        sock.sendall(start + b"a" * (HEADER_MAX - len(start)))
        assert still_open(sock, QUICK_S / 2) == b""
        received, _ = until_closed(sock, QUICK_S + 3)
    assert received == b""


@pytest.mark.parametrize("pieces", [1, 3, 400], ids=["at once", "in 3 writes", "in 400 writes"])
def test_on_the_image_a_complete_head_is_held_to_the_limit_however_it_arrives(image, pieces):
    for size, status in ((HEADER_MAX, 200), (HEADER_MAX + 1, 431)):
        request = head_of(size)
        assert len(request) == size
        step = -(-size // pieces)
        with connect(image) as sock:
            for i in range(0, size, step):
                sock.sendall(request[i : i + step])
                if pieces > 1:
                    time.sleep(0.002)
            received, _ = until_closed(sock, 5)
        assert status_of(received) == status, (size, received[:300])
        if status == 431:
            assert json.loads(received.partition(b"\r\n\r\n")[2]) == TOO_LARGE


def test_a_pipelined_head_past_the_limit_behind_a_stream_closes_the_connection(quick):
    """Another request is being answered (an event stream), so no refusal is written
    into its response: the connection is closed."""
    session = mcp_session(quick)
    with connect(quick) as sock:
        sock.sendall(event_stream(session))
        head = still_open(sock, 0.5)
        assert head.startswith(b"HTTP/1.1 200"), head[:300]
        start = b"GET /health HTTP/1.1\r\nHost: t\r\nX-Pad: "
        sock.sendall(start + b"a" * (HEADER_MAX + 1 - len(start)))
        received, after = until_closed(sock, 5)
    assert after < 3
    assert b"431" not in received and b"HEADERS_TOO_LARGE" not in received


def test_a_complete_head_past_the_limit_pipelined_behind_a_stream_closes_the_connection(quick):
    """A refusal queued behind an event stream would wait as long as the stream does,
    with the time for a head already stopped: the connection is closed instead."""
    session = mcp_session(quick)
    with connect(quick) as sock:
        sock.sendall(event_stream(session))
        head = still_open(sock, 0.5)
        assert head.startswith(b"HTTP/1.1 200"), head[:300]
        request = compact_head_over_the_limit()
        sock.sendall(request)
        received, after = until_closed(sock, 2 * QUICK_S + 3)
    assert after < QUICK_S  # at once, not at any deadline
    assert b"431" not in received and b"HEADERS_TOO_LARGE" not in received


def test_on_the_image_a_head_that_follows_a_request_in_the_same_read_is_counted(image):
    """One write: a whole request, then more than the limit of the next one's head with
    no end. The bytes after the first request ends count toward the second head, so the
    connection is refused at once rather than left to the deadline. If the first request
    is still being answered when the second is refused, nothing is written; if it was
    answered first (the write arrived in more than one read), its answer is followed by
    the refusal."""
    start = b"GET /health HTTP/1.1\r\nHost: t\r\nX-Pad: "
    request = b"GET /health HTTP/1.1\r\nHost: t\r\n\r\n"
    with connect(image) as sock:
        sock.sendall(request + start + b"a" * (HEADER_MAX + 1 - len(start)))
        received, after = until_closed(sock, HEADER_TIMEOUT_S + 5)
    assert after < 3, after
    if received:
        assert received.startswith(b"HTTP/1.1 200 OK"), received[:300]
        assert received.endswith(json.dumps(TOO_LARGE).encode()), received[-300:]
        assert received.count(b"HTTP/1.1 431 ") == 1


def test_many_unfinished_heads_past_the_limit_are_each_refused(image):
    socks = [connect(image) for _ in range(50)]
    start = b"GET /health HTTP/1.1\r\nHost: t\r\nX-Pad: "
    try:
        for sock in socks:
            sock.sendall(start + b"a" * (HEADER_MAX + 1 - len(start)))
        assert health(image) == 200
        answers = [until_closed(sock, 10)[0] for sock in socks]
    finally:
        for sock in socks:
            sock.close()
    assert all(a.startswith(b"HTTP/1.1 431 ") for a in answers)


def test_an_upgrade_request_is_served_as_http(image):
    """The image turns uvicorn's WebSocket support off (`ws="none"`)."""
    status, _, body, _ = exchange(
        image.port,
        b"GET /health HTTP/1.1\r\nHost: t\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n"
        b"Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n",
    )
    assert status == 200, body

