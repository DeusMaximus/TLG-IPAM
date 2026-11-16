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

from pydantic import BaseModel, Field, field_validator

# The protocol this server speaks. A check-in with any other value gets no jobs and
# `409 PROTOCOL_MISMATCH`, and the error is recorded on the agent (spec §5.6).
PROTOCOL_VERSION = 1

# spec §5.1: the agent checks in every minute by default
CHECKIN_INTERVAL_S = 60
# spec §5.6: the per-job timeout the agent enforces
JOB_TIMEOUT_S = 600

# caps on what an agent may report, matching the `agents` columns
VERSION_MAX = 40
PLATFORM_MAX = 120
HOSTNAME_MAX = 253
# Linux IFNAMSIZ is 16 bytes including the terminating NUL
INTERFACE_NAME_MAX = 15
INTERFACES_MAX = 64
CAPABILITIES_MAX = 16

_MAC_HEX = re.compile(r"^[0-9a-f]{12}$")
_CAPABILITY = re.compile(r"^[a-z0-9_-]{1,32}$")
# longest IPv6 address with a prefix length, plus slack for a dotted netmask
_INTERFACE_CIDR_MAX = 64


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
        if v is None:
            return None
        if len(v) > 32:
            raise ValueError("must be a MAC address")
        stripped = re.sub(r"[:.-]", "", v.strip()).lower()
        if not _MAC_HEX.match(stripped):
            raise ValueError("must be a MAC address")
        return ":".join(stripped[i : i + 2] for i in range(0, 12, 2))


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
            if not _CAPABILITY.match(item):
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
    # the subnet's DNS servers for rdns and fdns; empty means the agent's own resolver
    dns_servers: list[str]
    timeout_s: int
    # the names to resolve for fdns; only ever names under the subnet's domain
    fdns: list[FdnsName]


class CheckinResponse(BaseModel):
    """The check-in answer, inside the usual `{"data": ...}` envelope."""

    # when to check in next
    checkin_interval_s: int
    # at most one: the agent runs jobs one at a time, and holds no second lease
    jobs: list[Job]
