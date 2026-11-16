"""The agent's HTTP client for `/api/v1/agent/*` (spec §5.7).

TLS verification is always on, with no setting that turns it off (spec §5.6). The
system trust store is used; a private CA is added by mounting its bundle and pointing
`SSL_CERT_FILE` at it, which OpenSSL reads itself.

Every answer is sorted into one of three kinds, which is all the loop acts on:
`OK` (a 200 with a `data` object), `RETRY` (429, any 5xx, or no answer at all), and
`REFUSED` (every other status). Only an `OK` check-in answer carries jobs.
"""

import enum
import ssl
from dataclasses import dataclass
from typing import Any

import httpx

from . import __version__
from .config import Config
from .protocol import printable

CHECKIN_PATH = "/api/v1/agent/checkin"
RESULTS_PATH = "/api/v1/agent/results"
_MESSAGE_MAX = 300


class Kind(enum.Enum):
    OK = "ok"
    RETRY = "retry"
    REFUSED = "refused"


@dataclass(frozen=True)
class Answer:
    kind: Kind
    # None when the request got no answer
    status: int | None
    # the error envelope's `code`, when there is one
    code: str | None
    # for the log: the server's message, or what went wrong, made printable and short
    message: str
    # the `data` of a 200
    data: Any = None


def tls_context() -> ssl.SSLContext:
    """The only TLS settings the agent has: the server's certificate and name are
    always checked."""
    ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def make_http(config: Config) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=config.url,
        headers={
            "Authorization": f"Bearer {config.token}",
            "User-Agent": f"tlg-ipam-agent/{__version__}",
        },
        verify=tls_context(),
        timeout=httpx.Timeout(30.0, connect=10.0),
        # a redirect is refused, not followed: it could lead the token elsewhere
        follow_redirects=False,
        # no proxy, netrc or certificate settings picked up from the environment
        trust_env=False,
    )


def _error(res: httpx.Response) -> tuple[str | None, str]:
    try:
        error = res.json().get("error")
        code, message = error.get("code"), error.get("message")
    except (ValueError, AttributeError):
        return None, f"HTTP {res.status_code}"
    code = printable(code, 40) if isinstance(code, str) else None
    message = printable(message, _MESSAGE_MAX) if isinstance(message, str) else ""
    return code, f"HTTP {res.status_code} {code or ''}: {message}".replace(" :", ":")


class AgentClient:
    """`http` carries the base URL and the token (`make_http`); a test passes a client
    bound to the app in process."""

    def __init__(self, http: httpx.AsyncClient):
        self.http = http

    async def checkin(self, body: dict) -> Answer:
        return await self._post(CHECKIN_PATH, body)

    async def results(self, body: dict) -> Answer:
        return await self._post(RESULTS_PATH, body)

    async def _post(self, path: str, body: dict) -> Answer:
        try:
            res = await self.http.post(path, json=body)
        except httpx.HTTPError as exc:
            # connection refused, a TLS failure, a timeout: retried like a 503
            detail = printable(str(exc), _MESSAGE_MAX) if str(exc) else ""
            return Answer(Kind.RETRY, None, None, f"no answer ({type(exc).__name__}) {detail}")
        status = res.status_code
        if status == 200:
            try:
                data = res.json()["data"]
            except (ValueError, KeyError, TypeError):
                data = None
            if not isinstance(data, dict):
                return Answer(Kind.RETRY, status, None, "HTTP 200 without a data object")
            return Answer(Kind.OK, status, None, "HTTP 200", data)
        code, message = _error(res)
        kind = Kind.RETRY if status == 429 or status >= 500 else Kind.REFUSED
        return Answer(kind, status, code, message)
