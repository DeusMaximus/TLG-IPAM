from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastmcp import FastMCP
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from .auth import require_api_key
from .body_limit import BodyLimitMiddleware
from .config import settings
from .database import SessionLocal, init_models
from .errors import ApiError
from .mcp_server import mcp
from .oauth_provider import public_base_url_of, remote_writes_of
from .routers import agent, agents, changes, device_types, hosts, scan_targets, sections, subnets
from .routers import findings, link_window
from .services import oauth as oauth_svc
from .services.backups import create_snapshot
from .services.device_types import seed_defaults


class MCPPathRewriteMiddleware:
    """Treat /mcp and /mcp/ as the same endpoint. MCP clients (mcp-remote, Claude
    Desktop) request the bare path, which would otherwise miss the sub-app mount and
    fall through to the SPA catch-all. Only the exact path is rewritten: the OAuth
    routes under the mount answer 404 with a trailing slash (v0.2 spec §6.7)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path") == oauth_svc.MCP_MOUNT:
            scope = {**scope, "path": "/mcp/", "raw_path": b"/mcp/"}
        await self.app(scope, receive, send)


class SPAStaticFiles(StaticFiles):
    """Serve the built SPA, falling back to index.html for client-side routes."""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


async def api_error_handler(_request, exc: ApiError):
    return JSONResponse(
        status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message}}
    )


async def request_validation_handler(_request, exc: RequestValidationError):
    first = exc.errors()[0]
    loc = ".".join(str(p) for p in first["loc"] if p != "body")
    message = f"{loc}: {first['msg']}" if loc else first["msg"]
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "VALIDATION_ERROR", "message": message}},
    )


async def health():
    return {"status": "ok", "version": settings.version}


async def backup():
    snapshot = await run_in_threadpool(create_snapshot, settings.db_path)
    return FileResponse(
        snapshot,
        filename="ipam.db",
        media_type="application/octet-stream",
        background=BackgroundTask(snapshot.unlink, missing_ok=True),
    )


async def no_such_well_known(rest: str):
    """Every path under `/.well-known/` that is not an installed route (spec §6.7): a
    404, with OAuth on or off, never the SPA's `index.html`. A client that asked for
    metadata (`openid-configuration`, an MCP server card) must not be answered `200`
    with a web page."""
    raise ApiError("NOT_FOUND", "No such well-known resource", 404)


def create_app(server: FastMCP = mcp) -> FastAPI:
    """The whole application around one MCP server: the REST API, the server's HTTP
    app mounted at `/mcp` (with the OAuth routes inside it when its auth is the OAuth
    provider, v0.2 spec §6.2), the discovery routes at the root, and the SPA.

    `app` below is the one the server runs, built from the settings. The tests build a
    second one around a server with OAuth on.
    """
    mcp_app = server.http_app(path="/")
    # `/mcp/authorize/` is a 404, not a redirect to `/mcp/authorize` (spec §6.7). The
    # bare `/mcp` is rewritten to `/mcp/` by the middleware below and needs none.
    mcp_app.router.redirect_slashes = False
    # A write the database refuses on an OAuth route is the REST 503, not a bare 500
    mcp_app.add_exception_handler(ApiError, api_error_handler)

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

    # What the owner's link-window routes ask: whether this app has the OAuth server,
    # and whether that server may give a link write access (`MCP_REMOTE_WRITES`)
    app.state.public_base_url = public_base_url_of(server.auth)
    app.state.remote_writes = remote_writes_of(server.auth)

    app.add_middleware(MCPPathRewriteMiddleware)
    # Added last, so it wraps everything above: the body cap runs before routing, before
    # any route dependency (authentication included) and before any body is parsed, on
    # every route, `/mcp` and the OAuth routes under it included.
    app.add_middleware(BodyLimitMiddleware)

    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, request_validation_handler)

    app.add_api_route("/health", health, methods=["GET"])
    app.add_api_route(
        "/api/v1/backup", backup, methods=["GET"], dependencies=[Depends(require_api_key)]
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
        link_window.router,
    ):
        app.include_router(router, prefix="/api/v1", dependencies=[Depends(require_api_key)])

    # `/api/v1/agent/*` is guarded by `auth.require_agent` alone (on the router itself),
    # never `require_api_key`: it must not join the loop above. Its one exception is
    # `enrol`, on a router of its own, which takes an enrolment token
    # (`services/enrolment`) and neither key.
    app.include_router(agent.router, prefix="/api/v1")
    app.include_router(agent.enrol_router, prefix="/api/v1")

    # OAuth discovery, at the root where RFC 8414 and RFC 9728 put it and ahead of the
    # SPA catch-all. The provider has routes only when `PUBLIC_BASE_URL` is set; with
    # or without them, nothing else under `/.well-known/` is served.
    if server.auth is not None:
        app.router.routes.extend(server.auth.get_well_known_routes(mcp_path="/"))
    app.add_api_route(
        "/.well-known/{rest:path}",
        no_such_well_known,
        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        include_in_schema=False,
    )

    # Bearer auth for /mcp is the FastMCP server's own `auth=` (mcp_server.py).
    app.mount(oauth_svc.MCP_MOUNT, mcp_app)

    static = Path(settings.static_dir)
    if static.is_dir():
        app.mount("/", SPAStaticFiles(directory=static, html=True))
    return app


app = create_app()
