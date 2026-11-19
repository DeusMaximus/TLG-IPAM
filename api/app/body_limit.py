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
  `Content-Length` is not a plain number, and any request with a `Transfer-Encoding`
  header whatever its `Content-Length` says: a server that accepts both headers frames
  the body by `Transfer-Encoding` (RFC 9112 §6.3; h11 does, httptools refuses the
  request), so the declared length says nothing about how much will arrive.

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


def _declared_length(scope) -> int | None:
    """The request's `Content-Length` when it frames the body, else None: when it has
    none, one that is not a plain decimal number (which uvicorn refuses itself; another
    server might not), or any `Transfer-Encoding`, which frames the body instead. The
    largest of repeated headers counts."""
    headers = [(k.lower(), v) for k, v in scope.get("headers", ())]
    if any(k == _TRANSFER_ENCODING for k, _ in headers):
        return None
    values = [v for k, v in headers if k == _CONTENT_LENGTH]
    if not values or not all(v.isdigit() for v in values):  # bytes: ASCII digits only
        return None
    return max(int(v) for v in values)


class BodyLimitMiddleware:
    """Refuse a request body over `cap` bytes before the application sees the request."""

    def __init__(self, app, cap: int = BODY_MAX):
        self.app = app
        self.cap = cap

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = _declared_length(scope)
        if declared is not None:
            if declared > self.cap:
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
