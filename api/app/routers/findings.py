from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import request_source
from ..database import get_session
from ..schemas import FindingKeysRequest
from ..services import findings as svc

# Owner REST only. Over MCP, #17 adds `list_scan_findings`; there is no dismiss tool,
# and accepting goes through `create_host`/`update_host` (spec §5.8).
router = APIRouter(tags=["findings"])


@router.get("/subnets/{subnet_id}/findings")
async def list_findings(
    subnet_id: str,
    type: str | None = None,
    include_dynamic: bool = False,
    session: AsyncSession = Depends(get_session),
):
    data = await svc.list_findings(session, subnet_id, type, include_dynamic)
    return {"data": data, "total": len(data)}


@router.post("/findings/accept")
async def accept_findings(
    payload: FindingKeysRequest,
    session: AsyncSession = Depends(get_session),
    source: str = Depends(request_source),
):
    return {"data": await svc.accept(session, payload.findings, source)}


@router.post("/findings/dismiss")
async def dismiss_findings(
    payload: FindingKeysRequest,
    session: AsyncSession = Depends(get_session),
    source: str = Depends(request_source),
):
    return {"data": await svc.dismiss(session, payload.findings, source)}
