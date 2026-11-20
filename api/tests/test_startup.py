"""The server refuses to start without a real owner key (spec §4.2).

The in-process tests call `config.load_settings` against a patched environment.
The process tests start uvicorn the way the documented commands and the image's
CMD do, in a fresh interpreter, because the check runs when `app.config` is
imported and the test session has already imported it with its own key. The
child gets the test session's environment less anything that configures uvicorn
or the settings, so a developer's or CI's own values cannot change what it does.
"""

import os
import queue
import re
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

from app import config

API_DIR = Path(__file__).resolve().parents[1]
UNSET = object()

# A generated key, so a test can tell whether a refusal message repeats it
REAL_KEY = secrets.token_hex(32)
PADDED = "API_KEY starts or ends with whitespace (a space, tab or newline); remove it."

REFUSED = {
    "unset": (UNSET, "API_KEY is not set, or is empty."),
    "empty": ("", "API_KEY is not set, or is empty."),
    "blank": ("   ", "API_KEY is not set, or is empty."),
    "placeholder": ("change-me", "API_KEY is the placeholder `change-me` from .env.example."),
    "padded-placeholder": (" change-me\n", "API_KEY is the placeholder `change-me` from .env.example."),
    "leading-space": (f" {REAL_KEY}", PADDED),
    "trailing-space": (f"{REAL_KEY} ", PADDED),
    "leading-tab": (f"\t{REAL_KEY}", PADDED),
    "trailing-tab": (f"{REAL_KEY}\t", PADDED),
    "leading-newline": (f"\n{REAL_KEY}", PADDED),
    "trailing-newline": (f"{REAL_KEY}\n", PADDED),
    "trailing-crlf": (f"{REAL_KEY}\r\n", PADDED),
}
# The key in AGENTS.md → Commands, the test suite's, a generated one, and keys with
# whitespace inside them: only whitespace at either end is refused
ACCEPTED = {
    "dev-key": "dev-key",
    "test-key": "test-key",
    "generated": REAL_KEY,
    "inner-space": "dev key",
    "inner-tab": f"{REAL_KEY[:32]}\t{REAL_KEY[32:]}",
}
# Where a uvicorn start must finish by, from launch; one deadline for the whole wait
START_DEADLINE = 60


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
    assert REAL_KEY not in message


@pytest.mark.parametrize("value", ACCEPTED.values(), ids=ACCEPTED.keys())
def test_load_settings_accepts_a_real_key(monkeypatch, value):
    _set_api_key(monkeypatch, value)
    assert config.load_settings().api_key == value


def test_settings_has_no_usable_default_key():
    # The published `change-me` default is gone: an unset API_KEY reads as empty
    assert config.Settings.model_fields["api_key"].default == ""


# --- Starting uvicorn ---------------------------------------------------------------


# Settings fields; pydantic-settings reads their env names case-insensitively
_SETTINGS_ENV = {name.upper() for name in config.Settings.model_fields}


def _inherited(name):
    """Whether the child may keep this variable of the test session's environment.

    `UVICORN_*` and `WEB_CONCURRENCY` configure uvicorn itself (`UVICORN_LOG_LEVEL=warning`
    hides the log lines the start test waits for); the settings are set per test.
    """
    upper = name.upper()
    return not (upper in _SETTINGS_ENV or upper.startswith("UVICORN_") or upper == "WEB_CONCURRENCY")


def _uvicorn(tmp_path, api_key, *args):
    """Start uvicorn as the image does, on a loopback port it picks; `args` are more of
    uvicorn's options (`--http httptools`, say)."""
    env = {n: v for n, v in os.environ.items() if _inherited(n)}
    if api_key is not UNSET:
        env["API_KEY"] = api_key
    env["DB_PATH"] = str(tmp_path / "ipam.db")
    env["STATIC_DIR"] = str(tmp_path / "no-static")
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "0", *args],
        cwd=API_DIR,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


def _stop(proc, reader=None):
    """Stop the server if it still runs, reap it, and close its pipe, so a
    failing test leaves no process behind."""
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    if reader is not None:
        reader.join(timeout=10)
    proc.stderr.close()


def _read_stderr(proc):
    """Queue the child's stderr lines from a thread, so the wait can time out.
    `None` follows the last line, once the child has closed its stderr."""
    lines: queue.Queue[str | None] = queue.Queue()

    def read():
        for line in proc.stderr:
            lines.put(line)
        lines.put(None)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    return lines, reader


def _wait_until_serving(lines, deadline):
    """The port uvicorn listens on, once it has finished starting.

    Fails the test when the child closes its stderr (exits) first, or when `deadline` (a
    `time.monotonic()` value) passes, however the lines arrive.
    """
    seen = []
    port = None
    while "Application startup complete." not in "".join(seen) or port is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            pytest.fail("uvicorn did not finish starting:\n" + "".join(seen))
        try:
            line = lines.get(timeout=remaining)
        except queue.Empty:
            continue
        if line is None:
            pytest.fail("uvicorn exited while starting:\n" + "".join(seen))
        seen.append(line)
        if match := re.search(r"Uvicorn running on http://127\.0\.0\.1:(\d+)", seen[-1]):
            port = int(match.group(1))
    return port


@pytest.mark.parametrize(("value", "reason"), REFUSED.values(), ids=REFUSED.keys())
def test_uvicorn_refuses_to_start(tmp_path, value, reason):
    proc = _uvicorn(tmp_path, value)
    try:
        _, stderr = proc.communicate(timeout=START_DEADLINE)
    finally:
        _stop(proc)
    assert proc.returncode == 1, stderr
    assert reason in stderr
    assert "openssl rand -hex 32" in stderr
    assert REAL_KEY not in stderr
    assert "Uvicorn running on" not in stderr
    assert not (tmp_path / "ipam.db").exists()


def test_uvicorn_starts_and_serves_with_a_real_key(tmp_path, monkeypatch):
    # Inherited uvicorn settings must not reach the child: at `warning` it would not
    # print the lines this test waits for, and the test would fail on a healthy server
    monkeypatch.setenv("UVICORN_LOG_LEVEL", "warning")
    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    key = secrets.token_hex(32)
    deadline = time.monotonic() + START_DEADLINE
    proc = _uvicorn(tmp_path, key)
    lines, reader = _read_stderr(proc)
    try:
        port = _wait_until_serving(lines, deadline)
        base = f"http://127.0.0.1:{port}"
        # The server is up and has its environment. Now give this process settings that
        # would break an httpx client built from the environment, though the requests are
        # plain HTTP to the loopback address: an unreadable certificate bundle, and a
        # proxy that nothing listens on with no exemption for the loopback address
        monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "no-such-bundle.pem"))
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
        monkeypatch.setenv("NO_PROXY", "")
        monkeypatch.setenv("no_proxy", "")
        # `trust_env=False`: the client reads neither proxy nor certificate settings
        with httpx.Client(trust_env=False) as client:
            assert client.get(f"{base}/api/v1/sections", headers={"X-API-Key": key}).status_code == 200
            assert client.get(f"{base}/api/v1/sections", headers={"X-API-Key": ""}).status_code == 403
            mcp = client.post(
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
        _stop(proc, reader)


def test_child_environment_drops_uvicorn_and_settings_variables():
    for name in ["UVICORN_LOG_LEVEL", "uvicorn_port", "WEB_CONCURRENCY", "API_KEY", "api_key", "Db_Path", "STATIC_DIR"]:
        assert not _inherited(name), name
    for name in ["PATH", "HOME", "PYTHONPATH", "UVICORN"]:
        assert _inherited(name), name


def test_a_start_that_never_completes_fails_by_the_deadline_and_is_reaped():
    # A child that prints nothing and never exits stands in for a hung uvicorn
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(600)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    lines, reader = _read_stderr(proc)
    started = time.monotonic()
    try:
        with pytest.raises(pytest.fail.Exception, match="did not finish starting"):
            _wait_until_serving(lines, started + 1)
        assert time.monotonic() - started < 5
    finally:
        _stop(proc, reader)
    assert proc.poll() is not None
    assert not reader.is_alive()


def test_a_start_that_exits_fails_at_once():
    proc = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stderr.write('gone\\n')"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    lines, reader = _read_stderr(proc)
    started = time.monotonic()
    try:
        with pytest.raises(pytest.fail.Exception, match="exited while starting:\ngone"):
            _wait_until_serving(lines, started + START_DEADLINE)
        assert time.monotonic() - started < 5
    finally:
        _stop(proc, reader)
