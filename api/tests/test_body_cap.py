"""The one request-body cap for the whole server: `main.REQUEST_BODY_MAX`, enforced by
`main.RequestBodyCapMiddleware` before the body is buffered and before any route or
dependency runs — agent routes and owner routes alike, and the `/mcp` mount.

The issue's acceptance: an over-cap body gets 413 in the error shape on an agent route
and on an owner route, with `Content-Length` and without it (chunked), with no
credentials, and neither the authentication dependency nor the JSON parser runs; a body
at the cap is served.
"""

import json

from app.main import REQUEST_BODY_MAX

from .conftest import make_section, make_subnet


def _over_cap() -> bytes:
    return b" " * (REQUEST_BODY_MAX + 1)


def _cap_payload(**fields) -> bytes:
    """A JSON object padded with trailing spaces to exactly the cap; `json.loads`
    accepts trailing whitespace, so the body is valid and served."""
    body = json.dumps(fields).encode()
    return body + b" " * (REQUEST_BODY_MAX - len(body))


def _assert_refused(res):
    assert res.status_code == 413
    assert res.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert f"{REQUEST_BODY_MAX} bytes" in res.json()["error"]["message"]


async def test_an_over_cap_body_on_an_owner_route_is_refused_without_credentials(anon_client):
    """No `X-API-Key`: `require_api_key` would answer 401, so the answer is the cap's
    413 — the cap ran before any route dependency."""
    res = await anon_client.post("/api/v1/hosts", content=_over_cap())
    _assert_refused(res)


async def test_an_over_cap_chunked_body_on_an_owner_route_is_refused(anon_client):
    """No `Content-Length`: the middleware counts the bytes as the body streams and
    refuses at the cap."""

    async def chunks():
        for _ in range(REQUEST_BODY_MAX // 65536 + 2):
            yield b" " * 65536

    res = await anon_client.post("/api/v1/hosts", content=chunks())
    _assert_refused(res)


async def test_an_over_cap_body_on_an_agent_route_is_refused_without_credentials(anon_client):
    res = await anon_client.post("/api/v1/agent/checkin", content=_over_cap())
    _assert_refused(res)


async def test_an_over_cap_chunked_body_on_an_agent_route_is_refused(anon_client):
    async def chunks():
        for _ in range(REQUEST_BODY_MAX // 65536 + 2):
            yield b" " * 65536

    res = await anon_client.post("/api/v1/agent/checkin", content=chunks())
    _assert_refused(res)


async def _asgi_request(
    method: str, path: str, headers: dict[str, str], body: bytes = b""
) -> tuple[int, int]:
    """POST/GET straight into the ASGI app, counting how often it asks for the body."""
    from app.main import app

    reads = 0
    started = []

    async def receive():
        nonlocal reads
        reads += 1
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            started.append(message["status"])

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": ("127.0.0.1", 1),
        "server": ("test", 80),
    }
    await app(scope, receive, send)
    return started[0], reads


async def test_an_over_cap_declared_length_on_an_owner_route_is_refused_without_reading():
    """A declared length over the cap is refused unread: the app — its authentication
    dependency and JSON parser included — never runs (reads == 0)."""
    status, reads = await _asgi_request(
        "POST",
        "/api/v1/hosts",
        {"content-type": "application/json", "content-length": str(REQUEST_BODY_MAX + 1)},
        b"{}",
    )
    assert (status, reads) == (413, 0)


async def test_an_over_cap_declared_length_on_a_get_is_refused_without_reading():
    """The declared-length refusal applies to every method — GET included (the probe
    found `GET /api/v1/hosts` with `Content-Length: 2097153` answered 401 before the
    cap covered GETs)."""
    status, reads = await _asgi_request(
        "GET", "/api/v1/hosts", {"content-length": str(REQUEST_BODY_MAX + 1)}, b""
    )
    assert (status, reads) == (413, 0)


async def test_an_over_cap_chunked_body_on_a_get_is_refused():
    """A chunked GET is a body the server does not enforce on its own: it is counted
    too."""
    status, reads = await _asgi_request(
        "GET", "/api/v1/hosts", {"transfer-encoding": "chunked"}, _over_cap()
    )
    assert (status, reads) == (413, 1)


async def test_a_body_at_the_cap_is_served_on_an_owner_route(client):
    subnet = await make_subnet(client, (await make_section(client))["id"])
    body = _cap_payload(hosts=[{"ip": f"10.0.0.{i}", "hostname": "pad"} for i in (1, 2)])
    res = await client.post(
        f"/api/v1/subnets/{subnet['id']}/hosts/bulk",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 200, res.text


async def test_the_largest_bulk_host_create_body_is_served(client):
    """The largest the UI sends: 1,000 hosts (the `BulkImportRequest.hosts` cap) at
    schema maxima — `hostname` 253, `owner` 120, `device_type` 40, `mac` — with the
    schema-unbounded `domain` and `notes` at 100 and 500 bytes, as the import CSV
    columns carry them."""
    subnet = await make_subnet(client, (await make_section(client))["id"])
    hosts = [
        {
            "ip": f"10.0.0.{(i % 250) + 1}",
            "hostname": "h" * 253,
            "domain": "d" * 100,
            "mac": f"02:00:00:00:00:{i % 256:02x}",
            "device_type": "server",
            "status": "active",
            "owner": "o" * 120,
            "notes": "n" * 500,
        }
        for i in range(1, 1001)
    ]
    body = json.dumps({"hosts": hosts}).encode()
    assert len(body) < REQUEST_BODY_MAX  # the largest legitimate bulk body
    res = await client.post(
        f"/api/v1/subnets/{subnet['id']}/hosts/bulk",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 200, res.text


async def test_an_over_cap_body_on_the_mcp_mount_is_refused(anon_client):
    """The cap covers the `/mcp` mount: a tool call carries its arguments in the body,
    and the mount's bearer is the owner key — the cap runs before it."""
    res = await anon_client.post("/mcp", content=_over_cap())
    _assert_refused(res)
