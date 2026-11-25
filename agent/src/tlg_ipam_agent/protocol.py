"""The agent's side of the wire contract, `api/app/agent_protocol.py` (spec §5.7).

The agent does not import the server's module: that module needs pydantic, and the
agent's dependencies are an HTTP client, icmplib and dnspython (spec §5.6). The
values and rules here are copies, and `tests/test_contract.py` checks each against
the server's module, so the two cannot drift apart unnoticed.

Everything in a job is read as data in a fixed vocabulary. Nothing in it is run,
fetched or installed (spec §7.2, invariant 1).
"""

import ipaddress
import re
from dataclasses import dataclass
from typing import Any

PROTOCOL_VERSION = 2
# The codes of the enrol route's refusal of an agent on another protocol, `409`
# (agent_protocol.ENROL_TOO_OLD, ENROL_TOO_NEW; #165): the enrolment token is not spent.
ENROL_TOO_OLD = "AGENT_TOO_OLD"
ENROL_TOO_NEW = "AGENT_TOO_NEW"
CHECKIN_INTERVAL_S = 60
JOB_TIMEOUT_S = 600

# refusal handling, spec §5.7 (agent_protocol.CHECKIN_RETRY_*)
CHECKIN_RETRY_MIN_S = 10
CHECKIN_RETRY_MAX_S = 300
CHECKIN_REFUSED_RETRY_S = 900

# caps on what the agent reports (agent_protocol)
VERSION_MAX = 40
PLATFORM_MAX = 120
HOSTNAME_MAX = 253
INTERFACE_NAME_MAX = 15
INTERFACES_MAX = 64
OBSERVATIONS_MAX = 4096
# names in a job, answers in a result set, and addresses in one answer (`fdns`, #20)
FDNS_MAX = 4096
FDNS_ADDRESSES_MAX = 32
RDNS_NAME_MAX = 253
SCAN_ERROR_MAX = 500
# the job and subnet ids the server sends are UUIDs
ID_MAX = 36

# spec §5.1: a job's lease outlives its timeout by this margin (jobs.LEASE, 15 min)
LEASE_S = 15 * 60

METHODS = frozenset({"icmp", "arp", "rdns", "fdns"})
LIVENESS = frozenset({"icmp", "arp"})
# what this release can run: every method (`fdns` since #20)
SUPPORTED = frozenset({"icmp", "arp", "rdns", "fdns"})

_HOST_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")


def rdns_name(value: Any) -> str | None:
    """A PTR name as the server keeps it, or None: agent_protocol.rdns_name, copied.

    A name that is not a DNS hostname is dropped whole, never repaired. The agent
    applies the rule before sending so that a hostile PTR answer never leaves it."""
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


def fdns_name(value: Any) -> str | None:
    """A name a forward-DNS job may ask, or None: agent_protocol.fdns_name, copied. A
    DNS hostname already in the form `rdns_name` keeps (lowercase, no trailing dot).
    The agent cannot tell whether a name is under the subnet's domain (the server
    decides that, spec §7.2 invariant 5); what it can hold is that every name it asks
    is a hostname, so a job naming anything else is refused (`parse_job`)."""
    name = rdns_name(value)
    return name if name is not None and name == value else None


def _scan_error_uncut(value: str) -> str:
    text = "".join(c if c.isprintable() else " " for c in value)
    return " ".join(text.split())


def scan_error(value: str | None) -> str | None:
    """agent_protocol.scan_error, copied: printable, whitespace collapsed, capped."""
    if value is None:
        return None
    text = _scan_error_uncut(value)[:SCAN_ERROR_MAX].strip()
    return text or None


def scan_error_fits(value: str) -> bool:
    """Whether `scan_error` keeps `value` whole: what is left once it is made printable
    and its whitespace collapsed is within SCAN_ERROR_MAX, the agent's copy of the
    server's cap (`test_contract.py` holds the two equal), so nothing of it is cut."""
    return len(_scan_error_uncut(value)) <= SCAN_ERROR_MAX


def printable(value: str, cap: int) -> str:
    """`value` cut to `cap` characters with anything unprintable replaced, for a field
    the agent reports or a line it logs. The server refuses an empty or unprintable
    reported field (agent_protocol.printable), so the agent never sends one."""
    text = "".join(c if c.isprintable() else " " for c in value).strip()
    return text[:cap].strip() or "unknown"


class JobRefused(Exception):
    """The agent will not run this job. `reason` is reported to the server as the
    result set's `error`; it quotes only values the agent has already parsed."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class JobRef:
    """What a result set must quote back: enough to report a refusal."""

    job_id: str
    subnet_id: str


Address = ipaddress.IPv4Address | ipaddress.IPv6Address
Network = ipaddress.IPv4Network | ipaddress.IPv6Network


@dataclass(frozen=True)
class Job:
    ref: JobRef
    network: Network
    # inclusive (start, end) pairs, as sent; `guard.plan` checks them against `network`
    ranges: tuple[tuple[Address, Address], ...]
    methods: frozenset[str]
    dns_servers: tuple[str, ...]
    timeout_s: int
    # (host_id, fqdn) pairs to resolve for `fdns`, each a hostname (`fdns_name`), each
    # host once; empty unless `methods` holds fdns
    fdns: tuple[tuple[str, str], ...] = ()


def _id(value: Any) -> str | None:
    if isinstance(value, str) and 0 < len(value) <= ID_MAX and value.isascii() and value.isprintable():
        return value
    return None


def job_ref(raw: Any) -> JobRef | None:
    """The job's ids, or None when there is nothing to quote back and so no way to
    report on the job at all."""
    if not isinstance(raw, dict):
        return None
    job_id, subnet_id = _id(raw.get("job_id")), _id(raw.get("subnet_id"))
    if job_id is None or subnet_id is None:
        return None
    return JobRef(job_id, subnet_id)


def _address(value: Any) -> Address:
    if not isinstance(value, str) or len(value) > 45:
        raise ValueError
    return ipaddress.ip_address(value)


def parse_job(ref: JobRef, raw: dict) -> Job:
    """A job from the check-in answer, checked for shape. Raises JobRefused."""
    cidr = raw.get("cidr")
    try:
        if not isinstance(cidr, str) or len(cidr) > 64:
            raise ValueError
        network = ipaddress.ip_network(cidr, strict=True)
    except ValueError:
        raise JobRefused("refused: the job's cidr is not a network") from None

    ranges = raw.get("ranges")
    if not isinstance(ranges, list) or not ranges:
        raise JobRefused("refused: the job has no ranges")
    if len(ranges) > OBSERVATIONS_MAX:
        raise JobRefused("refused: the job has more ranges than addresses it may report")
    parsed = []
    for item in ranges:
        try:
            if not isinstance(item, dict):
                raise ValueError
            start, end = _address(item.get("start")), _address(item.get("end"))
        except ValueError:
            raise JobRefused("refused: a range of the job is not a pair of addresses") from None
        parsed.append((start, end))

    methods = raw.get("methods")
    if not isinstance(methods, list) or not all(isinstance(m, str) for m in methods):
        raise JobRefused("refused: the job's methods are not a list of names")
    unknown = sorted(set(methods) - METHODS)
    if unknown:
        raise JobRefused("refused: the job names a method this agent does not know")
    if not LIVENESS & set(methods):
        raise JobRefused("refused: the job has no liveness method (icmp or arp)")

    servers = raw.get("dns_servers", [])
    if not isinstance(servers, list):
        raise JobRefused("refused: the job's dns_servers is not a list")
    try:
        dns_servers = tuple(str(_address(s)) for s in servers)
    except ValueError:
        raise JobRefused("refused: a DNS server of the job is not an IP address") from None

    timeout_s = raw.get("timeout_s")
    if type(timeout_s) is not int or timeout_s < 1:
        raise JobRefused("refused: the job's timeout_s is not a positive whole number")

    names = raw.get("fdns", [])
    if not isinstance(names, list):
        raise JobRefused("refused: the job's fdns is not a list")
    if len(names) > FDNS_MAX:
        raise JobRefused(f"refused: the job names more than {FDNS_MAX} hosts to resolve")
    fdns = []
    hosts = set()
    for item in names:
        host_id = _id(item.get("host_id")) if isinstance(item, dict) else None
        fqdn = fdns_name(item.get("fqdn")) if isinstance(item, dict) else None
        if host_id is None or fqdn is None or host_id in hosts:
            raise JobRefused("refused: an fdns entry of the job is not one host id and a DNS hostname")
        hosts.add(host_id)
        fdns.append((host_id, fqdn))

    return Job(
        ref=ref,
        network=network,
        ranges=tuple(parsed),
        methods=frozenset(methods),
        dns_servers=dns_servers,
        # the agent's own ceiling holds whatever the server asks for, so a scan always
        # ends while its lease is live
        timeout_s=min(timeout_s, JOB_TIMEOUT_S),
        # names are resolved only for a job issued fdns; without it they are not asked
        fdns=tuple(fdns) if "fdns" in methods else (),
    )
