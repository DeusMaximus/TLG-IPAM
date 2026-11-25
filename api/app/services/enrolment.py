"""The enrolment exchange, `POST /api/v1/agent/enrol` (v0.2 spec §4.2, §5.6, §5.7).

An agent presents its single-use enrolment token (`tlge_…`) as `Authorization: Bearer`
and gets its long-lived agent token (`tlga_…`) back. The exchange marks the enrolment
token used, revokes any agent token the agent held before (re-enrol), issues the new
one and makes the agent `active`, in one transaction. The agent keeps its id, its name
and its scan targets.

Before it claims the token, the exchange reads the protocol the body states
(`agent_protocol.enrol_protocol_refusal`; none means protocol 2, every release before
#165) and refuses one other than the server's, `409 AGENT_TOO_OLD` or `AGENT_TOO_NEW`,
with the token still unused, so an agent that could not read the answer, or whose
check-ins would all be refused, can be updated and enrol with the same token.

It is the only agent route reachable without a long-lived token, so it is not behind
`auth.require_agent` and it has its own guard: a small global throttle on attempts
(`throttle`). It never uses `agents.authenticate` or the agent token's context in
`session.info` (`agents.token_in_use`), which belong to an `agent` token, and refuses to
run on a session that carries one.

A refusal for the protocol is recorded in process memory alone
(`agents.enrol_refusals`, #175), for the Agents page to say why the agent never
arrived: by token, the server's code and the time, with nothing locked or written.

What it records of the token's use: `used_at` on the enrolment token, which is what
makes it single-use. It writes no `last_used_at`, on either token: the new agent
token's first is written by the agent's first check-in, as for any agent token.
"""

import time
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError
from sqlalchemy import exists, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..agent_protocol import ENROL_TOO_OLD, EnrolRequest, EnrolResponse, enrol_protocol_refusal
from ..errors import ApiError, database_unavailable, validation_error
from ..models import Agent, Token
from ..times import utc
from . import agents, changes, jobs, scans

# The throttle (spec §5.6): at most THROTTLE_ATTEMPTS attempts that did not enrol in any
# THROTTLE_WINDOW_S, across every caller. An attempt that enrols gives its place back.
# While the window is full, every attempt is refused `429` before its token is looked
# up or its body read, a good token included, and a refused attempt does not lengthen
# the wait. A legitimate install makes one attempt and an operator fixing a mistyped
# token a few, so ten a minute never stands in their way. Guessing a token (256 random
# bits) is hopeless at any rate; what the throttle bounds is the work and the log noise
# that a scanner hammering the one unauthenticated agent route can cause. It is global
# because the route sits behind whatever proxy or tunnel the owner runs, where a
# client's address says nothing. In memory: the server runs one process (`AGENTS.md`),
# and a restart empties it.
THROTTLE_ATTEMPTS = 10
THROTTLE_WINDOW_S = 60.0


class AttemptThrottle:
    """A sliding window of attempts. Single event loop: `admit` takes its place before
    the request awaits anything, so concurrent attempts cannot all pass a full window."""

    def __init__(self, attempts: int, window_s: float, clock: Callable[[], float] = time.monotonic):
        self.attempts = attempts
        self.window_s = window_s
        self.clock = clock
        self._taken: deque[float] = deque()

    def admit(self) -> float | None:
        """Take a place in the window, or None when it is full."""
        now = self.clock()
        while self._taken and self._taken[0] <= now - self.window_s:
            self._taken.popleft()
        if len(self._taken) >= self.attempts:
            return None
        self._taken.append(now)
        return now

    def give_back(self, place: float) -> None:
        """Return the place an attempt that enrolled took."""
        try:
            self._taken.remove(place)
        except ValueError:
            pass  # already outside the window

    def reset(self) -> None:
        self._taken.clear()


throttle = AttemptThrottle(THROTTLE_ATTEMPTS, THROTTLE_WINDOW_S)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _throttled() -> ApiError:
    return ApiError(
        "RATE_LIMITED",
        f"Too many enrolment attempts; try again in {int(THROTTLE_WINDOW_S)} seconds",
        429,
    )


def _missing() -> ApiError:
    return ApiError("UNAUTHORIZED", "Missing enrolment token (Authorization: Bearer)", 401)


def _refused() -> ApiError:
    # one answer for every refusal, so a caller learns nothing about a token it guessed
    return ApiError(
        "FORBIDDEN",
        "Invalid enrolment token: it is unknown, used, expired or revoked, or its agent "
        "is disabled. Re-enrol the agent for a new one",
        403,
    )


def _protocol_refused(code: str) -> ApiError:
    # fixed text, naming neither protocol: the code alone says which way it is (#165)
    if code == ENROL_TOO_OLD:
        message = (
            "This agent release is older than the server: install the agent release that "
            "matches the server. The enrolment token was not used"
        )
    else:
        message = (
            "This agent release is newer than the server: update the server, or install "
            "the agent release that matches it. The enrolment token was not used"
        )
    return ApiError(code, message, 409)


def _usable(token: Token, agent: Agent, now: datetime) -> bool:
    return token.kind == "enrol" and agents.live(token, now) and agent.state != "disabled"


def _validation_message(exc: ValidationError) -> str:
    # field names and the validators' own messages only: never the rejected value
    first = exc.errors(include_input=False, include_url=False)[0]
    loc = ".".join(str(part) for part in first["loc"])
    return f"Enrolment refused: {loc}: {first['msg']}" if loc else f"Enrolment refused: {first['msg']}"


async def _claim(session: AsyncSession, token_id: str, now: datetime) -> bool:
    """Mark the enrolment token used, if it still can be: a compare-and-set that
    requires it unused, unrevoked and unexpired and its agent not disabled. The caller
    holds SQLite's write lock, so everything after it reads the database as committed:
    two exchanges of one token cannot both pass, and an owner's revoke, re-enrol or
    disable that commits first refuses it. `kind` is defensive: no API changes a
    token's kind, and the lookup has already checked it."""
    agent_usable = exists().where(Agent.id == Token.agent_id, Agent.state != "disabled")
    result = await session.execute(
        update(Token)
        .where(
            Token.id == token_id,
            Token.kind == "enrol",
            Token.used_at.is_(None),
            Token.revoked_at.is_(None),
            Token.expires_at > now,
            agent_usable,
        )
        .values(used_at=now)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


async def _exchange(
    session: AsyncSession, presented: str | None, read_body: Callable[[], Awaitable[Any]]
) -> dict:
    if not presented:
        raise _missing()
    now = _now()
    row = (
        await session.execute(
            select(Token, Agent)
            .join(Agent, Token.agent_id == Agent.id)
            .where(Token.token_hash == agents.hash_token(presented))
        )
    ).first()
    if row is None or not _usable(*row, now):
        raise _refused()
    token, agent = row
    # read only once the token is known to be usable
    body = await read_body()
    # The agent's protocol, before the rest of the body (which another protocol may
    # shape differently) and before the claim, so an agent this server cannot answer
    # is refused with its enrolment token unspent (#165). An attempt refused here
    # counts against the throttle, as every refusal but its own does. What the owner is
    # shown of it (#175) is kept in process memory (`agents.enrol_refusals`), keyed by
    # this token: the server's code and time, never a value the body sent, and no
    # database write, so nothing here locks, writes or needs an audit entry.
    refusal = enrol_protocol_refusal(body)
    if refusal is not None:
        agents.enrol_refusals.record(token.id, refusal, _now())
        raise _protocol_refused(refusal)
    try:
        report = EnrolRequest.model_validate(body)
    except ValidationError as exc:
        raise validation_error(_validation_message(exc)) from None
    # the token as it was: the compare-and-set below proves it was unused and unrevoked
    token_before = changes.snapshot(token)

    try:
        # The claim is judged at its own time, not the lookup's: the body may have taken
        # long to arrive (a declared Content-Length is streamed, not buffered), and the
        # wait for SQLite's writer may be long too (its busy timeout). So the write lock
        # is taken first, inside this block (a refused lock is a 503 with nothing
        # written), and the time is read once it is held: a token that expired while
        # the exchange queued is not spent.
        await scans.take_write_lock(session)
        now = _now()
        if not await _claim(session, token.id, now):
            await session.rollback()
            raise _refused()
        agent = await session.scalar(
            select(Agent)
            .options(selectinload(Agent.tokens))
            .where(Agent.id == token.agent_id)
            .execution_options(populate_existing=True)
        )
        actor = agents.actor_for(agent)
        enrol_token = next(t for t in agent.tokens if t.id == token.id)
        agent_before = changes.snapshot(agent)
        agents.record_token(
            session,
            enrol_token,
            "update",
            f"enrolment token {agents.display(enrol_token)} of agent {agent.name} exchanged",
            "agent",
            token_before,
            actor,
        )
        # re-enrol: the agent token it held before is refused from this commit on
        agents.revoke_live_tokens(
            session, agent, "agent", " (re-enrolled)", kinds=("agent",), actor=actor, now=now
        )
        # the revoke reaches the database before the insert, for the partial unique index
        # that allows one unrevoked agent token per agent
        await session.flush()
        new_token, plain = agents.issue_token(session, agent, "agent", now)
        await session.flush()
        agents.record_token(
            session,
            new_token,
            "create",
            f"token {agents.display(new_token)} issued to agent {agent.name} (enrolment)",
            "agent",
            actor=actor,
        )
        agent.state = "active"
        # what the agent reports; its protocol is judged above, not stored
        for field, value in report.model_dump(exclude_none=True, exclude={"protocol"}).items():
            setattr(agent, field, value)
        changes.record(
            session,
            entity_type="agent",
            entity_id=agent.id,
            action="update",
            summary=f"agent {agent.name} enrolled",
            source="agent",
            actor=actor,
            before=agent_before,
            after=changes.snapshot(agent),
        )
        # the agent's own entries keep their cap, enrolment's as check-ins' (spec §5.7)
        await session.flush()
        await jobs.prune_agent_audit(session, agent)
        answer = EnrolResponse(agent_id=agent.id, agent_token=plain).model_dump(mode="json")
        await session.commit()
    except OperationalError as exc:
        await session.rollback()
        raise database_unavailable("enrolment", exc) from exc
    # tidiness only: an enrolled agent's only current token is a live one, and it has
    # none now (`agents.current_enrolment`)
    agents.enrol_refusals.drop(t.id for t in agent.tokens)
    return answer


async def enrol(
    session: AsyncSession, presented: str | None, read_body: Callable[[], Awaitable[Any]]
) -> dict:
    """Handle `POST /api/v1/agent/enrol`: exchange the presented enrolment token for
    the agent's token. `read_body` reads the request body as JSON; it is called only
    once the attempt is admitted and the token found usable.

    Refusals: `429 RATE_LIMITED` while the throttle is full; `401` with no bearer; `403`
    for any bearer that is not a usable enrolment token (unknown, used, expired,
    revoked, another kind of token, the owner key, or one whose agent is disabled);
    `409 AGENT_TOO_OLD` / `409 AGENT_TOO_NEW` for a body whose `protocol` is not the
    server's (`agent_protocol.enrol_protocol_refusal`, absent meaning protocol 2), and
    `422` for a body that is not JSON or not an `EnrolRequest`, both of which leave the
    token usable; `503 UNAVAILABLE` for a write the database refused, rolled back whole.
    Every refusal but the throttle's own counts against it."""
    if agents.carries_agent_token(session):
        raise RuntimeError("the enrolment exchange runs on its own session, never an agent token's")
    place = throttle.admit()
    if place is None:
        raise _throttled()
    answer = await _exchange(session, presented, read_body)
    throttle.give_back(place)
    return answer
