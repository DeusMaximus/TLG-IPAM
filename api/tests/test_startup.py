"""The server refuses to start without a real owner key (spec §4.2).

The in-process tests call `config.load_settings` against a patched environment.
The process tests start uvicorn the way the documented commands and the image's
CMD do, in a fresh interpreter, because the check runs when `app.config` is
imported and the test session has already imported it with its own key.
"""

import os
import queue
import re
import secrets
import subprocess
import sys
import threading
from pathlib import Path

import httpx
import pytest

from app import config

API_DIR = Path(__file__).resolve().parents[1]
UNSET = object()

REFUSED = {
    "unset": (UNSET, "API_KEY is not set, or is empty."),
    "empty": ("", "API_KEY is not set, or is empty."),
    "blank": ("   ", "API_KEY is not set, or is empty."),
    "placeholder": ("change-me", "API_KEY is the placeholder `change-me` from .env.example."),
}
# The key in AGENTS.md → Commands, the test suite's, and a generated one
ACCEPTED = ["dev-key", "test-key", secrets.token_hex(32)]


def _set_api_key(monkeypatch, value):
    # pydantic-settings matches env names case-insensitively, so clear every spelling
    for name in [n for n in os.environ if n.upper() == "API_KEY"]:
        monkeypatch.delenv(name)
    if value is not UNSET:
        monkeypatch.setenv("API_KEY", value)


@pytest.mark.parametrize(("value", "reason"), REFUSED.values(), ids=REFUSED.keys())
def test_load_settings_refuses(monkeypatch, value, reason):
    _set_api_key(monkeypatch, value)
    with pytest.raises(SystemExit) as refused:
        config.load_settings()
    message = str(refused.value.code)
    assert reason in message
    assert "openssl rand -hex 32" in message


@pytest.mark.parametrize("value", ACCEPTED, ids=["dev-key", "test-key", "generated"])
def test_load_settings_accepts_a_real_key(monkeypatch, value):
    _set_api_key(monkeypatch, value)
    assert config.load_settings().api_key == value


def test_settings_has_no_usable_default_key():
    # The published `change-me` default is gone: an unset API_KEY reads as empty
    assert config.Settings.model_fields["api_key"].default == ""


# --- Starting uvicorn ---------------------------------------------------------------


def _uvicorn(tmp_path, api_key):
    env = {n: v for n, v in os.environ.items() if n.upper() != "API_KEY"}
    if api_key is not UNSET:
        env["API_KEY"] = api_key
    env["DB_PATH"] = str(tmp_path / "ipam.db")
    env["STATIC_DIR"] = str(tmp_path / "no-static")
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "0"],
        cwd=API_DIR,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


@pytest.mark.parametrize(("value", "reason"), REFUSED.values(), ids=REFUSED.keys())
def test_uvicorn_refuses_to_start(tmp_path, value, reason):
    proc = _uvicorn(tmp_path, value)
    try:
        _, stderr = proc.communicate(timeout=60)
    finally:
        proc.kill()
    assert proc.returncode == 1, stderr
    assert reason in stderr
    assert "openssl rand -hex 32" in stderr
    assert "Uvicorn running on" not in stderr
    assert not (tmp_path / "ipam.db").exists()


def test_uvicorn_starts_and_serves_with_a_real_key(tmp_path):
    key = secrets.token_hex(32)
    proc = _uvicorn(tmp_path, key)
    lines: queue.Queue[str] = queue.Queue()
    reader = threading.Thread(target=lambda: [lines.put(line) for line in proc.stderr], daemon=True)
    reader.start()
    seen = []
    try:
        port = None
        while "Application startup complete." not in "".join(seen) or port is None:
            try:
                seen.append(lines.get(timeout=60))
            except queue.Empty:
                pytest.fail("uvicorn did not finish starting:\n" + "".join(seen))
            if proc.poll() is not None and lines.empty():
                pytest.fail("uvicorn exited while starting:\n" + "".join(seen))
            if match := re.search(r"Uvicorn running on http://127\.0\.0\.1:(\d+)", seen[-1]):
                port = int(match.group(1))

        base = f"http://127.0.0.1:{port}"
        assert httpx.get(f"{base}/api/v1/sections", headers={"X-API-Key": key}).status_code == 200
        assert httpx.get(f"{base}/api/v1/sections", headers={"X-API-Key": ""}).status_code == 403
        mcp = httpx.post(
            f"{base}/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            },
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json, text/event-stream"},
        )
        assert mcp.status_code == 200, mcp.text
    finally:
        proc.terminate()
        proc.wait(timeout=30)
        reader.join(timeout=30)
        proc.stderr.close()
