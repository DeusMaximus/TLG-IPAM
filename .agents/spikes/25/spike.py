"""#25 spike: do the web MCP clients link against a dynamic-registration-only server?

FastMCP's `InMemoryOAuthProvider` (an `OAuthProvider`, so no CIMD: that lives in
`OAuthProxy` in fastmcp 4.1.8) behind a public URL, with three tools:

- `spike_ping`: no scope.
- `spike_read`: requires `ipam:read`, which every token is granted.
- `spike_write`: requires `ipam:write`, which no token is ever granted, so the
  log shows how FastMCP presents a tool the caller lacks the scope for.

Every HTTP request is written to a JSON-lines log with its time, method, path,
status and user agent; a registration's client metadata is logged whole (it holds
no secret), and the authorise and token requests by their non-secret fields only.
No authorisation code, PKCE value, state, token or client secret is logged.

The provider approves every authorise request without asking anyone: this is a
throwaway harness holding no data, to be run only while the clients are linked.

    PUBLIC_BASE_URL=https://mcp-spike.example.com SPIKE_LOG=/logs/spike.jsonl \
        python spike.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from urllib.parse import parse_qsl

import uvicorn
from fastmcp import FastMCP
from fastmcp.server.auth import require_scopes
from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider
from fastmcp.server.middleware import Middleware
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions

BASE_URL = os.environ["PUBLIC_BASE_URL"].rstrip("/")
LOG_PATH = os.environ.get("SPIKE_LOG", "spike.jsonl")
GRANTED = ["ipam:read"]  # what every token carries, whatever was asked for
NEVER_GRANTED = "ipam:write"

_AUTHORIZE_KEEP = {"client_id", "redirect_uri", "response_type", "scope", "resource", "code_challenge_method"}
_TOKEN_KEEP = {"grant_type", "client_id", "redirect_uri", "scope", "resource", "client_assertion_type"}


def log(event: str, **fields) -> None:
    line = {"t": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int(time.time() * 1000) % 1000:03d}Z", "event": event, **fields}
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, sort_keys=True) + "\n")
    print(json.dumps(line, sort_keys=True), file=sys.stderr, flush=True)


class SpikeProvider(InMemoryOAuthProvider):
    """Grants `GRANTED` only, so `spike_write` is always out of scope."""

    async def register_client(self, client_info):
        await super().register_client(client_info)
        log("client_registered", client_id=client_info.client_id,
            client_name=client_info.client_name, scope=client_info.scope,
            redirect_uris=[str(u) for u in (client_info.redirect_uris or [])],
            grant_types=client_info.grant_types,
            token_endpoint_auth_method=client_info.token_endpoint_auth_method)

    async def authorize(self, client, params):
        asked = list(params.scopes or [])
        params.scopes = list(GRANTED)
        log("authorized", client_id=client.client_id, scopes_asked=asked, scopes_granted=GRANTED)
        return await super().authorize(client, params)


class McpLog(Middleware):
    async def on_list_tools(self, context, call_next):
        tools = await call_next(context)
        log("mcp_list_tools", tools=[t.name for t in tools])
        return tools

    async def on_call_tool(self, context, call_next):
        name = context.message.name
        try:
            result = await call_next(context)
        except Exception as exc:  # what the client is told, by type and message
            log("mcp_call_tool", tool=name, outcome="raised", error_type=type(exc).__name__, error=str(exc)[:300])
            raise
        log("mcp_call_tool", tool=name, outcome="ok")
        return result


provider = SpikeProvider(
    base_url=BASE_URL,
    client_registration_options=ClientRegistrationOptions(
        # A client that registers without a scope is registered for both, so its authorise
        # request for either is accepted; the grant is then cut to GRANTED (SpikeProvider).
        enabled=True, valid_scopes=["ipam:read", NEVER_GRANTED], default_scopes=["ipam:read", NEVER_GRANTED]),
    revocation_options=RevocationOptions(enabled=True),
)
mcp = FastMCP("TLG-IPAM spike", auth=provider, middleware=[McpLog()])


@mcp.tool
def spike_ping() -> str:
    """Answer 'pong'. Needs no scope. Use it to check the connector works."""
    return "pong"


@mcp.tool(auth=require_scopes("ipam:read"))
def spike_read() -> dict:
    """Return a fixed placeholder record. Needs the ipam:read scope."""
    return {"subnet": "192.0.2.0/24", "hosts": 3}


@mcp.tool(auth=require_scopes(NEVER_GRANTED))
def spike_write(note: str) -> str:
    """Pretend to write a note. Needs the ipam:write scope, which this server never grants."""
    return f"wrote {len(note)} characters"


class HttpLog:
    """Pure ASGI: logs each request; reads the small register and token bodies to log their safe fields."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path, method = scope["path"], scope["method"]
        headers = {k.decode().lower(): v.decode("latin-1") for k, v in scope["headers"]}
        entry = {"method": method, "path": path, "ua": headers.get("user-agent", "")[:200],
                 "bearer": headers.get("authorization", "").lower().startswith("bearer "),
                 "accept": headers.get("accept", "")[:100]}
        if path.endswith("/authorize"):
            q = dict(parse_qsl(scope.get("query_string", b"").decode()))
            entry["query"] = {k: v for k, v in q.items() if k in _AUTHORIZE_KEEP}
        body_receive = receive
        if method == "POST" and (path.endswith("/register") or path.endswith("/token") or path == "/mcp"):
            chunks, more = [], True
            while more:
                msg = await receive()
                chunks.append(msg.get("body", b""))
                more = msg.get("more_body", False)
            body = b"".join(chunks)
            if path.endswith("/register"):
                try:
                    meta = json.loads(body)
                    entry["register"] = {k: v for k, v in meta.items() if "secret" not in k and k != "jwks"}
                except ValueError:
                    entry["register"] = "unparsed"
            elif path.endswith("/token"):
                form = dict(parse_qsl(body.decode(errors="replace")))
                entry["token"] = {k: v for k, v in form.items() if k in _TOKEN_KEEP}
                entry["client_auth"] = "basic" if headers.get("authorization", "").lower().startswith("basic ") else (
                    "secret_post" if "client_secret" in form else ("jwt" if "client_assertion" in form else "none"))
            else:
                try:
                    rpc = json.loads(body)
                    rpcs = rpc if isinstance(rpc, list) else [rpc]
                    entry["rpc"] = [{"method": r.get("method"), "tool": (r.get("params") or {}).get("name")} for r in rpcs if isinstance(r, dict)]
                except ValueError:
                    entry["rpc"] = "unparsed"
            sent = False

            async def body_receive():
                nonlocal sent
                if not sent:
                    sent = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()

        status = {}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                for k, v in message.get("headers", []):
                    if k.decode().lower() in ("www-authenticate", "location"):
                        val = v.decode("latin-1")
                        if k.decode().lower() == "location":
                            val = val.split("?")[0]  # the redirect's query holds the code and state
                        entry[k.decode().lower()] = val[:300]
                log("http", status=status["code"], **entry)
            await send(message)

        await self.app(scope, body_receive, send_wrapper)


app = HttpLog(mcp.http_app(path="/mcp"))

if __name__ == "__main__":
    log("start", base_url=BASE_URL, fastmcp=__import__("fastmcp").__version__)
    uvicorn.run(app, host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8090")),
                proxy_headers=False, log_level="warning")
