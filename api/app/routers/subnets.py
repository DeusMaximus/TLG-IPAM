from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import request_source
from ..database import get_session
from ..schemas import (
    AllocateRequest,
    BulkImportRequest,
    ReservedRangeCreate,
    ReservedRangeOut,
    SubnetCreate,
    SubnetUpdate,
)
from ..services import hosts as hosts_svc
from ..services import subnets as svc

router = APIRouter(prefix="/subnets", tags=["subnets"])

_out = svc.subnet_out


@router.get("")
async def list_subnets(
    section_id: str | None = None, session: AsyncSession = Depends(get_session)
):
    subnets = await svc.list_subnets(session, section_id)
    data = [await _out(session, subnet) for subnet in subnets]
    return {"data": data, "total": len(data)}


@router.get("/{subnet_id}")
async def get_subnet(subnet_id: str, session: AsyncSession = Depends(get_session)):
    subnet = await svc.get_subnet(session, subnet_id)
    return {"data": await _out(session, subnet)}


@router.post("", status_code=201)
async def create_subnet(
    payload: SubnetCreate,
    session: AsyncSession = Depends(get_session),
    source: str = Depends(request_source),
):
    subnet = await svc.create_subnet(session, payload, source)
    return {"data": await _out(session, subnet)}


@router.put("/{subnet_id}")
async def update_subnet(
    subnet_id: str,
    payload: SubnetUpdate,
    session: AsyncSession = Depends(get_session),
    source: str = Depends(request_source),
):
    subnet = await svc.update_subnet(session, subnet_id, payload, source)
    return {"data": await _out(session, subnet)}


@router.delete("/{subnet_id}", status_code=204)
async def delete_subnet(
    subnet_id: str,
    force: bool = Query(default=False, description="Also delete all hosts in the subnet"),
    session: AsyncSession = Depends(get_session),
    source: str = Depends(request_source),
):
    await svc.delete_subnet(session, subnet_id, source, force=force)


@router.get("/{subnet_id}/free")
async def get_free_ips(
    subnet_id: str,
    limit: int = Query(default=100, ge=1, le=1024),
    session: AsyncSession = Depends(get_session),
):
    ips = await svc.free_ips(session, subnet_id, limit=limit)
    return {"data": ips, "total": len(ips)}


@router.post("/{subnet_id}/next-free", status_code=201)
async def allocate_next_free(
    subnet_id: str,
    payload: AllocateRequest,
    session: AsyncSession = Depends(get_session),
    source: str = Depends(request_source),
):
    host = await hosts_svc.allocate_next(session, subnet_id, payload, source)
    return {"data": await hosts_svc.host_out(session, host)}


@router.post("/{subnet_id}/hosts/bulk")
async def bulk_create_hosts(
    subnet_id: str,
    payload: BulkImportRequest,
    session: AsyncSession = Depends(get_session),
    source: str = Depends(request_source),
):
    result = await hosts_svc.bulk_create(session, subnet_id, payload.hosts, source)
    return {"data": result}


@router.post("/{subnet_id}/reserved", status_code=201)
async def create_reserved_range(
    subnet_id: str,
    payload: ReservedRangeCreate,
    session: AsyncSession = Depends(get_session),
    source: str = Depends(request_source),
):
    block = await svc.create_reserved_range(session, subnet_id, payload, source)
    return {"data": ReservedRangeOut.model_validate(block).model_dump()}


@router.delete("/{subnet_id}/reserved/{range_id}", status_code=204)
async def delete_reserved_range(
    subnet_id: str,
    range_id: str,
    session: AsyncSession = Depends(get_session),
    source: str = Depends(request_source),
):
    await svc.delete_reserved_range(session, subnet_id, range_id, source)
