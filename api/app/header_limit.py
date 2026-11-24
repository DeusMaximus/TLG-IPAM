"""The limits below the app: a request's head, a connection waiting for one, and a
connection the client has stopped reading (#132).

uvicorn calls the app only once a request's head (its request line, header lines and
the blank line that ends them) is complete, so nothing in the app, `BodyLimitMiddleware`
included, can see a head that is still arriving, or a connection that has sent
nothing. Measured at `7063421` on uvicorn 0.54 with httptools 0.9, the parser the image
runs: 8 MiB of one unfinished header was taken and the worker grew by 119 MiB, and a
connection that sent nothing, or that sent a header a byte at a time, was still open
after 75 s. uvicorn's own keep-alive timeout is armed only once a response has been
sent and is cancelled by the first byte that arrives after it, and httptools sets no
limit on a head's size.

`HeaderLimitProtocol` is uvicorn's httptools protocol with these limits added:

- **The size of a head** (`HEADER_MAX`): the bytes of it as received, from the first
  byte after the request before it ended to the end of its blank line, whatever its
  spelling. A head that passes it is refused with `431` in the REST error shape and the
  connection closed, and the app is not called; the server reads at most one byte past
  the limit before it refuses. To count exactly, reads reach the parser in pieces of at
  most `_PIECE` bytes, cut where a head ends (its first CRLF CRLF, once per head: the
  parser skips blank lines before a request line, so a CRLF CRLF there ends nothing,
  and the rest of that head is cut by size) and where a body with a declared length
  ends, and of one byte once a head reaches the limit. httptools says that a request
  has ended, not where, so the cuts are what put the next head's first byte at the
  start of a piece. A head that follows a chunked body in the same piece is the one
  case not counted exactly: it is counted from the start of that piece, at most
  `_PIECE` bytes over. Cutting a chunked body at each CRLF CRLF would end that
  exception, but its sender chooses how many there are, and a body of them cost the
  worker 1.3 s of parsing for 1.9 MB (#132, round 4).
- **What parsing costs.** No input makes the parser do more work per byte than plain
  uvicorn by more than a constant: it is handed one piece per read, as uvicorn does,
  plus at most one per `_PIECE` bytes and three per request (the end of its head, the
  end of a declared body, a byte at the limit).
- **The time for a head** (`HEADER_TIMEOUT_S`). A connection has that long, from when
  it opens and again from when its last request has been both received whole and
  answered, to deliver the next request's head whole. One that has not is closed
  without an answer, whether it sent nothing, part of a head, or a byte at a time. The
  time stops when the head is complete: a body then has `body_limit.BODY_TIMEOUT_S`,
  and a response (an MCP event stream held open) is never timed by it. uvicorn's
  keep-alive timeout (`serve.KEEP_ALIVE_S`) still closes a connection that sends
  nothing after a response, sooner.
- **One request ahead.** While a request is queued behind the one being answered
  (pipelined), nothing more is parsed and nothing more is read: what has arrived
  waits unparsed, so a connection holds at most one queued request and what one read
  brought. The piece a chunked body ended in is the one place a second request can
  complete before the parse stops: the connection then takes no more requests
  (`_stop_taking`): those taken are answered, the newest with the connection closed,
  and the rest is discarded.
- **A refusal after requests taken.** When the parser refuses what follows (malformed
  bytes) while a request received whole is still being answered or queued, uvicorn
  would write its `400` and close at once, in front of their answers, one of which may
  be a write that has committed. Instead the connection takes no more requests in the
  same way (`send_400_response`). A request refused part way through its own body has
  nothing ahead of it, and gets uvicorn's `400`.
- **Nothing more read** while bytes wait unparsed or the taking has stopped: uvicorn
  resumes reading whenever a request being answered asks for its body, and the
  transport wrapper (`_Transport`) holds that back.
  Without it, uvicorn queued every request one read held (788 small ones in 64 KiB).
- **A client that stops reading** (`STALL_TIMEOUT_S`). A response whose send buffer
  stays full (uvicorn has paused it) for that long without the client taking any of
  it, and a connection the server has closed whose client takes none of what it still
  holds for that long, are dropped (`abort`): `close()` alone waits for a flush that
  never comes. An
  event stream the client reads is never paused, so never dropped.

A head over the limit that arrives while another request on the connection is still
being answered (pipelined behind an event stream, say) drops the connection with no
answer, finished or not, so no refusal is written into that response or queued behind
it.

This subclasses uvicorn's `HttpToolsProtocol` and relies on its parser callbacks and
the attributes they set (`url`, `headers`, `cycle`, `pipeline`, `app`, `flow`), and on
the transport it is given passing `close` through: the tests in `test_header_limit.py`,
in process and against the image's server on loopback, are what show a uvicorn upgrade
has kept them. What the limits do not bound, how many connections there are and how
many one client opens, is left to the reverse proxy or tunnel (brief §4.3).
"""

import asyncio
import json

from uvicorn.protocols.http.httptools_impl import HttpToolsProtocol

from .body_limit import _length_digits

# The most bytes of a request's head the server takes, chosen for #132. A head the
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

# How long a client may leave the server's send buffer full, or leave unsent bytes on a
# connection the server has closed, before the connection is dropped, in seconds
# (#132). uvicorn pauses a response at 64 KiB unsent and resumes it at 16 KiB, so a
# client has to take 48 KiB in this time, under 1 KB/s; a proxy in front reads at its
# own speed. The same as the body deadline (`body_limit.BODY_TIMEOUT_S`).
STALL_TIMEOUT_S = 60

# The most bytes handed to the parser at once (module docstring): small enough that the
# one case counted from a piece's start is close, large enough that a body costs a
# few hundred calls per MB.
_PIECE = 4 * 1024

_CRLF_CRLF = b"\r\n\r\n"  # ends a head

_MESSAGE = f"The request line and headers may be at most {HEADER_MAX} bytes"
_BODY = json.dumps({"error": {"code": "HEADERS_TOO_LARGE", "message": _MESSAGE}}).encode()
_HEADERS = [
    (b"content-type", b"application/json"),
    (b"content-length", str(len(_BODY)).encode()),
    (b"connection", b"close"),
]


async def _too_large(scope, receive, send) -> None:
    """The answer to a complete head over the limit, served in the app's place."""
    await send({"type": "http.response.start", "status": 431, "headers": _HEADERS})
    await send({"type": "http.response.body", "body": _BODY})


class _Transport:
    """The connection's transport as uvicorn sees it: every `close`, uvicorn's own (a
    keep-alive timeout, `Connection: close`, a `400`) and this module's, starts the time
    the connection has to flush what it still holds, and uvicorn's `resume_reading` is
    held back while the protocol keeps reading paused."""

    def __init__(self, transport, protocol: "HeaderLimitProtocol") -> None:
        self._transport = transport
        self._protocol = protocol

    def close(self) -> None:
        self._transport.close()
        self._protocol._closed()

    def resume_reading(self) -> None:
        """uvicorn resumes reading whenever a request being answered asks for its body;
        not while bytes wait unparsed behind a queued request, or after the parser has
        refused what follows. The protocol resumes it itself when that ends."""
        if not (self._protocol._holding or self._protocol._stopped):
            self._transport.resume_reading()

    def __getattr__(self, name):
        return getattr(self._transport, name)


class HeaderLimitProtocol(HttpToolsProtocol):
    """uvicorn's httptools protocol with the limits of the module docstring."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._awaiting_head = True  # the parser is before or inside a request's head
        self._head_bytes = 0  # the bytes of that head received so far
        self._tail = b""  # its last bytes, where an end of head may begin
        self._body_left: int | None = None  # body bytes still due, when declared
        self._unparsed = b""  # what arrived while a request was queued
        self._holding = False  # reading is paused here while it waits
        self._dropped = False  # the connection was dropped inside a parse
        self._stopped = False  # it takes no more requests: those taken are answered (`_stop_taking`)
        self._head_cut = False  # the head being received has been cut at a CRLF CRLF
        self._piece = 0  # the size of the piece being parsed
        self._ended_in_piece = False  # a request ended inside it
        self._lost = False
        self._head_deadline: asyncio.TimerHandle | None = None
        self._stall: asyncio.TimerHandle | None = None
        self._flush_deadline: asyncio.TimerHandle | None = None
        self._flush_left = 0  # unsent bytes when the flush was last checked

    # The connection

    def connection_made(self, transport) -> None:  # type: ignore[override]
        super().connection_made(_Transport(transport, self))
        self._arm_deadline()

    def connection_lost(self, exc) -> None:
        self._lost = True
        for timer in (self._head_deadline, self._stall, self._flush_deadline):
            if timer is not None:
                timer.cancel()
        self._head_deadline = self._stall = self._flush_deadline = None
        super().connection_lost(exc)

    def data_received(self, data: bytes) -> None:
        if self._unparsed:
            data, self._unparsed = self._unparsed + data, b""
        self._parse(data)

    def _hold_reading(self) -> None:
        """Stop reading while bytes wait unparsed. uvicorn pauses reading when it queues
        a request but resumes it whenever the request being answered asks for its body,
        so without this what waits could grow by a read each time."""
        if not self.transport.is_closing():
            self._holding = True
            self.transport.pause_reading()  # again each time: uvicorn may have resumed it

    def pause_writing(self) -> None:
        super().pause_writing()
        if self._stall is None and not self._lost:
            self._stall = self.loop.call_later(STALL_TIMEOUT_S, self._stalled)

    def resume_writing(self) -> None:
        super().resume_writing()
        if self._stall is not None:
            self._stall.cancel()
            self._stall = None

    # Parsing, a piece at a time

    def _parse(self, data: bytes) -> None:
        view = memoryview(data)
        offset = 0
        while offset < len(data):
            if self._dropped or self._stopped or self.transport.is_closing():
                return
            if self.pipeline:  # a request is queued: parse no further until it runs
                self._unparsed = bytes(view[offset:])
                self._hold_reading()
                return
            size = self._piece_size(data, offset)
            piece = view[offset : offset + size]
            offset += size
            self._piece, self._ended_in_piece = size, False
            if self._awaiting_head:
                self._head_bytes += size
                self._tail = (self._tail + bytes(piece[-3:]))[-3:]
            super().data_received(piece)
            if self._dropped or self._stopped or self.transport.is_closing():
                return
            if self._awaiting_head and self._head_bytes > HEADER_MAX:
                self._refuse_unfinished_head()
                return
            if self.parser.should_upgrade():
                return  # httptools parses nothing after an `Upgrade` request, as before

    def _piece_size(self, data: bytes, offset: int) -> int:
        available = len(data) - offset
        if self._awaiting_head:
            room = HEADER_MAX - self._head_bytes
            limit = min(_PIECE, room, available) if room > 0 else 1
            if self._head_cut:
                return limit  # one cut per head (module docstring): by size from here
            window = self._tail + data[offset : offset + limit]
            end = window.find(_CRLF_CRLF)
            if end < 0:
                return limit
            # cut just after the end of the head, so the next byte starts what follows
            self._head_cut = True
            return end + len(_CRLF_CRLF) - len(self._tail)
        if self._body_left:
            return min(_PIECE, self._body_left, available)
        return min(_PIECE, available)  # a chunked body: by size (module docstring)

    def _resume(self) -> None:
        if self._stopped:
            return
        if self._unparsed and not self.pipeline and not self._lost:
            data, self._unparsed = self._unparsed, b""
            self._parse(data)
        if self._holding and not self._unparsed and not self._lost:
            self._holding = False
            if not self.flow.read_paused and not self.transport.is_closing():
                self.transport.resume_reading()  # passes the wrapper: no longer holding

    # The parser's callbacks

    def on_message_begin(self) -> None:
        super().on_message_begin()
        if self._ended_in_piece:  # after a chunked body: it began inside this piece
            self._head_bytes = self._piece

    def on_headers_complete(self) -> None:
        self._cancel_deadline()
        size, self._head_bytes, self._tail = self._head_bytes, 0, b""
        self._awaiting_head, self._head_cut = False, False
        if self.pipeline:
            # a second request ahead, which only a piece a chunked body ended in can
            # hold (every other end of a request is a cut): take no more requests
            self._stop_taking()
            return
        self._body_left = self._declared_length()
        if size <= HEADER_MAX:
            super().on_headers_complete()
            return
        if self._answering():
            # behind a response in progress (an event stream, which may never end): a
            # refusal queued behind it would hold the connection as long, so drop it,
            # writing nothing into that response
            self._drop()
            return
        # uvicorn's own path for a request it will not pass on (as it serves
        # `--limit-concurrency`'s 503), so the refusal is framed and closes the
        # connection as any response does
        app, self.app = self.app, _too_large
        try:
            super().on_headers_complete()
        finally:
            self.app = app

    def on_body(self, body: bytes) -> None:
        if self._dropped or self._stopped:
            return
        if self._body_left:
            self._body_left -= len(body)
        super().on_body(body)

    def on_message_complete(self) -> None:
        if self._dropped or self._stopped:
            return
        super().on_message_complete()
        self._awaiting_head, self._head_bytes, self._tail, self._head_cut = True, 0, b"", False
        self._body_left = None
        self._ended_in_piece = True
        self._arm_if_waiting()

    def on_response_complete(self) -> None:
        super().on_response_complete()
        self._arm_if_waiting()
        if (self._unparsed or self._holding) and not self.pipeline:
            self.loop.call_soon(self._resume)

    def send_400_response(self, msg: str) -> None:
        """The parser refused what follows the requests already taken. uvicorn writes its
        `400` and closes at once, which would put it in front of their answers (one may
        be a write that has committed): while one received whole is still being
        answered or queued, the connection takes nothing more instead and closes after
        the last of them is answered. A request refused part way through its own body
        is the one being answered, with nothing ahead of it: uvicorn's `400` is its
        answer."""
        if self._answering() and not self.cycle.more_body:
            self._stop_taking()
            return
        super().send_400_response(msg)

    def _stop_taking(self) -> None:
        """Take no more requests on this connection: those taken are answered, the
        newest of them with the connection closed, and nothing more is read or
        parsed, so the request being parsed and everything after it is discarded."""
        self._stopped = True
        self._awaiting_head = False
        self._cancel_deadline()
        self.cycle.keep_alive = False  # the newest taken, queued or being answered
        self.transport.pause_reading()

    # The limits

    def _declared_length(self) -> int | None:
        """The body's declared length, when one frames it (a single `Content-Length`,
        no `Transfer-Encoding`), so the piece that ends the body ends with it."""
        lengths = [value for name, value in self.headers if name == b"content-length"]
        if len(lengths) != 1 or any(name == b"transfer-encoding" for name, _ in self.headers):
            return None
        digits = _length_digits(lengths[0])
        return int(digits) if digits is not None and len(digits) <= 18 else None

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
        if not self.transport.is_closing():
            self.transport.close()

    def _stalled(self) -> None:
        self._stall = None
        if not self._lost:
            self.transport.abort()

    def _closed(self) -> None:
        """The transport was asked to close: it is dropped once `STALL_TIMEOUT_S` passes
        in which the client took none of what it still holds."""
        if not self._lost and self._flush_deadline is None:
            self._flush_left = self.transport.get_write_buffer_size()
            self._flush_deadline = self.loop.call_later(STALL_TIMEOUT_S, self._flush_check)

    def _flush_check(self) -> None:
        self._flush_deadline = None
        if self._lost:
            return
        left = self.transport.get_write_buffer_size()
        if 0 < left < self._flush_left:  # the client took some: another period
            self._flush_left = left
            self._flush_deadline = self.loop.call_later(STALL_TIMEOUT_S, self._flush_check)
            return
        self.transport.abort()

    def _drop(self) -> None:
        """Drop the connection without an answer, from inside a parse: the rest of the
        piece being parsed is ignored, and so is anything still unsent."""
        self._dropped = True
        self._awaiting_head = False
        self._cancel_deadline()
        self.transport.abort()

    def _refuse_unfinished_head(self) -> None:
        self._awaiting_head = False
        self._cancel_deadline()
        if self.transport.is_closing():
            return
        if self._answering():
            self._drop()
            return
        status = b"HTTP/1.1 431 Request Header Fields Too Large\r\n"
        fields = [*self.server_state.default_headers, *_HEADERS]
        self.transport.write(status + b"".join(n + b": " + v + b"\r\n" for n, v in fields) + b"\r\n" + _BODY)
        self.transport.close()
