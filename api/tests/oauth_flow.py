"""What the OAuth tests share (#26): a second app with `PUBLIC_BASE_URL` set, and the
steps of a link.

The test session's own app (`app.main.app`) has no `PUBLIC_BASE_URL`, so every other
test file runs with OAuth off, as a server without the setting does. `oauth_app` is
built by the same factories around a server whose auth is the OAuth provider with the
owner-key verifier beside it, on the same database.

No HTTP route approves a link before the consent page exists (#27), so `approve` here
calls `services.oauth.approve` directly, in the consent handler's place, and the
`linking` fixture opens the gate that #27 will make the link window.
"""

import base64
import hashlib
import json
import sqlite3
from pathlib import Path

import httpx
import httpx2
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from mcp.server.auth.middleware.auth_context import auth_context_var
from sqlalchemy import select, update

from app.config import settings
from app.database import SessionLocal
from app.main import create_app
from app.mcp_server import build_mcp
from app.models import OAuthClient, OAuthGrant, OAuthToken
from app.oauth_provider import mcp_auth
from app.services import oauth

BASE = "https://ipam.example.com"
RESOURCE = BASE + "/mcp"
CALLBACK = "https://client.example.com/cb"
STATE = "state-123"
VERIFIER = "v" * 64
SPIKE_LOG = Path(__file__).resolve().parents[2] / ".agents" / "spikes" / "25" / "spike.jsonl"

oauth_mcp = build_mcp(mcp_auth(BASE))
oauth_app = create_app(oauth_mcp)

# The tools by what they do. Every tool is in exactly one list (`test_oauth_mcp.py`).
READ_TOOLS = {
    "list_sections",
    "list_device_types",
    "list_subnets",
    "get_subnet",
    "get_free_ips",
    "list_hosts",
    "get_host",
    "list_changes",
    "list_scan_findings",
}
WRITE_TOOLS = {
    "add_reserved_range",
    "delete_reserved_range",
    "create_host",
    "allocate_next_ip",
    "bulk_create_hosts",
    "bulk_delete_hosts",
    "update_host",
    "delete_host",
    "request_scan",
}


def running(app=oauth_app):
    """The app's lifespan, entered in the test body (`test_auth.running_app`): requests
    to the MCP transport need the session manager it starts."""
    return app.router.lifespan_context(app)


def http_client(app=oauth_app, **kwargs) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE, **kwargs)


def mcp_client(bearer: str, app=oauth_app) -> Client:
    """A FastMCP client on the app's `/mcp` over HTTP, presenting `bearer`."""

    def factory(**kwargs):
        kwargs.setdefault("timeout", httpx2.Timeout(10.0))
        return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), **kwargs)

    return Client(
        StreamableHttpTransport(f"{BASE}/mcp", auth=bearer, httpx_client_factory=factory)
    )


@pytest.fixture
async def web():
    async with http_client() as client:
        yield client


@pytest.fixture
def linking(monkeypatch):
    """Linking open, as it is inside a link window (#27). Without this fixture it is
    closed, as it always is in this server until the window exists."""
    monkeypatch.setattr(oauth, "linking_open", lambda: True)


@pytest.fixture
def no_ambient_token():
    """No token in the context: `conftest.in_process_mcp_is_the_owner` gives every test
    the owner's, and a test about what a grant's token may do must not be able to pass
    on it."""
    token = auth_context_var.set(None)
    yield
    auth_context_var.reset(token)


def challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


async def register(web, **metadata) -> dict:
    """A registration as the server answers it: the client, with its secret in plain
    when it is a confidential one."""
    res = await web.post(
        "/mcp/register", json={"redirect_uris": [CALLBACK], "client_name": "Example", **metadata}
    )
    assert res.status_code == 201, res.text
    return res.json()


def authorise_query(client: dict, **overrides) -> dict:
    query = {
        "client_id": client["client_id"],
        "response_type": "code",
        "code_challenge": challenge_for(VERIFIER),
        "code_challenge_method": "S256",
        "redirect_uri": client["redirect_uris"][0],
        "state": STATE,
        "scope": "ipam:read ipam:write",
        "resource": RESOURCE,
        **overrides,
    }
    return {name: value for name, value in query.items() if value is not None}


async def authorise(web, client: dict, **overrides) -> httpx.Response:
    return await web.get("/mcp/authorize", params=authorise_query(client, **overrides))


def transaction(res: httpx.Response) -> str:
    """The pending transaction an authorise answer sends the browser to consent with."""
    assert res.status_code == 302, res.text
    location = httpx.URL(res.headers["location"])
    assert str(location.copy_with(query=None)) == f"{BASE}/mcp/consent"
    assert set(location.params) == {"txn"}
    return location.params["txn"]


async def approve(txn: str, *, can_write: bool = False) -> httpx.URL:
    """The owner's approval, as the consent handler will give it (#27)."""
    async with SessionLocal() as session:
        return httpx.URL(await oauth.approve(session, txn, can_write=can_write))


async def link(web, client: dict, *, can_write: bool = False, **overrides) -> str:
    """Authorise and approve; returns the code the client is sent."""
    back = await approve(transaction(await authorise(web, client, **overrides)), can_write=can_write)
    assert back.params["state"] == overrides.get("state", STATE)
    return back.params["code"]


def _with_secret(client: dict, form: dict, secret) -> dict:
    if secret is ...:
        secret = client.get("client_secret")
    return form if secret is None else {**form, "client_secret": secret}


async def exchange(web, client: dict, code: str, *, secret=..., **overrides) -> httpx.Response:
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": client["redirect_uris"][0],
        "client_id": client["client_id"],
        "code_verifier": VERIFIER,
        "resource": RESOURCE,
        **overrides,
    }
    form = {name: value for name, value in form.items() if value is not None}
    return await web.post("/mcp/token", data=_with_secret(client, form, secret))


async def refresh(web, client: dict, token: str, *, secret=..., **extra) -> httpx.Response:
    form = {
        "grant_type": "refresh_token",
        "refresh_token": token,
        "client_id": client["client_id"],
        "resource": RESOURCE,
        **extra,
    }
    return await web.post("/mcp/token", data=_with_secret(client, form, secret))


async def linked(web, *, can_write: bool = False, **metadata) -> tuple[dict, dict]:
    """A registered client with a grant: the registration and its first token answer."""
    client = await register(web, **metadata)
    res = await exchange(web, client, await link(web, client, can_write=can_write))
    assert res.status_code == 200, res.text
    return client, res.json()


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def all_rows(model) -> list:
    async with SessionLocal() as session:
        return list((await session.scalars(select(model))).all())


async def the_grant() -> OAuthGrant:
    [grant] = await all_rows(OAuthGrant)
    return grant


async def token_row(plain: str) -> OAuthToken | None:
    async with SessionLocal() as session:
        return await session.scalar(
            select(OAuthToken).where(OAuthToken.token_hash == sha256(plain))
        )


async def set_token(plain: str, **values) -> None:
    async with SessionLocal() as session:
        await session.execute(
            update(OAuthToken).where(OAuthToken.token_hash == sha256(plain)).values(**values)
        )
        await session.commit()


async def set_grant(**values) -> None:
    async with SessionLocal() as session:
        await session.execute(update(OAuthGrant).values(**values))
        await session.commit()


async def stored_client(client: dict) -> OAuthClient:
    async with SessionLocal() as session:
        return await session.get(OAuthClient, client["client_id"])


def database_dump(*, without: tuple[str, ...] = ()) -> dict:
    """Every row of every table, to compare a database before and after."""
    con = sqlite3.connect(settings.db_path)
    try:
        tables = [name for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {
            table: sorted(repr(row) for row in con.execute(f"SELECT * FROM {table}"))
            for table in tables
            if table not in without
        }
    finally:
        con.close()


def database_text() -> str:
    """Everything the database holds, as text: what a search for a secret reads."""
    return json.dumps(database_dump())


def spike_requests() -> tuple[list[dict], list[dict]]:
    """The registrations and authorise queries of the four web clients and the
    harness's self-test, from the log of the #25 spike: each distinct one once."""
    registrations, queries = [], []
    for line in SPIKE_LOG.read_text().splitlines():
        entry = json.loads(line)
        if isinstance(entry.get("register"), dict) and entry["register"] not in registrations:
            registrations.append(entry["register"])
        if entry.get("path", "").endswith("/authorize") and entry.get("query"):
            query = {k: v for k, v in entry["query"].items() if k != "client_id"}
            if query not in queries:
                queries.append(query)
    return registrations, queries
