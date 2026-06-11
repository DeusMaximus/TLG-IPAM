import json

from fastapi import Header

from .config import settings
from .errors import ApiError


async def require_api_key(x_api_key: str | None = Header(default=None)):
    if x_api_key is None:
        raise ApiError("UNAUTHORIZED", "Missing X-API-Key header", 401)
    if x_api_key != settings.api_key:
        raise ApiError("FORBIDDEN", "Invalid API key", 403)


async def request_source(x_tlg_client: str | None = Header(default=None)) -> str:
    """Audit-log source: the SPA sends X-TLG-Client: ui; anything else is a raw API caller."""
    return "ui" if x_tlg_client == "ui" else "api"


class APIKeyASGIMiddleware:
    """X-API-Key enforcement for the mounted MCP sub-app, which bypasses FastAPI dependencies."""

    def __init__(self, app, api_key: str):
        self.app = app
        self.api_key = api_key

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        key = headers.get("x-api-key")
        if key is None:
            await self._reject(send, 401, "UNAUTHORIZED", "Missing X-API-Key header")
            return
        if key != self.api_key:
            await self._reject(send, 403, "FORBIDDEN", "Invalid API key")
            return
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(send, status: int, code: str, message: str):
        body = json.dumps({"error": {"code": code, "message": message}}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body})
