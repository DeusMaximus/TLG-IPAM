"""Agent-facing routes, `/api/v1/agent/*` (v0.2 spec §5.7).

`router` holds every route but `enrol` and is guarded by `auth.require_agent` alone, as
a dependency of the router itself, so that every route added to it is too.
`enrol_router` holds `enrol` alone, the one route reachable without a long-lived token:
it takes an enrolment token, checked by `services/enrolment` behind its own throttle,
and never `require_agent`. `main.py` mounts both outside the loop that adds
`require_api_key`: the owner key does not reach these routes.
"""

import json
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import bearer_token, require_agent
from ..database import get_session
from ..errors import validation_error
from ..models import Agent
from ..services import enrolment as enrolment_svc
from ..services import jobs as svc
from ..services import results as results_svc

router = APIRouter(prefix="/agent", tags=["agent"], dependencies=[Depends(require_agent)])
# `enrol` alone: a route added here is reachable with no agent token at all
enrol_router = APIRouter(prefix="/agent", tags=["agent"])


async def _json_body(request: Request) -> Any:
    """The request body parsed as JSON, read only once the route's token is checked. Its
    size is already bounded: `body_limit.BodyLimitMiddleware` refuses a body over
    `BODY_MAX` before any route runs."""
    try:
        return json.loads(await request.body())
    except (ValueError, RecursionError):
        raise validation_error("The request body must be JSON") from None


@enrol_router.post("/enrol")
async def enrol(
    # Read by hand, and only once the throttle has admitted the attempt and the token is
    # found usable (`services/enrolment.enrol`).
    request: Request,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    data = await enrolment_svc.enrol(
        session, bearer_token(authorization), lambda: _json_body(request)
    )
    return {"data": data}


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
    # Read by hand rather than as a `Body`, which FastAPI would read and parse before
    # the router's `require_agent` ran.
    request: Request,
    agent: Agent = Depends(require_agent),
    session: AsyncSession = Depends(get_session),
):
    body = await _json_body(request)
    return {"data": await results_svc.ingest(session, agent, body)}
