"""The request-body limits (#60, #90): every HTTP request to the app, `/mcp` included.

`BodyLimitMiddleware` wraps the whole application, so it runs before routing, before
any route dependency (the owner-key and agent-token checks among them) and before
FastAPI or FastMCP reads or parses a body. Every body byte reaches the app through it,
and it holds three limits:

- **The cap** (`BODY_MAX`, per request). A body over it is refused with
  `413 PAYLOAD_TOO_LARGE` in the REST error shape. A declared `Content-Length` over the
  cap is refused with nothing read; one at or under it is passed through, to be read
  when (and if) the route reads it: the HTTP server frames the body by that length and
  delivers no byte past it. Any other body is read here, counting, and refused as soon
  as it would pass the cap; one that fits is handed on whole. That is a body with no
  `Content-Length`, one whose `Content-Length` is not a decimal number, and any request
  with a `Transfer-Encoding` header whatever its `Content-Length` says: a server that
  accepts both headers frames the body by `Transfer-Encoding` (RFC 9112 §6.3; h11 does,
  httptools refuses the request), so the declared length says nothing about how much
  will arrive.
- **The budget** (`BODIES_MAX`, across all requests). Body bytes received by requests
  still in progress, read here or by the route, never total more than it: a chunk that
  would pass it is refused with `503 UNAVAILABLE`, and the bytes a request was counted
  for are given back when the app returns. Authentication does not end the count,
  since most routes read their body before authenticating (FastAPI parses a `Body`
  before the route's dependencies run) and the rest still hold it while they serve.
- **The deadline** (`BODY_TIMEOUT_S`, per request, from when the request reaches this
  middleware, its headers complete). A body not received whole by then is refused with
  `408 REQUEST_TIMEOUT`. Once a body is whole, later reads (a streamed response waiting
  for the disconnect) pass through untimed.

Every refusal closes the connection (`Connection: close`), so the server stops reading
the rest of the body rather than draining it to reuse the connection, and so does any
response the app starts before its request's body was received whole (a credential
refused before the body was read): otherwise the server would keep the connection
open to read and discard what is left of the body, for as long as the sender trickles
it. When a body the route is reading is refused, the middleware sends its refusal
itself (unless the route has already started a response), tells the route the client
has gone (`http.disconnect`), and drops whatever the route sends after it. A body is
kept in one buffer here, and handed to a route that reads it in blocks of at least
64 KiB, so what it costs is its bytes, not the number of chunks it was sent in.

What this does not bound is left to the reverse proxy or tunnel in front of the server
(brief §4.3, spec §5.7): how many connections there are and how many one client opens,
the time and size of request headers, idle connections, and the HTTP server's own
buffer per connection (uvicorn stops reading past 64 KiB that the app has not taken).

A declared length is read as the HTTP server passes it on, which is not always plain
digits (#89). uvicorn's httptools parser accepts a value with whitespace after the
digits and one with any number of leading zeroes, and hands the header on as it came;
h11 strips the whitespace and refuses the zeroes with `400`. So optional whitespace
(space and tab, RFC 9110 §5.6.3) at either end is dropped, and the digits are compared
with the cap by their count before any is converted, since `int()` refuses a string
of more than 4,300 digits (Python's integer-conversion guard, left on).
"""

import asyncio
import json

from starlette.requests import ClientDisconnect

# The largest request body the server reads, in bytes, chosen for #60 from the largest
# legitimate requests (brief §4.3): an agent's result set for a full /20 (4,094 records,
# each with a MAC and a 253-character PTR name) is about 1.3 MB, and the UI's bulk
# import of 1,000 hosts with every length-bounded field at its maximum about 1.15 MB.
BODY_MAX = 2 * 1024 * 1024

# The most body bytes all requests in progress hold at once (#90): eight bodies at the
# cap. One worker serves everything, so this is taken from the only process there is;
# with it full of stalled bodies the worker grew by 17 to 21 MiB (brief §4.3).
BODIES_MAX = 8 * BODY_MAX

# How long a request has to deliver its whole body, in seconds, from when its headers
# are complete (#90). Twice the agent's own deadline for a whole request
# (`client.REQUEST_TIMEOUT_S`, 30 s), so the agent gives up first; it admits a body at
# the cap sent at 35 KB/s, a full result set at 21 KB/s.
BODY_TIMEOUT_S = 60

# A body a route reads is handed to it in blocks of at least this many bytes (the last
# excepted), and one read here is kept in one buffer: a sender that splits its body into
# many small chunks would otherwise cost a Python object per chunk, held by the route's
# list of them (Starlette's `Request.body`), on top of the bytes the budget counts.
_BLOCK = 64 * 1024

_CONTENT_LENGTH = b"content-length"
_TRANSFER_ENCODING = b"transfer-encoding"
_CONNECTION = b"connection"
_OWS = b" \t"  # optional whitespace around a field value (RFC 9110 §5.6.3)


def _refusal(status: int, code: str, message: str) -> tuple[int, bytes]:
    return status, json.dumps({"error": {"code": code, "message": message}}).encode()


async def _refuse(send, refusal: tuple[int, bytes]) -> None:
    status, body = refusal
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (_CONNECTION, b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _length_digits(value: bytes) -> bytes | None:
    """A `Content-Length` value's significant digits, or None when it is not a decimal
    number. Optional whitespace (space, tab) at either end is dropped and leading zeroes
    are stripped, so `b"0"` stands for zero; nothing here converts the digits, so their
    count is unbounded. `bytes.isdigit` is ASCII digits only."""
    value = value.strip(_OWS)
    if not value.isdigit():  # also false for an empty value
        return None
    return value.lstrip(b"0") or b"0"


def _over(digits: bytes, cap: int) -> bool:
    """Whether the number `digits` spells (as `_length_digits` gives it) is over `cap`.
    Converted only once its count is at most the cap's, so `int()` sees a few digits."""
    return len(digits) > len(str(cap)) or int(digits) > cap


def _declared(scope) -> list[bytes] | None:
    """The significant digits of each declared `Content-Length`, or None when no
    declared length frames the body: when it has none, one that is not a decimal number
    (which neither of uvicorn's parsers passes on; another server might), or any
    `Transfer-Encoding`, which frames the body instead."""
    headers = [(k.lower(), v) for k, v in scope.get("headers", ())]
    if any(k == _TRANSFER_ENCODING for k, _ in headers):
        return None
    lengths = [_length_digits(v) for k, v in headers if k == _CONTENT_LENGTH]
    if not lengths or None in lengths:
        return None
    return lengths


def _declared_over(scope, cap: int) -> bool | None:
    """Whether the request's declared `Content-Length` is over `cap`, or None when no
    declared length frames the body (`_declared`). The largest of repeated headers
    counts."""
    lengths = _declared(scope)
    if lengths is None:
        return None
    return any(_over(digits, cap) for digits in lengths)


class _Body:
    """One request's body as the middleware lets it through: counted against the cap
    and the budget, and timed against the deadline, until it is whole."""

    def __init__(self, limits: "BodyLimitMiddleware", receive, send, complete: bool):
        self.limits = limits
        self._receive = receive
        self._send = send
        self.deadline = asyncio.get_running_loop().time() + limits.timeout
        self.size = 0  # bytes received, counted in `limits.held` until the app returns
        self.complete = complete  # the last of the body has been received
        self.cut = False  # the middleware refused the body before it was whole
        self.answered = False  # and its refusal is the response
        self.started = False  # a response has started

    async def read(self):
        """The server's next message, counted and timed; None once the body is refused,
        the refusal sent unless a response had already started."""
        try:
            if asyncio.get_running_loop().time() >= self.deadline:
                raise TimeoutError
            async with asyncio.timeout_at(self.deadline):
                message = await self._receive()
        except TimeoutError:
            return await self._cut_off(self.limits.timed_out)
        if message["type"] != "http.request":
            return message  # the client went away mid-body
        chunk = message.get("body", b"")
        if self.size + len(chunk) > self.limits.cap:
            return await self._cut_off(self.limits.too_large)
        if self.limits.held + len(chunk) > self.limits.budget:
            return await self._cut_off(self.limits.busy)
        self.size += len(chunk)
        self.limits.held += len(chunk)
        if not message.get("more_body", False):
            self.complete = True
        return message

    async def _cut_off(self, refusal) -> None:
        self.cut = True
        if not self.started:
            self.started = self.answered = True
            await _refuse(self._send, refusal)
        return None

    async def receive(self):
        """The app's `receive`: the body through `read`, merged into blocks of at least
        `_BLOCK` bytes but the last, then the server's own messages."""
        if self.cut:
            return {"type": "http.disconnect"}
        if self.complete:
            return await self._receive()
        block = bytearray()
        while True:
            message = await self.read()
            if message is None or message["type"] != "http.request":
                # refused, or the client went away: what was merged is no use to anyone
                return {"type": "http.disconnect"} if message is None else message
            block += message.get("body", b"")
            if self.complete or len(block) >= _BLOCK:
                return {"type": "http.request", "body": bytes(block), "more_body": not self.complete}

    async def send(self, message) -> None:
        """The app's `send`: dropped once the middleware's refusal has gone instead, and
        a response started before the body was whole closes the connection."""
        if self.answered:
            return
        if message["type"] == "http.response.start":
            self.started = True
            if not self.complete:
                headers = [(k, v) for k, v in message.get("headers", ()) if k.lower() != _CONNECTION]
                message = {**message, "headers": [*headers, (_CONNECTION, b"close")]}
        await self._send(message)


class BodyLimitMiddleware:
    """Hold every request body to the cap, the budget and the deadline before the
    application sees it."""

    def __init__(self, app, cap: int = BODY_MAX, budget: int | None = None, timeout: float | None = None):
        self.app = app
        self.cap = cap
        # Read when the app is built rather than when this module is imported, so that
        # a test can lower them for a server it starts.
        self.budget = BODIES_MAX if budget is None else budget
        self.timeout = BODY_TIMEOUT_S if timeout is None else timeout
        self.held = 0  # body bytes of the requests in progress, at most `budget`

    @property
    def too_large(self) -> tuple[int, bytes]:
        return _refusal(413, "PAYLOAD_TOO_LARGE", f"The request body may be at most {self.cap} bytes")

    @property
    def busy(self) -> tuple[int, bytes]:
        return _refusal(
            503, "UNAVAILABLE", "The server is receiving too many request bodies; retry the request"
        )

    @property
    def timed_out(self) -> tuple[int, bytes]:
        return _refusal(
            408, "REQUEST_TIMEOUT", f"The request body was not received within {self.timeout:g} s"
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        lengths = _declared(scope)
        if lengths is not None and any(_over(digits, self.cap) for digits in lengths):
            await _refuse(send, self.too_large)
            return

        empty = lengths is not None and all(digits == b"0" for digits in lengths)
        body = _Body(self, receive, send, complete=empty)
        try:
            if lengths is None:
                await self._read_whole(scope, body)
            else:
                await self.app(scope, body.receive, body.send)
        except ClientDisconnect:
            # The route read a body this middleware had refused, and was told the client
            # had gone: the refusal is the answer, and nothing is left to report.
            if not body.cut:
                raise
        finally:
            self.held -= body.size

    async def _read_whole(self, scope, body: _Body) -> None:
        """A body with no declared length: read here, whole, before the app is called."""
        buffer = bytearray()  # one buffer, however many chunks the body came in
        while True:
            message = await body.read()
            if message is None:
                return  # refused, and answered
            if message["type"] != "http.request":
                return  # the client went away mid-body: there is no one to answer
            buffer += message.get("body", b"")
            if not message.get("more_body", False):
                break
        whole = bytes(buffer)
        del buffer

        async def replay():
            # the body once, whole; after it, the server's own messages (a disconnect)
            nonlocal whole
            if whole is not None:
                message = {"type": "http.request", "body": whole, "more_body": False}
                whole = None  # held by the app from here on, not here as well
                return message
            return await body.receive()

        await self.app(scope, replay, body.send)
