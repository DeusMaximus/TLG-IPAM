"""The link window and the owner's consent (v0.2 spec §6.3, #27).

Linking a client needs a window the owner opens from the private UI. While one is open
the server registers clients and takes authorise requests (`oauth.linking_open` asks
`is_open`); outside one it does neither. Opening a window mints the **link code**, which
the owner types on the consent page. The code is the consent page's only credential:
it is what proves that the person approving is the owner (spec §7.2, invariant 6).

- **One window at a time**, in this process's memory (one worker): a restart cancels a
  link in progress and nothing else. Opening a window while one is open replaces it,
  and the old code stops working.
- It ends on the first successful link, after `LIFETIME_S`, or when the owner cancels
  or replaces it. **Nothing a caller without the owner key sends ends, shortens or
  blocks it.**
- **Wrong codes are counted for the request, not the window**: the
  `WRONG_CODES_MAX`th wrong code typed on one request's consent page ends that request,
  and only that request. The right code links whatever wrong codes other requests have
  had. What bounds guessing is the code itself (`CODE_ALPHABET` ** `CODE_LENGTH`, about
  5.9e14 values) against the posts the server can take in the window's `LIFETIME_S`
  (spec §6.3 gives the measurement), not a count an outsider could run out for the
  owner. The owner is told: the window's `wrong_codes` (all its requests together), and
  one change-log entry for the window's first wrong code, so an outsider can write the
  log at most once a window.
- **A registration is not a link**: any number of them, in any window, neither closes
  nor uses one up. A client registered in one window can authorise in a later one.
- **Pending transactions end with the window**: every way a window ends drops them all
  (`oauth.pending.reset`), and so does opening one, so no request a client sent in an
  earlier window can be approved in a later one.
- **Ending a window and approving a link are serialised** (`_transitions`): opening,
  cancelling and an approval through its commit each hold the one lock. So a cancel or
  a replacement the owner has been answered for is never followed by a grant from the
  window it ended: an approval in flight finishes first (the cancel's answer then says
  `linked`), and an approval that has not reached the lock finds its window gone and
  links nothing. A window whose approval is in flight does not expire under it.
- The code is `CODE_LENGTH` characters of `CODE_ALPHABET`, drawn by `secrets`, kept
  only as its SHA-256 and compared in constant time.
- **The consent form** is bound to its transaction by `consent_token`: an HMAC of the
  transaction id under a key drawn for the window, so it is valid for that transaction
  in that window only. A form post whose `Origin` is not the server's own is refused
  (`origin_allowed`). Neither is counted as a wrong code, and neither compares a code.
- **Writes.** A window records whether the link it makes may write (`can_write`), which
  is fixed into the grant. Until #28 brings `MCP_REMOTE_WRITES`, no window may allow it
  (`writes_may_be_allowed`), so no grant is issued with `can_write` the owner could not
  have chosen with the setting off, whichever of #27 and #28 merges first.

Audit (`entity_type="link_window"`): opening a window (`source` the owner's, `ui` or
`api`) and its first wrong code (`source="oauth"`). The summary never holds the code, a
client's name or a redirect URI. Cancelling, expiry, replacement and later wrong codes
are not logged; the grant a link writes is (`oauth.approve`).
"""

import asyncio
import base64
import hashlib
import hmac
import logging
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import ApiError, database_unavailable
from . import changes, oauth

log = logging.getLogger(__name__)

LIFETIME_S = 600
CODE_LENGTH = 10
# No 0/O, 1/I/L, or U: what is left reads one way in any font, in either case
CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
# Wrong codes one request may have before it is ended; the window is not
WRONG_CODES_MAX = 5
# What a typed code may hold besides its characters, which are dropped
CODE_SEPARATORS = " -"
# The most characters of a typed code that are looked at: past this it is no code
_TYPED_MAX = 64

AUDIT_ENTITY = "link_window"

# How the latest window ended, for the owner's UI (`state_out`)
LINKED, EXPIRED, CANCELLED, REPLACED, FAILED = (
    "linked",
    "expired",
    "cancelled",
    "replaced",
    "failed",
)

NOT_CONFIGURED = (
    "Remote MCP is not configured: a link window needs PUBLIC_BASE_URL to be set."
)
WRITES_OFF = "This server does not allow remote writes, so a link can only be read-only."


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def writes_may_be_allowed() -> bool:
    """Whether a window may be opened with `can_write`. Never, until #28 makes this
    `MCP_REMOTE_WRITES`: a grant's `can_write` is then always one the owner could have
    chosen with the setting off."""
    return False


@dataclass
class _Window:
    id: str
    code_hash: str
    can_write: bool
    opened_at: datetime
    # by the monotonic clock, so a wall clock stepped while it is open moves nothing
    deadline: float
    csrf_key: bytes
    # wrong codes typed on any of its requests, for the owner; this ends nothing
    wrong_codes: int = 0
    # whether the window's one change-log entry about wrong codes is written
    wrong_logged: bool = False
    # a correct code is being approved: no second link can start on this window
    claimed: bool = False

    @property
    def expires_at(self) -> datetime:
        return self.opened_at + timedelta(seconds=LIFETIME_S)

    def seconds_left(self) -> int:
        return max(0, int(self.deadline - time.monotonic()))


_current: _Window | None = None
# how the latest window ended, and when: (reason, at)
_last_end: tuple[str, datetime] | None = None
# wrong codes by pending transaction, in the open window; only pending ones are kept
_wrong: dict[str, int] = {}
# held by every change of which window is current and by an approval through its commit
_transitions = asyncio.Lock()


def _end(reason: str, at: datetime | None = None) -> None:
    """End the open window, and every transaction pending in it."""
    global _current, _last_end
    _current = None
    _last_end = (reason, at or _now())
    _wrong.clear()
    oauth.pending.reset()


def _live() -> _Window | None:
    """The open window, or None; a window whose time is up is ended here, unless an
    approval holds it: that approval decides how it ends."""
    window = _current
    if window is not None and not window.claimed and time.monotonic() >= window.deadline:
        _end(EXPIRED, window.expires_at)
        return None
    return window


def is_open() -> bool:
    """Whether a link window is open now: what `oauth.linking_open` answers."""
    return _live() is not None


def reset() -> None:
    """No window, as at start-up (the tests' fixture)."""
    global _current, _last_end, _transitions
    _current = None
    _last_end = None
    _wrong.clear()
    _transitions = asyncio.Lock()


def state_out(*, configured: bool) -> dict:
    """The window as the owner's routes answer with it: whether one is open, what its
    link may do, how long it has left by the server's clock (`expires_in_s`, which a
    countdown starts from, so it does not depend on the browser's clock) and how many
    wrong codes its requests have had, or how the latest one ended. Never the code:
    that is in the one answer that opened it."""
    window = _live() if configured else None
    if window is not None:
        return {
            "configured": True,
            "open": True,
            "can_write": window.can_write,
            "opened_at": window.opened_at,
            "expires_at": window.expires_at,
            "expires_in_s": window.seconds_left(),
            "wrong_codes": window.wrong_codes,
            "ended": None,
            "ended_at": None,
        }
    ended, ended_at = _last_end if configured and _last_end else (None, None)
    return {
        "configured": configured,
        "open": False,
        "can_write": None,
        "opened_at": None,
        "expires_at": None,
        "expires_in_s": None,
        "wrong_codes": None,
        "ended": ended,
        "ended_at": ended_at,
    }


def _snapshot(window: _Window) -> dict:
    return {
        "can_write": window.can_write,
        "opened_at": window.opened_at.isoformat(),
        "expires_at": window.expires_at.isoformat(),
    }


async def open_window(
    session: AsyncSession, *, configured: bool, can_write: bool, source: str
) -> tuple[dict, str]:
    """Open a link window, replacing any that is open, and return its state and the
    link code in plain: the one time the code is given out.

    Refused with nothing opened or logged when remote MCP is not configured (`409
    OAUTH_NOT_CONFIGURED`) and when `can_write` is asked while no window may allow it
    (`409 REMOTE_WRITES_OFF`, `writes_may_be_allowed`). The audit entry is committed
    before the window opens, so a database that refuses it opens nothing (`503`). An
    approval in flight on the window it replaces finishes first.
    """
    if not configured:
        raise ApiError("OAUTH_NOT_CONFIGURED", NOT_CONFIGURED, 409)
    if can_write and not writes_may_be_allowed():
        raise ApiError("REMOTE_WRITES_OFF", WRITES_OFF, 409)
    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    window = _Window(
        id=str(uuid.uuid4()),
        code_hash=_sha256(code),
        can_write=can_write,
        opened_at=_now(),
        deadline=time.monotonic() + LIFETIME_S,
        csrf_key=secrets.token_bytes(32),
    )
    async with _transitions:
        changes.record(
            session,
            entity_type=AUDIT_ENTITY,
            entity_id=window.id,
            action="open",
            summary=f"link window opened for a {'read-write' if can_write else 'read-only'} link",
            source=source,
            after=_snapshot(window),
        )
        try:
            await session.commit()
        except OperationalError as exc:
            await session.rollback()
            raise database_unavailable("link window", exc) from exc
        global _current, _last_end
        if _live() is not None:
            _end(REPLACED)
        # transactions left from a window that ran out go too
        oauth.pending.reset()
        _wrong.clear()
        _current, _last_end = window, None
        return state_out(configured=True), code


async def cancel(*, configured: bool) -> dict:
    """End the open window, if there is one; the state after. An approval in flight on
    it finishes first, so the answer is true of what happened: `linked` if it linked."""
    if configured:
        async with _transitions:
            if _live() is not None:
                _end(CANCELLED)
    return state_out(configured=configured)


# --- The consent page ---


def origin_allowed(origin: str | None, public_base_url: str) -> bool:
    """Whether a form post may be taken from a page with this `Origin`: none (a client
    that is not a browser), or the server's own public origin. A browser sends the
    origin of the page that posted, `null` for an opaque one."""
    return origin is None or origin == public_base_url


def consent_token(window: _Window, txn_id: str) -> str:
    mac = hmac.new(window.csrf_key, b"consent:" + txn_id.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def _token_matches(window: _Window, txn_id: str, presented: str | None) -> bool:
    return isinstance(presented, str) and hmac.compare_digest(
        consent_token(window, txn_id).encode(), presented.encode()
    )


def typed_code(presented: str | None) -> str | None:
    """A typed link code in the form codes are drawn in, or None when it cannot be one.
    Only ASCII is looked at: the `CODE_SEPARATORS` (space, hyphen) are dropped and
    letters taken in either case, so no other character becomes a code character by
    case mapping (`ſ` to `S`, `ß` to `SS`)."""
    if not isinstance(presented, str) or len(presented) > _TYPED_MAX or not presented.isascii():
        return None
    value = presented.translate({ord(c): None for c in CODE_SEPARATORS}).upper()
    if len(value) != CODE_LENGTH or any(c not in CODE_ALPHABET for c in value):
        return None
    return value


def redirect_host(redirect_uri: str) -> str:
    """The host the code is sent to, as the consent page shows it (spec §6.3): parsed
    from the redirect URI the request was validated for, in its ASCII form (an
    internationalised name is already `xn--…` there, since the URL type converts it
    at registration and at authorise; anything else is converted here), an IPv6
    address in brackets. Never taken from the client's name."""
    host = urlsplit(redirect_uri).hostname or ""
    if not host.isascii():
        host = host.encode("idna").decode("ascii")
    return f"[{host}]" if ":" in host else host


# What the consent page is shown (`consent.py` renders it)
NO_WINDOW = "no_window"  # no window open (a link already made with it included)
NOT_PENDING = "not_pending"  # unknown, approved, refused, or expired with its window
REQUEST_ENDED = "request_ended"  # this request had its WRONG_CODES_MAX wrong codes
START_AGAIN = "start_again"  # its registration was reclaimed while it waited
FORGED = "forged"  # not a form this server gave out for this transaction
BUSY = "busy"  # a link is being approved on this window
MALFORMED = "malformed"  # not in the form of a code: not counted
WRONG = "wrong"  # counted for the request
LINKED_NOW = "linked"
DENIED = "denied"
UNAVAILABLE = "unavailable"  # the database refused the grant
FAILED_NOW = "failed"  # the approval failed unexpectedly; the window was ended


@dataclass(frozen=True)
class Consent:
    """What the consent page shows for a pending transaction."""

    txn_id: str
    token: str
    client_name: str | None
    host: str
    # plain http to this machine (a loopback redirect), not https to a host
    loopback: bool
    can_write: bool
    seconds_left: int
    # wrong codes this request may still have
    attempts_left: int


@dataclass(frozen=True)
class Outcome:
    kind: str
    consent: Consent | None = None
    # where the browser goes back to the client, on a link
    redirect: str | None = None


async def _consent(session: AsyncSession, window: _Window, txn_id: str) -> Outcome:
    """The consent page's content for `txn_id`, or why there is none."""
    txn = oauth.pending.get(txn_id)
    if txn is None:
        return Outcome(NOT_PENDING)
    client = await oauth.get_client(session, txn.client_id)
    if client is None:
        oauth.pending.discard(txn_id)
        return Outcome(START_AGAIN)
    return Outcome(
        "consent",
        Consent(
            txn_id=txn_id,
            token=consent_token(window, txn_id),
            client_name=client.client_name,
            host=redirect_host(txn.redirect_uri),
            loopback=urlsplit(txn.redirect_uri).scheme == "http",
            can_write=window.can_write,
            seconds_left=window.seconds_left(),
            attempts_left=WRONG_CODES_MAX - _wrong.get(txn_id, 0),
        ),
    )


async def show(session: AsyncSession, txn_id: str | None) -> Outcome:
    """What `GET /mcp/consent?txn=…` shows: the request, while it and its window last."""
    window = _live()
    if window is None:
        return Outcome(NO_WINDOW)
    if not isinstance(txn_id, str):
        return Outcome(NOT_PENDING)
    return await _consent(session, window, txn_id)


async def _count_wrong(session: AsyncSession, window: _Window, txn_id: str) -> bool:
    """Count a wrong code for the request and the window. True when the request has had
    its `WRONG_CODES_MAX` and is ended. The window's first wrong code is logged, once: an
    outsider can write the change log at most once a window."""
    window.wrong_codes += 1
    # only pending requests are counted, so the counts are as bounded as `pending`
    for gone in [t for t in _wrong if oauth.pending.get(t) is None]:
        del _wrong[gone]
    _wrong[txn_id] = _wrong.get(txn_id, 0) + 1
    if not window.wrong_logged:
        window.wrong_logged = True
        changes.record(
            session,
            entity_type=AUDIT_ENTITY,
            entity_id=window.id,
            action="wrong_code",
            summary="a wrong link code was typed on a consent page of this link window",
            source=oauth.AUDIT_SOURCE,
            after=_snapshot(window),
        )
        try:
            await session.commit()
        except OperationalError as exc:
            await session.rollback()
            window.wrong_logged = False
            log.warning(
                "link window: a wrong code was not recorded (%s)", type(exc.orig).__name__
            )
    if _wrong.get(txn_id, 0) >= WRONG_CODES_MAX:
        _wrong.pop(txn_id, None)
        oauth.pending.discard(txn_id)
        return True
    return False


async def submit(
    session: AsyncSession,
    *,
    txn_id: str | None,
    token: str | None,
    code: str | None,
    deny: bool,
) -> Outcome:
    """A consent form posted. In order, and nothing later is looked at once a step
    refuses: an open window; the transaction pending in it; the form's token for that
    transaction (so no code is compared for a transaction whose page was not served);
    no link already being approved on the window; then the code.

    - A code not in the form codes are drawn in is refused uncounted: it cannot match.
    - A wrong code counts against its request: the `WRONG_CODES_MAX`th ends that request
      (the client starts again), never the window.
    - The right code claims the window and approves the transaction (`_link`).
    - `deny` drops the transaction and leaves the window as it is.
    """
    window = _live()
    if window is None:
        return Outcome(NO_WINDOW)
    if not isinstance(txn_id, str) or oauth.pending.get(txn_id) is None:
        return Outcome(NOT_PENDING)
    if not _token_matches(window, txn_id, token):
        return Outcome(FORGED)
    if deny:
        oauth.pending.discard(txn_id)
        return Outcome(DENIED)
    if window.claimed:
        return Outcome(BUSY)
    typed = typed_code(code)
    if typed is not None and hmac.compare_digest(_sha256(typed), window.code_hash):
        return await _link(session, window, txn_id)
    if typed is not None and await _count_wrong(session, window, txn_id):
        return Outcome(REQUEST_ENDED)
    # the page again, with what was wrong: unless the request or its window went meanwhile
    if _live() is not window:
        return Outcome(NO_WINDOW)
    shown = await _consent(session, window, txn_id)
    if shown.consent is None:
        return shown
    return Outcome(WRONG if typed is not None else MALFORMED, shown.consent)


async def _link(session: AsyncSession, window: _Window, txn_id: str) -> Outcome:
    """The right code: claim the window, then approve the transaction under the lock
    that opening and cancelling hold, so the window cannot end between the approval's
    check and its commit.

    - A link closes the window.
    - When the approval is refused (the registration was reclaimed while it waited, or
      the transaction is gone) or the database refuses the grant, nothing was linked and
      the window stays open, its requests' counts unchanged: the code was right, so the
      client is started again in it.
    - When the window was cancelled or replaced while this waited for the lock, nothing
      is approved.
    - Any other failure ends the window (`failed`): where it came from is not known, so
      no second link is risked on it; the owner opens another.
    """
    # `submit` checks `claimed` and the code and calls this with nothing awaited in
    # between, and nothing is awaited here before the claim: two posts of the right
    # code at once cannot both pass it.
    window.claimed = True
    try:
        async with _transitions:
            if _current is not window:
                return Outcome(NO_WINDOW)
            try:
                redirect = await oauth.approve(session, txn_id, can_write=window.can_write)
            except oauth.Refused:
                return Outcome(START_AGAIN)
            except ApiError:
                # the database refused the grant (`database_unavailable`), with nothing
                # committed; the transaction was taken, so the client is started again
                return Outcome(UNAVAILABLE)
            except Exception:
                log.exception("link window: the approval failed; the window is ended")
                _end(FAILED)
                return Outcome(FAILED_NOW)
            _end(LINKED)
            return Outcome(LINKED_NOW, redirect=redirect)
    finally:
        window.claimed = False
