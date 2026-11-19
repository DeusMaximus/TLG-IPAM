import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from .auth import require_api_key
from .config import settings
from .database import SessionLocal, init_models
from .errors import ApiError
from .mcp_server import mcp
from .routers import agent, agents, changes, device_types, hosts, scan_targets, sections, subnets
from .routers import findings
from .services.backups import create_snapshot
from .services.device_types import seed_defaults

mcp_app = mcp.http_app(path="/")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_models()
    async with SessionLocal() as session:
        await seed_defaults(session)
    async with mcp_app.lifespan(app):
        yield


app = FastAPI(
    title="TLG-IPAM",
    version=settings.version,
    lifespan=lifespan,
    docs_url="/api/v1/docs",
    openapi_url="/api/v1/openapi.json",
)


class MCPPathRewriteMiddleware:
    """Treat /mcp and /mcp/ as the same endpoint. MCP clients (mcp-remote, Claude
    Desktop) request the bare path, which would otherwise miss the sub-app mount and
    fall through to the SPA catch-all."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path") == "/mcp":
            scope = {**scope, "path": "/mcp/", "raw_path": b"/mcp/"}
        await self.app(scope, receive, send)


# The one limit on the size of a request body, applied before the body is buffered and
# before any route or dependency runs (`RequestBodyCapMiddleware`). 4 MiB: the largest
# legitimate bodies are the bulk host create — measured through the UI's own
# `csvToHosts` (web/src/utils/csv.js): a 1,000-row import (the `BulkImportRequest.hosts`
# cap, `api/app/schemas.py:275`) with 2,300-character notes measures 2,352,466 bytes —
# `notes` and `domain` are unbounded in the schema, so the default is the supported
# budget, measured from the largest the UI sends — and the 2 MiB results body #11
# capped `POST /api/v1/agent/results` with (`agent_protocol.RESULTS_BODY_MAX`, replaced
# by this shared cap; the largest legitimate results body measures 1,429,733 bytes).
REQUEST_BODY_MAX = 4 * 1024 * 1024

_BODY_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _declared_length(scope: dict) -> int | None:
    """The request's `Content-Length`, or None when it has none or it is not a number
    (`scope["headers"]` is a list of lowercased-name byte pairs)."""
    for name, value in scope.get("headers", []):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


def _replays(messages, receive):
    """A `receive` that yields the messages the cap middleware already read, then the
    original `receive` for anything after them."""
    queue = iter(messages)

    async def replayed():
        try:
            return next(queue)
        except StopIteration:
            return await receive()

    return replayed


class RequestBodyCapMiddleware:
    """Refuse request bodies over `REQUEST_BODY_MAX` before the app runs.

    A declared `Content-Length` over the cap is refused unread, on every method; a
    body sent without one is counted as it streams and refused once the count passes
    the cap. Either way the answer is 413 in the `{"error": {"code", "message"}}`
    shape, and neither the authentication dependency nor the JSON parser runs. One cap
    for every route, the `/mcp` mount included: a tool call carries its arguments in
    the body. The cap limits one payload, not aggregate anonymous memory or upload
    duration. A body at the cap is served.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = _declared_length(scope)
        if declared is not None and declared > REQUEST_BODY_MAX:
            await self._refuse(send)
            return
        chunked = any(
            name == b"transfer-encoding" and b"chunked" in value.lower()
            for name, value in scope.get("headers", [])
        )
        # Read the body here, so the cap holds before the app runs, when the request
        # may carry one the server does not refuse on its own: chunked, or a method
        # the server does not enforce a body on.
        reads = chunked or (declared is None and scope.get("method") in _BODY_METHODS)
        if not reads:
            # A declared length under the cap is enforced by the server; a bodyless
            # method without one is read by the app as before.
            await self.app(scope, receive, send)
            return
        body = bytearray()
        end = None  # the one message the app reads: the payload, or the disconnect
        while True:
            message = await receive()
            if message["type"] != "http.request":
                end = message  # http.disconnect: the app reads it, as before
                break
            body += message.get("body", b"")
            if len(body) > REQUEST_BODY_MAX:
                await self._refuse(send)
                return
            if not message.get("more_body", False):
                break
        if end is None:
            # One message for the app: the whole payload, or nothing when the body
            # was empty. Every read is gone but the payload bytes.
            end = {"type": "http.request", "body": bytes(body), "more_body": False}
        await self.app(scope, _replays([end], receive), send)

    async def _refuse(self, send):
        body = json.dumps(
            {
                "error": {
                    "code": "PAYLOAD_TOO_LARGE",
                    "message": f"The request body may be at most {REQUEST_BODY_MAX} bytes",
                }
            }
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


app.add_middleware(MCPPathRewriteMiddleware)
app.add_middleware(RequestBodyCapMiddleware)


@app.exception_handler(ApiError)
async def api_error_handler(_request, exc: ApiError):
    return JSONResponse(
        status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message}}
    )


@app.exception_handler(RequestValidationError)
async def request_validation_handler(_request, exc: RequestValidationError):
    first = exc.errors()[0]
    loc = ".".join(str(p) for p in first["loc"] if p != "body")
    message = f"{loc}: {first['msg']}" if loc else first["msg"]
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "VALIDATION_ERROR", "message": message}},
    )


@app.get("/health")
async def health():
    return {"status": "ok", "version": settings.version}


@app.get("/api/v1/backup", dependencies=[Depends(require_api_key)])
async def backup():
    snapshot = await run_in_threadpool(create_snapshot, settings.db_path)
    return FileResponse(
        snapshot,
        filename="ipam.db",
        media_type="application/octet-stream",
        background=BackgroundTask(snapshot.unlink, missing_ok=True),
    )


for router in (
    sections.router,
    subnets.router,
    hosts.router,
    changes.router,
    device_types.router,
    agents.router,
    scan_targets.router,
    findings.router,
):
    app.include_router(router, prefix="/api/v1", dependencies=[Depends(require_api_key)])

# `/api/v1/agent/*` is guarded by `auth.require_agent` alone (on the router itself),
# never `require_api_key`: it must not join the loop above.
app.include_router(agent.router, prefix="/api/v1")

# Bearer auth for /mcp is the FastMCP server's own `auth=` (mcp_server.py).
app.mount("/mcp", mcp_app)


class SPAStaticFiles(StaticFiles):
    """Serve the built SPA, falling back to index.html for client-side routes."""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


_static = Path(settings.static_dir)
if _static.is_dir():
    app.mount("/", SPAStaticFiles(directory=_static, html=True))
