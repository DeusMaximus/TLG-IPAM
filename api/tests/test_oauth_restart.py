"""Grants survive a restart (#26): three processes, one database file.

The first is a script that links a client: it registers, authorises, approves (in the
consent handler's place, #27) and exchanges the code, all against the app as a server
with `PUBLIC_BASE_URL` set imports it, then exits. The second and third are the server
itself, started the way the image starts it (`python -m app.serve`), one after the
other on the same file. Nothing the tokens need is in a process's memory.
"""

import json
import subprocess
import sys
import time

import httpx

from .test_startup import (
    API_DIR,
    REAL_KEY,
    START_DEADLINE,
    _child_env,
    _image,
    _read_stderr,
    _stop,
    _wait_until_serving,
)

BASE = "https://ipam.example.com"
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}

LINK = """
import asyncio, base64, hashlib, json
import httpx
from app.config import settings
from app.database import SessionLocal, init_models
from app.main import app
from app.services import oauth

oauth.linking_open = lambda: True   # the link window, which #27 adds
BASE, VERIFIER, CALLBACK = settings.public_base_url, "v" * 64, "https://client.example.com/cb"
challenge = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip("=")

async def main():
    await init_models()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as web:
        client = (await web.post("/mcp/register", json={"redirect_uris": [CALLBACK], "client_name": "Example"})).json()
        res = await web.get("/mcp/authorize", params={
            "client_id": client["client_id"], "response_type": "code", "code_challenge": challenge,
            "code_challenge_method": "S256", "redirect_uri": CALLBACK, "state": "s",
            "resource": BASE + "/mcp"})
        txn = httpx.URL(res.headers["location"]).params["txn"]
        async with SessionLocal() as session:
            back = httpx.URL(await oauth.approve(session, txn, can_write=False))
        tokens = (await web.post("/mcp/token", data={
            "grant_type": "authorization_code", "code": back.params["code"], "redirect_uri": CALLBACK,
            "client_id": client["client_id"], "client_secret": client["client_secret"],
            "code_verifier": VERIFIER})).json()
    print(json.dumps({"client": client, "tokens": tokens}))

asyncio.run(main())
"""


def _serve(tmp_path):
    proc = _image(tmp_path, REAL_KEY, env={"PUBLIC_BASE_URL": BASE})
    lines, reader = _read_stderr(proc)
    return proc, lines, reader


def _mcp(http, base, bearer):
    return http.post(
        f"{base}/mcp",
        json=INITIALIZE,
        headers={"Authorization": f"Bearer {bearer}", "Accept": "application/json, text/event-stream"},
    )


def _refresh(http, base, client, token):
    return http.post(
        f"{base}/mcp/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": token,
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
        },
    )


def test_a_grant_outlives_the_process_that_issued_it_and_a_server_restart(tmp_path):
    env = _child_env(tmp_path, REAL_KEY)
    env["PUBLIC_BASE_URL"] = BASE
    linked = subprocess.run(
        [sys.executable, "-c", LINK], cwd=API_DIR, env=env, capture_output=True, text=True, timeout=60
    )
    assert linked.returncode == 0, linked.stderr
    issued = json.loads(linked.stdout.strip().splitlines()[-1])
    client, first = issued["client"], issued["tokens"]
    assert first["scope"] == "ipam:read" and first["refresh_token"].startswith("tlgor_")

    # A server on the same file: the linking process is gone, and its tokens work
    deadline = time.monotonic() + START_DEADLINE
    proc, lines, reader = _serve(tmp_path)
    try:
        base = f"http://127.0.0.1:{_wait_until_serving(lines, deadline)}"
        with httpx.Client(trust_env=False) as http:
            metadata = http.get(f"{base}/.well-known/oauth-authorization-server").json()
            assert metadata["issuer"] == BASE + "/"
            assert _mcp(http, base, first["access_token"]).status_code == 200
            assert _mcp(http, base, REAL_KEY).status_code == 200
            assert _mcp(http, base, "tlgoa_" + "a" * 43).status_code == 401
            res = _refresh(http, base, client, first["refresh_token"])
            assert res.status_code == 200, res.text
            second = res.json()
            # no link can be started on a server as it ships: the window is #27's
            assert http.post(f"{base}/mcp/register", json={"redirect_uris": ["https://x.example/cb"]}).status_code == 403
    finally:
        _stop(proc, reader)

    # Restarted: the pair that server issued works on the next one
    deadline = time.monotonic() + START_DEADLINE
    proc, lines, reader = _serve(tmp_path)
    try:
        base = f"http://127.0.0.1:{_wait_until_serving(lines, deadline)}"
        with httpx.Client(trust_env=False) as http:
            assert _mcp(http, base, second["access_token"]).status_code == 200
            assert _mcp(http, base, first["access_token"]).status_code == 200
            res = _refresh(http, base, client, second["refresh_token"])
            assert res.status_code == 200, res.text
            assert res.json()["refresh_token"] not in (first["refresh_token"], second["refresh_token"])
            # the wrong secret is still refused, by the hash the database kept
            wrong = {**client, "client_secret": "0" * 64}
            assert _refresh(http, base, wrong, res.json()["refresh_token"]).status_code == 401
    finally:
        _stop(proc, reader)
