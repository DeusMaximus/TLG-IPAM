from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import request_source
from ..database import get_session
from ..schemas import BulkDeleteRequest, HostCreate, HostUpdate
from ..services import hosts as svc


router = APIRouter(prefix="/hosts", tags=["hosts"])


@router.get("")
async def list_hosts(
    subnet_id: str | None = None,
    status: str | None = None,
    q: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    hosts = await svc.list_hosts(session, subnet_id=subnet_id, status=status, q=q)
    return {"data": await svc.hosts_out(session, hosts), "total": len(hosts)}


@router.post("/bulk-delete")
async def bulk_delete_hosts(
    payload: BulkDeleteRequest,
    session: AsyncSession = Depends(get_session),
    source: str = Depends(request_source),
):
    result = await svc.bulk_delete(session, payload.host_ids, source)
    return {"data": result}


@router.get("/{host_id}")
async def get_host(host_id: str, session: AsyncSession = Depends(get_session)):
    host = await svc.get_host(session, host_id)
    return {"data": await svc.host_out(session, host)}


@router.post("", status_code=201)
async def create_host(
    payload: HostCreate,
    session: AsyncSession = Depends(get_session),
    source: str = Depends(request_source),
):
    host = await svc.create_host(session, payload, source)
    return {"data": await svc.host_out(session, host)}


@router.put("/{host_id}")
async def update_host(
    host_id: str,
    payload: HostUpdate,
    session: AsyncSession = Depends(get_session),
    source: str = Depends(request_source),
):
    host = await svc.update_host(session, host_id, payload, source)
    return {"data": await svc.host_out(session, host)}


@router.delete("/{host_id}", status_code=204)
async def delete_host(
    host_id: str,
    session: AsyncSession = Depends(get_session),
    source: str = Depends(request_source),
):
    await svc.delete_host(session, host_id, source)
