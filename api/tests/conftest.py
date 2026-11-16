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

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.database import SessionLocal, _configure_connection, engine, get_session
from app.main import app
from app.models import Base
from app.services import jobs
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
def unlimited_checkins(monkeypatch):
    """Tests check in back to back. The check-in rate limit is lifted here (a negative
    gap is never inside); `test_checkin.py` restores `jobs.DEFAULT_CHECKIN_GAP` where it
    tests the limit itself."""
    monkeypatch.setattr(jobs, "MIN_CHECKIN_GAP", -timedelta(hours=1))


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


async def make_host(client, subnet_id, ip, **extra) -> dict:
    res = await client.post("/api/v1/hosts", json={"subnet_id": subnet_id, "ip": ip, **extra})
    assert res.status_code == 201, res.text
    return res.json()["data"]
