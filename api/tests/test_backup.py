import sqlite3

from .conftest import make_section


async def test_backup_is_valid_snapshot_with_committed_data(client, tmp_path):
    section = await make_section(client, "Backup marker")

    res = await client.get("/api/v1/backup")

    assert res.status_code == 200
    assert 'filename="ipam.db"' in res.headers["content-disposition"]
    snapshot = tmp_path / "snapshot.db"
    snapshot.write_bytes(res.content)
    with sqlite3.connect(snapshot) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT name FROM sections WHERE id = ?", (section["id"],)).fetchone() == (
            "Backup marker",
        )
