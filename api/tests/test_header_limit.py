"""The limits below the app (#132; brief §4.3): a request's head, a connection waiting
for one, and a client that stops reading.

Nothing in the app can see a head that is still arriving, so most tests here send raw
bytes to a real uvicorn on loopback, started as the image starts it (`python -m
app.serve`, httptools through `header_limit.HeaderLimitProtocol`). `image` runs the
limits as shipped; `quick` lowers the two times to `QUICK_S` so that tests of what
happens around them take seconds, and serves files large enough to fill a send buffer.
The tests of how bytes are counted across reads also drive the protocol in process,
with a transport of their own, because the kernel decides how a write is split into
reads and the tests have to choose.
"""

import asyncio
import json
import os
import random
import re
import secrets
import socket
import subprocess
import sys
import time

import pytest
import uvicorn
from uvicorn.server import ServerState

from app import header_limit, serve
from app.header_limit import _PIECE, HEADER_MAX, HEADER_TIMEOUT_S, STALL_TIMEOUT_S, HeaderLimitProtocol

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
    assert STALL_TIMEOUT_S == 60
    assert serve.KEEP_ALIVE_S == 5
    assert serve.PINNED == {
        "workers": 1,
        "reload": False,
        "http": HeaderLimitProtocol,
        "http2": False,
        "ws": "none",
        "timeout_keep_alive": serve.KEEP_ALIVE_S,
    }


def _image_settings(monkeypatch, env: dict, argv: list[str]) -> tuple[str, dict]:
    """What the image's command hands to uvicorn's `run`, with `env` set."""
    for name in [n for n in list(os.environ) if n.upper().startswith("UVICORN_")]:
        monkeypatch.delenv(name)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    calls = []
    monkeypatch.setattr(serve.cli, "run", lambda app, **settings: calls.append((app, settings)))
    with pytest.raises(SystemExit) as done:
        serve.main(argv)
    assert done.value.code == 0
    [(app, settings)] = calls
    return app, settings


# Settings uvicorn's command line takes from the environment, as the old `CMD` did, and
# what each is taken as
HONOURED = {
    "UVICORN_FORWARDED_ALLOW_IPS": ("forwarded_allow_ips", "192.0.2.1"),
    "UVICORN_PROXY_HEADERS": ("proxy_headers", False),
    "UVICORN_ROOT_PATH": ("root_path", "/prefix"),
    "UVICORN_LOG_LEVEL": ("log_level", "error"),
    "UVICORN_ACCESS_LOG": ("access_log", False),
    "UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN": ("timeout_graceful_shutdown", 3),
    "UVICORN_LIFESPAN": ("lifespan", "off"),
}
SPELT = {"UVICORN_PROXY_HEADERS": "false", "UVICORN_ACCESS_LOG": "false", "UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN": "3"}
# Settings the environment would change on uvicorn's command line, which the image pins
OVERRIDDEN = {
    "UVICORN_WORKERS": "4",
    "UVICORN_RELOAD": "true",
    "UVICORN_HTTP": "h11",
    "UVICORN_HTTP2": "true",
    "UVICORN_WS": "websockets",
    "UVICORN_TIMEOUT_KEEP_ALIVE": "99",
    "UVICORN_HOST": "192.0.2.1",
    "UVICORN_PORT": "9999",
}


def test_the_image_command_takes_uvicorn_settings_from_the_environment_but_not_the_pinned_ones(monkeypatch):
    env = {name: SPELT.get(name, str(value)) for name, (_, value) in HONOURED.items()}
    app, settings = _image_settings(monkeypatch, {**env, **OVERRIDDEN}, [])
    assert app == "app.main:app"
    assert {k: settings[k] for k in serve.PINNED} == serve.PINNED
    assert (settings["host"], settings["port"]) == ("0.0.0.0", 8080)
    for name, (setting, value) in HONOURED.items():
        assert settings[setting] == value, name


def test_the_image_command_without_settings_runs_uvicorns_defaults(monkeypatch):
    app, settings = _image_settings(monkeypatch, {}, ["--host", "127.0.0.1", "--port", "0"])
    assert (settings["host"], settings["port"]) == ("127.0.0.1", 0)
    assert settings["forwarded_allow_ips"] is None  # uvicorn then reads FORWARDED_ALLOW_IPS, else 127.0.0.1
    assert settings["proxy_headers"] is True
    assert settings["root_path"] == ""
    assert settings["limit_concurrency"] is None


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


# Files `quick` serves: one larger than any socket buffers on the path can take, so a
# client that does not read fills the server's send buffer, and one a slow reader takes
LARGE = 64 * 1024 * 1024
MEDIUM = 4 * 1024 * 1024


@pytest.fixture(scope="module")
def quick(tmp_path_factory):
    """The image's server with the time for a head and the time a client may stop
    reading lowered to `QUICK_S`, serving `large.bin` and `medium.bin` as the SPA's
    files."""
    key = secrets.token_hex(32)
    tmp = tmp_path_factory.mktemp("quick")
    static = tmp / "static"
    static.mkdir()
    (static / "index.html").write_text("<!doctype html><title>t</title>")
    with open(static / "large.bin", "wb") as large:
        large.truncate(LARGE)
    (static / "medium.bin").write_bytes(b"m" * MEDIUM)
    code = (
        "import app.header_limit, app.serve\n"
        f"app.header_limit.HEADER_TIMEOUT_S = {QUICK_S}\n"
        f"app.header_limit.STALL_TIMEOUT_S = {QUICK_S}\n"
        "app.serve.main(['--host', '127.0.0.1', '--port', '0'])\n"
    )
    env = _child_env(tmp, key)
    env["STATIC_DIR"] = str(static)
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=API_DIR,
        env=env,
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


SPELLINGS = {
    "usual": (b"X-Pad: ", b""),  # one space after the colon
    "compact": (b"X-Pad:", b""),  # none
    "padded": (b"X-Pad: \t  ", b" \t "),  # whitespace on both sides, which the parser drops
}


def spelt_head(size: int, spelling: str = "usual", close: bool = True, start: bytes = b"GET /health") -> bytes:
    """A complete head of exactly `size` bytes, its padding field in `spelling`."""
    before, after = SPELLINGS[spelling]
    line = start + b" HTTP/1.1\r\nHost: t\r\n" + (b"Connection: close\r\n" if close else b"")
    pad = size - len(line) - len(before) - len(after) - len(b"\r\n\r\n")
    head = line + before + b"a" * pad + after + b"\r\n\r\n"
    assert len(head) == size
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


@pytest.mark.parametrize("spelling", SPELLINGS)
@pytest.mark.parametrize("pieces", [1, 3, 400], ids=["at once", "in 3 writes", "in 400 writes"])
def test_on_the_image_a_complete_head_is_held_to_the_limit_however_it_arrives(image, pieces, spelling):
    """The limit is on the head's bytes as sent, whatever its spelling: 65,536 is
    served and 65,537 refused, sent at once or in pieces."""
    for size, status in ((HEADER_MAX, 200), (HEADER_MAX + 1, 431)):
        request = spelt_head(size, spelling)
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
    with the time for a head already stopped: the connection is dropped instead. The
    head is complete at the byte past the limit, so it is the complete head's check
    that refuses it."""
    session = mcp_session(quick)
    with connect(quick) as sock:
        sock.sendall(event_stream(session))
        head = still_open(sock, 0.5)
        assert head.startswith(b"HTTP/1.1 200"), head[:300]
        sock.sendall(spelt_head(HEADER_MAX + 1))  # finished one byte past the limit
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


@pytest.mark.parametrize("first", [64, 4096, 4097, 8192], ids=lambda n: f"after a {n}-byte head")
@pytest.mark.parametrize("pieces", [1, 400], ids=["at once", "in 400 writes"])
def test_on_the_image_a_later_head_on_a_reused_connection_is_held_to_the_same_limit(image, first, pieces):
    """A head counts from its own first byte, whatever came before it on the
    connection: after a first request answered, 65,536 is served and 65,537 refused."""
    for size, status in ((HEADER_MAX, 200), (HEADER_MAX + 1, 431)):
        with connect(image) as sock:
            sock.sendall(spelt_head(first, close=False))
            assert status_of(one_response(sock)) == 200
            request = spelt_head(size)
            step = -(-size // pieces)
            for i in range(0, size, step):
                sock.sendall(request[i : i + step])
            received, _ = until_closed(sock, 5)
        assert status_of(received) == status, (first, size, received[:300])


def test_on_the_image_heads_after_a_request_in_the_same_write_are_counted_exactly(image):
    """One write: a request with a declared body, then a head of exactly the limit. The
    first request's end is a cut, so the second head is counted from its first byte and
    both are served."""
    body = json.dumps({"name": "same-write"}).encode()
    first = raw_post(
        "/api/v1/sections",
        str(len(body)).encode(),
        f"X-API-Key: {image.key}\r\nContent-Type: application/json\r\n".encode(),
        body,
    )
    with connect(image) as sock:
        sock.sendall(first + spelt_head(HEADER_MAX))
        received, _ = until_closed(sock, 5)
    assert received.startswith(b"HTTP/1.1 201 "), received[:300]
    assert received.count(b"HTTP/1.1 200 OK") == 1, received[-300:]


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



# --- A client that stops reading (`STALL_TIMEOUT_S`) ---


def small_receiver(server) -> socket.socket:
    """A connection whose receive buffer is small, so what it does not read stays in the
    server's send buffer."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    sock.settimeout(10)
    sock.connect(("127.0.0.1", server.port))
    return sock


def test_a_client_that_stops_reading_a_response_is_dropped_after_the_stall_time(quick):
    """A static file needs no credential. The client asks for one larger than the
    socket buffers can take and reads nothing: uvicorn pauses the response with its send
    buffer full, and the connection is dropped `STALL_TIMEOUT_S` later."""
    with small_receiver(quick) as sock:
        sock.sendall(b"GET /large.bin HTTP/1.1\r\nHost: t\r\n\r\n")
        time.sleep(QUICK_S + 2)  # read nothing
        received, _ = until_closed(sock, 10)
    assert received.startswith(b"HTTP/1.1 200"), received[:200]
    assert len(received) < LARGE  # the rest was never sent
    assert health(quick) == 200


def test_a_client_that_reads_slowly_gets_the_whole_response(quick):
    """Pauses shorter than the stall time, the send buffer full during each: the time
    starts again whenever the client takes some, so the whole file arrives."""
    with small_receiver(quick) as sock:
        sock.sendall(b"GET /medium.bin HTTP/1.1\r\nHost: t\r\nConnection: close\r\n\r\n")
        received = b""
        for quarter in range(1, 4):
            while len(received) < quarter * MEDIUM // 4:
                chunk = sock.recv(65536)
                assert chunk, f"dropped after {len(received)} bytes"
                received += chunk
            time.sleep(QUICK_S * 0.75)
        rest, _ = until_closed(sock, 10)
    response = received + rest
    head, _, body = response.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 200"), head[:200]
    assert len(body) == MEDIUM


# --- In process: how a connection's bytes are counted, queued and dropped ---
#
# The protocol driven directly, with a transport that records what is written, so a
# test chooses exactly how the bytes are split into reads. `Connection` starts it as
# uvicorn would, on an app that answers each request with its own path.


class FakeTransport:
    def __init__(self):
        self.written = bytearray()
        self.closed = False
        self.aborted = False
        self.reading = True
        self.buffered = 0  # what `get_write_buffer_size` reports

    def get_extra_info(self, name, default=None):
        return {"sockname": ("127.0.0.1", 8080), "peername": ("127.0.0.1", 50000)}.get(name, default)

    def write(self, data):
        self.written += data

    def close(self):
        self.closed = True

    def abort(self):
        self.closed = self.aborted = True

    def is_closing(self):
        return self.closed

    def pause_reading(self):
        self.reading = False

    def resume_reading(self):
        self.reading = True

    def get_write_buffer_size(self):
        return self.buffered


class Connection:
    def __init__(self, app=None):
        self.held: list[asyncio.Event] = []
        self.seen: list[str] = []
        config = uvicorn.Config(
            app=app or self.answer,
            http=HeaderLimitProtocol,
            ws="none",
            lifespan="off",
            interface="asgi3",
            log_config=None,
        )
        config.load()
        self.protocol = HeaderLimitProtocol(config=config, server_state=ServerState(), app_state={})
        self.transport = FakeTransport()
        self.protocol.connection_made(self.transport)

    async def answer(self, scope, receive, send):
        more = True
        while more:
            message = await receive()
            more = message.get("more_body", False)
        self.seen.append(scope["path"])
        if scope["path"] == "/hold":
            event = asyncio.Event()
            self.held.append(event)
            await event.wait()
        body = scope["path"].encode()
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})

    def feed(self, data: bytes, reads: list[int] | None = None) -> None:
        """Hand `data` to the protocol in reads of the sizes given (the rest in one)."""
        offset = 0
        for size in reads or []:
            self.protocol.data_received(data[offset : offset + size])
            offset += size
        if offset < len(data):
            self.protocol.data_received(data[offset:])

    async def settle(self) -> None:
        for _ in range(50):
            await asyncio.sleep(0)

    def statuses(self) -> list[int]:
        return [int(status) for status in re.findall(rb"HTTP/1\.1 (\d{3}) ", bytes(self.transport.written))]

    def close(self) -> None:
        if not self.protocol._lost:
            self.protocol.connection_lost(None)


def splits(size: int, seed: int) -> list[int]:
    rng = random.Random(seed)
    reads = []
    while sum(reads) < size:
        reads.append(rng.choice([1, 2, 3, 7, 100, 4095, 4096, 4097, 9000]))
    return reads


@pytest.mark.parametrize("spelling", SPELLINGS)
@pytest.mark.parametrize("seed", [None, 1, 2, 3], ids=["one read", "split 1", "split 2", "split 3"])
async def test_a_head_is_counted_by_its_bytes_however_the_reads_fall(spelling, seed):
    for size, status in ((HEADER_MAX, 200), (HEADER_MAX + 1, 431)):
        conn = Connection()
        head = spelt_head(size, spelling)
        conn.feed(head, None if seed is None else splits(size, seed))
        await conn.settle()
        assert conn.statuses() == [status], (spelling, seed, size)
        assert conn.seen == (["/health"] if status == 200 else [])
        conn.close()


@pytest.mark.parametrize("first", [64, 4095, 4096, 4097, 8192, HEADER_MAX - 30])
@pytest.mark.parametrize("body", [0, 1, 4096, 10000], ids=lambda n: f"body {n}")
@pytest.mark.parametrize("together", [True, False], ids=["one read", "separate reads"])
async def test_a_head_after_a_request_with_a_declared_body_counts_from_its_own_first_byte(first, body, together):
    """The end of a declared body and the end of a head are cuts, so whatever came
    before, a head of exactly the limit is served. In one read, the second request is
    queued behind the first; in separate reads the first is answered first."""
    first_head = spelt_head(first, close=False, start=b"POST /first")
    first_request = first_head[:-2] + f"Content-Length: {body}\r\n\r\n".encode() + b"b" * body
    conn = Connection()
    if together:
        conn.feed(first_request + spelt_head(HEADER_MAX))
    else:
        conn.feed(first_request)
        await conn.settle()
        conn.feed(spelt_head(HEADER_MAX))
    await conn.settle()
    assert conn.seen == ["/first", "/health"], (first, body, together)
    assert conn.statuses() == [200, 200]
    conn.close()


@pytest.mark.parametrize("together", [True, False], ids=["one read", "separate reads"])
async def test_a_head_one_byte_over_after_a_request_is_refused(together):
    conn = Connection()
    first = spelt_head(4096, close=False, start=b"GET /first")
    if together:
        conn.feed(first + spelt_head(HEADER_MAX + 1))
        await conn.settle()
        # still answering the first: dropped with nothing written for the second
        assert conn.transport.aborted
        assert 431 not in conn.statuses()
    else:
        conn.feed(first)
        await conn.settle()
        conn.feed(spelt_head(HEADER_MAX + 1))
        await conn.settle()
        assert conn.statuses() == [200, 431]
    conn.close()


async def test_after_a_chunked_body_a_head_in_the_same_read_is_counted_at_most_a_piece_over():
    """The one case not counted exactly (module docstring): the chunked body's end is no
    cut, so the next head is counted from the start of the piece that body ended in."""
    chunked = b"POST /first HTTP/1.1\r\nHost: t\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n"
    conn = Connection()
    conn.feed(chunked + spelt_head(HEADER_MAX - _PIECE))
    await conn.settle()
    assert conn.statuses() == [200, 200]
    conn.close()
    conn = Connection()
    conn.feed(chunked + spelt_head(HEADER_MAX + 1, close=False))
    await conn.settle()
    assert 200 in conn.statuses() and conn.seen == ["/first"]
    assert conn.transport.closed
    conn.close()


async def test_while_a_request_is_queued_nothing_more_is_parsed():
    """100 requests pipelined in one read behind one that is held: one is queued, the
    rest wait unparsed, and all are answered in turn once the first is released."""
    conn = Connection()
    requests = b"GET /hold HTTP/1.1\r\nHost: t\r\n\r\n" + b"".join(
        f"GET /r{i} HTTP/1.1\r\nHost: t\r\n\r\n".encode() for i in range(100)
    )
    conn.feed(requests)
    await conn.settle()
    assert conn.seen == ["/hold"]
    assert len(conn.protocol.pipeline) == 1
    assert len(conn.protocol._unparsed) == len(requests) - len(b"GET /hold HTTP/1.1\r\nHost: t\r\n\r\n") - len(b"GET /r0 HTTP/1.1\r\nHost: t\r\n\r\n")
    more = b"".join(f"GET /s{i} HTTP/1.1\r\nHost: t\r\n\r\n".encode() for i in range(10))
    conn.transport.reading = True  # as uvicorn resumes it when the held request asks for its body
    conn.feed(more)  # a read that arrived anyway: kept unparsed, and reading paused again
    assert not conn.transport.reading
    assert len(conn.protocol.pipeline) == 1
    conn.held[0].set()
    for _ in range(20):
        await conn.settle()
    assert conn.seen == ["/hold", *(f"/r{i}" for i in range(100)), *(f"/s{i}" for i in range(10))]
    assert conn.transport.reading  # resumed once nothing waits
    assert conn.statuses() == [200] * 111
    conn.close()


async def test_a_send_buffer_left_full_drops_the_connection(monkeypatch):
    monkeypatch.setattr(header_limit, "STALL_TIMEOUT_S", 0.05)
    conn = Connection()
    conn.protocol.pause_writing()
    await asyncio.sleep(0.03)
    conn.protocol.resume_writing()  # the client took some: the time stops
    await asyncio.sleep(0.1)
    assert not conn.transport.aborted
    conn.protocol.pause_writing()
    await asyncio.sleep(0.1)
    assert conn.transport.aborted
    conn.close()


async def test_a_closed_connection_whose_client_takes_nothing_is_dropped(monkeypatch):
    """`close()` waits for what the transport holds to be sent; the client taking some of
    it in each period keeps it, taking none drops it."""
    monkeypatch.setattr(header_limit, "STALL_TIMEOUT_S", 0.05)
    conn = Connection()
    conn.transport.buffered = 3000
    conn.protocol.transport.close()  # as uvicorn closes it, through the protocol's transport
    await asyncio.sleep(0.03)
    conn.transport.buffered = 2000  # some was taken in the first period
    await asyncio.sleep(0.04)
    assert not conn.transport.aborted
    await asyncio.sleep(0.08)  # none in the second
    assert conn.transport.aborted
    conn.close()


async def test_a_connection_gone_before_its_times_has_none_left(monkeypatch):
    monkeypatch.setattr(header_limit, "STALL_TIMEOUT_S", 0.05)
    monkeypatch.setattr(header_limit, "HEADER_TIMEOUT_S", 0.05)
    conn = Connection()
    conn.transport.buffered = 10
    conn.protocol.pause_writing()
    conn.protocol.transport.close()
    conn.protocol.connection_lost(None)
    assert conn.protocol._head_deadline is None and conn.protocol._stall is None and conn.protocol._flush_deadline is None
    await asyncio.sleep(0.1)
    assert not conn.transport.aborted


async def test_the_time_for_a_head_stops_when_it_is_complete(monkeypatch):
    """A response that takes longer than the time for a head is not cut by it, and the
    time starts again once it has been answered."""
    monkeypatch.setattr(header_limit, "HEADER_TIMEOUT_S", 0.05)
    conn = Connection()
    conn.feed(b"GET /hold HTTP/1.1\r\nHost: t\r\n\r\n")
    await asyncio.sleep(0.15)
    assert not conn.transport.closed
    conn.held[0].set()
    await conn.settle()
    assert conn.statuses() == [200]
    await asyncio.sleep(0.1)
    assert conn.transport.closed and not conn.transport.aborted
    conn.close()


async def test_a_declared_body_is_not_timed_as_a_head(monkeypatch):
    monkeypatch.setattr(header_limit, "HEADER_TIMEOUT_S", 0.05)
    conn = Connection()
    conn.feed(b"POST /slow HTTP/1.1\r\nHost: t\r\nContent-Length: 4\r\n\r\nab")
    await asyncio.sleep(0.15)
    assert not conn.transport.closed
    conn.feed(b"cd")
    await conn.settle()
    assert conn.statuses() == [200]
    conn.close()


@pytest.mark.parametrize("extra", [1, 3000, 70000])
async def test_an_unfinished_head_is_refused_with_one_byte_past_the_limit_parsed(monkeypatch, extra):
    """However much arrives in one read, the parser is handed at most one byte past the
    limit of an unfinished head before it is refused."""
    parsed = []
    original = header_limit.HttpToolsProtocol.data_received
    monkeypatch.setattr(
        header_limit.HttpToolsProtocol, "data_received", lambda self, data: (parsed.append(len(data)), original(self, data))
    )
    conn = Connection()
    start = b"GET /health HTTP/1.1\r\nHost: t\r\nX-Pad: "
    conn.feed(start + b"a" * (HEADER_MAX + extra - len(start)))
    await conn.settle()
    assert conn.statuses() == [431]
    assert sum(parsed) == HEADER_MAX + 1
    conn.close()



def test_on_the_image_an_upgrade_post_with_a_body_is_answered_at_once(image):
    """httptools hands on nothing after an `Upgrade` request's head, its body included,
    on plain uvicorn as here: the route sees no body and answers `422` at once. What
    matters below the app is that the request is answered, not left to the body
    deadline."""
    body = json.dumps({"name": "upgrade"}).encode()
    request = raw_post(
        "/api/v1/sections",
        str(len(body)).encode(),
        f"X-API-Key: {image.key}\r\nContent-Type: application/json\r\nConnection: Upgrade\r\nUpgrade: h2c\r\n".encode(),
        body,
    )
    started = time.monotonic()
    with connect(image) as sock:
        sock.sendall(request)
        answer = one_response(sock)
    assert time.monotonic() - started < 3
    assert status_of(answer) == 422, answer[:300]
