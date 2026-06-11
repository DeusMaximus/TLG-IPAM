from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import conflict, not_found
from ..models import Section, Subnet
from ..schemas import SectionCreate, SectionUpdate
from . import changes


async def get_section(session: AsyncSession, section_id: str) -> Section:
    section = await session.get(Section, section_id)
    if section is None:
        raise not_found("Section", section_id)
    return section


async def list_sections(session: AsyncSession) -> list[tuple[Section, int]]:
    query = (
        select(Section, func.count(Subnet.id))
        .outerjoin(Subnet)
        .group_by(Section.id)
        .order_by(Section.name)
    )
    return [(section, count) for section, count in (await session.execute(query)).all()]


async def create_section(session: AsyncSession, payload: SectionCreate, source: str) -> Section:
    section = Section(**payload.model_dump())
    session.add(section)
    await session.flush()
    changes.record(
        session,
        entity_type="section",
        entity_id=section.id,
        action="create",
        summary=f"section {section.name} created",
        source=source,
        after=changes.snapshot(section),
    )
    await session.commit()
    return section


async def update_section(
    session: AsyncSession, section_id: str, payload: SectionUpdate, source: str
) -> Section:
    section = await get_section(session, section_id)
    before = changes.snapshot(section)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(section, field, value)
    changes.record(
        session,
        entity_type="section",
        entity_id=section.id,
        action="update",
        summary=f"section {section.name} updated",
        source=source,
        before=before,
        after=changes.snapshot(section),
    )
    await session.commit()
    return section


async def delete_section(session: AsyncSession, section_id: str, source: str) -> None:
    section = await get_section(session, section_id)
    subnet_count = await session.scalar(
        select(func.count(Subnet.id)).where(Subnet.section_id == section_id)
    )
    if subnet_count:
        raise conflict(f"Section {section.name} still has {subnet_count} subnet(s)")
    before = changes.snapshot(section)
    changes.record(
        session,
        entity_type="section",
        entity_id=section.id,
        action="delete",
        summary=f"section {section.name} deleted",
        source=source,
        before=before,
    )
    await session.delete(section)
    await session.commit()
