from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import request_source
from ..database import get_session
from ..schemas import SectionCreate, SectionOut, SectionUpdate
from ..services import sections as svc

router = APIRouter(prefix="/sections", tags=["sections"])


def _out(section, subnet_count: int) -> dict:
    return SectionOut.model_validate(section).model_copy(update={"subnet_count": subnet_count}).model_dump()


@router.get("")
async def list_sections(session: AsyncSession = Depends(get_session)):
    rows = await svc.list_sections(session)
    data = [_out(section, count) for section, count in rows]
    return {"data": data, "total": len(data)}


@router.post("", status_code=201)
async def create_section(
    payload: SectionCreate,
    session: AsyncSession = Depends(get_session),
    source: str = Depends(request_source),
):
    section = await svc.create_section(session, payload, source)
    return {"data": _out(section, 0)}


@router.put("/{section_id}")
async def update_section(
    section_id: str,
    payload: SectionUpdate,
    session: AsyncSession = Depends(get_session),
    source: str = Depends(request_source),
):
    section = await svc.update_section(session, section_id, payload, source)
    return {"data": SectionOut.model_validate(section).model_dump()}


@router.delete("/{section_id}", status_code=204)
async def delete_section(
    section_id: str,
    session: AsyncSession = Depends(get_session),
    source: str = Depends(request_source),
):
    await svc.delete_section(session, section_id, source)
