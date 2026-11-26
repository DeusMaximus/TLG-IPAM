"""The owner's link window (v0.2 spec §6.3, #27): open, read, cancel. Owner key only,
as every route `main.create_app` includes with `require_api_key`. The window itself is
`services/link_window`."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import request_source
from ..database import get_session
from ..schemas import LinkWindowOpen, LinkWindowOpened, LinkWindowOut
from ..services import link_window as svc

router = APIRouter(prefix="/link-window", tags=["link window"])


def oauth_configured(request: Request) -> bool:
    """Whether the app serving the request has the OAuth server (`PUBLIC_BASE_URL`),
    as `main.create_app` recorded it."""
    return bool(getattr(request.app.state, "public_base_url", ""))


@router.get("")
async def get_link_window(configured: bool = Depends(oauth_configured)):
    return {"data": LinkWindowOut.model_validate(svc.state_out(configured=configured)).model_dump()}


@router.post("", status_code=201)
async def open_link_window(
    payload: LinkWindowOpen,
    session: AsyncSession = Depends(get_session),
    source: str = Depends(request_source),
    configured: bool = Depends(oauth_configured),
):
    state, code = await svc.open_window(
        session, configured=configured, can_write=payload.can_write, source=source
    )
    return {"data": LinkWindowOpened.model_validate({**state, "code": code}).model_dump()}


@router.delete("")
async def cancel_link_window(configured: bool = Depends(oauth_configured)):
    return {"data": LinkWindowOut.model_validate(svc.cancel(configured=configured)).model_dump()}
