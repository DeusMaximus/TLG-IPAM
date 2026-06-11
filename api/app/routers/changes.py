from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..schemas import ChangeOut
from ..services import changes as svc

router = APIRouter(prefix="/changes", tags=["changes"])


@router.get("")
async def list_changes(
    entity_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    rows = await svc.list_changes(session, entity_type=entity_type, limit=limit)
    return {"data": [ChangeOut.model_validate(c).model_dump() for c in rows], "total": len(rows)}
