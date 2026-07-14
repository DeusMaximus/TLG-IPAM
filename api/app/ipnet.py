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


def merge_intervals(
    intervals: list[tuple[int, int]], *, lower: int, upper: int
) -> list[tuple[int, int]]:
    """Clip and merge inclusive integer intervals within the supplied bounds."""
    clipped = sorted(
        (max(start, lower), min(end, upper))
        for start, end in intervals
        if end >= lower and start <= upper
    )
    merged: list[tuple[int, int]] = []
    for start, end in clipped:
        if not merged or start > merged[-1][1] + 1:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def exclusion_intervals(subnet, reserved_ranges=()) -> list[tuple[int, int]]:
    """Merged usable-address intervals excluded from automatic allocation."""
    net = parse_cidr(subnet.cidr)
    first, last = usable_bounds(net)
    intervals: list[tuple[int, int]] = []
    if subnet.gateway:
        gateway = int(ipaddress.IPv4Address(subnet.gateway))
        intervals.append((gateway, gateway))
    if subnet.dhcp_start and subnet.dhcp_end:
        start = int(ipaddress.IPv4Address(subnet.dhcp_start))
        end = int(ipaddress.IPv4Address(subnet.dhcp_end))
        intervals.append((start, end))
    for block in reserved_ranges:
        intervals.append((block.start_int, block.end_int))
    return merge_intervals(intervals, lower=first, upper=last)


def interval_size(intervals: list[tuple[int, int]]) -> int:
    return sum(end - start + 1 for start, end in intervals)


def iter_free(subnet, allocated: set[int], reserved_ranges=(), limit: int | None = None) -> list[str]:
    """Free IPs in ascending order: usable, not gateway/DHCP-pool/reserved, not allocated."""
    net = parse_cidr(subnet.cidr)
    first, last = usable_bounds(net)
    blocked = merge_intervals(
        [*exclusion_intervals(subnet, reserved_ranges), *((ip, ip) for ip in allocated)],
        lower=first,
        upper=last,
    )
    free: list[str] = []

    def append_gap(start: int, end: int) -> bool:
        for ip_int in range(start, end + 1):
            free.append(str(ipaddress.IPv4Address(ip_int)))
            if limit is not None and len(free) >= limit:
                return True
        return False

    cursor = first
    for start, end in blocked:
        if cursor < start and append_gap(cursor, start - 1):
            return free
        cursor = max(cursor, end + 1)
    if cursor <= last:
        append_gap(cursor, last)
    return free
