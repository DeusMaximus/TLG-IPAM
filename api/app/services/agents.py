"""Scan agents and their tokens (v0.2 spec §4.2, §5.4).

An agent token (`tlga_…`) is shown once, in the response that creates it. Only its
SHA-256 and a short display prefix are stored, so it cannot be recovered afterwards.
Agents and tokens are configured by the owner over REST only: nothing over MCP
creates, changes or lists them (spec §7.2, invariant 2).
"""

import hashlib
import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..errors import conflict, not_found
from ..models import Agent, Token
from ..schemas import AgentCreate, AgentUpdate
from . import changes

AGENT_TOKEN_PREFIX = "tlga_"
# characters of the token kept for display: the kind prefix plus 8 of the 43 random ones
_DISPLAY_LENGTH = len(AGENT_TOKEN_PREFIX) + 8


def _now() -> datetime:
    return datetime.now(timezone.utc)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def actor_for(agent: Agent) -> str:
    """The audit `actor` for a write the agent itself makes (spec §4.3)."""
    return f"agent:{agent.name}"


def _display(token: Token) -> str:
    return f"{token.token_prefix}…"


async def list_agents(session: AsyncSession) -> list[Agent]:
    query = select(Agent).options(selectinload(Agent.tokens)).order_by(Agent.name)
    return list((await session.scalars(query)).all())


async def get_agent(session: AsyncSession, agent_id: str) -> Agent:
    agent = await session.scalar(
        select(Agent).options(selectinload(Agent.tokens)).where(Agent.id == agent_id)
    )
    if agent is None:
        raise not_found("Agent", agent_id)
    return agent


async def _check_name_free(session: AsyncSession, name: str, agent_id: str | None = None) -> None:
    # compared in Python: SQLite's lower() folds ASCII only
    rows = (await session.execute(select(Agent.id, Agent.name))).all()
    for other_id, other_name in rows:
        if other_id != agent_id and other_name.casefold() == name.casefold():
            raise conflict(f"Agent {other_name} already exists")


async def _flush_name(session: AsyncSession, name: str) -> None:
    # UNIQUE(name) is the backstop for two requests racing past _check_name_free
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise conflict(f"Agent {name} already exists") from None


def _issue_agent_token(session: AsyncSession, agent: Agent) -> tuple[Token, str]:
    plain = AGENT_TOKEN_PREFIX + secrets.token_urlsafe(32)
    token = Token(
        kind="agent",
        agent=agent,
        token_hash=hash_token(plain),
        token_prefix=plain[:_DISPLAY_LENGTH],
        created_at=_now(),
    )
    session.add(token)
    return token, plain


def _record_token(
    session: AsyncSession, token: Token, action: str, summary: str, source: str, before=None
) -> None:
    changes.record(
        session,
        entity_type="token",
        entity_id=token.id,
        action=action,
        summary=summary,
        source=source,
        before=before,
        after=changes.snapshot(token),
    )


async def create_agent(
    session: AsyncSession, payload: AgentCreate, source: str
) -> tuple[Agent, str]:
    """Create an agent together with its agent token. Returns the agent and the plain
    token, which the caller shows once and must not store."""
    await _check_name_free(session, payload.name)
    agent = Agent(name=payload.name, state="active", created_at=_now(), tokens=[])
    session.add(agent)
    token, plain = _issue_agent_token(session, agent)
    await _flush_name(session, agent.name)
    changes.record(
        session,
        entity_type="agent",
        entity_id=agent.id,
        action="create",
        summary=f"agent {agent.name} created",
        source=source,
        after=changes.snapshot(agent),
    )
    _record_token(
        session, token, "create", f"token {_display(token)} issued to agent {agent.name}", source
    )
    await session.commit()
    return agent, plain


def _enabled_state(agent: Agent) -> str:
    # `pending` means the agent has never held an agent token (M2b: not yet enrolled)
    return "active" if any(t.kind == "agent" for t in agent.tokens) else "pending"


async def update_agent(
    session: AsyncSession, agent_id: str, payload: AgentUpdate, source: str
) -> Agent:
    agent = await get_agent(session, agent_id)
    before = changes.snapshot(agent)
    data = payload.model_dump(exclude_unset=True)
    parts = []
    if "name" in data and data["name"] != agent.name:
        await _check_name_free(session, data["name"], agent.id)
        parts.append(f"renamed from {agent.name}")
        agent.name = data["name"]
        await _flush_name(session, agent.name)
    if "enabled" in data:
        state = _enabled_state(agent) if data["enabled"] else "disabled"
        if (state == "disabled") != (agent.state == "disabled"):
            parts.append("enabled" if data["enabled"] else "disabled")
        agent.state = state
    if not parts:
        return agent
    changes.record(
        session,
        entity_type="agent",
        entity_id=agent.id,
        action="update",
        summary=f"agent {agent.name} {', '.join(parts)}",
        source=source,
        before=before,
        after=changes.snapshot(agent),
    )
    await session.commit()
    return agent


def _revoke_live_tokens(session: AsyncSession, agent: Agent, source: str, reason: str = "") -> int:
    now = _now()
    revoked = 0
    for token in agent.tokens:
        if token.revoked_at is not None:
            continue
        before = changes.snapshot(token)
        token.revoked_at = now
        summary = f"token {_display(token)} of agent {agent.name} revoked{reason}"
        _record_token(session, token, "revoke", summary, source, before)
        revoked += 1
    return revoked


async def revoke_tokens(session: AsyncSession, agent_id: str, source: str) -> Agent:
    """Revoke every unrevoked token of the agent. The agent itself is kept."""
    agent = await get_agent(session, agent_id)
    if not _revoke_live_tokens(session, agent, source):
        raise conflict(f"Agent {agent.name} has no unrevoked token")
    await session.commit()
    return agent


async def delete_agent(session: AsyncSession, agent_id: str, source: str) -> None:
    """Delete the agent. Its unrevoked tokens are revoked first, each with its own
    change-log entry; its token rows are then deleted with it (cascade)."""
    agent = await get_agent(session, agent_id)
    _revoke_live_tokens(session, agent, source, " (agent deleted)")
    changes.record(
        session,
        entity_type="agent",
        entity_id=agent.id,
        action="delete",
        summary=f"agent {agent.name} deleted",
        source=source,
        before=changes.snapshot(agent),
    )
    await session.delete(agent)
    await session.commit()


async def authenticate(session: AsyncSession, presented: str) -> Agent | None:
    """The agent a presented bearer belongs to, or None. Accepts only an unrevoked
    `agent` token of an `active` agent, and records its use in `last_used_at`.

    The lookup is by SHA-256 of the presented value, so no comparison ever touches
    the stored secret: timing reveals at most where an attacker's own hash sorts.
    """
    row = (
        await session.execute(
            select(Token, Agent)
            .join(Agent, Token.agent_id == Agent.id)
            .where(Token.token_hash == hash_token(presented))
        )
    ).first()
    if row is None:
        return None
    token, agent = row
    if token.kind != "agent" or token.revoked_at is not None or agent.state != "active":
        return None
    token.last_used_at = _now()
    await session.commit()
    return agent
