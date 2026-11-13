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
from .routers import agents, changes, device_types, hosts, sections, subnets
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


app.add_middleware(MCPPathRewriteMiddleware)


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
):
    app.include_router(router, prefix="/api/v1", dependencies=[Depends(require_api_key)])

# `/api/v1/agent/*` (check-in, results: #10) will be its own router guarded by
# `auth.require_agent` alone, never `require_api_key`.

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
