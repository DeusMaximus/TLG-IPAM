import sqlite3
from pathlib import Path

from fastmcp import Client
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import SessionLocal, init_models
from app.mcp_server import mcp
from app.schemas import ChangeOut
from app.services import changes as changes_svc

from .conftest import make_host, make_section, make_subnet

SCHEMA_BEFORE_ACTOR = Path(__file__).parent / "data" / "schema_before_actor.sql"


async def test_writes_are_logged_with_source(client):
    section = await make_section(client)
    subnet = await make_subnet(client, section["id"])
    host = await make_host(client, subnet["id"], "10.0.0.5", hostname="pbs-01")
    await client.delete(f"/api/v1/hosts/{host['id']}")

    res = await client.get("/api/v1/changes")
    body = res.json()
    assert body["total"] == 4
    newest = body["data"][0]
    assert newest["action"] == "delete"
    assert newest["entity_type"] == "host"
    assert "pbs-01" in newest["summary"]
    assert newest["before"]["ip"] == "10.0.0.5"
    assert all(c["source"] == "api" for c in body["data"])


async def test_ui_source_detected(client):
    res = await client.post(
        "/api/v1/sections", json={"name": "LAN"}, headers={"X-TLG-Client": "ui"}
    )
    assert res.status_code == 201
    change = (await client.get("/api/v1/changes")).json()["data"][0]
    assert change["source"] == "ui"


async def test_update_records_before_and_after(client):
    section = await make_section(client)
    await client.put(f"/api/v1/sections/{section['id']}", json={"name": "Renamed"})
    change = (await client.get("/api/v1/changes")).json()["data"][0]
    assert change["before"]["name"] == "Homelab"
    assert change["after"]["name"] == "Renamed"


async def test_entity_type_filter(client):
    section = await make_section(client)
    await make_subnet(client, section["id"])
    res = await client.get("/api/v1/changes?entity_type=subnet")
    body = res.json()
    assert body["total"] == 1
    assert body["data"][0]["entity_type"] == "subnet"


# --- changes.actor (spec §4.3) ---


async def test_existing_write_paths_record_null_actor(client):
    """Every write path that exists today, over REST and MCP, records actor = null."""

    async def ok(request, status=(200, 201, 204)):
        res = await request
        assert res.status_code in status, res.text
        return res.json()["data"] if res.status_code != 204 else None

    async def tool(name, args):
        result = await mcp_client.call_tool(name, args)
        assert not result.is_error, result.content
        return result.data

    # REST: every write route (17 audit-log call sites in the services)
    section = await make_section(client)
    await ok(client.put(f"/api/v1/sections/{section['id']}", json={"name": "Renamed"}))
    subnet = await make_subnet(client, section["id"], "10.0.0.0/24")
    await ok(client.put(f"/api/v1/subnets/{subnet['id']}", json={"name": "lab-2"}))
    block = await ok(
        client.post(
            f"/api/v1/subnets/{subnet['id']}/reserved",
            json={"start_ip": "10.0.0.200", "end_ip": "10.0.0.210"},
        )
    )
    await ok(client.delete(f"/api/v1/subnets/{subnet['id']}/reserved/{block['id']}"))
    host = await make_host(client, subnet["id"], "10.0.0.5", hostname="pbs-01")
    await ok(client.put(f"/api/v1/hosts/{host['id']}", json={"notes": "rack 2"}))
    await ok(client.delete(f"/api/v1/hosts/{host['id']}"))
    allocated = await ok(
        client.post(f"/api/v1/subnets/{subnet['id']}/next-free", json={"hostname": "nas"})
    )
    await ok(
        client.post(
            f"/api/v1/subnets/{subnet['id']}/hosts/bulk",
            json={"hosts": [{"ip": "10.0.0.20"}, {"ip": "10.0.0.21"}]},
        )
    )
    await ok(client.post("/api/v1/hosts/bulk-delete", json={"host_ids": [allocated["id"]]}))
    device_type = await ok(
        client.post("/api/v1/device-types", json={"name": "camera"}, headers={"X-TLG-Client": "ui"})
    )
    await ok(client.put(f"/api/v1/device-types/{device_type['id']}", json={"icon": "camera"}))
    await ok(client.delete(f"/api/v1/device-types/{device_type['id']}"))
    spare_section = await make_section(client, "Spare")
    spare_subnet = await make_subnet(client, spare_section["id"], "10.9.0.0/24")
    await ok(client.delete(f"/api/v1/subnets/{spare_subnet['id']}"))
    await ok(client.delete(f"/api/v1/sections/{spare_section['id']}"))

    # MCP: every write tool
    async with Client(mcp) as mcp_client:
        created = (await tool("create_host", {"subnet_id": subnet["id"], "ip": "10.0.0.30"}))["host"]
        await tool("update_host", {"host_id": created["id"], "owner": "lab"})
        await tool("delete_host", {"host_id": created["id"]})
        mcp_allocated = (
            await tool("allocate_next_ip", {"subnet_id": subnet["id"], "hostname": "printer"})
        )["host"]
        await tool("bulk_create_hosts", {"subnet_id": subnet["id"], "hosts": [{"ip": "10.0.0.40"}]})
        await tool("bulk_delete_hosts", {"host_ids": [mcp_allocated["id"]]})
        mcp_block = (
            await tool(
                "add_reserved_range",
                {"subnet_id": subnet["id"], "start_ip": "10.0.0.220", "end_ip": "10.0.0.230"},
            )
        )["reserved_range"]
        await tool("delete_reserved_range", {"subnet_id": subnet["id"], "range_id": mcp_block["id"]})

    rows = (await client.get("/api/v1/changes?limit=200")).json()["data"]
    # 19 REST writes (two of them make_* sections/subnets) and 8 MCP writes, one entry each
    assert len(rows) == 27
    assert {c["source"] for c in rows} == {"api", "ui", "mcp"}
    assert {c["entity_type"] for c in rows} == {"section", "subnet", "host", "device_type"}
    assert all("actor" in c and c["actor"] is None for c in rows)


async def test_actor_returned_by_rest_and_mcp(client):
    section = await make_section(client)
    async with SessionLocal() as session:
        changes_svc.record(
            session,
            entity_type="section",
            entity_id=section["id"],
            action="update",
            summary="section Homelab touched",
            source="agent",
            actor="agent:vlan20-scanner",
        )
        await session.commit()

    rest = (await client.get("/api/v1/changes")).json()["data"]
    assert [c["actor"] for c in rest] == ["agent:vlan20-scanner", None]

    async with Client(mcp) as mcp_client:
        listed = (await mcp_client.call_tool("list_changes", {})).data["changes"]
    assert [c["actor"] for c in listed] == ["agent:vlan20-scanner", None]


async def test_database_from_before_actor_gains_the_column(tmp_path):
    """A database created by main before this change gets changes.actor on startup,
    and the rows already in it read null."""
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(SCHEMA_BEFORE_ACTOR.read_text())
    old.execute(
        "INSERT INTO changes (id, ts, entity_type, entity_id, action, summary, source) "
        "VALUES ('c-1', '2026-11-01 10:00:00', 'section', 's-1', 'create', "
        "'section Homelab created', 'api')"
    )
    old.commit()
    assert "actor" not in {row[1] for row in old.execute("PRAGMA table_info(changes)")}
    old.close()

    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    try:
        await init_models(engine)
        await init_models(engine)  # a second start-up finds the column and adds nothing
        async with engine.connect() as conn:
            columns = {
                row[1]: row
                for row in (await conn.exec_driver_sql("PRAGMA table_info(changes)")).all()
            }
        # cid, name, type, notnull, default, pk
        assert columns["actor"][2:5] == ("VARCHAR(80)", 0, None)

        async with async_sessionmaker(engine)() as session:
            rows = await changes_svc.list_changes(session)
            assert [r.id for r in rows] == ["c-1"]
            assert ChangeOut.model_validate(rows[0]).actor is None
    finally:
        await engine.dispose()
