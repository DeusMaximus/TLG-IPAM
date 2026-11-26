"""The owner's **Linked clients** (v0.2 spec §6.6, #30): the links the owner has made,
and revoking one.

A link is a grant (`OAuthGrant`): the owner typed a link code on the consent page and
the client was given tokens for `/mcp`. Every grant needs that, so the number of links
is the number of times the owner linked a client, and the list of unrevoked links is
answered whole. Revoked links are kept in the database (their audit entries refer to
them) and the newest `REVOKED_SHOWN` are listed after the unrevoked ones; the change log
has every revoke. Both come from one statement, one snapshot (`list_links`).

What the list says of a link, and why:

- **The redirect host** (`link_window.redirect_host` of the grant's `redirect_uri`): the
  host the link was authorised through, which the consent page showed the owner, and
  not every host the client registered. In ASCII form, as the consent page shows it.
- **The client's name** (`OAuthClient.client_name`), the client's own claim about
  itself: null when no name was kept, or when the registration is gone
  (`client_registered` says which). A grant outlives its registration.
- **Access**: `can_write` is what the owner chose when opening the window, fixed into
  the grant; `writes` is what its tokens carry now (`oauth.grant_scopes`), which is
  read-only for every grant while `MCP_REMOTE_WRITES` is off (#28).
- **Last contact** is the grant's `last_used_at`, moved by a request to `/mcp`, a code
  exchange or a refresh. A client moves it with nobody using it, so it is not use.
- **Never completed** (`never_completed`, #190): the owner approved the link but the
  client never exchanged its code, which has expired (`oauth.never_completed`). The
  grant holds no token and can get none; it is left for the owner to revoke. A place
  in the registry belongs to a registration, not a link: the registration is kept while
  any of its links is unrevoked, so revoking this one frees the place only when its
  registration holds no other unrevoked link (spec §6.6). Every unrevoked link is in
  the answer with its `client_id`, so the page can tell which case it is from the
  answer alone.

**Revoke** is the owner's act: `source` `ui` or `api`, `actor` null (spec §4.3), on the
grant's entity (`entity_type="grant"`, `action="revoke"`), with a fixed summary that
holds no name and no URI. It takes SQLite's write lock before it reads the grant, so a
refresh, a code exchange or the reuse rule cannot change the grant between the read and
the revoke, and `oauth.revoke_grant` deletes its codes and tokens in the same
transaction: from the commit, the client's next request with any token it holds is
refused. Revoking a revoked link is `409` and writes nothing.
"""

import re
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import ApiError, database_unavailable, not_found
from ..models import OAuthClient, OAuthGrant
from ..times import utc
from . import link_window, oauth, scans

# Revoked links listed after the unrevoked ones, newest revoke first
REVOKED_SHOWN = 20

REVOKE_SUMMARY = "link revoked by the owner"

# A grant's id as the server draws it (`models._uuid`): anything else is not looked up
_GRANT_ID_FORM = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def link_out(
    grant: OAuthGrant,
    client: OAuthClient | None,
    *,
    remote_writes: bool,
    never_completed: bool = False,
) -> dict:
    """One link as the owner's routes answer with it. `never_completed` is
    `oauth.never_completed` as the list's statement read it; a revoked link is never
    one (the rule is about links that still hold a place)."""
    revoked = grant.revoked_at is not None
    return {
        "id": grant.id,
        "client_id": grant.client_id,
        "client_name": client.client_name if client is not None else None,
        "client_registered": client is not None,
        "redirect_host": link_window.redirect_host(grant.redirect_uri),
        "can_write": bool(grant.can_write),
        "writes": not revoked
        and oauth.SCOPE_WRITE in oauth.grant_scopes(grant, remote_writes=remote_writes),
        "created_at": utc(grant.created_at),
        "last_used_at": utc(grant.last_used_at),
        "revoked_at": utc(grant.revoked_at),
        "never_completed": bool(never_completed) and not revoked,
    }


def _with_client(*columns):
    return select(OAuthGrant, OAuthClient, *columns).outerjoin(
        OAuthClient, OAuthClient.client_id == OAuthGrant.client_id
    )


async def list_links(session: AsyncSession, *, remote_writes: bool) -> list[dict]:
    """Every unrevoked link, newest first, then the `REVOKED_SHOWN` most recently
    revoked. **One statement**, whatever the tables hold, so the answer is one snapshot
    of the database: SQLite gives two statements outside a write transaction no shared
    read snapshot, and a revoke or a link committed between two would put one grant in
    the answer twice, or an answer no moment of the database held (round 1 of #195's
    briefed review). The newest revoked are chosen by a subquery of the same
    statement, and whether each link was never completed (`oauth.never_completed`) is a
    column of it; the two groups are put in order here.

    The never-completed column is read by the server's clock just before the
    statement, without the write lock, as the rest of the list is: one snapshot, in
    which an exchange is either wholly committed (its claim, its tokens and
    `last_used_at`, one transaction) or absent. It can therefore be stale only for an
    exchange whose claim had passed, under the lock, by a clock reading earlier than
    this one and has not committed yet: every list read before that commit marks the
    link never completed, however many there are, and every read after it does not
    (`test_a_list_read_before_an_exchange_commits_may_flag_it_and_none_after`). It
    never removes a link from the answer, and nothing acts on it but the owner."""
    now = _now()
    newest_revoked = (
        select(OAuthGrant.id)
        .where(OAuthGrant.revoked_at.is_not(None))
        .order_by(OAuthGrant.revoked_at.desc(), OAuthGrant.id)
        .limit(REVOKED_SHOWN)
    )
    rows = (
        await session.execute(
            _with_client(oauth.never_completed(now).label("never_completed"))
            .where(
                or_(OAuthGrant.revoked_at.is_(None), OAuthGrant.id.in_(newest_revoked))
            )
            .execution_options(populate_existing=True)
        )
    ).all()
    live = sorted(
        (row for row in rows if row[0].revoked_at is None),
        key=lambda row: (_desc(row[0].created_at), row[0].id),
    )
    revoked = sorted(
        (row for row in rows if row[0].revoked_at is not None),
        key=lambda row: (_desc(row[0].revoked_at), row[0].id),
    )
    return [
        link_out(grant, client, remote_writes=remote_writes, never_completed=flag)
        for grant, client, flag in live + revoked
    ]


def _desc(value: datetime) -> float:
    """A key that sorts newest first."""
    return -utc(value).timestamp()


async def revoke(
    session: AsyncSession, grant_id: str, *, source: str, remote_writes: bool
) -> dict:
    """Revoke one link: the grant, its codes and its tokens, with the owner's audit
    entry. Read and decided under the write lock. `404` for an unknown link, `409
    ALREADY_REVOKED` for a revoked one (by the owner, or by the reuse rule), each with
    nothing written."""
    if not _GRANT_ID_FORM.fullmatch(grant_id):
        raise not_found("Linked client", grant_id)
    try:
        await scans.take_write_lock(session)
        grant = await session.get(OAuthGrant, grant_id, populate_existing=True)
        if grant is None:
            await session.rollback()
            raise not_found("Linked client", grant_id)
        if grant.revoked_at is not None:
            await session.rollback()
            raise ApiError("ALREADY_REVOKED", "This link is already revoked", 409)
        client = await session.get(OAuthClient, grant.client_id, populate_existing=True)
        # `client` None: the entry is the owner's, so its actor is null (spec §4.3), not
        # the `grant:<name>` the server's own entries about a grant carry
        await oauth.revoke_grant(
            session, grant, None, _now(), summary=REVOKE_SUMMARY, source=source
        )
        await session.commit()
    except OperationalError as exc:
        await session.rollback()
        raise database_unavailable("link revoke", exc) from exc
    return link_out(grant, client, remote_writes=remote_writes)
