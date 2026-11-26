"""The built-in OAuth server's rules and storage (v0.2 spec §6.2 to §6.4, #26).

`oauth_provider.IpamOAuthProvider` is the thin layer FastMCP and the MCP SDK call;
everything it decides, it decides here. The SDK supplies the protocol endpoints, PKCE
verification and the parsing of each request. This module supplies what a client may
register, how an authorise request's redirect URI and `resource` are judged, the grant,
and the life of every code and token.

Nothing here reads the settings: the provider passes the public base URL in, and
`MCP_REMOTE_WRITES` as `remote_writes` to every function that answers with a token's
scopes.

**The link window and the consent page (#27)** are built on three things here:

- `linking_open()` says whether registration and authorisation are taken now: while a
  link window is open (`services/link_window`). Outside one `/register` and
  `/authorize` refuse every request and no row is written by a caller without a
  credential.
- `begin_authorisation` checks an authorise request the SDK has validated (client,
  redirect URI, scope) and keeps it in `pending`, in process memory, under an opaque
  transaction id. The provider sends the browser to `CONSENT_PATH` with that id.
- `approve` is what the consent handler calls once the owner's link code is accepted
  (`link_window.submit`): it takes the transaction, records the grant with the redirect
  URI it was authorised for, issues the single-use code, writes the audit entry, and
  returns the URL to send the browser back to the client with. `pending.get` reads a
  transaction without taking it (for the page), `pending.discard` drops one (a refused
  consent), and every way a window ends drops them all.

**Scopes (#28).** A token's scopes are never stored: `grant_scopes` is asked each time
one is loaded, and answers `ipam:write` only for a grant with `can_write` while
`MCP_REMOTE_WRITES` is on. Turning the setting off (a restart) takes write access from
every grant at once, with no relink; turning it on gives none to a read-only grant.
"""

import base64
import hashlib
import hmac
import ipaddress
import logging
import re
import secrets
import string
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import AnyUrl
from sqlalchemy import and_, delete, exists, func, or_, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import database_unavailable
from ..models import OAuthClient, OAuthGrant, OAuthToken
from ..times import utc
from . import changes, link_window, scans

log = logging.getLogger(__name__)

SCOPE_READ = "ipam:read"
SCOPE_WRITE = "ipam:write"
# every scope the server has, in the order it writes them
VALID_SCOPES = (SCOPE_READ, SCOPE_WRITE)
# the grant types the token endpoint serves, in the order a registration is stored with
GRANT_TYPES = ("authorization_code", "refresh_token")

# Where `/mcp` is mounted, and so where the OAuth routes and the resource are (§6.7)
MCP_MOUNT = "/mcp"
# Under the mount: where the authorise step sends the browser, with `?txn=<id>` (#27)
CONSENT_PATH = "/consent"

# Lifetimes (spec §6.4)
CODE_LIFETIME = timedelta(minutes=5)
ACCESS_LIFETIME = timedelta(hours=1)
REFRESH_LIFETIME = timedelta(days=30)
# A refresh token presented again this soon after it was rotated is a retry (a lost
# answer, or two requests at once) and gets the same pair again. Later, it is taken
# for theft.
REFRESH_RETRY_WINDOW = timedelta(seconds=60)
# A grant's `last_used_at` is written at most this often, as an agent token's is
LAST_USED_GRANULARITY = timedelta(seconds=10)

# A refresh token is not rotated in its first five minutes: presented that young, it
# is answered with the pair it was issued in, again, and nothing is written. That is
# what bounds the rows a grant holds in `oauth_tokens`, without an error any client
# could meet. A grant has one chain of refresh tokens (a retry is answered with the
# same pair, never a second one), each link at least this old before the next exists,
# so at most 720 hours / 5 minutes = 8,640 unexpired refresh tokens and 13 unexpired
# access tokens, whatever a client does. A rotated refresh token is kept until it
# expires, because presenting it again is how a stolen one is noticed, and no row
# that has not expired is ever deleted to make room: an earlier cap on the row count
# let the holder of a stolen token refresh past it and erase the evidence (PR #189).
# A client that refreshes when its hour is up never notices the rule; one that
# refreshes on every request is handed the access token it already holds.
ROTATION_MIN_AGE = timedelta(minutes=5)

# How many registrations the server keeps (spec §6.6). At the cap a new one takes the
# place of the oldest that holds no unrevoked grant; when every one holds an unrevoked
# grant, it is refused until the owner revokes a link.
CLIENTS_MAX = 50
# A registration never linked is deleted once it is this old (spec §6.6, #29): that is
# what removes the spare of a client that registers twice for one link. One that was
# ever linked (`linked_at` set) is never deleted by this rule, revoked or not.
UNLINKED_LIFETIME = timedelta(hours=24)
REGISTRY_FULL = (
    f"This server already holds {CLIENTS_MAX} linked clients; a link must be revoked in "
    "the IPAM's own web interface before another client can register."
)

# What a registration may hold. Gemini registers six redirect URIs (§6.8).
REDIRECT_URIS_MAX = 16
REDIRECT_URI_MAX = 2048
# A declared name as stored (`sanitise_name`, spec §4.3, #29): at most this many
# characters, each an ASCII letter or digit, a space, a hyphen or an underscore
CLIENT_NAME_MAX = 60
CLIENT_NAME_CHARACTERS = frozenset(string.ascii_letters + string.digits + " -_")
# How much of a declared name is read: a name is cut to `CLIENT_NAME_MAX` anyway, and
# this bounds the work a 2 MiB name could ask for
CLIENT_NAME_READ = 256
# An authorise request's `state` is the client's, returned to it unread
STATE_MAX = 4096

# Authorise requests waiting for the owner's decision, in process memory (one worker).
# A transaction lasts as long as a link window does (§6.3).
PENDING_MAX = 32
PENDING_LIFETIME_S = 600

_SECRET_BYTES = 32
_SECRET = "[A-Za-z0-9_-]{43}"
_PREFIXES = {"code": "tlgoc_", "access": "tlgoa_", "refresh": "tlgor_"}
# The exact form of each value the server issues. A presented value in any other form
# is refused without a statement being run: the owner key, an agent token and anything
# an outsider makes up never reach the database through these routes.
TOKEN_FORMS = {kind: re.compile(prefix + _SECRET) for kind, prefix in _PREFIXES.items()}
# A client id as the MCP SDK's registration handler draws it (`uuid4`)
CLIENT_ID_FORM = re.compile(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}")
# An S256 PKCE challenge: the base64url of a SHA-256, unpadded (RFC 7636 §4.2)
_CHALLENGE_FORM = re.compile("[A-Za-z0-9_-]{43}")

AUDIT_SOURCE = "oauth"


class Refused(Exception):
    """A request the OAuth server refuses, by the protocol's own error code. The
    provider turns it into the SDK's error type for the route it came through."""

    def __init__(self, error: str, description: str):
        super().__init__(description)
        self.error = error
        self.description = description


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# --- The link window (#27) ---


def linking_open() -> bool:
    """Whether the server registers clients and takes authorise requests now: while the
    owner has a link window open (`link_window.is_open`, spec §6.3). Outside one the
    routes refuse and nothing is stored for a caller without a credential. Discovery and
    the token endpoint do not ask."""
    return link_window.is_open()


LINKING_CLOSED = (
    "Linking a client is started from the IPAM's own web interface, and is not open now."
)


# --- Redirect URIs (spec §6.3) ---


def _loopback_ip(host: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """The address `host` is, when it is a loopback IP literal: one in 127.0.0.0/8, or
    `::1`. A name, `localhost` included, is none, and so is an IPv4-mapped IPv6
    address."""
    try:
        address = ipaddress.ip_address((host or "").strip("[]"))
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv4Address):
        return address if address in ipaddress.ip_network("127.0.0.0/8") else None
    return address if address == ipaddress.IPv6Address("::1") else None


def redirect_problem(uri: AnyUrl) -> str | None:
    """Why a client may not register `uri` as a redirect URI, or None when it may.

    The SDK registers any URI it can parse, `javascript:alert(1)` and
    `http://client.example.com/cb` included. Taken here: `https` with a host, and `http`
    to `localhost` or a loopback address, for a client on the owner's own machine.
    Refused as well, in either scheme: credentials in the URI, whose host a reader takes
    for the wrong one, and a fragment (RFC 6749 §3.1.2). The reasons name the rule,
    never the URI.
    """
    if len(str(uri)) > REDIRECT_URI_MAX:
        return f"is longer than {REDIRECT_URI_MAX} characters"
    if uri.scheme not in ("https", "http"):
        return "must be an https URI, or http to localhost or a loopback address"
    if not uri.host:
        return "has no host"
    if uri.username is not None or uri.password is not None:
        return "must not hold a user name or password"
    if uri.fragment is not None:
        return "must not hold a fragment"
    if uri.scheme == "http" and uri.host != "localhost" and _loopback_ip(uri.host) is None:
        return "is plain http to a host other than localhost or a loopback address"
    return None


def match_redirect(registered: list[AnyUrl], requested: AnyUrl | None) -> AnyUrl:
    """The redirect URI an authorise request is answered at, or `Refused`.

    A request that names none gets the client's one registered URI. One that names a
    URI gets it when it is registered, compared whole, as the SDK compares. The one
    exception is OAuth 2.1's for a client on the owner's machine, which listens on a
    port it is given when it starts: a registered `http` URI on a loopback IP literal
    matches a request for the same address, path and query on any port. `localhost` by
    name keeps exact matching, and so does every `https` URI. The URI returned is the
    request's own, port included: it is what the code, the grant and the code exchange
    are held to.
    """
    if requested is None:
        if len(registered) == 1:
            return registered[0]
        raise Refused(
            "invalid_request",
            "redirect_uri must be specified unless the client has exactly one registered URI",
        )
    if requested in registered:
        return requested
    address = _loopback_ip(requested.host)
    if (
        requested.scheme == "http"
        and address is not None
        and requested.port
        and requested.username is None
        and requested.password is None
        and requested.fragment is None
    ):
        for candidate in registered:
            if (
                candidate.scheme == "http"
                and _loopback_ip(candidate.host) == address
                and candidate.path == requested.path
                and candidate.query == requested.query
            ):
                return requested
    raise Refused("invalid_request", "redirect_uri is not registered for this client")


# --- The resource indicator (spec §6.3) ---


def names_this_server(resource: str | None, public_base_url: str) -> bool:
    """Whether an authorise request's RFC 8707 `resource` names this server:
    `<public base URL>/mcp`, the scheme and the authority compared without regard to
    case and one trailing slash ignored. An absent `resource` names it."""
    if resource is None:
        return True
    if "?" in resource or "#" in resource:
        return False
    try:
        given = urlsplit(resource)
        expected = urlsplit(public_base_url + MCP_MOUNT)
    except ValueError:
        return False
    path = given.path[:-1] if given.path.endswith("/") else given.path
    return (
        given.scheme.lower() == expected.scheme
        and given.netloc.lower() == expected.netloc
        and path == expected.path
    )


# --- Registration ---


def sanitise_name(declared: object) -> str | None:
    """A client's declared name as this server stores it (spec §4.3, #29), or None.

    The name is the client's own claim, shown on the consent page, in `actor` and in
    the owner's list of links, so it is cut down to a stated set of characters and
    nothing else: ASCII letters and digits, a space, a hyphen and an underscore
    (`CLIENT_NAME_CHARACTERS`). A letter with accents keeps its base letter (the
    canonical decomposition, NFD, with its combining marks dropped); every other
    character outside the set (punctuation such as `.` `:` `·` `|` and quotation marks,
    symbols, check marks, emoji, controls, format and bidirectional characters, and
    letters of other scripts) becomes a space, so two words are never joined into one.
    Runs of spaces become one, the ends are trimmed, and the result is cut to
    `CLIENT_NAME_MAX` characters. A result with no letter or digit is no name (None),
    and so is anything that is not a string. Only the first `CLIENT_NAME_READ`
    characters of the declared value are read.

    Without `.` or `:` a name cannot spell a host name or a URL, and without quotation
    marks it cannot close the quotation the consent page shows it in. The function is
    idempotent: a stored name is returned unchanged.
    """
    if not isinstance(declared, str):
        return None
    kept = []
    for char in unicodedata.normalize("NFD", declared[:CLIENT_NAME_READ]):
        if char in CLIENT_NAME_CHARACTERS:
            kept.append(char)
        elif not unicodedata.category(char).startswith("M"):
            kept.append(" ")
    name = " ".join("".join(kept).split())[:CLIENT_NAME_MAX].rstrip()
    return name if any(char.isalnum() for char in name) else None


def actor_for(client: OAuthClient | None) -> str | None:
    """The `actor` of an audit entry about this client's grant (spec §4.3). The name is
    the client's own claim, which is why it goes here and never into a summary."""
    return f"grant:{client.client_name}" if client is not None and client.client_name else None


# The `actor` of an MCP write by a grant whose client has no kept name: the grant's own
# id, which the server drew. A prefix of its own, because any text after `grant:` is a
# name some client could declare.
GRANT_ID_ACTOR_PREFIX = "grant-id:"


async def grant_actor(session: AsyncSession, grant_id: str) -> str:
    """The `actor` of an MCP write made with a token of this grant (spec §4.3, #28):
    `grant:<client name>`, or `grant-id:<grant id>` when the client's declared name was
    not kept (`sanitise_name`) or its registration is gone. Never null, so a write from a
    grant is never taken for the owner's."""
    grant = await session.get(OAuthGrant, grant_id)
    client = await session.get(OAuthClient, grant.client_id) if grant is not None else None
    return actor_for(client) or f"{GRANT_ID_ACTOR_PREFIX}{grant_id}"


@dataclass(frozen=True)
class Registered:
    """What a registration was stored as, where that is narrower than what was sent."""

    client_name: str | None
    grant_types: list[str]
    scope: str


async def register_client(
    session: AsyncSession,
    *,
    client_id: str,
    client_name: str | None,
    redirect_uris: list[AnyUrl],
    auth_method: str,
    client_secret: str | None,
    grant_types: list[str],
    scope: str | None,
) -> Registered:
    """Store a registration the SDK's handler has parsed and drawn credentials for.

    Refused whole (`invalid_redirect_uri`) when any redirect URI is outside the policy
    of `redirect_problem`, or there are more than `REDIRECT_URIS_MAX`. Only the SHA-256
    of the secret is stored. Of the rest, only what the server uses: the name as
    `sanitise_name` leaves it, and the grant types and scopes among the server's own.

    Under the write lock, registrations never linked that are `UNLINKED_LIFETIME` old
    are deleted first (`_delete_unlinked`). At most `CLIENTS_MAX` registrations exist,
    counted under the write lock after that, so that requests at once cannot pass it.
    At the cap the oldest registration that holds no
    unrevoked grant (never linked, or its links revoked, alike) is deleted to make
    room; its grants and their audit entries stay, and its tokens were deleted when
    its grants were revoked. When every registration holds an unrevoked grant the new
    one is refused (`access_denied`) and nothing changes.

    Not audit-logged, storing, deleting or reclaiming: a registration is not a link
    (§6.3), it comes from a caller with no credential, and it grants nothing. The grant
    that may follow is logged.
    """
    if not linking_open():
        raise Refused("access_denied", LINKING_CLOSED)
    if not redirect_uris or len(redirect_uris) > REDIRECT_URIS_MAX:
        raise Refused(
            "invalid_redirect_uri", f"redirect_uris must hold 1 to {REDIRECT_URIS_MAX} URIs"
        )
    for position, uri in enumerate(redirect_uris):
        problem = redirect_problem(uri)
        if problem is not None:
            raise Refused("invalid_redirect_uri", f"redirect_uris[{position}] {problem}")
    if auth_method not in ("none", "client_secret_post", "client_secret_basic"):
        raise Refused("invalid_client_metadata", "token_endpoint_auth_method is not supported")
    if (auth_method == "none") != (client_secret is None):
        # the SDK draws a secret exactly when the method needs one; anything else is
        # a client this server could not authenticate
        raise Refused("invalid_client_metadata", "token_endpoint_auth_method is not supported")
    asked = set((scope or "").split())
    stored = Registered(
        client_name=sanitise_name(client_name),
        grant_types=[g for g in GRANT_TYPES if g in grant_types],
        scope=" ".join(s for s in VALID_SCOPES if s in asked),
    )
    try:
        await scans.take_write_lock(session)
        now = _now()
        await _delete_unlinked(session, now)
        if not await _make_room(session):
            await session.rollback()
            raise Refused("access_denied", REGISTRY_FULL)
        session.add(
            OAuthClient(
                client_id=client_id,
                client_name=stored.client_name,
                redirect_uris=[str(uri) for uri in redirect_uris],
                token_endpoint_auth_method=auth_method,
                client_secret_hash=_sha256(client_secret) if client_secret is not None else None,
                grant_types=stored.grant_types,
                scope=stored.scope,
                created_at=now,
            )
        )
        await session.commit()
    except OperationalError as exc:
        await session.rollback()
        raise database_unavailable("oauth registration", exc) from exc
    return stored


async def _make_room(session: AsyncSession) -> bool:
    """Leave room for one more registration, deleting the oldest reclaimable ones when
    `CLIENTS_MAX` are held. False when there are not enough to reclaim. The caller
    holds the write lock.

    The order is `created_at` alone, and `pending` is never read (#190, spec §6.3): a
    registration with an authorise request waiting for the owner is reclaimed like any
    other, and a request added or dropped while this runs changes nothing it decides.
    Sparing one would not protect the owner, who loses the request as surely to
    `PENDING_MAX` authorise requests on any registration, and as a rule would let an
    outsider's pending requests make this refuse while registrations are reclaimable.
    A link never completed (`never_completed`) is unrevoked, so its registration is
    not reclaimable until the owner revokes it."""
    held = await session.scalar(select(func.count()).select_from(OAuthClient))
    excess = held - CLIENTS_MAX + 1
    if excess <= 0:
        return True
    linked = exists().where(
        OAuthGrant.client_id == OAuthClient.client_id, OAuthGrant.revoked_at.is_(None)
    )
    reclaimable = list(
        await session.scalars(
            select(OAuthClient.client_id)
            .where(~linked)
            .order_by(OAuthClient.created_at, OAuthClient.client_id)
            .limit(excess)
        )
    )
    if len(reclaimable) < excess:
        return False
    await session.execute(
        delete(OAuthClient)
        .where(OAuthClient.client_id.in_(reclaimable))
        .execution_options(synchronize_session=False)
    )
    return True


def _unlinked_since(now: datetime) -> datetime:
    """Registrations never linked and created at or before this time are past
    `UNLINKED_LIFETIME`."""
    return now - UNLINKED_LIFETIME


def _past_lifetime(client: OAuthClient, now: datetime) -> bool:
    return client.linked_at is None and utc(client.created_at) <= _unlinked_since(now)


async def _delete_unlinked(session: AsyncSession, now: datetime) -> None:
    """Delete every registration that was never linked and is `UNLINKED_LIFETIME` old
    (spec §6.6, #29). One statement over a registry `CLIENTS_MAX` bounds; the caller
    holds the write lock. Nothing refers to what it deletes: a grant is written only
    with the `linked_at` that keeps its registration, so a registration never linked
    has no grant, token or audit entry. An authorise request still pending for one is
    refused at approval as for a reclaimed registration (`approve`). Not audit-logged,
    as a registration is not."""
    await session.execute(
        delete(OAuthClient)
        .where(OAuthClient.linked_at.is_(None), OAuthClient.created_at <= _unlinked_since(now))
        .execution_options(synchronize_session=False)
    )


async def get_client(session: AsyncSession, client_id: str) -> OAuthClient | None:
    """The registration with this id. An id that is not in the form the server issues
    is not looked up, and a registration never linked that is `UNLINKED_LIFETIME` old
    is not there: it is deleted at the next write that tidies the registry, and until
    then this read, which writes nothing, answers as if it were gone."""
    if not CLIENT_ID_FORM.fullmatch(client_id):
        return None
    client = await session.get(OAuthClient, client_id)
    if client is not None and _past_lifetime(client, _now()):
        return None
    return client


async def tidy_registrations(session: AsyncSession) -> None:
    """What the server does to the registry when it starts (#29): delete the
    registrations past `UNLINKED_LIFETIME`, and store every kept name as
    `sanitise_name` gives it now, so that a name stored under an earlier rule (until
    #29 a name was kept whole when it was printable) is never shown. At most
    `CLIENTS_MAX` rows, under the write lock, one transaction; a name that sanitises to
    nothing is cleared. Not audit-logged, as a registration is not. Past change-log
    entries keep the `actor` they were written with: the log is a record."""
    try:
        await scans.take_write_lock(session)
        await _delete_unlinked(session, _now())
        named = await session.scalars(
            select(OAuthClient).where(OAuthClient.client_name.is_not(None))
        )
        for client in named:
            name = sanitise_name(client.client_name)
            if name != client.client_name:
                client.client_name = name
        await session.commit()
    except BaseException:
        await session.rollback()
        raise


def secret_matches(client: OAuthClient, presented: str | None) -> bool:
    """Whether a token request has authenticated as this client (spec §6.4).

    A public client (`none`) has no secret and needs none. A confidential one needs the
    secret it was issued: the presented value is hashed and compared in constant time
    with the stored hash. A confidential registration with no stored hash authenticates
    nobody.
    """
    if client.token_endpoint_auth_method == "none":
        return client.client_secret_hash is None
    if not client.client_secret_hash or not presented:
        return False
    return hmac.compare_digest(_sha256(presented), client.client_secret_hash)


# --- Authorise: the pending transaction ---


@dataclass(frozen=True)
class Pending:
    """An authorise request that passed every check and waits for the owner."""

    id: str
    client_id: str
    # as the request gave it and `match_redirect` returned it, port included
    redirect_uri: str
    redirect_uri_explicit: bool
    state: str | None
    code_challenge: str
    expires: float


class PendingStore:
    """Pending authorise transactions, in this process's memory: a restart drops them,
    which cancels a link in progress and nothing else (§6.3). At most `PENDING_MAX`,
    the oldest dropped first, each for `PENDING_LIFETIME_S` by the monotonic clock."""

    def __init__(self) -> None:
        self._held: OrderedDict[str, Pending] = OrderedDict()

    def _expire(self) -> None:
        now = time.monotonic()
        for txn_id in [t for t, p in self._held.items() if p.expires <= now]:
            del self._held[txn_id]

    def add(self, **fields) -> Pending:
        self._expire()
        while len(self._held) >= PENDING_MAX:
            self._held.popitem(last=False)
        txn = Pending(
            id=secrets.token_urlsafe(_SECRET_BYTES),
            expires=time.monotonic() + PENDING_LIFETIME_S,
            **fields,
        )
        self._held[txn.id] = txn
        return txn

    def get(self, txn_id: str) -> Pending | None:
        """The transaction, left in place: for showing the consent page."""
        self._expire()
        return self._held.get(txn_id)

    def take(self, txn_id: str) -> Pending | None:
        """The transaction, removed: it can be approved once."""
        self._expire()
        return self._held.pop(txn_id, None)

    def discard(self, txn_id: str) -> None:
        self._held.pop(txn_id, None)

    def reset(self) -> None:
        self._held.clear()

    def __len__(self) -> int:
        self._expire()
        return len(self._held)


pending = PendingStore()


def begin_authorisation(
    *,
    client_id: str,
    redirect_uri: str,
    redirect_uri_explicit: bool,
    state: str | None,
    code_challenge: str,
    resource: str | None,
    public_base_url: str,
) -> Pending:
    """Keep an authorise request for the owner's decision, or refuse it.

    The SDK's handler has already found the client, matched the redirect URI
    (`match_redirect`, through the client it was handed) and checked the scopes asked
    against the registration. Checked here: that linking is open, that `resource`, when
    present, names this server (`invalid_target`, so no consent page is shown for a
    client that reached the server under another name), and that the PKCE challenge
    and `state` are of a size worth holding. The scopes asked are not kept: what a
    grant may do is the owner's choice when the link is opened (§6.5).
    """
    if not linking_open():
        raise Refused("access_denied", LINKING_CLOSED)
    if not names_this_server(resource, public_base_url):
        raise Refused("invalid_target", "resource does not name this server")
    if not _CHALLENGE_FORM.fullmatch(code_challenge):
        raise Refused("invalid_request", "code_challenge is not an S256 challenge")
    if state is not None and len(state) > STATE_MAX:
        raise Refused("invalid_request", f"state is longer than {STATE_MAX} characters")
    return pending.add(
        client_id=client_id,
        redirect_uri=redirect_uri,
        redirect_uri_explicit=redirect_uri_explicit,
        state=state,
        code_challenge=code_challenge,
    )


def _with_query(uri: str, **params: str | None) -> str:
    """`uri` with the parameters that are set added to its query."""
    parts = urlsplit(uri)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query += [(name, value) for name, value in params.items() if value is not None]
    return urlunsplit(parts._replace(query=urlencode(query)))


# --- Tokens ---


def grant_scopes(grant: OAuthGrant, *, remote_writes: bool) -> list[str]:
    """The scopes a token of this grant carries now, asked each time one is loaded
    (spec §6.5): `ipam:write` as well as `ipam:read` only while the grant has
    `can_write` **and** `MCP_REMOTE_WRITES` is on (`remote_writes`, from the provider).
    Nothing else widens it, a client's request included."""
    if grant.can_write and remote_writes:
        return [SCOPE_READ, SCOPE_WRITE]
    return [SCOPE_READ]


def never_completed(now: datetime):
    """SQL, over `OAuthGrant`: whether a grant is a link **never completed** at `now`
    (spec §6.6, #190). The owner approved it, the server issued its code, and the code
    expired with no exchange, so the grant holds no token and can never get one. It is
    left for the owner: listed as a link, marked, until the owner revokes it, and its
    registration holds its place in the registry until then.

    True exactly when all three hold:

    - the grant is unrevoked (`revoked_at` null);
    - it has never been used (`last_used_at` null). The code exchange sets it in the
      transaction that claims the code and stores the first tokens (`exchange_code`),
      and nothing ever clears it;
    - it has no row in `oauth_tokens` but codes, and none of those has time left: no
      access or refresh token, expired or not, and no code with `expires_at` after
      `now`, used or not.

    Either of the last two alone excludes every grant that holds a token today: a grant
    whose code was exchanged has its tokens' rows and `last_used_at`, from one commit.
    Both are asked so that each guards the other: a later change that pruned expired
    rows could not make a grant that lapsed after use (its tokens all expired, 30 days
    unused) read as never completed, and a grant written with a token but no
    `last_used_at` would not either. A grant that lapsed after use is not this case: it
    was completed, and stays a link with its last contact, for the owner to judge.

    The clock is the server's (`_now`), and the comparison with a code's `expires_at`
    is the exact complement of the one `exchange_code` claims a code by (`expires_at >
    now`): at any instant a code is claimable or the grant can be flagged, never both.
    The rule only reads, and is judged here alone; `linked_clients.list_links` asks it
    in its one statement. It revokes nothing, so a wrong answer could never take a
    token from anyone: see that function for how fresh its answer is."""
    holds_more_than_a_spent_code = exists().where(
        OAuthToken.grant_id == OAuthGrant.id,
        or_(OAuthToken.kind != "code", OAuthToken.expires_at > now),
    )
    return and_(
        OAuthGrant.revoked_at.is_(None),
        OAuthGrant.last_used_at.is_(None),
        ~holds_more_than_a_spent_code,
    )


_LIFETIMES = {"code": CODE_LIFETIME, "access": ACCESS_LIFETIME, "refresh": REFRESH_LIFETIME}


def _random(kind: str) -> str:
    return _PREFIXES[kind] + secrets.token_urlsafe(_SECRET_BYTES)


def _derive(salt: str, plain: str, kind: str) -> str:
    """The token of `kind` that belongs to the refresh token `plain`: the access token
    issued with it, or the refresh token that replaces it. An HMAC-SHA256 keyed with
    the salt on that token's row, so it is the same value each time the server is
    shown `plain`, which is what lets a retry be answered with the first answer again
    while the database holds only hashes. It cannot be computed from the database
    alone (that needs `plain`) nor by the holder of `plain` alone (that needs the
    salt, which never leaves the server), and it is in the form of a drawn token."""
    digest = hmac.new(salt.encode(), f"{kind}:{plain}".encode(), hashlib.sha256).digest()
    return _PREFIXES[kind] + base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _store(session: AsyncSession, grant_id: str, kind: str, plain: str, now: datetime, **fields):
    session.add(
        OAuthToken(
            token_hash=_sha256(plain),
            grant_id=grant_id,
            kind=kind,
            created_at=now,
            expires_at=now + _LIFETIMES[kind],
            **fields,
        )
    )


@dataclass(frozen=True)
class Issued:
    """A token answer: the plain values, which are never stored."""

    access_token: str
    refresh_token: str | None
    expires_in: int
    scopes: list[str]


def _issue_pair(
    session: AsyncSession,
    grant: OAuthGrant,
    refresh: str | None,
    now: datetime,
    *,
    remote_writes: bool,
) -> Issued:
    """Store a new refresh token `refresh`, with a salt of its own, and the access
    token derived from the two; or, for a client that registered for no refresh token
    (`refresh` None), a drawn access token alone. Does not flush."""
    if refresh is None:
        access = _random("access")
    else:
        salt = secrets.token_urlsafe(_SECRET_BYTES)
        _store(session, grant.id, "refresh", refresh, now, derive_salt=salt)
        access = _derive(salt, refresh, "access")
    _store(session, grant.id, "access", access, now)
    return Issued(
        access_token=access,
        refresh_token=refresh,
        expires_in=int(ACCESS_LIFETIME.total_seconds()),
        scopes=grant_scopes(grant, remote_writes=remote_writes),
    )


async def _tidy(session: AsyncSession, grant_id: str, now: datetime) -> None:
    """At each rotation: delete the grant's expired rows, and nothing that has not
    expired; and erase the salt of each refresh token rotated longer ago than the
    retry window, which no answer needs again. A copy of the database then holds the
    salts of the chain's newest tokens only, so an old refresh token that leaked
    cannot be walked forward to the current one with it."""
    await session.execute(
        delete(OAuthToken)
        .where(OAuthToken.grant_id == grant_id, OAuthToken.expires_at <= now)
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        update(OAuthToken)
        .where(
            OAuthToken.grant_id == grant_id,
            OAuthToken.derive_salt.is_not(None),
            OAuthToken.rotated_at <= now - REFRESH_RETRY_WINDOW,
        )
        .values(derive_salt=None)
        .execution_options(synchronize_session=False)
    )


async def _find(
    session: AsyncSession, plain: str, kind: str, *, fresh: bool = False
) -> tuple[OAuthToken, OAuthGrant] | None:
    """The stored row for a presented value of `kind`, with its grant. A value not in
    the form the server issues for that kind is not looked up."""
    if not TOKEN_FORMS[kind].fullmatch(plain):
        return None
    query = (
        select(OAuthToken, OAuthGrant)
        .join(OAuthGrant, OAuthGrant.id == OAuthToken.grant_id)
        .where(OAuthToken.token_hash == _sha256(plain), OAuthToken.kind == kind)
    )
    if fresh:
        query = query.execution_options(populate_existing=True)
    row = (await session.execute(query)).first()
    return (row[0], row[1]) if row is not None else None


async def _touch(session: AsyncSession, grant_id: str, now: datetime) -> None:
    """Move the grant's `last_used_at`, at most once per `LAST_USED_GRANULARITY`: the
    condition is in the statement, so requests that race write it once."""
    stale = now - LAST_USED_GRANULARITY
    await session.execute(
        update(OAuthGrant)
        .where(
            OAuthGrant.id == grant_id,
            or_(OAuthGrant.last_used_at.is_(None), OAuthGrant.last_used_at <= stale),
        )
        .values(last_used_at=now)
        .execution_options(synchronize_session=False)
    )


async def approve(session: AsyncSession, txn_id: str, *, can_write: bool) -> str:
    """Record the grant for a pending authorise transaction and issue its code: the
    step the consent handler takes once the owner's link code is accepted (#27).

    The caller is the authority for the approval; this function asks nobody. It takes
    the transaction, so one approval is all it gets, and in one transaction under the
    write lock stores the grant with the redirect URI the request was authorised for,
    the single-use code bound to the request's PKCE challenge, the client's
    `linked_at` when this is its first link, and the audit entry. Returns the URL that
    sends the browser back to the client, with the code and the client's `state`.

    `Refused` (`access_denied`) when the transaction is unknown, already taken or
    expired, or its registration is gone: reclaimed at the cap, or deleted because it
    was never linked and is now `UNLINKED_LIFETIME` old, which is decided here, under
    the write lock, by the time read after it (the deletion runs first). Nothing else
    is written then.
    """
    txn = pending.take(txn_id)
    if txn is None:
        raise Refused("access_denied", "this authorisation request is no longer pending")
    try:
        await scans.take_write_lock(session)
        now = _now()
        await _delete_unlinked(session, now)
        client = await session.get(OAuthClient, txn.client_id, populate_existing=True)
        if client is None:
            # the deletion is kept: a registration past its lifetime is gone either way
            await session.commit()
            raise Refused("access_denied", "the client's registration no longer exists")
        grant = OAuthGrant(
            client_id=client.client_id,
            redirect_uri=txn.redirect_uri,
            can_write=can_write,
            created_at=now,
        )
        session.add(grant)
        await session.flush()
        code = _random("code")
        _store(
            session,
            grant.id,
            "code",
            code,
            now,
            code_challenge=txn.code_challenge,
            redirect_uri_explicit=txn.redirect_uri_explicit,
        )
        if client.linked_at is None:
            client.linked_at = now
        changes.record(
            session,
            entity_type="grant",
            entity_id=grant.id,
            action="create",
            summary=f"{'read-write' if can_write else 'read-only'} grant issued to a linked client",
            source=AUDIT_SOURCE,
            actor=actor_for(client),
            after=changes.snapshot(grant),
        )
        await session.commit()
    except OperationalError as exc:
        await session.rollback()
        raise database_unavailable("oauth grant", exc) from exc
    return _with_query(txn.redirect_uri, code=code, state=txn.state)


@dataclass(frozen=True)
class CodeInfo:
    """An authorisation code as the SDK's token handler needs it, to check the client,
    the expiry, the redirect URI and PKCE before it asks for the exchange."""

    client_id: str
    expires_at: datetime
    code_challenge: str
    redirect_uri: str
    redirect_uri_explicit: bool
    scopes: list[str]


async def load_code(
    session: AsyncSession, client_id: str, plain: str, *, remote_writes: bool
) -> CodeInfo | None:
    """The unused code of an unrevoked grant of this client, or None. Only reads: the
    code is spent by `exchange_code`, which checks all of this again as it claims it."""
    found = await _find(session, plain, "code")
    if found is None:
        return None
    token, grant = found
    if grant.client_id != client_id or grant.revoked_at is not None or token.used_at is not None:
        return None
    return CodeInfo(
        client_id=grant.client_id,
        expires_at=utc(token.expires_at),
        code_challenge=token.code_challenge or "",
        redirect_uri=grant.redirect_uri,
        redirect_uri_explicit=bool(token.redirect_uri_explicit),
        scopes=grant_scopes(grant, remote_writes=remote_writes),
    )


async def exchange_code(
    session: AsyncSession, client_id: str, plain: str, *, remote_writes: bool
) -> Issued:
    """Spend an authorisation code for the grant's first tokens.

    The SDK's handler has authenticated the client and verified the redirect URI and
    PKCE against `load_code`'s answer. Here, under the write lock, the code is claimed
    with a compare-and-set that requires it unused, unexpired, and its grant this
    client's and unrevoked, at the claim's own time: two exchanges of one code cannot
    both pass. `Refused` (`invalid_grant`) otherwise, with nothing written.
    """
    try:
        await scans.take_write_lock(session)
        now = _now()
        found = await _find(session, plain, "code", fresh=True)
        usable_grant = exists().where(
            OAuthGrant.id == OAuthToken.grant_id,
            OAuthGrant.client_id == client_id,
            OAuthGrant.revoked_at.is_(None),
        )
        claimed = False
        if found is not None:
            result = await session.execute(
                update(OAuthToken)
                .where(
                    OAuthToken.id == found[0].id,
                    OAuthToken.kind == "code",
                    OAuthToken.used_at.is_(None),
                    OAuthToken.expires_at > now,
                    usable_grant,
                )
                .values(used_at=now)
                .execution_options(synchronize_session=False)
            )
            claimed = result.rowcount == 1
        client = await session.get(OAuthClient, client_id, populate_existing=True)
        if not claimed or client is None:
            await session.rollback()
            raise Refused("invalid_grant", "authorization code does not exist")
        grant = found[1]
        # the grant's one chain of refresh tokens starts here, with a drawn one
        first = _random("refresh") if "refresh_token" in client.grant_types else None
        issued = _issue_pair(session, grant, first, now, remote_writes=remote_writes)
        await _touch(session, grant.id, now)
        await session.commit()
    except OperationalError as exc:
        await session.rollback()
        raise database_unavailable("oauth code exchange", exc) from exc
    return issued


@dataclass(frozen=True)
class RefreshInfo:
    client_id: str
    expires_at: datetime
    # the most a refresh request may ask for; what it gets is `grant_scopes`
    scopes: list[str]


async def load_refresh(session: AsyncSession, client_id: str, plain: str) -> RefreshInfo | None:
    """A refresh token of an unrevoked grant of this client, or None. Only reads, and
    answers for a token that has been rotated too: `exchange_refresh` is where a retry
    is told from a reuse, and it must be reached to revoke the grant."""
    found = await _find(session, plain, "refresh")
    if found is None:
        return None
    token, grant = found
    if grant.client_id != client_id or grant.revoked_at is not None:
        return None
    return RefreshInfo(
        client_id=grant.client_id, expires_at=utc(token.expires_at), scopes=list(VALID_SCOPES)
    )


async def revoke_grant(
    session: AsyncSession,
    grant: OAuthGrant,
    client: OAuthClient | None,
    now: datetime,
    *,
    summary: str,
    source: str,
) -> None:
    """Revoke a grant and delete its codes and tokens, which are refused from the
    commit on, with the audit entry; does not commit. The caller holds the write lock
    and has read the grant under it."""
    before = changes.snapshot(grant)
    grant.revoked_at = now
    await session.execute(
        delete(OAuthToken)
        .where(OAuthToken.grant_id == grant.id)
        .execution_options(synchronize_session=False)
    )
    changes.record(
        session,
        entity_type="grant",
        entity_id=grant.id,
        action="revoke",
        summary=summary,
        source=source,
        actor=actor_for(client),
        before=before,
        after=changes.snapshot(grant),
    )


async def _same_pair(
    session: AsyncSession,
    grant: OAuthGrant,
    refresh: str,
    salt: str | None,
    now: datetime,
    *,
    remote_writes: bool,
) -> Issued | None:
    """The pair the refresh token `refresh` was issued in, as it stands now: its access
    token derived again, with the time that one has left. None when it cannot be
    given again (no salt, or the access token is gone or has run out)."""
    if not salt:
        return None
    access = _derive(salt, refresh, "access")
    found = await _find(session, access, "access", fresh=True)
    if found is None or utc(found[0].expires_at) <= now:
        return None
    return Issued(
        access_token=access,
        refresh_token=refresh,
        expires_in=int((utc(found[0].expires_at) - now).total_seconds()),
        scopes=grant_scopes(grant, remote_writes=remote_writes),
    )


async def exchange_refresh(
    session: AsyncSession, client_id: str, plain: str, *, remote_writes: bool
) -> Issued:
    """Answer a refresh request (spec §6.4). Everything is read and decided under the
    write lock, so two requests with one token are served one after the other.

    - A token younger than `ROTATION_MIN_AGE` is not rotated: the answer is the pair it
      was issued in, again, and no token row is written.
    - Otherwise a token used for the first time is rotated: its replacement and the
      access token that goes with it are derived (`_derive`) and stored.
    - A token presented again within `REFRESH_RETRY_WINDOW` of its rotation is a retry
      (a lost answer, or two requests at once) and gets the same pair the rotation
      gave, again; nothing is written. So a grant has one chain of refresh tokens: two
      holders of one token cannot each walk off with a chain of their own.
    - Presented later than that, it revokes the whole grant, which is committed and
      audit-logged (`entity_type="grant"`, `source="oauth"`) before the request is
      refused.

    `Refused` (`invalid_grant`) for a token that is unknown, expired, another client's
    or of a revoked grant, with nothing written.
    """
    try:
        await scans.take_write_lock(session)
        now = _now()
        found = await _find(session, plain, "refresh", fresh=True)
        client = await session.get(OAuthClient, client_id, populate_existing=True)
        if (
            found is None
            or client is None
            or found[1].client_id != client_id
            or found[1].revoked_at is not None
            or utc(found[0].expires_at) <= now
        ):
            await session.rollback()
            raise Refused("invalid_grant", "refresh token does not exist")
        token, grant = found
        salt = token.derive_salt
        issued = None
        if token.rotated_at is None:
            if now - utc(token.created_at) < ROTATION_MIN_AGE:
                issued = await _same_pair(
                    session, grant, plain, salt, now, remote_writes=remote_writes
                )
            if issued is None and salt:
                token.rotated_at = now
                await _tidy(session, grant.id, now)
                issued = _issue_pair(
                    session,
                    grant,
                    _derive(salt, plain, "refresh"),
                    now,
                    remote_writes=remote_writes,
                )
        elif now - utc(token.rotated_at) <= REFRESH_RETRY_WINDOW:
            if salt:
                replacement = _derive(salt, plain, "refresh")
                child = await _find(session, replacement, "refresh", fresh=True)
                if child is not None:
                    issued = await _same_pair(
                        session,
                        grant,
                        replacement,
                        child[0].derive_salt,
                        now,
                        remote_writes=remote_writes,
                    )
        else:
            await revoke_grant(
                session,
                grant,
                client,
                now,
                summary="grant revoked: a refresh token was used again after it had been replaced",
                source=AUDIT_SOURCE,
            )
            await session.commit()
            raise Refused("invalid_grant", "refresh token does not exist")
        if issued is None:
            # a token that can be neither rotated nor answered for again: no row the
            # server writes is in that state
            await session.rollback()
            raise Refused("invalid_grant", "refresh token does not exist")
        await _touch(session, grant.id, now)
        await session.commit()
    except OperationalError as exc:
        await session.rollback()
        raise database_unavailable("oauth refresh", exc) from exc
    return issued


@dataclass(frozen=True)
class AccessInfo:
    client_id: str
    grant_id: str
    expires_at: datetime
    scopes: list[str]


async def load_access(
    session: AsyncSession, plain: str, *, remote_writes: bool
) -> AccessInfo | None:
    """The grant a bearer on `/mcp` acts for, or None when it is not an unexpired
    access token of an unrevoked grant.

    The lookup only reads. The grant's `last_used_at` is then written when the value
    read is unset or `LAST_USED_GRANULARITY` old, so a token used at request rate
    drives no write statement; it is a heartbeat, in a transaction of its own, and a
    database that refuses it does not refuse the request.
    """
    found = await _find(session, plain, "access")
    if found is None:
        return None
    token, grant = found
    now = _now()
    if grant.revoked_at is not None or utc(token.expires_at) <= now:
        return None
    info = AccessInfo(
        client_id=grant.client_id,
        grant_id=grant.id,
        expires_at=utc(token.expires_at),
        scopes=grant_scopes(grant, remote_writes=remote_writes),
    )
    last = utc(grant.last_used_at)
    if last is None or last <= now - LAST_USED_GRANULARITY:
        try:
            await _touch(session, grant.id, now)
            await session.commit()
        except OperationalError as exc:
            await session.rollback()
            log.warning(
                "oauth: last contact of a grant not recorded (%s)", type(exc.orig).__name__
            )
    return info
