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
MCP_ACCEPT = "application/json, text/event-stream"


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


async def test_mcp_mount_requires_key(anon_client):
    res = await anon_client.post("/mcp/", json={})
    assert res.status_code == 401


async def test_mcp_reachable_without_trailing_slash(anon_client):
    # mcp-remote and Claude Desktop request the bare /mcp path; a 401 here proves the
    # request reached the MCP auth layer instead of falling through to the SPA
    res = await anon_client.post("/mcp", json={})
    assert res.status_code == 401


def running_app():
    """The app's lifespan, entered in the test body: MCP requests that pass auth
    need the session manager it starts. (Not a fixture: the lifespan's cancel
    scope must be exited by the task that entered it.)"""
    return app.router.lifespan_context(app)


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
            headers={"Authorization": "Bearer test-key", "Accept": MCP_ACCEPT},
        )
    assert res.status_code == 200, res.text
    assert "mcp-session-id" in res.headers


@pytest.mark.parametrize("path", MCP_PATHS)
async def test_mcp_missing_bearer_is_401(anon_client, path):
    res = await anon_client.post(path, json=INITIALIZE, headers={"Accept": MCP_ACCEPT})
    assert res.status_code == 401
    assert res.headers["www-authenticate"] == "Bearer"
    assert res.content == b""


@pytest.mark.parametrize("path", MCP_PATHS)
async def test_mcp_x_api_key_alone_is_401(anon_client, path):
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
    res = await anon_client.post(
        path, json=INITIALIZE, headers={"Authorization": authorization, "Accept": MCP_ACCEPT}
    )
    assert res.status_code == 401
    assert res.headers["www-authenticate"].startswith('Bearer error="invalid_token"')
    assert res.json()["error"] == "invalid_token"


async def test_mcp_other_auth_scheme_is_401(anon_client):
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


async def test_owner_key_is_compared_in_constant_time(anon_client, monkeypatch):
    # The spy patches the hmac module itself, so assert on the arguments, not a count
    calls = []
    real = auth.hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(auth.hmac, "compare_digest", spy)

    await anon_client.get("/api/v1/sections", headers={"X-API-Key": "rest-nope"})
    assert (b"rest-nope", b"test-key") in calls

    await anon_client.post("/mcp", json={}, headers={"Authorization": "Bearer mcp-nope"})
    assert (b"mcp-nope", b"test-key") in calls
