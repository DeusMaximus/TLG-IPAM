import hmac

from fastapi import Header
from fastmcp.server.auth import AccessToken, TokenVerifier

from .config import settings
from .errors import ApiError

# Every scope the owner key carries. Write tools start requiring `ipam:write` in M3,
# when OAuth grants that lack it exist; until then nothing checks them.
OWNER_SCOPES = ["ipam:read", "ipam:write"]


def is_owner_key(presented: str) -> bool:
    """Constant-time comparison against the owner key (`API_KEY`).

    Compared as UTF-8 bytes: `hmac.compare_digest` raises on a non-ASCII `str`,
    and a header value can be any Latin-1 text.
    """
    return hmac.compare_digest(presented.encode(), settings.api_key.encode())


async def require_api_key(x_api_key: str | None = Header(default=None)):
    if x_api_key is None:
        raise ApiError("UNAUTHORIZED", "Missing X-API-Key header", 401)
    if not is_owner_key(x_api_key):
        raise ApiError("FORBIDDEN", "Invalid API key", 403)


async def request_source(x_tlg_client: str | None = Header(default=None)) -> str:
    """Audit-log source: the SPA sends X-TLG-Client: ui; anything else is a raw API caller."""
    return "ui" if x_tlg_client == "ui" else "api"


class OwnerKeyVerifier(TokenVerifier):
    """Bearer auth for `/mcp`: accepts the owner key and grants every scope.

    FastMCP's HTTP app answers 401 itself when the bearer is missing or this
    returns None, so `/mcp` never sees `X-API-Key`.
    """

    async def verify_token(self, token: str) -> AccessToken | None:
        if not is_owner_key(token):
            return None
        return AccessToken(token=token, client_id="owner", scopes=list(OWNER_SCOPES))
