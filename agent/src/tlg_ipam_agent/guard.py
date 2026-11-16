"""The agent-side guard (spec §5.6): which addresses a job may touch, decided before
any packet is sent.

The server tells the agent what to sweep. `ALLOWED_CIDRS` is the owner's own limit on
that, set on the agent's host, so that a mistyped public CIDR in the IPAM cannot turn
the agent into a scanner pointed at someone else's network. A job whose subnet is
outside it, or whose ranges leave the subnet, is refused whole: nothing of it is
scanned, and the refusal is reported to the server.
"""

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
