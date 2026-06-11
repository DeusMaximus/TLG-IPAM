"""IPv4 helpers. All address maths goes through `ipaddress` so IPv6 support later
is a matter of widening validation, not rewriting logic."""

import ipaddress
import re

from .errors import validation_error

_MAC_RE = re.compile(r"^[0-9a-f]{12}$")
_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$")


def parse_cidr(cidr: str) -> ipaddress.IPv4Network:
    try:
        net = ipaddress.ip_network(cidr, strict=True)
    except ValueError as exc:
        raise validation_error(f"CIDR {cidr!r} is invalid: {exc}") from None
    if net.version != 4:
        raise validation_error("Only IPv4 subnets are supported in v1")
    return net


def parse_ip(ip: str) -> ipaddress.IPv4Address:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        raise validation_error(f"IP {ip!r} is invalid") from None
    if addr.version != 4:
        raise validation_error("Only IPv4 addresses are supported in v1")
    return addr


def ip_in_subnet(ip: str, cidr: str) -> ipaddress.IPv4Address:
    addr = parse_ip(ip)
    net = parse_cidr(cidr)
    if addr not in net:
        raise validation_error(f"IP {ip} is not within subnet {cidr}")
    return addr


def normalise_domain(domain: str) -> str:
    cleaned = domain.strip().strip(".").lower()
    if not _DOMAIN_RE.match(cleaned):
        raise validation_error(f"Domain suffix {domain!r} is invalid")
    return cleaned


def normalise_mac(mac: str) -> str:
    stripped = re.sub(r"[^0-9a-fA-F]", "", mac).lower()
    if not _MAC_RE.match(stripped):
        raise validation_error(f"MAC address {mac!r} is invalid")
    return ":".join(stripped[i : i + 2] for i in range(0, 12, 2))


def usable_bounds(net: ipaddress.IPv4Network) -> tuple[int, int]:
    """First/last usable address as ints. /31 and /32 have no network/broadcast exclusion."""
    first = int(net.network_address)
    last = int(net.broadcast_address)
    if net.prefixlen <= 30:
        return first + 1, last - 1
    return first, last


def total_usable(net: ipaddress.IPv4Network) -> int:
    first, last = usable_bounds(net)
    return last - first + 1


def excluded_ints(subnet, reserved_ranges=()) -> set[int]:
    """Addresses that are usable but never auto-allocatable: the gateway, the DHCP
    pool, and any reserved ranges."""
    excluded: set[int] = set()
    if subnet.gateway:
        excluded.add(int(ipaddress.IPv4Address(subnet.gateway)))
    if subnet.dhcp_start and subnet.dhcp_end:
        start = int(ipaddress.IPv4Address(subnet.dhcp_start))
        end = int(ipaddress.IPv4Address(subnet.dhcp_end))
        excluded.update(range(start, end + 1))
    for block in reserved_ranges:
        excluded.update(range(block.start_int, block.end_int + 1))
    return excluded


def iter_free(subnet, allocated: set[int], reserved_ranges=(), limit: int | None = None) -> list[str]:
    """Free IPs in ascending order: usable, not gateway/DHCP-pool/reserved, not allocated."""
    net = parse_cidr(subnet.cidr)
    first, last = usable_bounds(net)
    excluded = excluded_ints(subnet, reserved_ranges)
    free: list[str] = []
    for ip_int in range(first, last + 1):
        if ip_int in allocated or ip_int in excluded:
            continue
        free.append(str(ipaddress.IPv4Address(ip_int)))
        if limit is not None and len(free) >= limit:
            break
    return free
