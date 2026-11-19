"""Agent-facing routes, `/api/v1/agent/*` (v0.2 spec §5.7).

Guarded by `auth.require_agent` alone, on the router itself so that every route added
here is too. `main.py` mounts this router outside the loop that adds
`require_api_key`: the owner key does not reach these routes.
"""

from typing import Any

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_agent
from ..database import get_session
from ..errors import validation_error
from ..models import Agent
from ..services import jobs as svc
from ..services import results as results_svc

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


@router.post("/results")
async def results(
    # Read by hand rather than as a `Body`: the body is capped for the whole server
    # (`main.REQUEST_BODY_MAX`) before this runs; it is read after the agent token is
    # checked.
    request: Request,
    agent: Agent = Depends(require_agent),
    session: AsyncSession = Depends(get_session),
):
    try:
        body = await request.json()
    except (ValueError, RecursionError):
        raise validation_error("The request body must be JSON") from None
    return {"data": await results_svc.ingest(session, agent, body)}
