from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class DeviceType(Base):
    """User-defined device types. Hosts store the type name as a plain string;
    writes are validated against this table and renames cascade to hosts."""

    __tablename__ = "device_types"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(40), unique=True)
    icon: Mapped[str] = mapped_column(String(40), default="help-circle")


class Section(Base):
    __tablename__ = "sections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    colour: Mapped[str | None] = mapped_column(String(9))

    subnets: Mapped[list["Subnet"]] = relationship(back_populates="section")


class Subnet(Base):
    __tablename__ = "subnets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    section_id: Mapped[str] = mapped_column(ForeignKey("sections.id"))
    cidr: Mapped[str] = mapped_column(String(43))
    vlan_id: Mapped[int | None] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(120))
    domain: Mapped[str | None] = mapped_column(String(253))
    gateway: Mapped[str | None] = mapped_column(String(39))
    dns_servers: Mapped[list | None] = mapped_column(JSON)
    dhcp_start: Mapped[str | None] = mapped_column(String(39))
    dhcp_end: Mapped[str | None] = mapped_column(String(39))
    notes: Mapped[str | None] = mapped_column(Text)

    section: Mapped[Section] = relationship(back_populates="subnets")
    hosts: Mapped[list["Host"]] = relationship(back_populates="subnet")
    reserved_ranges: Mapped[list["ReservedRange"]] = relationship(back_populates="subnet")


class ReservedRange(Base):
    """A block of IPs excluded from automatic allocation (but manual host creation
    inside it is still allowed) — e.g. addresses held for network gear."""

    __tablename__ = "reserved_ranges"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    subnet_id: Mapped[str] = mapped_column(ForeignKey("subnets.id"))
    start_ip: Mapped[str] = mapped_column(String(39))
    end_ip: Mapped[str] = mapped_column(String(39))
    start_int: Mapped[int] = mapped_column(BigInteger)
    end_int: Mapped[int] = mapped_column(BigInteger)
    description: Mapped[str | None] = mapped_column(String(255))

    subnet: Mapped[Subnet] = relationship(back_populates="reserved_ranges")


class Host(Base):
    __tablename__ = "hosts"
    __table_args__ = (UniqueConstraint("subnet_id", "ip", name="uq_hosts_subnet_ip"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    subnet_id: Mapped[str] = mapped_column(ForeignKey("subnets.id"))
    ip: Mapped[str] = mapped_column(String(39))
    # integer form of the address, kept for correct numeric ordering and range queries
    ip_int: Mapped[int] = mapped_column(BigInteger)
    hostname: Mapped[str | None] = mapped_column(String(253))
    # domain suffix override; null inherits the subnet's default domain
    domain: Mapped[str | None] = mapped_column(String(253))
    mac: Mapped[str | None] = mapped_column(String(17))
    device_type: Mapped[str] = mapped_column(String(40), default="other")
    status: Mapped[str] = mapped_column(String(16), default="active")
    owner: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(Text)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    subnet: Mapped[Subnet] = relationship(back_populates="hosts")


class Change(Base):
    __tablename__ = "changes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    entity_type: Mapped[str] = mapped_column(String(16))
    entity_id: Mapped[str] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(16))
    summary: Mapped[str] = mapped_column(Text)
    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict | None] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(8))
