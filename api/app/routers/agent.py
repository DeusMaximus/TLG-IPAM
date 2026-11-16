"""Agent-facing routes, `/api/v1/agent/*` (v0.2 spec §5.7).

Guarded by `auth.require_agent` alone, on the router itself so that every route added
here is too. `main.py` mounts this router outside the loop that adds
`require_api_key`: the owner key does not reach these routes.
"""

import json
from typing import Any

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent_protocol import RESULTS_BODY_MAX
from ..auth import require_agent
from ..database import get_session
from ..errors import ApiError, validation_error
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


def _too_large(cap: int) -> ApiError:
    return ApiError("PAYLOAD_TOO_LARGE", f"The request body may be at most {cap} bytes", 413)


async def _capped_json(request: Request, cap: int) -> Any:
    """The request body parsed as JSON, read only after authentication and never past
    `cap` bytes: a declared length over it is refused unread, and so is a body that
    grows past it while streaming. Transport only; #60 replaces this with one cap
    applied to every route."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > cap:
        raise _too_large(cap)
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > cap:
            raise _too_large(cap)
    try:
        return json.loads(bytes(body))
    except (ValueError, RecursionError):
        raise validation_error("The request body must be JSON") from None


@router.post("/results")
async def results(
    # Read by hand rather than as a `Body`, which FastAPI would parse before the
    # router's `require_agent` ran, and without a size cap.
    request: Request,
    agent: Agent = Depends(require_agent),
    session: AsyncSession = Depends(get_session),
):
    body = await _capped_json(request, RESULTS_BODY_MAX)
    return {"data": await results_svc.ingest(session, agent, body)}
