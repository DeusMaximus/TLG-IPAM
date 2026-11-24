"""The agent's HTTP client for `/api/v1/agent/*` (spec §5.7).

TLS verification is always on, with no setting that turns it off (spec §5.6). The
system trust store is used; a private CA is added by mounting its bundle and pointing
`SSL_CERT_FILE` at it, which OpenSSL reads itself.

Every answer is sorted into one of three kinds, which is all the loop acts on:
`OK` (a 200 whose `data` has the route's shape), `RETRY` (429, any 5xx, a 200 that is
too large or not that shape, or no complete answer in time), and `REFUSED` (every
other status). Only an `OK` check-in answer carries jobs, and only an `OK` enrol answer
carries an agent token (checked for a token's shape before anything keeps it).
"""

import asyncio
import enum
import json
import re
import ssl
from dataclasses import dataclass
from typing import Any

import httpx

from . import __version__

ENROL_PATH = "/api/v1/agent/enrol"
CHECKIN_PATH = "/api/v1/agent/checkin"
RESULTS_PATH = "/api/v1/agent/results"
# one deadline for the whole of a request: connecting, sending, headers and body
REQUEST_TIMEOUT_S = 30.0
# the largest answer read; a results answer rejecting all 4,096 records is ~250 KiB
RESPONSE_MAX = 1024 * 1024
# an error code as the server's vocabulary writes them
_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,39}")
# no count the server returns can be larger than a result set's records
_COUNT_MAX = 4096
# the server's ids are UUIDs (models._uuid)
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# the agent token the current protocol issues: the prefix and secrets.token_urlsafe(32),
# 43 characters of the URL-safe base64 alphabet (services/agents.issue_token;
# tests/test_contract.py holds this to what the server really issues)
ISSUED_AGENT_TOKEN = re.compile(r"tlga_[A-Za-z0-9_-]{43}")


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
    # for the log: the status and a vocabulary-shaped code, or what went wrong. Never
    # text the server wrote: a server can echo anything, the agent's token included.
    message: str
    # the `data` of a 200, checked for the route's shape (`checkin_data`, `results_data`)
    data: Any = None


def tls_context() -> ssl.SSLContext:
    """The only TLS settings the agent has: the server's certificate and name are
    always checked."""
    ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def make_http(url: str, token: str) -> httpx.AsyncClient:
    """A client for the server at `url` that presents `token`: the agent token, or for
    the enrolment exchange alone, the enrolment token."""
    return httpx.AsyncClient(
        base_url=url,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": f"tlg-ipam-agent/{__version__}",
            # nothing is decompressed (`_bounded_body`), so nothing compressed is asked for
            "Accept-Encoding": "identity",
        },
        verify=tls_context(),
        timeout=httpx.Timeout(30.0, connect=10.0),
        # a redirect is refused, not followed: it could lead the token elsewhere
        follow_redirects=False,
        # no proxy, netrc or certificate settings picked up from the environment
        trust_env=False,
    )


def _code(raw: bytes) -> str | None:
    """The error envelope's `code`, only when it is a code of the server's vocabulary
    shape (`RATE_LIMITED`, `PROTOCOL_MISMATCH`). The `message` is never read: the agent
    logs no text the server wrote."""
    try:
        error = json.loads(raw).get("error")
        code = error.get("code")
    except (ValueError, AttributeError, RecursionError):
        return None
    return code if isinstance(code, str) and _CODE.fullmatch(code) else None


def _transport_reason(exc: BaseException) -> str:
    """Why a request got no answer, from the exception's type and, for TLS, OpenSSL's
    reason code; never the exception's text, which can carry the peer's bytes."""
    reason = type(exc).__name__
    seen = exc
    for _ in range(8):
        seen = seen.__cause__ or seen.__context__
        if seen is None:
            break
        if isinstance(seen, ssl.SSLError):
            code = seen.reason
            if isinstance(code, str) and _CODE.fullmatch(code):
                reason += f" {code}"
            break
    return reason


def _is_count(value: Any) -> bool:
    return type(value) is int and 0 <= value <= _COUNT_MAX


def checkin_data(data: dict) -> dict | None:
    """The check-in answer's `data`, when it has the shape agent_protocol.CheckinResponse
    gives it: an integer interval and a list of jobs (each checked by `parse_job`)."""
    interval, jobs = data.get("checkin_interval_s"), data.get("jobs")
    if type(interval) is not int or not isinstance(jobs, list):
        return None
    return {"checkin_interval_s": interval, "jobs": jobs}


def enrol_data(data: dict) -> dict | None:
    """The enrol answer, when it has the shape agent_protocol.EnrolResponse gives it: an
    agent id that is a UUID and an agent token of exactly the form the server issues
    (ISSUED_AGENT_TOKEN), checked before the agent writes it anywhere. Anything else is
    a 200 not in the route's shape (spec §5.7), retried, and never stored: the review of
    #130 found `tlga_!` passing the looser `config.parse_token`, which the agent still
    uses for a token the operator gives it."""
    agent_id, token = data.get("agent_id"), data.get("agent_token")
    if not isinstance(agent_id, str) or not _UUID.fullmatch(agent_id):
        return None
    if not isinstance(token, str) or not ISSUED_AGENT_TOKEN.fullmatch(token):
        return None
    return {"agent_id": agent_id, "agent_token": token}


def results_data(data: dict) -> dict | None:
    """The results acknowledgment, as counts, when it has the shape
    agent_protocol.ResultsResponse gives it; nothing of it but these integers is kept."""
    accepted, rejected = data.get("accepted"), data.get("rejected")
    if not _is_count(accepted) or not isinstance(rejected, list) or len(rejected) > _COUNT_MAX:
        return None
    for item in rejected:
        if not isinstance(item, dict) or not isinstance(item.get("reason"), str):
            return None
        if item.get("ip") is not None and not isinstance(item.get("ip"), str):
            return None
    return {"accepted": accepted, "rejected": len(rejected)}


class AgentClient:
    """`http` carries the base URL and the token (`make_http`); a test passes a client
    bound to the app in process.

    Every request has one deadline for all of it, connection, headers and body:
    httpx's own timeouts are per phase and its read timeout is per chunk, so a server
    that drips its answer would otherwise hold the request for ever. The body is read
    undecoded into a buffer that never passes RESPONSE_MAX bytes (`_bounded_body`)."""

    def __init__(self, http: httpx.AsyncClient, request_timeout: float = REQUEST_TIMEOUT_S):
        self.http = http
        self.request_timeout = request_timeout

    async def enrol(self, body: dict) -> Answer:
        """The enrolment exchange: `http` presents the enrolment token. An `OK` answer's
        `data` is `enrol_data`'s."""
        return await self._post(ENROL_PATH, body, self.request_timeout, enrol_data)

    async def checkin(self, body: dict) -> Answer:
        return await self._post(CHECKIN_PATH, body, self.request_timeout, checkin_data)

    async def results(self, body: dict, budget: float | None = None) -> Answer:
        """`budget`: the seconds the job's lease has left; the request ends by then."""
        timeout = self.request_timeout if budget is None else min(self.request_timeout, budget)
        return await self._post(RESULTS_PATH, body, timeout, results_data)

    async def _post(self, path: str, body: dict, timeout: float, shape) -> Answer:
        if timeout <= 0:
            return Answer(Kind.RETRY, None, None, "no time left for the request")
        try:
            async with asyncio.timeout(timeout):
                async with self.http.stream("POST", path, json=body) as res:
                    status = res.status_code
                    try:
                        raw, unreadable = await _bounded_body(res), None
                    except _Unreadable as exc:
                        raw, unreadable = None, str(exc)
        except TimeoutError:
            return Answer(Kind.RETRY, None, None, f"no complete answer within {timeout:.0f} s")
        except httpx.HTTPError as exc:
            # connection refused, a TLS failure, a dropped connection: retried like a 503
            return Answer(Kind.RETRY, None, None, f"no answer ({_transport_reason(exc)})")
        if status == 200:
            if raw is None:
                return Answer(Kind.RETRY, status, None, f"HTTP 200 {unreadable}")
            try:
                data = json.loads(raw)["data"]
            except (ValueError, KeyError, TypeError, RecursionError):
                data = None
            data = shape(data) if isinstance(data, dict) else None
            if data is None:
                return Answer(Kind.RETRY, status, None, "HTTP 200 answer not in the expected shape")
            return Answer(Kind.OK, status, None, "HTTP 200", data)
        code = _code(raw) if raw is not None else None
        kind = Kind.RETRY if status == 429 or status >= 500 else Kind.REFUSED
        return Answer(kind, status, code, f"HTTP {status} {code}" if code else f"HTTP {status}")


class _Unreadable(Exception):
    """An answer the agent does not read: its reason is logged, its bytes are not."""


async def _bounded_body(res: httpx.Response) -> bytes:
    """The body as sent, never more than RESPONSE_MAX bytes of it held.

    The agent asks for `identity` and refuses any other content encoding, so nothing
    is decompressed: a small compressed answer cannot expand in memory. The raw
    stream arrives in reads of at most 64 KiB (httpcore), and each is checked before
    it is appended, so the buffer never passes the cap. Raises _Unreadable."""
    encoding = res.headers.get("content-encoding", "").strip().lower()
    if encoding not in ("", "identity"):
        raise _Unreadable("answer with a content encoding")
    declared = res.headers.get("content-length", "")
    if declared.isascii() and declared.isdigit() and int(declared) > RESPONSE_MAX:
        raise _Unreadable(f"answer over {RESPONSE_MAX} bytes")
    if res.is_stream_consumed:
        # a response built whole in memory (a test transport): nothing left to stream
        if len(res.content) > RESPONSE_MAX:
            raise _Unreadable(f"answer over {RESPONSE_MAX} bytes")
        return res.content
    body = bytearray()
    async for chunk in res.aiter_raw():
        if len(body) + len(chunk) > RESPONSE_MAX:
            raise _Unreadable(f"answer over {RESPONSE_MAX} bytes")
        body += chunk
    return bytes(body)
