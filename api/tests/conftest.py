import os
import shutil
import sqlite3
import tempfile
from contextlib import asynccontextmanager, contextmanager
from datetime import timedelta

# Each pytest session gets its own directory, so two sessions on one machine
# (agents in separate worktrees, say) never share a database. The environment
# must be set before `app` is imported, hence module level and not a fixture.
_SESSION_DIR = tempfile.mkdtemp(prefix="tlg-ipam-test-")

os.environ["API_KEY"] = "test-key"
os.environ["DB_PATH"] = os.path.join(_SESSION_DIR, "ipam.db")
os.environ["STATIC_DIR"] = os.path.join(_SESSION_DIR, "no-static")
# OAuth off, whatever the developer's environment holds: the session's app is a server
# without `PUBLIC_BASE_URL`. `tests/oauth_flow.py` builds the one with it.
os.environ["PUBLIC_BASE_URL"] = ""

import pytest
from httpx import ASGITransport, AsyncClient
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from sqlalchemy import event, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import OWNER_SCOPES
from app.config import settings
from app.database import SessionLocal, _configure_connection, engine, get_session
from app.main import app
from app.models import Base, Subnet
from app.services import agents, enrolment, jobs, link_window, oauth
from app.services.device_types import seed_defaults


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_SESSION_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
async def fresh_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as session:
        await seed_defaults(session)
    yield


@pytest.fixture(autouse=True)
def in_process_mcp_is_the_owner():
    """An in-process MCP client (`Client(mcp)`) passes no HTTP layer, so it carries no
    token, and since #26 a caller with no token is not shown a write tool. The tests
    that use one are the owner's: this gives them the token the owner-key verifier
    returns (`auth.OwnerKeyVerifier`), where the MCP SDK's bearer middleware would put
    it. A request over HTTP carries its own, which FastMCP reads first."""
    from fastmcp.server.auth import AccessToken

    owner = AccessToken(token="test-key", client_id="owner", scopes=list(OWNER_SCOPES))
    token = auth_context_var.set(AuthenticatedUser(owner))
    yield
    auth_context_var.reset(token)


@pytest.fixture(autouse=True)
def unlimited_checkins(monkeypatch):
    """Tests check in back to back. The check-in rate limit is lifted here (a negative
    gap is never inside); `test_checkin.py` restores `jobs.DEFAULT_CHECKIN_GAP` where it
    tests the limit itself."""
    monkeypatch.setattr(jobs, "MIN_CHECKIN_GAP", -timedelta(hours=1))


@pytest.fixture(autouse=True)
def fresh_enrol_throttle():
    """The enrolment throttle is process memory (`services/enrolment.throttle`): each test
    starts with it empty, so one test's refused attempts never refuse another's. So are
    the enrolment refusals the Agents page is shown (`services/agents.enrol_refusals`)."""
    enrolment.throttle.reset()
    agents.enrol_refusals.reset()
    yield
    enrolment.throttle.reset()
    agents.enrol_refusals.reset()


@pytest.fixture(autouse=True)
def fresh_pending_authorisations():
    """Pending authorise transactions and the link window are process memory
    (`services/oauth.pending`, `services/link_window`): each test starts with none."""
    oauth.pending.reset()
    link_window.reset()
    yield
    oauth.pending.reset()
    link_window.reset()


async def enrol(client, enrol_token: str, **body):
    """`POST /api/v1/agent/enrol` with the enrolment token as the bearer."""
    return await client.post(
        "/api/v1/agent/enrol", json=body, headers={"Authorization": f"Bearer {enrol_token}"}
    )


async def make_enrolled_agent(client, name: str) -> tuple[dict, str]:
    """An agent created over the owner route and enrolled with its enrolment token, as
    the agent does on first start (#22). Returns the agent as `GET /agents/{id}` gives
    it, now `active`, and its plain agent token."""
    res = await client.post("/api/v1/agents", json={"name": name})
    assert res.status_code == 201, res.text
    created = res.json()["data"]
    res = await enrol(client, created["enrol_token"])
    assert res.status_code == 200, res.text
    token = res.json()["data"]["agent_token"]
    res = await client.get(f"/api/v1/agents/{created['id']}")
    return res.json()["data"], token


@contextmanager
def write_lock():
    """Another connection holding SQLite's write lock (`BEGIN IMMEDIATE`) until the block
    ends. Readers carry on (WAL); a writer waits out its busy timeout, then fails."""
    con = sqlite3.connect(settings.db_path, isolation_level=None)
    try:
        con.execute("BEGIN IMMEDIATE")
        yield
    finally:
        con.rollback()
        con.close()


@asynccontextmanager
async def app_database(*, busy_timeout_ms: int = 5000, read_only: bool = False):
    """The app's requests served from their own engine on the same database file,
    configured as the app's (`database._configure_connection`) except for a shorter
    busy timeout, or opened read-only. A real SQLite refusal, without waiting out the
    app's 5-second timeout in every test that needs one."""
    path = f"file:{settings.db_path}?mode=ro&uri=true" if read_only else settings.db_path
    other = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(other.sync_engine, "connect")
    def _configure(dbapi_connection, record):
        _configure_connection(dbapi_connection, record)
        cursor = dbapi_connection.cursor()
        cursor.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        cursor.close()

    sessions = async_sessionmaker(other, expire_on_commit=False)

    async def session():
        async with sessions() as s:
            yield s

    app.dependency_overrides[get_session] = session
    try:
        yield sessions
    finally:
        app.dependency_overrides.pop(get_session, None)
        await other.dispose()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers={"X-API-Key": "test-key"}
    ) as c:
        yield c


@pytest.fixture
async def anon_client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def make_section(client, name="Homelab") -> dict:
    res = await client.post("/api/v1/sections", json={"name": name})
    assert res.status_code == 201, res.text
    return res.json()["data"]


async def make_subnet(client, section_id, cidr="10.0.0.0/24", name="lab", **extra) -> dict:
    res = await client.post(
        "/api/v1/subnets", json={"section_id": section_id, "cidr": cidr, "name": name, **extra}
    )
    assert res.status_code == 201, res.text
    return res.json()["data"]


# The spelling of 192.168.240.0/20 the review of PR #95 stored (#97): a prefix length
# padded with 4,298 zeroes, which `ipaddress.ip_network` takes.
PADDED_CIDR = "192.168.240.0/" + "0" * 4298 + "20"


async def store_cidr_as(subnet_id: str, cidr: str) -> None:
    """Write a subnet's `cidr` column as a row stored before #97 may hold it: in the
    spelling it was typed, which the subnet service no longer stores."""
    async with SessionLocal() as session:
        await session.execute(update(Subnet).where(Subnet.id == subnet_id).values(cidr=cidr))
        await session.commit()


async def make_host(client, subnet_id, ip, **extra) -> dict:
    res = await client.post("/api/v1/hosts", json={"subnet_id": subnet_id, "ip": ip, **extra})
    assert res.status_code == 201, res.text
    return res.json()["data"]
