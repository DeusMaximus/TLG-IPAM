"""Agent-facing routes, `/api/v1/agent/*` (v0.2 spec §5.7).

Guarded by `auth.require_agent` alone, on the router itself so that every route added
here is too. `main.py` mounts this router outside the loop that adds
`require_api_key`: the owner key does not reach these routes.
"""

from typing import Any

from fastapi import APIRouter, Body, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_agent
from ..database import get_session
from ..models import Agent
from ..services import jobs as svc

router = APIRouter(prefix="/agent", tags=["agent"], dependencies=[Depends(require_agent)])


@router.post("/checkin")
async def checkin(
    # A plain object here: `services/jobs.checkin` checks the protocol before the rest
    # of the body, against `agent_protocol.CheckinRequest`.
    body: dict[str, Any] = Body(...),
    agent: Agent = Depends(require_agent),
    session: AsyncSession = Depends(get_session),
):
    return {"data": await svc.checkin(session, agent, body)}
