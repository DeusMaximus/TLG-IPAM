"""The one request-body cap (#60; v0.2 spec §5.7, brief §4.3).

Most tests drive the ASGI app directly, so they can count what it asks of the body and
send a body with no `Content-Length` in chunks of their choosing. Two spies prove the
order: the authentication check (`require_agent` / `require_api_key` through
`dependency_overrides`, FastMCP's `OwnerKeyVerifier` on `/mcp`) and
`Request.body`, through which FastAPI, the agent routes and the MCP transport all read a
body before parsing it.
"""

import ipaddress
import json

import pytest
from starlette.requests import Request

from app.auth import OwnerKeyVerifier, require_agent, require_api_key
from app.body_limit import BODY_MAX, BodyLimitMiddleware
from app.main import app

from .conftest import make_section, make_subnet
from .test_auth import INITIALIZE, INITIALIZED, MCP_ACCEPT, OWNER_BEARER, running_app
from .test_checkin import agent_api, make_agent  # noqa: F401 (agent_api is a fixture)

CHUNK = 64 * 1024
REFUSAL = {
    "error": {
        "code": "PAYLOAD_TOO_LARGE",
        "message": f"The request body may be at most {BODY_MAX} bytes",
    }
}


class Answer:
    def __init__(self):
        self.status: int | None = None
        self.headers: dict[str, str] = {}
        self.body = b""
        self.reads = 0  # how often the app asked for the body
        self.read_bytes = 0  # how much of it it was given

    def json(self):
        return json.loads(self.body)


async def asgi_post(target, path, headers, chunks, declare_length=True) -> Answer:
    """POST `chunks` to the ASGI app `target`. With `declare_length`, the request carries
    a `Content-Length` of their total; without it, `Transfer-Encoding: chunked`."""
    answer = Answer()
    chunks = list(chunks)
    pending = list(chunks)
    headers = {k.lower(): v for k, v in headers.items()}
    if declare_length:
        headers.setdefault("content-length", str(sum(len(c) for c in chunks)))
    else:
        headers["transfer-encoding"] = "chunked"

    async def receive():
        answer.reads += 1
        if not pending:
            return {"type": "http.disconnect"}
        chunk = pending.pop(0)
        answer.read_bytes += len(chunk)
        return {"type": "http.request", "body": chunk, "more_body": bool(pending)}

    async def send(message):
        if message["type"] == "http.response.start":
            answer.status = message["status"]
            answer.headers = {k.decode(): v.decode() for k, v in message["headers"]}
        elif message["type"] == "http.response.body":
            answer.body += message.get("body", b"")

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(k.encode(), v.encode() if isinstance(v, str) else v) for k, v in headers.items()],
        "client": ("127.0.0.1", 1),
        "server": ("test", 80),
    }
    await target(scope, receive, send)
    return answer


def over_the_cap() -> list[bytes]:
    """A JSON body one byte over the cap, in 64 KiB chunks."""
    body = b'{"x": "' + b"a" * (BODY_MAX - 8) + b'"}'
    assert len(body) == BODY_MAX + 1
    json.loads(body)
    return [body[i : i + CHUNK] for i in range(0, len(body), CHUNK)]


@pytest.fixture
def spies(monkeypatch):
    """Record every authentication check and every body read."""
    calls: list[str] = []

    async def agent_spy():
        calls.append("require_agent")

    async def owner_spy():
        calls.append("require_api_key")

    real_body = Request.body

    async def body_spy(self):
        calls.append("Request.body")
        return await real_body(self)

    real_verify = OwnerKeyVerifier.verify_token

    async def verify_spy(self, token):
        calls.append("OwnerKeyVerifier")
        return await real_verify(self, token)

    app.dependency_overrides[require_agent] = agent_spy
    app.dependency_overrides[require_api_key] = owner_spy
    monkeypatch.setattr(Request, "body", body_spy)
    monkeypatch.setattr(OwnerKeyVerifier, "verify_token", verify_spy)
    yield calls
    app.dependency_overrides.pop(require_agent, None)
    app.dependency_overrides.pop(require_api_key, None)


ROUTES = [
    pytest.param("/api/v1/agent/checkin", {}, id="agent check-in"),
    pytest.param("/api/v1/agent/results", {}, id="agent results"),
    pytest.param("/api/v1/hosts", {}, id="owner host create"),
    pytest.param("/api/v1/subnets/x/hosts/bulk", {}, id="owner bulk import"),
    pytest.param("/mcp", {"accept": MCP_ACCEPT}, id="mcp"),
    pytest.param("/mcp", {"accept": MCP_ACCEPT, "authorization": "Bearer test-key"}, id="mcp with the key"),
]


@pytest.mark.parametrize("path,extra", ROUTES)
async def test_a_declared_length_over_the_cap_is_refused_unread(spies, path, extra):
    async with running_app():
        res = await asgi_post(
            app, path, {"content-type": "application/json", **extra}, over_the_cap()
        )
    assert res.status == 413
    assert res.json() == REFUSAL
    assert res.headers["content-type"] == "application/json"
    assert res.headers["connection"] == "close"
    assert (res.reads, spies) == (0, [])


@pytest.mark.parametrize("path,extra", ROUTES)
async def test_a_chunked_body_over_the_cap_is_refused_before_the_app(spies, path, extra):
    async with running_app():
        res = await asgi_post(
            app,
            path,
            {"content-type": "application/json", **extra},
            over_the_cap(),
            declare_length=False,
        )
    assert res.status == 413
    assert res.json() == REFUSAL
    assert res.headers["connection"] == "close"
    assert spies == []
    # read up to the chunk that would cross the cap, and no further
    assert res.read_bytes <= BODY_MAX + CHUNK
    assert res.reads == BODY_MAX // CHUNK + 1


@pytest.mark.parametrize("path,extra", ROUTES)
async def test_a_small_declared_length_does_not_let_a_chunked_body_past(spies, path, extra):
    """Review of PR #80: `Content-Length: 2` beside `Transfer-Encoding: chunked`. h11
    frames such a body by the chunks, so a large one arrived uncounted. Any
    `Transfer-Encoding` now means the body is counted, whatever the length says."""
    async with running_app():
        res = await asgi_post(
            app,
            path,
            {
                "content-type": "application/json",
                "content-length": "2",
                "transfer-encoding": "chunked",
                **extra,
            },
            over_the_cap(),
        )
    assert res.status == 413
    assert res.json() == REFUSAL
    assert spies == []
    assert res.reads == BODY_MAX // CHUNK + 1


@pytest.mark.parametrize("name", [b"transfer-encoding", b"Transfer-Encoding"])
async def test_any_transfer_encoding_means_the_body_is_counted(name):
    """Whatever the declared length: a small one does not pass a large body, and a
    large one does not refuse a small body the chunks frame."""
    async def drain(scope, receive, send):
        await receive()
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    for declared, body, status in [(b"2", b"x" * 11, 413), (b"999", b"x" * 10, 204)]:
        answer = Answer()

        async def receive(body=body):
            answer.reads += 1
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            if message["type"] == "http.response.start":
                answer.status = message["status"]

        scope = {"type": "http", "headers": [(b"content-length", declared), (name, b"chunked")]}
        await BodyLimitMiddleware(drain, cap=10)(scope, receive, send)
        assert (declared, answer.status, answer.reads) == (declared, status, 1)


async def test_a_chunked_body_is_refused_at_the_chunk_that_crosses_the_cap(spies):
    """One chunk larger than the cap is refused whole, never appended."""
    res = await asgi_post(
        app, "/api/v1/hosts", {}, [b"{", b" " * BODY_MAX, b"}"], declare_length=False
    )
    assert (res.status, res.reads, spies) == (413, 2, [])


async def test_the_owner_key_does_not_lift_the_cap(spies):
    res = await asgi_post(app, "/api/v1/hosts", {"x-api-key": "test-key"}, over_the_cap())
    assert (res.status, res.reads, spies) == (413, 0, [])


# --- At the cap ---


def padded(document: dict, size: int = BODY_MAX) -> bytes:
    body = json.dumps(document).encode()
    assert len(body) <= size
    return body + b" " * (size - len(body))


@pytest.mark.parametrize("declare_length", [True, False], ids=["content-length", "chunked"])
async def test_a_body_at_the_cap_is_served(client, declare_length):
    section = await make_section(client)
    subnet = await make_subnet(client, section["id"])
    body = padded({"subnet_id": subnet["id"], "ip": "10.0.0.5", "hostname": "nas"})
    chunks = [body[i : i + CHUNK] for i in range(0, len(body), CHUNK)]
    res = await asgi_post(
        app,
        "/api/v1/hosts",
        {"x-api-key": "test-key", "content-type": "application/json"},
        chunks,
        declare_length=declare_length,
    )
    assert res.status == 201, res.body
    assert res.json()["data"]["hostname"] == "nas"


async def test_a_chunked_body_under_the_cap_reaches_an_agent_route_whole(client):
    """The middleware hands on a chunked body as one message; the route reads it after
    the agent token is checked, as it reads one with a declared length."""
    _agent, token = await make_agent(client)
    body = json.dumps({"protocol": 0}).encode()
    res = await asgi_post(
        app,
        "/api/v1/agent/checkin",
        {"authorization": f"Bearer {token}", "content-type": "application/json"},
        [body[:3], body[3:]],
        declare_length=False,
    )
    # the protocol check, which reads the parsed body
    assert res.status == 409, res.body
    assert res.json()["error"]["code"] == "PROTOCOL_MISMATCH"


def largest_batch() -> list[dict]:
    """1,000 rows, the most one import takes, with every length-bounded field at its
    maximum and each `owner` character four bytes in UTF-8."""
    label = "a" * 63
    dns253 = ".".join([label, label, label, "a" * 61])
    addresses = list(ipaddress.ip_network("10.0.0.0/22").hosts())[:1000]
    return [
        {
            "ip": str(address),
            "hostname": dns253,
            "domain": dns253,
            "mac": f"aa:bb:cc:dd:{i >> 8:02x}:{i & 255:02x}",
            "device_type": "other",
            "status": "reserved",
            "owner": "\U0001f600" * 120,
        }
        for i, address in enumerate(addresses)
    ]


def encoded(document) -> bytes:
    """As the UI (`JSON.stringify`) and FastMCP's client send JSON: compact, UTF-8."""
    return json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()


async def big_subnet(client) -> dict:
    section = await make_section(client)
    return await make_subnet(client, section["id"], cidr="10.0.0.0/22", name="big")


async def test_the_largest_ui_batch_is_accepted(client):
    subnet = await big_subnet(client)
    body = encoded({"hosts": largest_batch()})
    assert 1_100_000 < len(body) < BODY_MAX
    res = await client.post(
        f"/api/v1/subnets/{subnet['id']}/hosts/bulk",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["data"]["created"] == 1000


def _sse_data(res) -> dict:
    if res.headers["content-type"].startswith("application/json"):
        return res.json()
    lines = [line for line in res.text.splitlines() if line.startswith("data:")]
    return json.loads(lines[-1][len("data:") :])


async def test_the_largest_mcp_bulk_call_is_served(client, anon_client):
    """`/mcp` is under the cap: the largest tool call, `bulk_create_hosts` with 1,000
    rows, still fits."""
    subnet = await big_subnet(client)
    headers = {**OWNER_BEARER, "Accept": MCP_ACCEPT}
    async with running_app():
        res = await anon_client.post("/mcp", json=INITIALIZE, headers=headers)
        assert res.status_code == 200, res.text
        headers["mcp-session-id"] = res.headers["mcp-session-id"]
        res = await anon_client.post("/mcp", json=INITIALIZED, headers=headers)
        assert res.status_code == 202, res.text
        call = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "bulk_create_hosts",
                "arguments": {"subnet_id": subnet["id"], "hosts": largest_batch()},
            },
        }
        body = encoded(call)
        assert 1_100_000 < len(body) < BODY_MAX
        res = await anon_client.post(
            "/mcp", content=body, headers={**headers, "Content-Type": "application/json"}
        )
    assert res.status_code == 200, res.text
    result = _sse_data(res)["result"]
    assert not result.get("isError"), result
    assert result["structuredContent"]["created"] == 1000


# --- What the middleware passes through ---


async def test_a_request_with_no_body_passes_through(client):
    res = await client.get("/health")
    assert res.status_code == 200


async def test_a_declared_length_at_the_cap_is_not_read_by_the_middleware():
    """Under the cap, a declared body is the application's to read, when it chooses."""
    seen = []

    async def inner(scope, receive, send):
        seen.append("called")
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    res = await asgi_post(BodyLimitMiddleware(inner), "/", {}, [b"x" * BODY_MAX])
    assert (res.status, res.reads, seen) == (204, 0, ["called"])


@pytest.mark.parametrize(
    "lengths",
    [[b"12abc"], [b"-1"], [b"\xb2"], [b"10", b"x"]],
    ids=["not a number", "negative", "a non-ASCII digit", "one of two not a number"],
)
async def test_an_unusable_content_length_is_counted_as_if_absent(lengths):
    """No server should deliver such a request, uvicorn does not: if one did, the body
    is counted like a chunked one, never trusted."""
    called = []

    async def inner(scope, receive, send):
        called.append(True)

    target = BodyLimitMiddleware(inner, cap=10)
    answer = Answer()
    pending = [b"x" * 6, b"x" * 6]

    async def receive():
        answer.reads += 1
        return {"type": "http.request", "body": pending.pop(0), "more_body": bool(pending)}

    async def send(message):
        if message["type"] == "http.response.start":
            answer.status = message["status"]

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [(b"content-length", v) for v in lengths],
    }
    await target(scope, receive, send)
    assert (answer.status, answer.reads, called) == (413, 2, [])


async def test_the_largest_of_repeated_lengths_counts():
    async def inner(scope, receive, send):
        raise AssertionError("not reached")

    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        raise AssertionError("nothing is read")

    scope = {
        "type": "http",
        "headers": [(b"content-length", b"5"), (b"Content-Length", b"11")],
    }
    await BodyLimitMiddleware(inner, cap=10)(scope, receive, send)
    assert sent[0]["status"] == 413


async def test_a_client_gone_mid_body_is_not_answered():
    called, sent = [], []

    async def inner(scope, receive, send):
        called.append(True)

    messages = [
        {"type": "http.request", "body": b"abc", "more_body": True},
        {"type": "http.disconnect"},
    ]

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    await BodyLimitMiddleware(inner, cap=10)({"type": "http", "headers": []}, receive, send)
    assert (called, sent) == ([], [])


async def test_after_the_replayed_body_the_servers_own_messages_follow():
    """A route that waits for a disconnect (a streamed response) still gets it."""
    got = []

    async def inner(scope, receive, send):
        got.append(await receive())
        got.append(await receive())

    messages = [
        {"type": "http.request", "body": b"ab", "more_body": True},
        {"type": "http.request", "body": b"c", "more_body": False},
        {"type": "http.disconnect"},
    ]

    async def receive():
        return messages.pop(0)

    async def send(message):
        pass

    await BodyLimitMiddleware(inner, cap=10)({"type": "http", "headers": []}, receive, send)
    assert got == [
        {"type": "http.request", "body": b"abc", "more_body": False},
        {"type": "http.disconnect"},
    ]


async def test_other_scopes_pass_through():
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["type"])

    async def nothing():
        raise AssertionError("not read")

    await BodyLimitMiddleware(inner, cap=1)({"type": "lifespan"}, nothing, nothing)
    assert seen == ["lifespan"]


def test_the_cap_wraps_the_whole_app():
    """Outermost of the app's own middleware, so it runs before the `/mcp` rewrite,
    routing and every dependency."""
    assert app.user_middleware[0].cls is BodyLimitMiddleware
