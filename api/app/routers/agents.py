from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import request_source
from ..database import get_session
from ..schemas import AgentCreate, AgentCreated, AgentOut, AgentUpdate
from ..services import agents as svc

router = APIRouter(prefix="/agents", tags=["agents"])


def _out(agent) -> dict:
    return AgentOut.model_validate(agent).model_dump()


@router.get("")
async def list_agents(session: AsyncSession = Depends(get_session)):
    rows = await svc.list_agents(session)
    return {"data": [_out(a) for a in rows], "total": len(rows)}


@router.post("", status_code=201)
async def create_agent(
    payload: AgentCreate,
    session: AsyncSession = Depends(get_session),
    source: str = Depends(request_source),
):
    agent, token = await svc.create_agent(session, payload, source)
    return {"data": AgentCreated.model_validate({**_out(agent), "token": token}).model_dump()}


@router.get("/{agent_id}")
async def get_agent(agent_id: str, session: AsyncSession = Depends(get_session)):
    return {"data": _out(await svc.get_agent(session, agent_id))}


@router.put("/{agent_id}")
async def update_agent(
    agent_id: str,
    payload: AgentUpdate,
    session: AsyncSession = Depends(get_session),
    source: str = Depends(request_source),
):
    return {"data": _out(await svc.update_agent(session, agent_id, payload, source))}


@router.post("/{agent_id}/revoke")
async def revoke_agent_tokens(
    agent_id: str,
    session: AsyncSession = Depends(get_session),
    source: str = Depends(request_source),
):
    return {"data": _out(await svc.revoke_tokens(session, agent_id, source))}


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: str,
    session: AsyncSession = Depends(get_session),
    source: str = Depends(request_source),
):
    await svc.delete_agent(session, agent_id, source)
