from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import request_source
from ..database import get_session
from ..schemas import ScanTargetCreate, ScanTargetUpdate
from ..services import scans as svc

router = APIRouter(prefix="/scan-targets", tags=["scan-targets"])


@router.get("")
async def list_scan_targets(session: AsyncSession = Depends(get_session)):
    data = await svc.targets_out(session, await svc.list_targets(session))
    return {"data": data, "total": len(data)}


@router.post("", status_code=201)
async def create_scan_target(
    payload: ScanTargetCreate,
    session: AsyncSession = Depends(get_session),
    source: str = Depends(request_source),
):
    target = await svc.create_target(session, payload, source)
    return {"data": await svc.target_out(session, target)}


@router.get("/{target_id}")
async def get_scan_target(target_id: str, session: AsyncSession = Depends(get_session)):
    return {"data": await svc.target_out(session, await svc.get_target(session, target_id))}


@router.put("/{target_id}")
async def update_scan_target(
    target_id: str,
    payload: ScanTargetUpdate,
    session: AsyncSession = Depends(get_session),
    source: str = Depends(request_source),
):
    target = await svc.update_target(session, target_id, payload, source)
    return {"data": await svc.target_out(session, target)}


@router.delete("/{target_id}", status_code=204)
async def delete_scan_target(
    target_id: str,
    session: AsyncSession = Depends(get_session),
    source: str = Depends(request_source),
):
    await svc.delete_target(session, target_id, source)
