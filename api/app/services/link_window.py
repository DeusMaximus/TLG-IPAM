"""The link window and the owner's consent (v0.2 spec §6.3, #27).

Linking a client needs a window the owner opens from the private UI. While one is open
the server registers clients and takes authorise requests (`oauth.linking_open` asks
`is_open`); outside one it does neither. Opening a window mints the **link code**, which
the owner types on the consent page. The code is the consent page's only credential:
it is what proves that the person approving is the owner (spec §7.2, invariant 6).

- **One window at a time**, in this process's memory (one worker): a restart cancels a
  link in progress and nothing else. Opening a window while one is open replaces it,
  and the old code stops working.
- It ends on the first successful link, after `LIFETIME_S`, when the owner cancels it,
  or when `WRONG_CODES_MAX` wrong codes have been typed into its consent pages (the
  window is "burned"). Wrong codes are counted for the window, not the transaction, so
  an outsider who sends its own authorise requests gets five guesses in all.
- **A registration is not a link**: any number of them, in any window, neither closes
  nor uses one up. A client registered in one window can authorise in a later one.
- **Pending transactions end with the window**: every way a window ends drops them all
  (`oauth.pending.reset`), and so does opening one, so no request a client sent in an
  earlier window can be approved in a later one.
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
`api`) and burning one (`source="oauth"`). The summary never holds the code, a client's
name or a redirect URI. Cancelling, expiry and replacement are not logged; the grant a
link writes is (`oauth.approve`).
"""

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
WRONG_CODES_MAX = 5
# The most characters of a typed code that are looked at: past this it is no code
_TYPED_MAX = 64

AUDIT_ENTITY = "link_window"

# How the latest window ended, for the owner's UI (`state_out`)
LINKED, BURNED, EXPIRED, CANCELLED, REPLACED = "linked", "burned", "expired", "cancelled", "replaced"

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
    wrong: int = 0
    # a correct code is being approved: no second link can start on this window
    claimed: bool = False

    @property
    def expires_at(self) -> datetime:
        return self.opened_at + timedelta(seconds=LIFETIME_S)

    def seconds_left(self) -> int:
        return max(0, int(self.deadline - time.monotonic()))

    def attempts_left(self) -> int:
        return WRONG_CODES_MAX - self.wrong


_current: _Window | None = None
# how the latest window ended, and when: (reason, at)
_last_end: tuple[str, datetime] | None = None


def _end(reason: str, at: datetime | None = None) -> None:
    """End the open window, and every transaction pending in it."""
    global _current, _last_end
    _current = None
    _last_end = (reason, at or _now())
    oauth.pending.reset()


def _live() -> _Window | None:
    """The open window, or None; a window whose time is up is ended here."""
    window = _current
    if window is not None and time.monotonic() >= window.deadline:
        _end(EXPIRED, window.expires_at)
        return None
    return window


def is_open() -> bool:
    """Whether a link window is open now: what `oauth.linking_open` answers."""
    return _live() is not None


def reset() -> None:
    """No window, as at start-up (the tests' fixture)."""
    global _current, _last_end
    _current = None
    _last_end = None


def state_out(*, configured: bool) -> dict:
    """The window as the owner's routes answer with it: whether one is open, what its
    link may do, and how long it has left by the server's clock (`expires_in_s`, which a
    countdown starts from, so it does not depend on the browser's clock), or how the
    latest one ended. Never the code: that is in the one answer that opened it."""
    window = _live() if configured else None
    if window is not None:
        return {
            "configured": True,
            "open": True,
            "can_write": window.can_write,
            "opened_at": window.opened_at,
            "expires_at": window.expires_at,
            "expires_in_s": window.seconds_left(),
            "attempts_left": window.attempts_left(),
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
        "attempts_left": None,
        "ended": ended,
        "ended_at": ended_at,
    }


def _snapshot(window: _Window, **extra) -> dict:
    return {
        "can_write": window.can_write,
        "opened_at": window.opened_at.isoformat(),
        "expires_at": window.expires_at.isoformat(),
        **extra,
    }


async def open_window(
    session: AsyncSession, *, configured: bool, can_write: bool, source: str
) -> tuple[dict, str]:
    """Open a link window, replacing any that is open, and return its state and the
    link code in plain: the one time the code is given out.

    Refused with nothing opened or logged when remote MCP is not configured (`409
    OAUTH_NOT_CONFIGURED`) and when `can_write` is asked while no window may allow it
    (`409 REMOTE_WRITES_OFF`, `writes_may_be_allowed`). The audit entry is committed
    before the window opens, so a database that refuses it opens nothing (`503`).
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
    _current, _last_end = window, None
    return state_out(configured=True), code


def cancel(*, configured: bool) -> dict:
    """End the open window, if there is one; the state after."""
    if configured and _live() is not None:
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
    """A typed link code in the form codes are drawn in, or None when it cannot be one:
    spaces and hyphens are dropped and letters taken in either case."""
    if not isinstance(presented, str) or len(presented) > _TYPED_MAX:
        return None
    value = "".join(presented.split()).replace("-", "").upper()
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
WINDOW_BURNED = "burned"  # the window was burned, now or earlier
NOT_PENDING = "not_pending"  # unknown, approved, refused, or expired with its window
START_AGAIN = "start_again"  # its registration was reclaimed while it waited
FORGED = "forged"  # not a form this server gave out for this transaction
BUSY = "busy"  # a link is being approved on this window
MALFORMED = "malformed"  # not in the form of a code: not counted
WRONG = "wrong"  # counted
LINKED_NOW = "linked"
DENIED = "denied"
UNAVAILABLE = "unavailable"  # the database refused the grant


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
    attempts_left: int


@dataclass(frozen=True)
class Outcome:
    kind: str
    consent: Consent | None = None
    # where the browser goes back to the client, on a link
    redirect: str | None = None


def _closed() -> Outcome:
    burned = _last_end is not None and _last_end[0] == BURNED
    return Outcome(WINDOW_BURNED if burned else NO_WINDOW)


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
            attempts_left=window.attempts_left(),
        ),
    )


async def show(session: AsyncSession, txn_id: str | None) -> Outcome:
    """What `GET /mcp/consent?txn=…` shows: the request, while it and its window last."""
    window = _live()
    if window is None:
        return _closed()
    if not isinstance(txn_id, str):
        return Outcome(NOT_PENDING)
    return await _consent(session, window, txn_id)


async def _burn(session: AsyncSession, window: _Window) -> None:
    """End the window for its wrong codes, then log it. The window is burned whether
    or not the database takes the entry."""
    _end(BURNED)
    changes.record(
        session,
        entity_type=AUDIT_ENTITY,
        entity_id=window.id,
        action="burn",
        summary=f"link window closed after {WRONG_CODES_MAX} wrong link codes",
        source=oauth.AUDIT_SOURCE,
        after=_snapshot(window, wrong_codes=window.wrong),
    )
    try:
        await session.commit()
    except OperationalError as exc:
        await session.rollback()
        log.warning(
            "link window: burned, but the entry was not recorded (%s)", type(exc.orig).__name__
        )


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
    - A wrong code counts against the window; the `WRONG_CODES_MAX`th burns it, which
      ends every pending transaction and is audit-logged.
    - The right code claims the window and approves the transaction
      (`oauth.approve`). A link closes the window. When the approval is refused (the
      registration was reclaimed while it waited, or the transaction is gone) or the
      database refuses the grant, the window stays open, its count unchanged: the code
      was right and nothing was linked, so the client is started again in it.
    - `deny` drops the transaction and leaves the window as it is.
    """
    window = _live()
    if window is None:
        return _closed()
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
    if typed is not None:
        window.wrong += 1
        if window.wrong >= WRONG_CODES_MAX:
            await _burn(session, window)
            return Outcome(WINDOW_BURNED)
    # the page again, with what was wrong: unless the request went meanwhile
    shown = await _consent(session, window, txn_id)
    if shown.consent is None:
        return shown
    return Outcome(WRONG if typed is not None else MALFORMED, shown.consent)


async def _link(session: AsyncSession, window: _Window, txn_id: str) -> Outcome:
    """The right code: claim the window and approve the transaction."""
    # `submit` checks `claimed` and the code and calls this with nothing awaited in
    # between, and nothing is awaited here before the claim: two posts of the right
    # code at once cannot both pass it.
    window.claimed = True
    try:
        redirect = await oauth.approve(session, txn_id, can_write=window.can_write)
    except oauth.Refused:
        window.claimed = False
        return Outcome(START_AGAIN)
    except ApiError:
        # the database refused the grant (`database_unavailable`); the transaction was
        # taken, so the client is started again
        window.claimed = False
        return Outcome(UNAVAILABLE)
    if _current is window:
        _end(LINKED)
    return Outcome(LINKED_NOW, redirect=redirect)
