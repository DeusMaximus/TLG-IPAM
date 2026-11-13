from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
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
    # who acted, when narrower than `source` (spec §4.3): null for the owner key,
    # later `agent:<name>` or `grant:<client name>`
    actor: Mapped[str | None] = mapped_column(String(80))


class Agent(Base):
    """A scan agent (v0.2 spec §5.4). Created by the owner; its credential lives in
    `tokens`, which points here, so a token can be replaced without touching the agent."""

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    # pending (never held an agent token), active, disabled
    state: Mapped[str] = mapped_column(String(16), default="pending")
    # reported at check-in (#10); null until the agent first checks in
    version: Mapped[str | None] = mapped_column(String(40))
    platform: Mapped[str | None] = mapped_column(String(120))
    hostname: Mapped[str | None] = mapped_column(String(253))
    interfaces: Mapped[list | None] = mapped_column(JSON)
    last_checkin_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    tokens: Mapped[list["Token"]] = relationship(
        back_populates="agent", order_by="Token.created_at.desc()", cascade="all, delete-orphan"
    )


class Token(Base):
    """A credential bound to an agent (v0.2 spec §4.2). Only the SHA-256 of the token
    is stored, with a short prefix for display; the plain token is shown once."""

    __tablename__ = "tokens"
    __table_args__ = (
        # at most one unrevoked `agent` token per agent; the service revokes before issuing
        Index(
            "uq_tokens_live_agent_token",
            "agent_id",
            unique=True,
            sqlite_where=text("kind = 'agent' AND revoked_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # `agent`, or `enrol` (M2b: nothing issues one yet)
    kind: Mapped[str] = mapped_column(String(8))
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    token_prefix: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # enrolment tokens only
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    agent: Mapped[Agent] = relationship(back_populates="tokens")
