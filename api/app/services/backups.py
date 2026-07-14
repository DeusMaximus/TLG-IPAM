"""Consistent SQLite backup snapshots."""

import sqlite3
import tempfile
from pathlib import Path

from ..errors import ApiError


def create_snapshot(db_path: str) -> Path:
    """Copy the live database through SQLite's online backup API.

    The returned temporary path belongs to the caller and must be removed after the
    response finishes.
    """
    source_path = Path(db_path)
    if not source_path.is_file():
        raise ApiError("NOT_FOUND", "Database file not found", 404)

    handle = tempfile.NamedTemporaryFile(prefix="tlg-ipam-backup-", suffix=".db", delete=False)
    snapshot_path = Path(handle.name)
    handle.close()
    try:
        with sqlite3.connect(source_path) as source, sqlite3.connect(snapshot_path) as target:
            source.backup(target)
    except Exception:
        snapshot_path.unlink(missing_ok=True)
        raise
    return snapshot_path
