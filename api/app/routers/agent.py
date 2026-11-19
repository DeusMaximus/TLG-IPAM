"""Agent-facing routes, `/api/v1/agent/*` (v0.2 spec §5.7).

Guarded by `auth.require_agent` alone, on the router itself so that every route added
here is too. `main.py` mounts this router outside the loop that adds
`require_api_key`: the owner key does not reach these routes.
"""

import json
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


async def _json_body(request: Request) -> Any:
    """The request body parsed as JSON, read only once the agent token is checked. Its
    size is already bounded: `body_limit.BodyLimitMiddleware` refuses a body over
    `BODY_MAX` before any route runs."""
    try:
        return json.loads(await request.body())
    except (ValueError, RecursionError):
        raise validation_error("The request body must be JSON") from None


@router.post("/results")
async def results(
    # Read by hand rather than as a `Body`, which FastAPI would read and parse before
    # the router's `require_agent` ran.
    request: Request,
    agent: Agent = Depends(require_agent),
    session: AsyncSession = Depends(get_session),
):
    body = await _json_body(request)
    return {"data": await results_svc.ingest(session, agent, body)}
