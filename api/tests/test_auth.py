import ast
import hmac
import inspect
import json
import textwrap

import anyio
import httpx2
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from app import auth
from app.auth import OWNER_SCOPES, OwnerKeyVerifier
from app.main import app

from .test_mcp import EXPECTED_TOOLS

MCP_PATHS = ("/mcp", "/mcp/")
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}
TOOLS_LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
MCP_ACCEPT = "application/json, text/event-stream"
OWNER_BEARER = {"Authorization": "Bearer test-key"}


async def test_health_needs_no_auth(anon_client):
    res = await anon_client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


async def test_missing_key_is_401(anon_client):
    res = await anon_client.get("/api/v1/sections")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "UNAUTHORIZED"


async def test_wrong_key_is_403(anon_client):
    res = await anon_client.get("/api/v1/sections", headers={"X-API-Key": "nope"})
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "FORBIDDEN"


async def test_non_ascii_key_is_403_not_500(anon_client):
    # hmac.compare_digest raises TypeError on a non-ASCII str; the check compares bytes
    res = await anon_client.get("/api/v1/sections", headers={"X-API-Key": "café".encode("latin-1")})
    assert res.status_code == 403


async def test_rest_does_not_accept_bearer(anon_client):
    res = await anon_client.get("/api/v1/sections", headers={"Authorization": "Bearer test-key"})
    assert res.status_code == 401


def running_app():
    """The app's lifespan, entered in the test body: MCP requests that pass auth
    need the session manager it starts. (Not a fixture: the lifespan's cancel
    scope must be exited by the task that entered it.)

    The refusal tests enter it too, so that a check which wrongly lets a request
    through fails on the status assertion, not on an uninitialised session manager.
    """
    return app.router.lifespan_context(app)


async def test_mcp_mount_requires_key(anon_client):
    async with running_app():
        res = await anon_client.post("/mcp/", json={})
    assert res.status_code == 401


async def test_mcp_reachable_without_trailing_slash(anon_client):
    # mcp-remote and Claude Desktop request the bare /mcp path; a 401 here proves the
    # request reached the MCP auth layer instead of falling through to the SPA
    async with running_app():
        res = await anon_client.post("/mcp", json={})
    assert res.status_code == 401


def _asgi_client_factory(**kwargs):
    kwargs.setdefault("timeout", httpx2.Timeout(10.0))
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), **kwargs)


@pytest.mark.parametrize("path", MCP_PATHS)
async def test_mcp_accepts_owner_key_as_bearer(path):
    transport = StreamableHttpTransport(
        f"http://test{path}", auth="test-key", httpx_client_factory=_asgi_client_factory
    )
    async with running_app(), Client(transport) as mcp_client:
        tools = await mcp_client.list_tools()
        assert {t.name for t in tools} == EXPECTED_TOOLS
        result = await mcp_client.call_tool("list_sections", {})
        assert result.data == {"sections": []}


@pytest.mark.parametrize("path", MCP_PATHS)
async def test_mcp_bearer_initialize_is_answered_in_place(anon_client, path):
    # Neither path redirects: the client above follows redirects, this one does not
    async with running_app():
        res = await anon_client.post(
            path,
            json=INITIALIZE,
            headers={**OWNER_BEARER, "Accept": MCP_ACCEPT},
        )
    assert res.status_code == 200, res.text
    assert "mcp-session-id" in res.headers


@pytest.mark.parametrize("path", MCP_PATHS)
async def test_mcp_missing_bearer_is_401(anon_client, path):
    async with running_app():
        res = await anon_client.post(path, json=INITIALIZE, headers={"Accept": MCP_ACCEPT})
    assert res.status_code == 401
    assert res.headers["www-authenticate"] == "Bearer"
    assert res.content == b""


@pytest.mark.parametrize("path", MCP_PATHS)
async def test_mcp_x_api_key_alone_is_401(anon_client, path):
    async with running_app():
        res = await anon_client.post(
            path, json=INITIALIZE, headers={"X-API-Key": "test-key", "Accept": MCP_ACCEPT}
        )
    assert res.status_code == 401
    assert res.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "authorization",
    [
        "Bearer nope",
        "Bearer test-key-and-more",
        "Bearer ",
        b"Bearer caf\xe9",
    ],
)
@pytest.mark.parametrize("path", MCP_PATHS)
async def test_mcp_wrong_bearer_is_401(anon_client, path, authorization):
    async with running_app():
        res = await anon_client.post(
            path, json=INITIALIZE, headers={"Authorization": authorization, "Accept": MCP_ACCEPT}
        )
    assert res.status_code == 401
    assert res.headers["www-authenticate"].startswith('Bearer error="invalid_token"')
    assert res.json()["error"] == "invalid_token"


async def test_mcp_other_auth_scheme_is_401(anon_client):
    async with running_app():
        res = await anon_client.post(
            "/mcp", json=INITIALIZE, headers={"Authorization": "Basic dGVzdC1rZXk=", "Accept": MCP_ACCEPT}
        )
    assert res.status_code == 401
    assert res.headers["www-authenticate"] == "Bearer"


async def test_owner_key_verifier_grants_both_scopes():
    verifier = OwnerKeyVerifier()
    token = await verifier.verify_token("test-key")
    assert token is not None
    assert set(token.scopes) == {"ipam:read", "ipam:write"} == set(OWNER_SCOPES)
    assert await verifier.verify_token("nope") is None
    assert await verifier.verify_token("") is None


# --- The owner-key comparison -------------------------------------------------------
#
# Every surface that takes the owner key must reach a decision through
# hmac.compare_digest, for accepted keys as well as refused ones, and the digest's
# answer must be the decision. A call spy shows the digest ran; it cannot show that
# nothing compared the key before it, which is what the source check is for.

SURFACES = ("rest", *MCP_PATHS)
ACCEPTED = {"rest": 200, "/mcp": 200, "/mcp/": 200}
REFUSED = {"rest": 403, "/mcp": 401, "/mcp/": 401}


class DigestStandIn:
    """Takes the place of `hmac` inside `app.auth`. Records each compare_digest
    call and returns the real answer, or `answer` when one is forced."""

    def __init__(self, answer=None):
        self.calls = []
        self.answer = answer

    def compare_digest(self, a, b):
        self.calls.append((a, b))
        real = hmac.compare_digest(a, b)
        return real if self.answer is None else self.answer


async def _present_key(anon_client, surface, key):
    """Present `key` where `surface` takes the owner key; return the response."""
    if surface == "rest":
        return await anon_client.get("/api/v1/sections", headers={"X-API-Key": key})
    async with running_app():
        return await anon_client.post(
            surface,
            json=INITIALIZE,
            headers={"Authorization": f"Bearer {key}", "Accept": MCP_ACCEPT},
        )


@pytest.mark.parametrize("key", ["test-key", "test-kez", "nope"])
@pytest.mark.parametrize("surface", SURFACES)
async def test_owner_key_is_compared_in_constant_time(anon_client, monkeypatch, surface, key):
    digest = DigestStandIn()
    monkeypatch.setattr(auth, "hmac", digest)

    res = await _present_key(anon_client, surface, key)

    expected = ACCEPTED if key == "test-key" else REFUSED
    assert res.status_code == expected[surface]
    assert (key.encode(), b"test-key") in digest.calls, (
        f"{key!r} on {surface} was decided without hmac.compare_digest"
    )


@pytest.mark.parametrize(
    ("answer", "key", "outcome"),
    [(False, "test-key", REFUSED), (True, "nope", ACCEPTED)],
    ids=["digest-refuses-the-owner-key", "digest-accepts-a-wrong-key"],
)
@pytest.mark.parametrize("surface", SURFACES)
async def test_digest_answer_decides_acceptance(anon_client, monkeypatch, surface, answer, key, outcome):
    # Forcing the digest's answer flips the outcome: nothing else decides
    monkeypatch.setattr(auth, "hmac", DigestStandIn(answer=answer))

    res = await _present_key(anon_client, surface, key)

    assert res.status_code == outcome[surface], (
        f"hmac.compare_digest answered {answer} for {key!r} on {surface}, "
        f"but the response was {res.status_code}: something besides the digest decided"
    )


def test_is_owner_key_has_no_equality_shortcut():
    source = textwrap.dedent(inspect.getsource(auth.is_owner_key))
    shortcuts = [
        ast.unparse(node)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Compare)
        and any(isinstance(op, (ast.Eq, ast.NotEq, ast.In, ast.NotIn)) for op in node.ops)
    ]
    assert shortcuts == [], f"is_owner_key compares outside hmac.compare_digest: {shortcuts}"


# --- Session reuse ------------------------------------------------------------------
#
# A session id from an authenticated initialize is not a credential: every request
# on the session needs the bearer again.

REPLAY_CREDENTIALS = {
    "no-credential": {},
    "wrong-bearer": {"Authorization": "Bearer nope"},
    "x-api-key-only": {"X-API-Key": "test-key"},
}
REPLAY_REQUESTS = {
    "GET": ({"Accept": "text/event-stream"}, b""),
    "POST": (
        {"Accept": MCP_ACCEPT, "Content-Type": "application/json"},
        json.dumps(TOOLS_LIST).encode(),
    ),
    "DELETE": ({}, b""),
}


async def _open_session(anon_client, path):
    res = await anon_client.post(path, json=INITIALIZE, headers={**OWNER_BEARER, "Accept": MCP_ACCEPT})
    assert res.status_code == 200, res.text
    session = {"mcp-session-id": res.headers["mcp-session-id"]}
    res = await anon_client.post(
        path, json=INITIALIZED, headers={**OWNER_BEARER, **session, "Accept": MCP_ACCEPT}
    )
    assert res.status_code == 202, res.text
    return session


async def _response_start(method, path, headers, body=b""):
    """Send one request straight to the app and return the status and headers it
    starts its response with, without reading the body.

    An accepted GET opens an SSE stream that never ends, and the ASGI transports
    buffer the whole body, so a check that wrongly accepted the request would hang
    the test instead of failing it on the status.
    """
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in {"Host": "test", **headers}.items()],
        "client": ("127.0.0.1", 50000),
        "server": ("test", 80),
    }
    request_sent = False
    start = {}

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        await anyio.sleep_forever()

    with anyio.fail_after(10):
        async with anyio.create_task_group() as tg:

            async def send(message):
                if message["type"] == "http.response.start":
                    start["status"] = message["status"]
                    start["headers"] = {k.decode().lower(): v.decode() for k, v in message["headers"]}
                    tg.cancel_scope.cancel()

            tg.start_soon(app, scope, receive, send)
    return start["status"], start["headers"]


@pytest.mark.parametrize("credential", REPLAY_CREDENTIALS)
@pytest.mark.parametrize("method", REPLAY_REQUESTS)
@pytest.mark.parametrize("path", MCP_PATHS)
async def test_mcp_session_id_without_owner_bearer_is_401(anon_client, path, method, credential):
    request_headers, body = REPLAY_REQUESTS[method]
    async with running_app():
        session = await _open_session(anon_client, path)
        status, headers = await _response_start(
            method, path, {**request_headers, **session, **REPLAY_CREDENTIALS[credential]}, body
        )
        # Control: the session is live, and the refused request did not end it
        control = await anon_client.post(
            path, json=TOOLS_LIST, headers={**OWNER_BEARER, **session, "Accept": MCP_ACCEPT}
        )

    # Asserted outside the lifespan, so a failure is reported here and not
    # re-raised through the lifespan's task group
    assert status == 401, f"{method} {path} on an open session with {credential} was not refused"
    assert headers["www-authenticate"].startswith("Bearer")
    assert control.status_code == 200, control.text
    assert "list_sections" in control.text
