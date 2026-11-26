from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, StrictBool, field_validator

from .agent_protocol import ENROL_TOO_NEW, ENROL_TOO_OLD, ScanRange
from .ipnet import normalise_domain, normalise_mac, parse_ip
from .times import utc

# Every datetime field of a response model (every model here that is not a request
# model, whatever its name, and every model one holds). SQLite reads a stored datetime
# back without its zone, so a model serialises what it is given: a row still in memory
# with `+00:00`, the same row read back without. This gives every datetime in a REST or
# MCP response one form, ISO 8601 in UTC with `+00:00`, either way (brief §4.3): the
# same string from `model_dump()` and `model_dump(mode="json")` (whose own form for UTC
# is `Z`). Applied when the model is dumped, so a value set with
# `model_copy(update=...)` is covered too. Request models keep plain `datetime`:
# services pass their `model_dump()` to the ORM.
UtcDatetime = Annotated[
    datetime, PlainSerializer(lambda value: utc(value).isoformat(), return_type=str)
]

HostStatus = Literal["active", "reserved", "dhcp", "offline"]
HostClearField = Literal["hostname", "domain", "mac", "owner", "notes", "last_seen"]
LastSeenSource = Literal["manual", "scan"]


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

# The most DNS servers the owner API takes for a subnet (`services/subnets` checks it,
# since an update that resends a longer list stored before this bound is let through
# unchanged). The field documents what the subnet hands out, which may be more than
# the three a resolver uses (glibc's MAXNS, and the agent's `MAX_DNS_SERVERS`): a
# dual-stack subnet with four resolvers per family. A scan job carries only the first
# `agent_protocol.DNS_SERVERS_MAX` of them.
SUBNET_DNS_SERVERS_MAX = 8
# The most reserved ranges one subnet holds (#86), excluded from scanning or not: every
# visit of `findings.list_named` reads a subnet's excluded ranges to build its scope,
# and `get_subnet` returns them all, so a client must not be able to grow them without
# end (MCP's `add_reserved_range` adds one per call). A homelab subnet has a handful. A
# subnet stored with more before the limit keeps them; adding one is refused until it
# holds fewer (`services/subnets.create_reserved_range`).
SUBNET_RESERVED_RANGES_MAX = 100


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
    # REST only: the MCP tool never sets it, so a range made over MCP is scanned
    scan_excluded: bool = False

    @field_validator("start_ip", "end_ip")
    @classmethod
    def _valid_ip(cls, v):
        parse_ip(v)
        return v


class ReservedRangeUpdate(BaseModel):
    scan_excluded: bool


class ReservedRangeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    start_ip: str
    end_ip: str
    description: str | None
    # stored null (a range from before the column existed) reads as scanned
    scan_excluded: bool = False

    @field_validator("scan_excluded", mode="before")
    @classmethod
    def _null_is_scanned(cls, v):
        return bool(v)


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
    # from the subnet's scan target; null when the subnet has none (it is not scanned)
    scan_dhcp_pool: bool | None = None


# --- Hosts ---

# The most characters a host's `notes` holds (#91), checked by `services/hosts` on every
# write that sends notes, a bulk import's rows included (a longer one is that row's
# `error`). It bounds one host, not an import: 1,000 rows fit the request-body cap only
# with short notes (brief §4.3). An update that resends notes stored longer before the
# limit, unchanged, is let through, so such a host can still be edited from the UI.
HOST_NOTES_MAX = 4096


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

    @field_validator("last_seen")
    @classmethod
    def _last_seen_utc(cls, v):
        # SQLite keeps the wall-clock time and drops the zone, so a value sent with an
        # offset is converted to UTC first; a value without one is taken as UTC. The
        # result is aware and the stored value reads back naive, so `hosts.update_host`
        # compares the two as instants before it assigns one (#85).
        return utc(v)

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
    # the later of the stored value (set by hand) and the address's observation
    last_seen: UtcDatetime | None
    # which of the two `last_seen` is: "manual", "scan", or null when neither is set
    last_seen_source: LastSeenSource | None = None
    created_at: UtcDatetime
    updated_at: UtcDatetime


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
    ts: UtcDatetime
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
    created_at: UtcDatetime
    last_used_at: UtcDatetime | None
    expires_at: UtcDatetime | None
    used_at: UtcDatetime | None
    revoked_at: UtcDatetime | None
    # Computed on read by `services/agents.agent_out` (`agents.expires_in_s`); nothing
    # stores it. The whole seconds a live enrolment token has left by the server's
    # clock when the answer was built; null when it is not live, and for an agent token.
    expires_in_s: int | None = None


class EnrolRefusalOut(BaseModel):
    """An enrolment refused for the agent's protocol (#175, `agents.enrol_refusal`):
    the server's code for which way it was, and when."""

    code: Literal[ENROL_TOO_OLD, ENROL_TOO_NEW]
    refused_at: UtcDatetime


class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    state: AgentState
    version: str | None
    platform: str | None
    hostname: str | None
    interfaces: list | None
    last_checkin_at: UtcDatetime | None
    last_error: str | None
    # the agent's own record of its last job; it is busy while that is live
    current_job_id: str | None
    job_issued_at: UtcDatetime | None
    created_at: UtcDatetime
    tokens: list[TokenOut]
    # Computed on read by `services/agents.agent_out`; nothing stores them. Whether the
    # agent's release is older than the server's (null: either is unknown or not a
    # version), and whether its last check-in was refused for another protocol.
    update_available: bool | None = None
    protocol_mismatch: bool = False
    # Computed on read too, from process memory (`agents.enrol_refusals`): the last
    # enrolment refused for the agent's protocol, while the token it was refused for is
    # neither used nor revoked; null otherwise, and after a restart until it is refused
    # again.
    enrol_refusal: EnrolRefusalOut | None = None


class AgentEnrolment(AgentOut):
    """The create and re-enrol responses, the only ones that carry an enrolment token:
    the plain `tlge_…` value, shown once, and when it stops being accepted. The agent
    exchanges it for its agent token at `POST /api/v1/agent/enrol`."""

    enrol_token: str
    enrol_token_expires_at: UtcDatetime


# --- Scan targets ---

# null = manual only; otherwise between five minutes and a week
SCAN_INTERVAL_MIN = 5
SCAN_INTERVAL_MAX = 7 * 24 * 60


class ScanTargetCreate(BaseModel):
    subnet_id: str
    agent_id: str | None = None
    enabled: bool = True
    # checked and put in canonical order by services/scans.normalise_methods
    methods: list[str] = ["icmp", "arp", "rdns"]
    scan_dhcp_pool: bool = False
    interval_minutes: int | None = Field(
        default=None, ge=SCAN_INTERVAL_MIN, le=SCAN_INTERVAL_MAX
    )


class ScanTargetUpdate(BaseModel):
    """The subnet of a target is fixed; delete the target and create another instead."""

    agent_id: str | None = None
    enabled: bool | None = None
    methods: list[str] | None = None
    scan_dhcp_pool: bool | None = None
    interval_minutes: int | None = Field(
        default=None, ge=SCAN_INTERVAL_MIN, le=SCAN_INTERVAL_MAX
    )

    @field_validator("enabled", "methods", "scan_dhcp_pool")
    @classmethod
    def _required_fields_cannot_be_null(cls, v):
        if v is None:
            raise ValueError("must not be null")
        return v


class ScanSuggestionOut(BaseModel):
    """A subnet an agent could scan (`scans.suggestions`): the Agents page offers "Scan
    *subnet* with this agent?" for it, and accepting creates the subnet's scan target
    with the default methods and scope."""

    agent_id: str
    agent_name: str
    subnet_id: str
    subnet_name: str
    # `ipnet.canonical_cidr`, whatever the row holds
    subnet_cidr: str


class ScopeRangeOut(ScanRange):
    """A range of a scan target's scope: the addresses, and the same bounds as integers
    of the subnet's family (`ipnet`), so a client tells whether a host is in scope by
    comparing its `ip_int` and never parses an address. A job's ranges stay
    `agent_protocol.ScanRange`, without the integers."""

    start_int: int
    end_int: int


class ScanTargetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    subnet_id: str
    agent_id: str | None
    enabled: bool
    methods: list[str]
    scan_dhcp_pool: bool
    interval_minutes: int | None
    scan_requested_at: UtcDatetime | None
    current_job_id: str | None
    job_issued_at: UtcDatetime | None
    job_methods: list[str] | None
    created_at: UtcDatetime
    last_scan_at: UtcDatetime | None
    last_scan_duration_ms: int | None
    last_scan_complete: bool | None
    # the error the agent reported with its last result set, sanitised at ingest
    last_scan_error: str | None = None
    # What a job for this target would run on its agent now, from the interfaces the
    # agent reported at its last accepted check-in: `jobs.job_methods`, the rule
    # check-in issues jobs by. `[]`: no liveness method is left, so the target cannot be
    # scanned by this agent. Null: not known (no agent, or it has never reported its
    # interfaces). Computed on read; nothing stores it.
    runnable_methods: list[str] | None = None
    # How old that evidence is: the agent's last check-in (its `last_checkin_at`) and
    # whether it was refused. Only an accepted check-in reports the interfaces, so when
    # the last one was accepted they are from `agent_checkin_at`; when it was refused
    # (409 or 422) they are from an earlier accepted check-in, whose time is not stored.
    # Both null when `runnable_methods` is.
    agent_checkin_at: UtcDatetime | None = None
    agent_checkin_refused: bool | None = None
    # the subnet's scan scope (services/scans.scan_scope): inclusive address ranges
    scope: list[ScopeRangeOut] = []


# --- Findings ---

# v0.2 spec §5.5, in the order findings at one address are listed
FindingType = Literal[
    "unknown_host",
    "mac_missing",
    "mac_mismatch",
    "name_missing",
    "name_mismatch",
    "fdns_mismatch",
    "stale",
]


class FindingKey(BaseModel):
    """What identifies a finding (spec §5.5): accept and dismiss take these, as listed."""

    model_config = ConfigDict(extra="forbid")

    subnet_id: str = Field(max_length=36)
    ip: str = Field(max_length=39)
    type: FindingType
    # a MAC, a PTR name, forward-DNS addresses or `unresolved`, a last-seen timestamp,
    # `never`, or empty
    observed: str = Field(max_length=253)
    # the finding's fingerprint as listed: required to accept, checked if sent to dismiss
    fingerprint: str | None = Field(default=None, max_length=64)


class FindingKeysRequest(BaseModel):
    # keys are validated one by one, a non-object included, so a bad key is reported in
    # the per-record results instead of failing the request
    findings: list[Any] = Field(min_length=1, max_length=1000)


class FindingOut(FindingKey):
    model_config = ConfigDict(extra="ignore")

    # integer form of ip, the numeric sort key
    ip_int: int
    # the host at the address; null for `unknown_host`
    host_id: str | None
    # an `unknown_host` inside the DHCP pool (only when the pool is scanned): hidden
    # unless asked for
    dynamic: bool
    # the host fields accepting the finding writes
    proposed: dict
    # digest of the key, `host_id` and `proposed`: accept is refused once it changes
    fingerprint: str


# --- The link window (v0.2 spec §6.3, #27) ---

LinkWindowEnd = Literal["linked", "expired", "cancelled", "replaced", "failed"]


class LinkWindowOpen(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # whether the link may write; refused (`409 REMOTE_WRITES_OFF`) while
    # `MCP_REMOTE_WRITES` is off (#28)
    can_write: StrictBool = False


class LinkWindowOut(BaseModel):
    """The link window as `services/link_window.state_out` gives it. Never the code."""

    # whether remote MCP is configured (`PUBLIC_BASE_URL`): without it no window opens
    configured: bool
    # whether a window may be opened for a read-write link: `MCP_REMOTE_WRITES` is on
    # (#28). False when `configured` is false
    remote_writes: bool
    open: bool
    # while open: the window's own id (the `entity_id` of its audit entries, not a
    # secret), which a cancel can name so that it ends this window and no other (#30)
    window_id: str | None = None
    # while open: what its link may do, when it opened and runs out, and how long it has
    # left by the server's clock, which a countdown starts from
    can_write: bool | None
    opened_at: UtcDatetime | None
    expires_at: UtcDatetime | None
    expires_in_s: int | None
    # wrong link codes typed on its consent pages, all its requests together: what the
    # owner is told. They end requests (five each), never the window
    wrong_codes: int | None
    # while none is open: how the latest window ended, and when (process memory: null
    # after a restart until one has been opened and has ended); `failed` is an approval
    # that failed unexpectedly, which ends the window
    ended: LinkWindowEnd | None
    ended_at: UtcDatetime | None
    # the URL a client is given to link: `<PUBLIC_BASE_URL>/mcp`, the resource the OAuth
    # server publishes; null when `configured` is false (#30)
    mcp_url: str | None = None


class LinkWindowOpened(LinkWindowOut):
    """The answer that opens a window, the only one that carries its link code."""

    code: str


class LinkWindowCancelled(LinkWindowOut):
    """The answer to a cancel: the state after it, and whether this cancel ended a
    window. False when none was open, or when the cancel named a window that is no
    longer the current one, which it then left alone (#30)."""

    cancelled: bool


class LinkedClientOut(BaseModel):
    """One link on the owner's **Linked clients** list (`services/linked_clients`,
    v0.2 spec §6.6, #30): a grant, with what its client declared."""

    id: str
    client_id: str
    # the client's own claim about itself, as stored; null when no name was kept or the
    # registration is gone (`client_registered`)
    client_name: str | None
    client_registered: bool
    # the host of the redirect URI the link was authorised through, in ASCII form
    redirect_host: str
    # what the owner chose when opening the window, and what its tokens carry now:
    # read-only while `MCP_REMOTE_WRITES` is off, whatever `can_write` says
    can_write: bool
    writes: bool
    created_at: UtcDatetime
    # the client's last contact (a request to `/mcp`, a code exchange or a refresh), a
    # heartbeat and not the owner's last use
    last_used_at: UtcDatetime | None
    revoked_at: UtcDatetime | None
