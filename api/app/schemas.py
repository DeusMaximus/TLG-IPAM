from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .ipnet import normalise_domain, normalise_mac, parse_ip

HostStatus = Literal["active", "reserved", "dhcp", "offline"]
HostClearField = Literal["hostname", "domain", "mac", "owner", "notes", "last_seen"]


# --- Device types ---


class DeviceTypeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    icon: str = Field(default="help-circle", min_length=1, max_length=40)


class DeviceTypeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    icon: str | None = Field(default=None, min_length=1, max_length=40)

    @field_validator("name", "icon")
    @classmethod
    def _required_fields_cannot_be_null(cls, v):
        if v is None:
            raise ValueError("must not be null")
        return v


class DeviceTypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    icon: str
    host_count: int = 0


# --- Sections ---


class SectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    colour: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


class SectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    colour: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")

    @field_validator("name")
    @classmethod
    def _required_fields_cannot_be_null(cls, v):
        if v is None:
            raise ValueError("must not be null")
        return v


class SectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None
    colour: str | None
    subnet_count: int = 0


# --- Subnets ---


class _SubnetFields(BaseModel):
    vlan_id: int | None = Field(default=None, ge=1, le=4094)
    domain: str | None = None
    gateway: str | None = None
    dns_servers: list[str] | None = None
    dhcp_start: str | None = None
    dhcp_end: str | None = None
    notes: str | None = None

    @field_validator("gateway", "dhcp_start", "dhcp_end")
    @classmethod
    def _valid_ip(cls, v):
        if v is not None:
            parse_ip(v)
        return v

    @field_validator("domain")
    @classmethod
    def _norm_domain(cls, v):
        return normalise_domain(v) if v else v

    @field_validator("dns_servers")
    @classmethod
    def _valid_dns(cls, v):
        if v is not None:
            for ip in v:
                parse_ip(ip)
        return v


class SubnetCreate(_SubnetFields):
    section_id: str
    cidr: str
    name: str = Field(min_length=1, max_length=120)


class SubnetUpdate(_SubnetFields):
    section_id: str | None = None
    cidr: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("section_id", "cidr", "name")
    @classmethod
    def _required_fields_cannot_be_null(cls, v):
        if v is None:
            raise ValueError("must not be null")
        return v


class ReservedRangeCreate(BaseModel):
    start_ip: str
    end_ip: str
    description: str | None = Field(default=None, max_length=255)

    @field_validator("start_ip", "end_ip")
    @classmethod
    def _valid_ip(cls, v):
        parse_ip(v)
        return v


class ReservedRangeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    start_ip: str
    end_ip: str
    description: str | None


class SubnetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    section_id: str
    cidr: str
    vlan_id: int | None
    name: str
    domain: str | None
    gateway: str | None
    dns_servers: list[str] | None
    dhcp_start: str | None
    dhcp_end: str | None
    notes: str | None
    # utilisation: used = host records, usable = addresses excl. network/broadcast,
    # free = allocatable right now (also excludes gateway, DHCP pool, reserved ranges)
    used: int = 0
    usable: int = 0
    free: int = 0
    reserved_ranges: list[ReservedRangeOut] = []


# --- Hosts ---


class _HostFields(BaseModel):
    hostname: str | None = Field(default=None, max_length=253)
    # domain suffix override; null inherits the subnet's default
    domain: str | None = None
    mac: str | None = None
    owner: str | None = Field(default=None, max_length=120)
    notes: str | None = None
    last_seen: datetime | None = None

    @field_validator("mac")
    @classmethod
    def _norm_mac(cls, v):
        return normalise_mac(v) if v else v

    @field_validator("domain")
    @classmethod
    def _norm_domain(cls, v):
        return normalise_domain(v) if v else v


class HostCreate(_HostFields):
    subnet_id: str
    ip: str
    device_type: str = Field(default="other", max_length=40)
    status: HostStatus = "active"


class HostUpdate(_HostFields):
    ip: str | None = None
    device_type: str | None = Field(default=None, max_length=40)
    status: HostStatus | None = None

    @field_validator("ip", "device_type", "status")
    @classmethod
    def _required_fields_cannot_be_null(cls, v):
        if v is None:
            raise ValueError("must not be null")
        return v


class HostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    subnet_id: str
    ip: str
    # integer form of ip — the numeric sort key, so clients never parse address strings
    ip_int: int
    hostname: str | None
    domain: str | None
    # computed: hostname + effective domain (host override, else subnet default)
    fqdn: str | None = None
    mac: str | None
    device_type: str
    status: str
    owner: str | None
    notes: str | None
    last_seen: datetime | None
    created_at: datetime
    updated_at: datetime


class AllocateRequest(_HostFields):
    hostname: str = Field(min_length=1, max_length=253)
    device_type: str = Field(default="other", max_length=40)
    status: HostStatus = "active"


class BulkHostItem(_HostFields):
    ip: str
    device_type: str = Field(default="other", max_length=40)
    status: HostStatus = "active"


class BulkImportRequest(BaseModel):
    # items are validated individually so one bad record reports an error in the
    # per-record results instead of aborting the whole batch
    hosts: list[dict] = Field(min_length=1, max_length=1000)


class BulkDeleteRequest(BaseModel):
    host_ids: list[str] = Field(min_length=1, max_length=1000)


# --- Changes ---


class ChangeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    ts: datetime
    entity_type: str
    entity_id: str
    action: str
    summary: str
    before: dict | None
    after: dict | None
    source: str
    actor: str | None


# --- Agents ---

# `agent:<name>` is the audit actor for an agent's own writes (spec §4.3) and must fit
# `changes.actor` (80 characters), so names stop at 80 - len("agent:").
AGENT_NAME_MAX = 74
AgentState = Literal["pending", "active", "disabled"]


def _agent_name(v: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError("must not be empty")
    if len(v) > AGENT_NAME_MAX:
        raise ValueError(f"must be at most {AGENT_NAME_MAX} characters")
    if not v.isprintable():
        raise ValueError("must contain printable characters only")
    return v


class AgentCreate(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v):
        return _agent_name(v)


class AgentUpdate(BaseModel):
    name: str | None = None
    enabled: bool | None = None

    @field_validator("name", "enabled")
    @classmethod
    def _required_fields_cannot_be_null(cls, v, info):
        if v is None:
            raise ValueError("must not be null")
        return _agent_name(v) if info.field_name == "name" else v


class TokenOut(BaseModel):
    """A token as listed: never the token itself or its hash."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    token_prefix: str
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    used_at: datetime | None
    revoked_at: datetime | None


class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    state: AgentState
    version: str | None
    platform: str | None
    hostname: str | None
    interfaces: list | None
    last_checkin_at: datetime | None
    last_error: str | None
    created_at: datetime
    tokens: list[TokenOut]


class AgentCreated(AgentOut):
    """The create response, the only one that carries the plain token."""

    token: str
