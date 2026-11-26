"""The owner's **Linked clients** (v0.2 spec §6.6, #30): list the links and revoke
one. Owner key only, as every route `main.create_app` includes with `require_api_key`;
a grant's token is no credential here (spec §6.5). The rules are
`services/linked_clients`."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import request_source
from ..database import get_session
from ..schemas import LinkedClientOut
from ..services import linked_clients as svc
from .link_window import remote_writes

router = APIRouter(prefix="/linked-clients", tags=["linked clients"])


@router.get("")
async def list_linked_clients(
    session: AsyncSession = Depends(get_session), writes: bool = Depends(remote_writes)
):
    links = await svc.list_links(session, remote_writes=writes)
    return {"data": [LinkedClientOut.model_validate(link).model_dump() for link in links]}


@router.post("/{grant_id}/revoke")
async def revoke_linked_client(
    grant_id: str,
    session: AsyncSession = Depends(get_session),
    source: str = Depends(request_source),
    writes: bool = Depends(remote_writes),
):
    link = await svc.revoke(session, grant_id, source=source, remote_writes=writes)
    return {"data": LinkedClientOut.model_validate(link).model_dump()}
