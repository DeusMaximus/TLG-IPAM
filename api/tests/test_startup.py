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


def _child_env(tmp_path, api_key):
    env = {n: v for n, v in os.environ.items() if _inherited(n)}
    if api_key is not UNSET:
        env["API_KEY"] = api_key
    env["DB_PATH"] = str(tmp_path / "ipam.db")
    env["STATIC_DIR"] = str(tmp_path / "no-static")
    return env


def _uvicorn(tmp_path, api_key, *args):
    """Start uvicorn from its command line, as the documented commands do, on a loopback
    port it picks; `args` are more of uvicorn's options (`--http h11`, say)."""
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "0", *args],
        cwd=API_DIR,
        env=_child_env(tmp_path, api_key),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


def _image(tmp_path, api_key, env=None):
    """Start the server as the image's `CMD` does (`python -m app.serve`), on a loopback
    port it picks; `env` is added to the child's environment as it is."""
    child_env = _child_env(tmp_path, api_key)
    child_env.update(env or {})
    return subprocess.Popen(
        [sys.executable, "-m", "app.serve", "--host", "127.0.0.1", "--port", "0"],
        cwd=API_DIR,
        env=child_env,
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


STARTS = {"command line": _uvicorn, "image": _image}


@pytest.mark.parametrize("start", STARTS.values(), ids=STARTS.keys())
@pytest.mark.parametrize(("value", "reason"), REFUSED.values(), ids=REFUSED.keys())
def test_uvicorn_refuses_to_start(tmp_path, value, reason, start):
    proc = start(tmp_path, value)
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


@pytest.mark.parametrize("start", STARTS.values(), ids=STARTS.keys())
def test_uvicorn_starts_and_serves_with_a_real_key(tmp_path, monkeypatch, start):
    # Inherited uvicorn settings must not reach the child: at `warning` it would not
    # print the lines this test waits for, and the test would fail on a healthy server
    monkeypatch.setenv("UVICORN_LOG_LEVEL", "warning")
    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    key = secrets.token_hex(32)
    deadline = time.monotonic() + START_DEADLINE
    proc = start(tmp_path, key)
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


def test_the_image_runs_one_worker_whatever_the_environment_says(tmp_path):
    """`app.serve` passes one worker itself (brief §2.2): the variables that set the
    count on uvicorn's command line reach the child here and change nothing."""
    key = secrets.token_hex(32)
    proc = _image(tmp_path, key, {"WEB_CONCURRENCY": "4", "UVICORN_WORKERS": "4"})
    lines, reader = _read_stderr(proc)
    try:
        port = _wait_until_serving(lines, time.monotonic() + START_DEADLINE)
        with httpx.Client(trust_env=False) as client:
            assert client.get(f"http://127.0.0.1:{port}/health").status_code == 200
        children = subprocess.run(["pgrep", "-P", str(proc.pid)], capture_output=True, text=True)
        assert children.returncode == 1 and children.stdout == "", children.stdout  # no child process
    finally:
        _stop(proc, reader)


def test_the_dockerfile_starts_the_image_command():
    """The image's `CMD` is the command `_image` runs, so the tests that start a server
    as the image does start the one the image runs."""
    dockerfile = (API_DIR.parent / "Dockerfile").read_text()
    commands = [line for line in dockerfile.splitlines() if line.startswith("CMD")]
    assert commands == ['CMD ["python", "-m", "app.serve"]']


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


# --- Not root (#173) -----------------------------------------------------------------


def _final_stage():
    """The Dockerfile's last stage, one instruction per entry, continuation lines joined
    and comments left out."""
    text = (API_DIR.parent / "Dockerfile").read_text()
    lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    instructions = " ".join(line.strip() for line in "\n".join(lines).replace("\\\n", " ").splitlines() if line.strip())
    joined = re.split(r" (?=(?:FROM|WORKDIR|COPY|RUN|ENV|EXPOSE|USER|CMD|ENTRYPOINT|ARG|VOLUME) )", " " + instructions)
    stages = [i.strip() for i in joined if i.strip()]
    last_from = max(n for n, i in enumerate(stages) if i.startswith("FROM "))
    return stages[last_from:]


def test_the_image_runs_as_uid_10001_and_gives_it_data():
    """The server image runs as `ipam`, uid and gid 10001, written as literals (no build
    argument can change them), and makes `/data` that user's, mode 0700, so a new volume
    takes it. `USER` comes after the last `RUN`, so nothing after it runs as root, and
    there is no `ENTRYPOINT`, so nothing runs before the `CMD`: CI's `server-image` job
    (`.github/scripts/check-server-image.sh`) starts the image to see the process's ids."""
    stage = _final_stage()
    kinds = [i.split(" ", 1)[0] for i in stage]
    assert kinds.count("USER") == 1
    assert "USER 10001:10001" in stage
    assert "ARG" not in kinds and "ENTRYPOINT" not in kinds and "VOLUME" not in kinds
    assert max(n for n, k in enumerate(kinds) if k == "RUN") < kinds.index("USER") < kinds.index("CMD")
    (account,) = [i for i in stage if "useradd" in i]
    for part in [
        "groupadd --system --gid 10001 ipam",
        "useradd --system --uid 10001 --gid 10001 ",
        "install -d -m 0700 -o 10001 -g 10001 /data",
    ]:
        assert part in account, part
    assert any("DB_PATH=/data/ipam.db" in i for i in stage if i.startswith("ENV "))


needs_non_root = pytest.mark.skipif(os.geteuid() == 0, reason="root passes every permission check")


@needs_non_root
def test_storage_is_usable_in_a_writable_directory_with_or_without_files(tmp_path):
    from app import database

    path = tmp_path / "ipam.db"
    assert database.storage_problem(str(path)) is None
    for name in ["ipam.db", "ipam.db-wal", "ipam.db-shm"]:
        (tmp_path / name).write_bytes(b"")
    assert database.storage_problem(str(path)) is None


@needs_non_root
def test_storage_problem_names_a_missing_or_unwritable_directory(tmp_path):
    from app import database

    missing = tmp_path / "missing"
    assert database.storage_problem(str(missing / "ipam.db")) == (
        f"the database's directory {missing} does not exist"
    )
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o555)
    try:
        assert database.storage_problem(str(locked / "ipam.db")) == (
            f"the database's directory {locked} is not writable by this process"
        )
    finally:
        locked.chmod(0o755)


@needs_non_root
@pytest.mark.parametrize("name", ["ipam.db", "ipam.db-wal", "ipam.db-shm"])
@pytest.mark.parametrize("mode", [0o444, 0o200, 0o000])
def test_storage_problem_names_a_database_file_it_cannot_read_and_write(tmp_path, name, mode):
    """A volume an image that ran as root made holds root's files in a directory that
    may be writable (one given to uid 10001 by hand, say): each file is checked too."""
    from app import database

    for each in ["ipam.db", "ipam.db-wal", "ipam.db-shm"]:
        (tmp_path / each).write_bytes(b"")
    (tmp_path / name).chmod(mode)
    try:
        assert database.storage_problem(str(tmp_path / "ipam.db")) == (
            f"{tmp_path / name} is not readable and writable by this process"
        )
    finally:
        (tmp_path / name).chmod(0o644)


@needs_non_root
def test_the_image_refuses_to_start_on_storage_it_cannot_write(tmp_path):
    """SQLite opens a database it cannot write read-only, so without the check the
    server would start and refuse every write (#173). The start stops before uvicorn
    serves anything, with the one-time command for this process's own ids."""
    data = tmp_path / "data"
    data.mkdir()
    data.chmod(0o555)
    try:
        proc = _image(tmp_path, REAL_KEY, {"DB_PATH": str(data / "ipam.db")})
        try:
            _, stderr = proc.communicate(timeout=START_DEADLINE)
        finally:
            _stop(proc)
    finally:
        data.chmod(0o755)
    assert proc.returncode == 1, stderr
    assert f"TLG-IPAM will not start: the database's directory {data} is not writable" in stderr
    assert f"--user root --entrypoint chown ipam -R {os.getuid()}:{os.getgid()} {data}`" in stderr
    assert "Uvicorn running on" not in stderr
    assert REAL_KEY not in stderr
    assert not (data / "ipam.db").exists()


@needs_non_root
def test_a_missing_directory_is_refused_without_the_volume_command(tmp_path):
    from app import database

    missing = tmp_path / "missing"
    with pytest.raises(SystemExit) as refused:
        database.refuse_unwritable_storage(str(missing / "ipam.db"))
    assert str(refused.value.code) == (
        f"TLG-IPAM will not start: the database's directory {missing} does not exist (DB_PATH)."
    )
    assert database.refuse_unwritable_storage(str(tmp_path / "ipam.db")) is None
