import os
import shutil
import tempfile

# Each pytest session gets its own directory, so two sessions on one machine
# (agents in separate worktrees, say) never share a database. The environment
# must be set before `app` is imported, hence module level and not a fixture.
_SESSION_DIR = tempfile.mkdtemp(prefix="tlg-ipam-test-")

os.environ["API_KEY"] = "test-key"
os.environ["DB_PATH"] = os.path.join(_SESSION_DIR, "ipam.db")
os.environ["STATIC_DIR"] = os.path.join(_SESSION_DIR, "no-static")

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import SessionLocal, engine
from app.main import app
from app.models import Base
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
