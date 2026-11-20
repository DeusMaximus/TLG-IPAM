"""The one request-body cap (#60): every HTTP request to the app, `/mcp` included.

`BodyLimitMiddleware` wraps the whole application, so it runs before routing, before
any route dependency (the owner-key and agent-token checks among them) and before
FastAPI or FastMCP reads or parses a body. A body over `BODY_MAX` is refused with
`413 PAYLOAD_TOO_LARGE` in the REST error shape, and the application is never called:

- A declared `Content-Length` over the cap is refused with nothing read. One at or
  under it is passed through untouched, to be read when (and if) the route reads it:
  the HTTP server frames the body by that length and delivers no byte past it.
- Any other body is read here, counting, and refused as soon as it would pass the cap;
  one that fits is handed on whole. So such a body is held before authentication, but
  never more than the cap of it. That is a body with no `Content-Length`, one whose
  `Content-Length` is not a decimal number, and any request with a `Transfer-Encoding`
  header whatever its `Content-Length` says: a server that accepts both headers frames
  the body by `Transfer-Encoding` (RFC 9112 §6.3; h11 does, httptools refuses the
  request), so the declared length says nothing about how much will arrive.

A declared length is read as the HTTP server passes it on, which is not always plain
digits (#89). uvicorn's httptools parser accepts a value with whitespace after the
digits and one with any number of leading zeroes, and hands the header on as it came;
h11 strips the whitespace and refuses the zeroes with `400`. So optional whitespace
(space and tab, RFC 9110 §5.6.3) at either end is dropped, and the digits are compared
with the cap by their count before any is converted, since `int()` refuses a string
of more than 4,300 digits (Python's integer-conversion guard, left on).

The refusal closes the connection (`Connection: close`), so the server stops reading
the rest of an oversized body rather than draining it to reuse the connection.
"""

import json

# The largest request body the server reads, in bytes, chosen for #60 from the largest
# legitimate requests (brief §4.3): an agent's result set for a full /20 (4,094 records,
# each with a MAC and a 253-character PTR name) is about 1.3 MB, and the UI's bulk
# import of 1,000 hosts with every length-bounded field at its maximum about 1.15 MB.
BODY_MAX = 2 * 1024 * 1024

_CONTENT_LENGTH = b"content-length"
_TRANSFER_ENCODING = b"transfer-encoding"
_OWS = b" \t"  # optional whitespace around a field value (RFC 9110 §5.6.3)
_REFUSAL = json.dumps(
    {
        "error": {
            "code": "PAYLOAD_TOO_LARGE",
            "message": f"The request body may be at most {BODY_MAX} bytes",
        }
    }
).encode()


async def _refuse(send) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(_REFUSAL)).encode()),
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": _REFUSAL})


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


def _declared_over(scope, cap: int) -> bool | None:
    """Whether the request's declared `Content-Length` is over `cap`, or None when no
    declared length frames the body: when it has none, one that is not a decimal number
    (which neither of uvicorn's parsers passes on; another server might), or any
    `Transfer-Encoding`, which frames the body instead. The largest of repeated headers
    counts."""
    headers = [(k.lower(), v) for k, v in scope.get("headers", ())]
    if any(k == _TRANSFER_ENCODING for k, _ in headers):
        return None
    lengths = [_length_digits(v) for k, v in headers if k == _CONTENT_LENGTH]
    if not lengths or None in lengths:
        return None
    return any(_over(digits, cap) for digits in lengths)


class BodyLimitMiddleware:
    """Refuse a request body over `cap` bytes before the application sees the request."""

    def __init__(self, app, cap: int = BODY_MAX):
        self.app = app
        self.cap = cap

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        over = _declared_over(scope, self.cap)
        if over is not None:
            if over:
                await _refuse(send)
                return
            await self.app(scope, receive, send)
            return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return  # the client went away mid-body: there is no one to answer
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > self.cap:
                await _refuse(send)
                return
            body += chunk
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay():
            # the body once, whole; after it, the server's own messages (a disconnect)
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
