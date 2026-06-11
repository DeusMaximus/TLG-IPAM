from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import request_source
from ..database import get_session
from ..schemas import DeviceTypeCreate, DeviceTypeOut, DeviceTypeUpdate
from ..services import device_types as svc

router = APIRouter(prefix="/device-types", tags=["device-types"])


def _out(device_type, host_count: int = 0) -> dict:
    return (
        DeviceTypeOut.model_validate(device_type)
        .model_copy(update={"host_count": host_count})
        .model_dump()
    )


@router.get("")
async def list_device_types(session: AsyncSession = Depends(get_session)):
    rows = await svc.list_with_counts(session)
    return {"data": [_out(dt, count) for dt, count in rows], "total": len(rows)}


@router.post("", status_code=201)
async def create_device_type(
    payload: DeviceTypeCreate,
    session: AsyncSession = Depends(get_session),
    source: str = Depends(request_source),
):
    device_type = await svc.create_device_type(session, payload, source)
    return {"data": _out(device_type)}


@router.put("/{type_id}")
async def update_device_type(
    type_id: str,
    payload: DeviceTypeUpdate,
    session: AsyncSession = Depends(get_session),
    source: str = Depends(request_source),
):
    device_type = await svc.update_device_type(session, type_id, payload, source)
    return {"data": _out(device_type)}


@router.delete("/{type_id}", status_code=204)
async def delete_device_type(
    type_id: str,
    session: AsyncSession = Depends(get_session),
    source: str = Depends(request_source),
):
    await svc.delete_device_type(session, type_id, source)
