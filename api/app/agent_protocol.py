"""The agent wire protocol: what a scan agent sends to `/api/v1/agent/*` and what it
gets back (v0.2 spec §5.1, §5.7). The agent package is built against this module.

It depends on the standard library and pydantic only, so the agent can use it as is.
Changing a shape here that an agent of the current protocol would read or send
differently means raising `PROTOCOL_VERSION`.

Every field an agent reports is untrusted input. It is checked here, at ingest:
length-capped and restricted to printable characters (spec §5.4, §7.2 invariant 8).
"""

import ipaddress
import re
from typing import Any

from pydantic import (
    AwareDatetime,
    BaseModel,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

# The protocol this server speaks. A check-in with any other value gets no jobs and
# `409 PROTOCOL_MISMATCH`, and the error is recorded on the agent (spec §5.6).
# 2 (#100): a result set says whether its liveness sweep completed apart from
# `complete`, and a record says when its PTR lookup was answered "no name".
PROTOCOL_VERSION = 2
# The protocols whose result sets ingest takes, each by its own rule (spec §5.7). A
# result set without `protocol` is protocol 1's, which carried none: a job leased
# before the server was upgraded is still reported by an agent of that protocol.
RESULTS_PROTOCOLS = (1, 2)

# spec §5.1: the agent checks in every minute by default
CHECKIN_INTERVAL_S = 60
# spec §5.6: the per-job timeout the agent enforces
JOB_TIMEOUT_S = 600

# How an agent answers a check-in the server refused (spec §5.7). A refused check-in
# authorises no scan: only a `200` carries jobs, and the agent sends no packet on the
# strength of anything else.
# - `429 RATE_LIMITED`, `503 UNAVAILABLE`, any other 5xx and a request that got no
#   answer are retried with a bounded back-off. Neither 429 nor 503 carries
#   `Retry-After`. Retry n (from 0) waits a time drawn uniformly from
#   [CHECKIN_RETRY_MIN_S, min(CHECKIN_RETRY_MAX_S, CHECKIN_RETRY_MIN_S * 2**(n+1))],
#   so it is never shorter than the gap the server enforces between check-ins and
#   never longer than CHECKIN_RETRY_MAX_S. An accepted check-in resets n.
# - `401`, `403`, `409 PROTOCOL_MISMATCH`, `422` and any other 4xx are not retried in
#   a tight loop: the agent waits a time drawn uniformly from
#   [0.9 * CHECKIN_REFUSED_RETRY_S, CHECKIN_REFUSED_RETRY_S] before it tries again.
#   Each needs the owner (a new token, re-enabling the agent, a matching release), and
#   the next try is how the agent sees that it has been done.
CHECKIN_RETRY_MIN_S = 10
CHECKIN_RETRY_MAX_S = 300
CHECKIN_REFUSED_RETRY_S = 900

# caps on what an agent may report, matching the `agents` columns
VERSION_MAX = 40
PLATFORM_MAX = 120
HOSTNAME_MAX = 253
# Linux IFNAMSIZ is 16 bytes including the terminating NUL
INTERFACE_NAME_MAX = 15
INTERFACES_MAX = 64
CAPABILITIES_MAX = 16

_MAC_HEX = re.compile(r"[0-9a-f]{12}")
_CAPABILITY = re.compile(r"[a-z0-9_-]{1,32}")
# longest IPv6 address with a prefix length, plus slack for a dotted netmask
_INTERFACE_CIDR_MAX = 64


def mac_address(value: str | None) -> str | None:
    """A MAC address as `aa:bb:cc:dd:ee:ff`, from colon, dash, dot or bare hex forms;
    the same form as `Host.mac`."""
    if value is None:
        return None
    if len(value) > 32:
        raise ValueError("must be a MAC address")
    stripped = re.sub(r"[:.-]", "", value.strip()).lower()
    if not _MAC_HEX.fullmatch(stripped):
        raise ValueError("must be a MAC address")
    return ":".join(stripped[i : i + 2] for i in range(0, 12, 2))


def printable(value: str, cap: int) -> str:
    """Trimmed, non-empty, at most `cap` characters, printable characters only.

    `str.isprintable` refuses control characters, line and paragraph separators, and
    format characters such as the bidirectional overrides, so a value cannot break a
    line or reorder the text around it where it is shown.
    """
    value = value.strip()
    if not value:
        raise ValueError("must not be empty")
    if len(value) > cap:
        raise ValueError(f"must be at most {cap} characters")
    if not value.isprintable():
        raise ValueError("must contain printable characters only")
    return value


# --- Enrolment ---

# The two kinds of token an agent holds (spec §4.2). An enrolment token is exchanged
# once, at `POST /api/v1/agent/enrol`, for the agent token every other agent route
# takes.
AGENT_TOKEN_PREFIX = "tlga_"
ENROL_TOKEN_PREFIX = "tlge_"
# A token's form on the wire, both kinds: its prefix, then TOKEN_SECRET_LENGTH
# characters of the URL-safe base64 alphabet (RFC 4648 §5, no padding), the
# `secrets.token_urlsafe(TOKEN_SECRET_BYTES)` the server issues (#140). An agent stores
# only an enrol answer whose `agent_token` is exactly AGENT_TOKEN_FORM (its
# `client.ISSUED_AGENT_TOKEN`), and takes any other answer as a malformed `200` after
# the enrolment token is spent: a server that issues another form, longer or shorter,
# raises PROTOCOL_VERSION.
TOKEN_SECRET_BYTES = 32
TOKEN_SECRET_LENGTH = 43
TOKEN_SECRET_ALPHABET = "A-Za-z0-9_-"
AGENT_TOKEN_FORM = re.compile(f"{re.escape(AGENT_TOKEN_PREFIX)}[{TOKEN_SECRET_ALPHABET}]{{{TOKEN_SECRET_LENGTH}}}")
ENROL_TOKEN_FORM = re.compile(f"{re.escape(ENROL_TOKEN_PREFIX)}[{TOKEN_SECRET_ALPHABET}]{{{TOKEN_SECRET_LENGTH}}}")


class EnrolRequest(BaseModel):
    """`POST /api/v1/agent/enrol`, with the enrolment token as `Authorization: Bearer`.
    Unknown fields are ignored. Each field is optional, so the exchange can be made by
    hand (`-d '{}'`); a field that is sent is checked as check-in checks it and stored
    on the agent. The body carries no `protocol`: the agent's first check-in, which
    follows at once, is where a mismatch is refused."""

    version: str | None = None
    hostname: str | None = None
    platform: str | None = None

    @field_validator("version")
    @classmethod
    def _version(cls, v: str | None) -> str | None:
        return None if v is None else printable(v, VERSION_MAX)

    @field_validator("hostname")
    @classmethod
    def _hostname(cls, v: str | None) -> str | None:
        return None if v is None else printable(v, HOSTNAME_MAX)

    @field_validator("platform")
    @classmethod
    def _platform(cls, v: str | None) -> str | None:
        return None if v is None else printable(v, PLATFORM_MAX)


class EnrolResponse(BaseModel):
    """The enrol answer, inside the usual `{"data": ...}` envelope: the agent's id and
    its long-lived agent token, which no other response carries."""

    agent_id: str
    agent_token: str


# --- Check-in ---


class Interface(BaseModel):
    """One network interface of the agent's host. The server uses these to decide
    which subnets are on the same layer 2 segment as the agent (spec §5.2)."""

    # the kernel name, e.g. `eth0`
    name: str
    # the interface's address with its prefix length, e.g. `10.0.20.5/24`
    cidr: str
    # null for an interface without one (a tunnel, say)
    mac: str | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return printable(v, INTERFACE_NAME_MAX)

    @field_validator("cidr")
    @classmethod
    def _cidr(cls, v: str) -> str:
        v = v.strip()
        if len(v) > _INTERFACE_CIDR_MAX or "/" not in v or "%" in v:
            # `%` would carry an IPv6 scope id, which is free text
            raise ValueError("must be an address with a prefix length, such as 10.0.20.5/24")
        try:
            return str(ipaddress.ip_interface(v))
        except ValueError:
            raise ValueError(
                "must be an address with a prefix length, such as 10.0.20.5/24"
            ) from None

    @field_validator("mac")
    @classmethod
    def _mac(cls, v: str | None) -> str | None:
        return mac_address(v)


class CheckinRequest(BaseModel):
    """`POST /api/v1/agent/checkin`. Unknown fields are ignored."""

    # the agent's own release, shown on the Agents page
    version: str
    # must equal PROTOCOL_VERSION; checked before anything else in the body
    protocol: int
    hostname: str
    platform: str
    interfaces: list[Interface] = Field(max_length=INTERFACES_MAX)
    # Reserved: what the agent can do. Checked, not stored, and not yet used to
    # choose a job's methods.
    capabilities: list[str] = Field(default=[], max_length=CAPABILITIES_MAX)

    @field_validator("version")
    @classmethod
    def _version(cls, v: str) -> str:
        return printable(v, VERSION_MAX)

    @field_validator("hostname")
    @classmethod
    def _hostname(cls, v: str) -> str:
        return printable(v, HOSTNAME_MAX)

    @field_validator("platform")
    @classmethod
    def _platform(cls, v: str) -> str:
        return printable(v, PLATFORM_MAX)

    @field_validator("capabilities")
    @classmethod
    def _capabilities(cls, v: list[str]) -> list[str]:
        for item in v:
            # fullmatch: `$` would also match before a trailing newline
            if not _CAPABILITY.fullmatch(item):
                raise ValueError(
                    "each capability must be 1-32 characters of a-z, 0-9, _ and -"
                )
        return v


class ScanRange(BaseModel):
    """An inclusive range of addresses, both ends inside the job's subnet."""

    start: str
    end: str


class FdnsName(BaseModel):
    """A host whose name the agent should resolve (`fdns`, spec §5.2)."""

    host_id: str
    fqdn: str


# The most DNS servers a job carries: the agent asks at most this many per lookup
# (`probe.MAX_DNS_SERVERS`, checked in the agent's `test_contract.py`). A subnet may
# list more (`schemas.SUBNET_DNS_SERVERS_MAX`, or a longer list stored before that
# bound existed); a job carries the first of them, in the order listed.
DNS_SERVERS_MAX = 3


class Job(BaseModel):
    """One scan of one subnet. There is no field through which the server can send a
    command, a script, a binary, or an address outside the subnet (spec §5.1)."""

    # quoted back with the results; it must match the target's lease
    job_id: str
    subnet_id: str
    cidr: str
    # the subnet's scan scope (`services/scans.scan_scope`): nothing outside these
    ranges: list[ScanRange]
    # the methods to run, a subset of icmp, arp, rdns, fdns with icmp or arp among them
    methods: list[str]
    # the first DNS_SERVERS_MAX of the subnet's DNS servers, for rdns and fdns; empty
    # means the agent's own resolver
    dns_servers: list[str] = Field(max_length=DNS_SERVERS_MAX)
    timeout_s: int
    # the names to resolve for fdns; only ever names under the subnet's domain
    fdns: list[FdnsName]


class CheckinResponse(BaseModel):
    """The check-in answer, inside the usual `{"data": ...}` envelope."""

    # when to check in next
    checkin_interval_s: int
    # at most one: the agent runs jobs one at a time, and holds no second lease
    jobs: list[Job]


# --- Results ---

# A results body, like every request body, is at most `body_limit.BODY_MAX` (2 MiB),
# refused with `413` before authentication or parsing. A /20, the largest subnet with
# a scan target, has 4,094 usable addresses, and a record with a MAC and a
# 253-character name is under 330 bytes: a full result set is about 1.3 MB.
# records in one result set: every address of a /20
OBSERVATIONS_MAX = 4096
FDNS_MAX = 4096
FDNS_ADDRESSES_MAX = 32
RDNS_NAME_MAX = 253
SCAN_ERROR_MAX = 500
# the job and subnet ids the server sends are UUIDs
_ID_MAX = 36
# the longest textual IPv6 address, with an embedded IPv4 tail
_IP_MAX = 45
_HOST_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")

# Why a record was rejected, as `rejected[].reason`. The record is not stored, and its
# address is neither marked alive nor dead by this result set.
REJECT_INVALID = "invalid"  # not an object, or a field of the wrong type
REJECT_INVALID_IP = "invalid_ip"
REJECT_INVALID_MAC = "invalid_mac"
REJECT_OUT_OF_SCOPE = "out_of_scope"  # outside the subnet's scan scope at ingest
REJECT_DUPLICATE = "duplicate"  # the address was already reported in this result set
REJECT_MAC_NOT_ISSUED = "mac_not_issued"  # a MAC from a job not issued `arp`
REJECT_RDNS_NOT_ISSUED = "rdns_not_issued"  # a PTR name from a job not issued `rdns`


def rdns_name(value: Any) -> str | None:
    """A PTR name as the server stores it, or None (spec §5.7).

    `rdns_name` is untrusted: any device can name itself, and the name is later read by
    an assistant over MCP. It is kept only when it is a DNS hostname: ASCII letters,
    digits and hyphens in dot-separated labels of 1-63 characters that neither start
    nor end with a hyphen, at most 253 characters, one trailing dot allowed. It is
    lowercased. Anything else (spaces, markup, underscores, other scripts, a sentence)
    is dropped whole, never repaired into something that looks like a name.
    """
    if not isinstance(value, str) or not value.isascii():
        return None
    name = value.lower()
    if name.endswith("."):
        name = name[:-1]
    if not name or len(name) > RDNS_NAME_MAX:
        return None
    if all(_HOST_LABEL.fullmatch(label) for label in name.split(".")):
        return name
    return None


def scan_error(value: str | None) -> str | None:
    """The error an agent reports with a result set, as stored: every character that is
    not printable (line breaks, controls, bidirectional overrides) becomes a space,
    runs of whitespace collapse to one, and the text is cut to SCAN_ERROR_MAX. A long
    or multi-line error is kept, shortened, rather than costing the results with it."""
    if value is None:
        return None
    text = "".join(c if c.isprintable() else " " for c in value)
    text = " ".join(text.split())[:SCAN_ERROR_MAX].strip()
    return text or None


class Observation(BaseModel):
    """One live address in a result set. The server validates each record on its own,
    so one bad record is rejected without the rest (`parse_observation`)."""

    ip: str
    # only from a job issued `arp`; any other job's record carrying one is rejected
    mac: str | None = None
    # the PTR name; only from a job issued `rdns`. Not a hostname: dropped (`rdns_name`)
    rdns_name: str | None = None
    # Protocol 2: true when a DNS server answered this address's PTR lookup that it has
    # no name (NXDOMAIN, or NOERROR without a PTR record). Only such a record clears a
    # stored name; one with neither this nor a name leaves it (the lookup got no usable
    # answer, was cut off, or did not run). Only from a job issued `rdns`; a record that
    # carries a name as well is rejected (`invalid`).
    rdns_no_name: StrictBool = False

    @model_validator(mode="before")
    @classmethod
    def _one_answer(cls, data: Any) -> Any:
        # read before sanitising: a name that sanitising would drop is still a name
        if isinstance(data, dict) and data.get("rdns_no_name") is True and data.get("rdns_name") is not None:
            raise ValueError("a record carries either rdns_name or rdns_no_name")
        return data

    @field_validator("ip", mode="before")
    @classmethod
    def _ip(cls, v: Any) -> str:
        if not isinstance(v, str) or len(v) > _IP_MAX:
            raise ValueError("must be an IP address")
        try:
            return str(ipaddress.ip_address(v))
        except ValueError:
            raise ValueError("must be an IP address") from None

    @field_validator("mac", mode="before")
    @classmethod
    def _mac(cls, v: Any) -> str | None:
        if v is not None and not isinstance(v, str):
            raise ValueError("must be a MAC address")
        return mac_address(v)

    @field_validator("rdns_name", mode="before")
    @classmethod
    def _rdns_name(cls, v: Any) -> str | None:
        return rdns_name(v)


def parse_observation(raw: Any) -> tuple[Observation | None, str | None]:
    """One record of `observations`: the parsed record, or None and a REJECT_ reason."""
    if not isinstance(raw, dict):
        return None, REJECT_INVALID
    try:
        return Observation.model_validate(raw), None
    except ValueError as exc:  # pydantic's ValidationError is a ValueError
        field = next((e["loc"][0] for e in exc.errors() if e["loc"]), None)
        reason = {"ip": REJECT_INVALID_IP, "mac": REJECT_INVALID_MAC}.get(field, REJECT_INVALID)
        return None, reason


def address_of(raw: Any) -> str | None:
    """The `ip` of a raw record, normalised, when it is an IP address; else None. The
    server reads it even from a record it rejects, so that an address the agent named
    in a complete job is not marked dead because another field was bad."""
    ip = raw.get("ip") if isinstance(raw, dict) else None
    try:
        return Observation._ip(ip)
    except ValueError:
        return None


def echo_ip(raw: Any) -> str | None:
    """The `ip` of a raw record as quoted back in `rejected`: the agent's own string
    when it is short, ASCII and printable, as every IP address is; else null. Any other
    string may not even be encodable (JSON can carry a lone surrogate, which UTF-8
    cannot), and the answer must always be."""
    ip = raw.get("ip") if isinstance(raw, dict) else None
    if isinstance(ip, str) and len(ip) <= _IP_MAX and ip.isascii() and ip.isprintable():
        return ip
    return None


class FdnsAnswer(BaseModel):
    """The forward-DNS answer for one host the job named (`fdns`, spec §5.2). No job
    names a host until the forward-DNS check exists (#20), so none is accepted yet."""

    host_id: str = Field(max_length=_ID_MAX)
    # the IPv4 addresses the name resolved to; empty when it did not resolve
    addresses: list[str] = Field(max_length=FDNS_ADDRESSES_MAX)


class ResultsRequest(BaseModel):
    """`POST /api/v1/agent/results`: what one job found. Unknown fields are ignored."""

    # the protocol the result set is sent under, one of RESULTS_PROTOCOLS; absent is 1
    protocol: StrictInt = 1
    # the job's id from the check-in answer; it must still be this agent's current job
    job_id: str = Field(max_length=_ID_MAX)
    subnet_id: str = Field(max_length=_ID_MAX)
    # by the agent's clock; used only for the scan's duration
    started_at: AwareDatetime
    finished_at: AwareDatetime
    # true only when every issued method ran over every address of the job before the
    # timeout
    complete: StrictBool
    # Protocol 2, and required there: true when every issued liveness method (icmp,
    # arp) ran over every address before the timeout, whatever the PTR lookups did.
    # It alone decides whether the silent addresses are marked dead. Never false when
    # `complete` is true. Protocol 1 has no such field: `complete` decides it.
    liveness_complete: StrictBool | None = None
    # live addresses only; each record is checked on its own (`parse_observation`)
    observations: list[Any] = Field(max_length=OBSERVATIONS_MAX)
    fdns: list[FdnsAnswer] = Field(default=[], max_length=FDNS_MAX)
    # what went wrong, if anything; sanitised (`scan_error`) and kept on the target
    error: str | None = None

    @field_validator("error")
    @classmethod
    def _error(cls, v: str | None) -> str | None:
        return scan_error(v)

    @field_validator("protocol")
    @classmethod
    def _protocol(cls, v: int) -> int:
        if v not in RESULTS_PROTOCOLS:
            raise ValueError(f"must be one of {', '.join(map(str, RESULTS_PROTOCOLS))}")
        return v

    @model_validator(mode="after")
    def _times(self):
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must not be earlier than started_at")
        return self

    @model_validator(mode="after")
    def _liveness(self):
        if self.protocol >= 2:
            if self.liveness_complete is None:
                raise ValueError("liveness_complete is required from protocol 2")
            if self.complete and not self.liveness_complete:
                raise ValueError("liveness_complete must be true when complete is")
        return self

    def marks_dead(self) -> bool:
        """Whether this result set's liveness sweep completed, by the rule of the
        protocol it was sent under: protocol 1 knew only `complete`."""
        return self.complete if self.protocol == 1 else bool(self.liveness_complete)


class Rejected(BaseModel):
    # the record's `ip` as the agent sent it (`echo_ip`)
    ip: str | None
    # one of the REJECT_ values
    reason: str


class ResultsResponse(BaseModel):
    """The results answer, inside the usual `{"data": ...}` envelope."""

    # how many records were stored
    accepted: int
    rejected: list[Rejected]
