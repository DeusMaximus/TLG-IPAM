"""Grants survive a restart (#26), and a link in progress does not (#27): three
processes, one database file.

The first is a script that links a client: it registers, authorises, approves (in the
consent handler's place) and exchanges the code, all against the app as a server with
`PUBLIC_BASE_URL` set imports it, then exits. The second and third are the server
itself, started the way the image starts it (`python -m app.serve`), one after the
other on the same file. Nothing the tokens need is in a process's memory; the link
window and the requests pending in it are, so the second server's window and the
request a client sent in it are gone on the third.
"""

import base64
import hashlib
import json
import sqlite3
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

oauth.linking_open = lambda: True   # open, in a link window's place
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


CALLBACK = "https://client.example.com/cb"


def _authorise(client):
    challenge = base64.urlsafe_b64encode(hashlib.sha256(b"v" * 64).digest()).decode().rstrip("=")
    return {
        "client_id": client["client_id"],
        "response_type": "code",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "redirect_uri": CALLBACK,
        "state": "s",
    }


def _five_minutes_pass(tmp_path):
    """Make every refresh token old enough to be rotated (`oauth.ROTATION_MIN_AGE`),
    while no server runs on the file."""
    con = sqlite3.connect(tmp_path / "ipam.db")
    try:
        con.execute("UPDATE oauth_tokens SET created_at = '2026-01-01 00:00:00.000000'")
        con.commit()
    finally:
        con.close()


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
    _five_minutes_pass(tmp_path)
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
            assert second["refresh_token"] != first["refresh_token"]
            # no link can be started without a window; one is opened, and a client
            # registers and authorises in it
            assert http.post(f"{base}/mcp/register", json={"redirect_uris": [CALLBACK]}).status_code == 403
            res = http.post(f"{base}/api/v1/link-window", json={}, headers={"X-API-Key": REAL_KEY})
            assert res.status_code == 201, res.text
            other = http.post(f"{base}/mcp/register", json={"redirect_uris": [CALLBACK]})
            assert other.status_code == 201, other.text
            res = http.get(f"{base}/mcp/authorize", params=_authorise(other.json()))
            assert res.headers["location"].startswith(BASE + "/mcp/consent?txn=")
            txn = httpx.URL(res.headers["location"]).params["txn"]
            assert http.get(f"{base}/mcp/consent", params={"txn": txn}).status_code == 200
    finally:
        _stop(proc, reader)

    # Restarted: the pair that server issued works on the next one
    _five_minutes_pass(tmp_path)
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
            # the restart cancelled the link in progress, and nothing else: no window,
            # the request gone with it, the registration kept
            state = http.get(f"{base}/api/v1/link-window", headers={"X-API-Key": REAL_KEY})
            assert state.json()["data"]["open"] is False
            assert http.get(f"{base}/mcp/consent", params={"txn": txn}).status_code == 403
            assert http.get(f"{base}/mcp/authorize", params=_authorise(other.json())).status_code == 403
    finally:
        _stop(proc, reader)


# --- MCP_REMOTE_WRITES across a restart (#28) ---

LINK_READ_WRITE = LINK.replace("can_write=False", "can_write=True")


def _rpc(http, base, bearer, session, method, params=None, *, rid=2):
    """One JSON-RPC request on an MCP session over HTTP; its result, from a JSON body or
    from the event stream's one message."""
    headers = {
        "Authorization": f"Bearer {bearer}",
        "Accept": "application/json, text/event-stream",
        "mcp-session-id": session,
    }
    res = http.post(
        f"{base}/mcp",
        json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    if res.headers["content-type"].startswith("text/event-stream"):
        [data] = [line[5:] for line in res.text.splitlines() if line.startswith("data:")]
        return json.loads(data)
    return res.json()


def _session(http, base, bearer):
    res = _mcp(http, base, bearer)
    assert res.status_code == 200, res.text
    session = res.headers["mcp-session-id"]
    http.post(
        f"{base}/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={
            "Authorization": f"Bearer {bearer}",
            "Accept": "application/json, text/event-stream",
            "mcp-session-id": session,
        },
    )
    return session


def _tools(http, base, bearer):
    session = _session(http, base, bearer)
    return session, {t["name"] for t in _rpc(http, base, bearer, session, "tools/list")["result"]["tools"]}


def _call(http, base, bearer, session, name, arguments):
    return _rpc(http, base, bearer, session, "tools/call", {"name": name, "arguments": arguments})["result"]


def test_a_read_write_grant_writes_while_the_setting_is_on_and_not_after_a_restart_with_it_off(
    tmp_path,
):
    """The acceptance line across processes: a grant linked read-write with
    `MCP_REMOTE_WRITES=true` writes, and is recorded as `grant:<client name>`; the
    server restarted with the setting off loads the same token with `ipam:read` alone,
    with no relink; and the owner key keeps both scopes on each."""
    env = _child_env(tmp_path, REAL_KEY)
    env.update(PUBLIC_BASE_URL=BASE, MCP_REMOTE_WRITES="true")
    linked = subprocess.run(
        [sys.executable, "-c", LINK_READ_WRITE], cwd=API_DIR, env=env, capture_output=True, text=True, timeout=60
    )
    assert linked.returncode == 0, linked.stderr
    issued = json.loads(linked.stdout.strip().splitlines()[-1])
    client, tokens = issued["client"], issued["tokens"]
    assert tokens["scope"] == "ipam:read ipam:write"
    owner = {"X-API-Key": REAL_KEY}
    access = tokens["access_token"]

    deadline = time.monotonic() + START_DEADLINE
    proc = _image(tmp_path, REAL_KEY, env={"PUBLIC_BASE_URL": BASE, "MCP_REMOTE_WRITES": "true"})
    lines, reader = _read_stderr(proc)
    try:
        base = f"http://127.0.0.1:{_wait_until_serving(lines, deadline)}"
        with httpx.Client(trust_env=False) as http:
            assert http.get(f"{base}/api/v1/link-window", headers=owner).json()["data"]["remote_writes"] is True
            section = http.post(f"{base}/api/v1/sections", json={"name": "Homelab"}, headers=owner).json()["data"]
            subnet = http.post(
                f"{base}/api/v1/subnets",
                json={"section_id": section["id"], "cidr": "10.0.0.0/24", "name": "lab"},
                headers=owner,
            ).json()["data"]
            session, tools = _tools(http, base, access)
            assert {"create_host", "request_scan", "list_hosts"} <= tools
            made = _call(http, base, access, session, "create_host", {"subnet_id": subnet["id"], "ip": "10.0.0.20", "hostname": "nas"})
            assert made.get("isError") is not True, made
            [entry] = [
                c for c in http.get(f"{base}/api/v1/changes", headers=owner).json()["data"] if c["source"] == "mcp"
            ]
            assert entry["actor"] == "grant:Example"
            assert "create_host" in _tools(http, base, REAL_KEY)[1]
    finally:
        _stop(proc, reader)

    # Restarted with the setting off (unset): the same grant and token, no relink
    _five_minutes_pass(tmp_path)
    deadline = time.monotonic() + START_DEADLINE
    proc, lines, reader = _serve(tmp_path)
    try:
        base = f"http://127.0.0.1:{_wait_until_serving(lines, deadline)}"
        with httpx.Client(trust_env=False) as http:
            assert http.get(f"{base}/api/v1/link-window", headers=owner).json()["data"]["remote_writes"] is False
            session, tools = _tools(http, base, access)
            assert "list_hosts" in tools and not tools & {"create_host", "request_scan"}
            for name, arguments in (
                ("create_host", {"subnet_id": subnet["id"], "ip": "10.0.0.21"}),
                ("request_scan", {"subnet_id": subnet["id"]}),
            ):
                refused = _call(http, base, access, session, name, arguments)
                assert refused["isError"] is True
                assert refused["content"][0]["text"] == f"Unknown tool: '{name}'"
            listed = _call(http, base, access, session, "list_hosts", {"subnet_id": subnet["id"]})
            assert listed.get("isError") is not True
            assert [h["hostname"] for h in listed["structuredContent"]["hosts"]] == ["nas"]
            res = _refresh(http, base, client, tokens["refresh_token"])
            assert res.status_code == 200, res.text
            assert res.json()["scope"] == "ipam:read"
            # the owner key keeps both scopes with the setting off
            assert {"create_host", "request_scan"} <= _tools(http, base, REAL_KEY)[1]
            # and no read-write window can be opened
            refused = http.post(f"{base}/api/v1/link-window", json={"can_write": True}, headers=owner)
            assert refused.status_code == 409 and refused.json()["error"]["code"] == "REMOTE_WRITES_OFF"
    finally:
        _stop(proc, reader)
