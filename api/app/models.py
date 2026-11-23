from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
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
    # v0.2 spec §5.3: null or false = scanned. Nullable so `_add_missing_columns` can add
    # it to an existing database, where every range then reads as scanned.
    scan_excluded: Mapped[bool | None] = mapped_column(Boolean)

    subnet: Mapped[Subnet] = relationship(back_populates="reserved_ranges")

    __table_args__ = (
        # what the scope reads (`scans._scopes_of_chunk`): a subnet's excluded ranges,
        # found without passing over its scanned ranges or another subnet's (#86)
        Index(
            "ix_reserved_ranges_subnet_excluded",
            "subnet_id",
            "scan_excluded",
            "start_int",
            "end_int",
        ),
    )


class Host(Base):
    __tablename__ = "hosts"
    __table_args__ = (
        UniqueConstraint("subnet_id", "ip", name="uq_hosts_subnet_ip"),
        # the order `findings.list_named` reads a subnet in, a chunk at a time (#86)
        Index("ix_hosts_subnet_ip_int", "subnet_id", "ip_int"),
    )

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
    name: Mapped[str] = mapped_column(String(120))
    # name.casefold(): makes names unique case-insensitively in the database itself, so
    # two racing requests cannot save `Scanner` and `scanner`. Case-folding can lengthen
    # a name (ß → ss), hence the wider column.
    name_key: Mapped[str] = mapped_column(String(255), unique=True)
    # pending (never held an agent token), active, disabled
    state: Mapped[str] = mapped_column(String(16), default="pending")
    # reported at check-in (#10); null until the agent first checks in
    version: Mapped[str | None] = mapped_column(String(40))
    platform: Mapped[str | None] = mapped_column(String(120))
    hostname: Mapped[str | None] = mapped_column(String(253))
    interfaces: Mapped[list | None] = mapped_column(JSON)
    last_checkin_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    # The job the agent was last issued, and when (`services/jobs`). The agent is busy
    # while this is live. Kept here, not only on the target, so that reassigning or
    # deleting the target mid-scan does not free an agent that is still scanning.
    current_job_id: Mapped[str | None] = mapped_column(String(36))
    job_issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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


class ScanTarget(Base):
    """A subnet the owner has chosen to scan, and how (v0.2 spec §5.4). One per subnet.

    What is scanned is the subnet's scan scope, computed only by
    `services/scans.scan_scope`. Check-in writes the lease (`services/jobs`) and
    request-scan writes `scan_requested_at`; results ingest (`services/results`) clears
    the lease and writes the last-scan columns.
    """

    __tablename__ = "scan_targets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    subnet_id: Mapped[str] = mapped_column(ForeignKey("subnets.id"), unique=True)
    # null = unassigned: nothing is scanned until an agent is chosen. Deleting the agent
    # sets this to null in `services/agents.delete_agent`; SET NULL is the backstop.
    agent_id: Mapped[str | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # subset of icmp, arp, rdns, fdns, in that order, with icmp or arp among them
    methods: Mapped[list] = mapped_column(JSON)
    scan_dhcp_pool: Mapped[bool] = mapped_column(Boolean, default=False)
    # null = manual only
    interval_minutes: Mapped[int | None] = mapped_column(Integer)
    # the last "Scan now"; never cleared, since it also times the cooldown
    scan_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_job_id: Mapped[str | None] = mapped_column(String(36))
    job_issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # the methods the leased job was issued with (`arp` only when L2-local): results
    # ingest trusts these, not what the agent says it ran
    job_methods: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # the `job_issued_at` of the last completed job, by the server's clock
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_scan_duration_ms: Mapped[int | None] = mapped_column(Integer)
    last_scan_complete: Mapped[bool | None] = mapped_column(Boolean)
    # The error the agent reported with its last result set, sanitised at ingest
    # (`agent_protocol.scan_error`). Agent text: kept out of audit snapshots, which MCP
    # can read (`changes._SNAPSHOT_EXCLUDE`).
    last_scan_error: Mapped[str | None] = mapped_column(Text)


class Observation(Base):
    """The latest scan state of one address (v0.2 spec §5.4), upserted by results
    ingest (`services/results`). No history: an address that stops answering keeps its
    row with `alive=false`, and rows unseen for 90 days are pruned at ingest.

    Rows exist only inside the subnet's scan scope. A change that puts addresses out of
    scope deletes theirs (`services/scans.drop_out_of_scope`), and deleting the subnet
    deletes all of them. Ingest never writes `hosts` (spec §7.2, invariant 3).
    """

    __tablename__ = "observations"
    __table_args__ = (
        UniqueConstraint("subnet_id", "ip", name="uq_observations_subnet_ip"),
        Index("ix_observations_subnet_ip_int", "subnet_id", "ip_int"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    subnet_id: Mapped[str] = mapped_column(ForeignKey("subnets.id"), index=True)
    ip: Mapped[str] = mapped_column(String(39))
    ip_int: Mapped[int] = mapped_column(BigInteger)
    # normalised like `Host.mac`; only from a job issued `arp`
    mac: Mapped[str | None] = mapped_column(String(17))
    # the locally-administered bit of `mac` is set
    mac_randomised: Mapped[bool] = mapped_column(Boolean, default=False)
    # the PTR name, sanitised to the hostname character set (`agent_protocol.rdns_name`)
    rdns_name: Mapped[str | None] = mapped_column(String(253))
    # answered in the most recent scan that could tell
    alive: Mapped[bool] = mapped_column(Boolean, default=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # only moves when the address answers
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # the agent that reported it; no foreign key, so deleting an agent rewrites nothing
    agent_id: Mapped[str | None] = mapped_column(String(36))


class FindingDismissal(Base):
    """A finding the owner dismissed (v0.2 spec §5.4, §5.5). Findings themselves are
    computed at read time (`services/findings`) and never stored; a dismissal hides the
    one at its `(subnet_id, ip, type)` while that finding's observed value is still
    `observed`, and stops hiding it once the value changes.

    Like observations, rows exist only inside the subnet's scan scope: a change that puts
    the address out of scope deletes them (`services/scans.drop_out_of_scope`), and so
    does deleting the subnet.
    """

    __tablename__ = "finding_dismissals"
    __table_args__ = (
        UniqueConstraint("subnet_id", "ip", "type", name="uq_finding_dismissals_subnet_ip_type"),
        Index("ix_finding_dismissals_subnet_ip_int", "subnet_id", "ip_int"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    subnet_id: Mapped[str] = mapped_column(ForeignKey("subnets.id"), index=True)
    ip: Mapped[str] = mapped_column(String(39))
    # integer form of ip, so the scope check needs no parsing
    ip_int: Mapped[int] = mapped_column(BigInteger)
    type: Mapped[str] = mapped_column(String(16))
    # the finding's observed value when it was dismissed: a MAC, a PTR name, a
    # timestamp, `never`, or empty
    observed: Mapped[str] = mapped_column(String(253))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
