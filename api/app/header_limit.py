"""The limits on a request's head and on a connection waiting for one (#132).

uvicorn calls the app only once a request's head (its request line and header fields)
is complete, so nothing in the app, `BodyLimitMiddleware` included, can see a head
that is still arriving, or a connection that has sent nothing. Measured at `7063421`
on uvicorn 0.54 with httptools 0.9, the parser the image runs: 8 MiB of one unfinished
header was taken and the worker grew by 119 MiB, and a connection that sent nothing,
or that sent a header a byte at a time, was still open after 75 s. uvicorn's own
keep-alive timeout is armed only once a response has been sent and is cancelled by
the first byte that arrives after it, and httptools sets no limit on a head's size.

`HeaderLimitProtocol` is uvicorn's httptools protocol with two limits added, both
held below the app:

- **The size** (`HEADER_MAX`). A head whose bytes received pass it while it is still
  unfinished is refused with `431` in the REST error shape and the connection closed,
  at the read that takes it past (bytes are counted as they are read, so up to one
  read beyond the limit is held before the refusal). A complete head is measured as it
  is spelt with one space after each field's colon (`_head_size`: what was received,
  for a head sent the way clients send it) and refused the same way when it passes the
  limit, however few reads it came in, so no request whose head is larger reaches the
  app. When another request on the connection is still being answered (the head is
  pipelined behind it), the connection is closed without an answer instead, so no
  refusal is written into that response.
- **The time** (`HEADER_TIMEOUT_S`). A connection has that long, from when it opens and
  again from when its last request has been both received whole and answered, to
  deliver the next request's head whole. One that has not is closed without an
  answer, whether it sent nothing, part of a head, or a byte at a time. The time stops
  when the head is complete: a body then has `body_limit.BODY_TIMEOUT_S`, and a
  response (an MCP event stream held open) is never timed here. uvicorn's keep-alive
  timeout (`serve.KEEP_ALIVE_S`) still closes a connection that sends nothing after a
  response, sooner.

This subclasses uvicorn's `HttpToolsProtocol` and relies on its parser callbacks and
the attributes they set (`url`, `headers`, `cycle`, `pipeline`, `app`): the tests that
start the image's server on loopback (`test_header_limit.py`) are what show a uvicorn
upgrade has kept them. What the limits do not bound, how many connections there are
and how many one client opens, is left to the reverse proxy or tunnel (brief §4.3).
"""

import asyncio
import json

from uvicorn.protocols.http.httptools_impl import HttpToolsProtocol

# The most bytes of a request's head the server holds, chosen for #132. A head the
# server's own clients send (the UI, the agent, an MCP client) is under 2 KiB, and a
# proxy's own fields (forwarding headers, an access proxy's assertion and cookie) add a
# few KiB; nginx's defaults pass at most 32 KiB in all. Cloudflare passes up to 128 KB,
# so a head between the two is refused here, which only a client sending that much
# meets. It is the size of uvicorn's own buffer for a body the app has not read, so a
# connection holds about as much of a head as it already could of a body.
HEADER_MAX = 64 * 1024

# How long a connection has to deliver a request's head whole, in seconds, from when
# it opens and from when its last request was received and answered (#132). Every
# client of the server sends its head at once; at this limit a connection that sends
# nothing holds its memory for 10 s, and holding many open means opening them again
# every 10 s.
HEADER_TIMEOUT_S = 10

_MESSAGE = f"The request line and headers may be at most {HEADER_MAX} bytes"
_BODY = json.dumps({"error": {"code": "HEADERS_TOO_LARGE", "message": _MESSAGE}}).encode()
_HEADERS = [
    (b"content-type", b"application/json"),
    (b"content-length", str(len(_BODY)).encode()),
    (b"connection", b"close"),
]


def _head_size(method: bytes, target: bytes, headers: list[tuple[bytes, bytes]]) -> int:
    """A complete head's size in bytes, spelt as clients send it: `METHOD target
    HTTP/1.1` and one `name: value` line per field, each ending in CRLF, and the blank
    line. A head received in that spelling measures what was received, so it is held to
    the limit the same way whether it arrived complete or a read at a time."""
    request_line = len(method) + 1 + len(target) + len(b" HTTP/1.1\r\n")
    return request_line + sum(len(name) + 2 + len(value) + 2 for name, value in headers) + 2


async def _too_large(scope, receive, send) -> None:
    """The answer to a complete head over the limit, served in the app's place."""
    await send({"type": "http.response.start", "status": 431, "headers": _HEADERS})
    await send({"type": "http.response.body", "body": _BODY})


class HeaderLimitProtocol(HttpToolsProtocol):
    """uvicorn's httptools protocol with a limit on a head's size and on the time a
    connection has to deliver one (module docstring)."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._awaiting_head = True  # the parser is before or inside a request's head
        self._head_bytes = 0  # bytes read while it was
        self._head_deadline: asyncio.TimerHandle | None = None

    # The connection

    def connection_made(self, transport) -> None:  # type: ignore[override]
        super().connection_made(transport)
        self._arm_deadline()

    def connection_lost(self, exc) -> None:
        self._cancel_deadline()
        super().connection_lost(exc)

    def data_received(self, data: bytes) -> None:
        if self._awaiting_head:
            self._head_bytes += len(data)
        super().data_received(data)
        if self._awaiting_head and self._head_bytes > HEADER_MAX:
            self._refuse_unfinished_head()

    # The parser's callbacks

    def on_headers_complete(self) -> None:
        self._cancel_deadline()
        self._awaiting_head = False
        self._head_bytes = 0
        if _head_size(self.parser.get_method(), self.url, self.headers) <= HEADER_MAX:
            super().on_headers_complete()
            return
        # uvicorn's own path for a request it will not pass on (as it serves
        # `--limit-concurrency`'s 503), so the refusal is framed, queued behind a
        # pipelined request and closes the connection as any response does
        app, self.app = self.app, _too_large
        try:
            super().on_headers_complete()
        finally:
            self.app = app

    def on_message_complete(self) -> None:
        super().on_message_complete()
        self._awaiting_head = True  # what follows is the next request's head
        self._head_bytes = 0
        self._arm_if_waiting()

    def on_response_complete(self) -> None:
        super().on_response_complete()
        self._arm_if_waiting()

    # The limits

    def _answering(self) -> bool:
        return self.cycle is not None and not self.cycle.response_complete

    def _arm_if_waiting(self) -> None:
        """Start the time for the next head once the connection is waiting for one: the
        last request has been received whole and answered (a response can end before
        its request's body has arrived, and a request is received before it is
        answered, so this is checked at both ends)."""
        if self._awaiting_head and not self._answering() and not self.transport.is_closing():
            self._arm_deadline()

    def _arm_deadline(self) -> None:
        self._cancel_deadline()
        self._head_deadline = self.loop.call_later(HEADER_TIMEOUT_S, self._deadline_passed)

    def _cancel_deadline(self) -> None:
        if self._head_deadline is not None:
            self._head_deadline.cancel()
            self._head_deadline = None

    def _deadline_passed(self) -> None:
        self._head_deadline = None
        if not self.transport.is_closing() and not self._answering():
            self.transport.close()

    def _refuse_unfinished_head(self) -> None:
        self._awaiting_head = False
        self._cancel_deadline()
        if self.transport.is_closing():
            return
        if not self._answering():
            status = b"HTTP/1.1 431 Request Header Fields Too Large\r\n"
            fields = [*self.server_state.default_headers, *_HEADERS]
            self.transport.write(status + b"".join(n + b": " + v + b"\r\n" for n, v in fields) + b"\r\n" + _BODY)
        self.transport.close()
