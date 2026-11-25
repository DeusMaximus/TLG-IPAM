"""The agent-side guard (spec §5.6): which addresses a job may touch, decided before
any packet is sent.

The server tells the agent what to sweep. `ALLOWED_CIDRS` is the owner's own limit on
that, set on the agent's host, so that a mistyped public CIDR in the IPAM cannot turn
the agent into a scanner pointed at someone else's network. A job whose subnet is
outside it, or whose ranges leave the subnet, is refused whole: nothing of it is
scanned, and the refusal is reported to the server.

`ALLOWED_DNS_SERVERS` is the same kind of limit on the PTR lookups' DNS servers (#93),
which the job names, as the subnet's resolvers, or the host's resolv.conf lists when it
names none: a server outside it is not asked (`dns_servers`). It is a narrowing of the
servers the agent would ask, not a refusal of the job, since the liveness sweep owes
nothing to the subnet's DNS settings.
"""

import ipaddress

from .protocol import OBSERVATIONS_MAX, Address, Job, JobRefused, Network


def plan(job: Job, allowed: tuple[Network, ...]) -> tuple[Address, ...]:
    """Every address the job's sweep sends to, in order. Raises JobRefused."""
    network = job.network
    if not any(network.version == a.version and network.subnet_of(a) for a in allowed):
        raise JobRefused(f"refused: subnet {network} is outside ALLOWED_CIDRS")

    total = 0
    for start, end in job.ranges:
        if start.version != network.version or end.version != network.version:
            raise JobRefused(f"refused: a range is not inside subnet {network}")
        if start not in network or end not in network or start > end:
            raise JobRefused(f"refused: a range is not inside subnet {network}")
        total += int(end) - int(start) + 1
        # counted before any list is built, so a huge range costs nothing
        if total > OBSERVATIONS_MAX:
            raise JobRefused(
                f"refused: the job's ranges hold more than {OBSERVATIONS_MAX} addresses"
            )

    addresses = set()
    for start, end in job.ranges:
        addresses.update(range(int(start), int(end) + 1))
    # spec §5.2: the sweep skips the network and broadcast addresses (/30 and shorter)
    if network.version == 4 and network.prefixlen <= 30:
        addresses.discard(int(network.network_address))
        addresses.discard(int(network.broadcast_address))
    if not addresses:
        raise JobRefused(f"refused: the job's ranges hold no address of subnet {network}")
    # the network's own class: ip_address() would read a small IPv6 integer as IPv4
    cls = type(network.network_address)
    return tuple(cls(a) for a in sorted(addresses))


def dns_servers(
    servers: tuple[str, ...], allowed: tuple[Network, ...] | None
) -> tuple[tuple[str, ...], int]:
    """Of `servers`, the ones the PTR lookups may ask, in their order, and how many
    were left out for lying outside `allowed` (`ALLOWED_DNS_SERVERS`). With `allowed`
    None (the setting unset), every server is kept, as before #93.

    `servers` are those the agent would ask without the setting (`Resolver.servers`:
    addresses only, already cut to MAX_DNS_SERVERS), so the setting only ever takes a
    server away: it never makes one asked that would not have been. An entry that is
    not an address is left out and counted, though `Resolver.servers` passes none.

    An IPv6 server written with a zone (`fe80::53%eth1`, `fe80::53%0`) is never kept,
    whatever the list holds (round 1 of the briefed review, finding 1): `ipaddress`
    finds it inside a network whatever its zone, but the query goes to the interface
    the zone names, and the same link-local address is another host on each interface.
    The list holds no zone (`config.parse_allowed_dns` refuses one), so it cannot say
    which interface it means. A link-local server written without a zone is compared
    by its address alone, like any other, and when it is kept its query is handed to
    the socket with no interface named: the host's routing then picks the link it goes
    out on (round 2, finding 1), which this does not decide."""
    if allowed is None:
        return servers, 0
    kept = []
    for server in servers:
        try:
            address = ipaddress.ip_address(server)
        except ValueError:
            continue
        if address.version == 6 and address.scope_id is not None:
            continue
        if any(address.version == network.version and address in network for network in allowed):
            kept.append(server)
    return tuple(kept), len(servers) - len(kept)
