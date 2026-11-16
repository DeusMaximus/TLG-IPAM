"""What sends packets: the ICMP echo prober and the PTR resolver (spec §5.2).

These are the only two places in the agent that put anything on the network apart
from its requests to the server, and each is handed one address at a time by
`scan.run`, which takes its addresses only from `guard.plan`. Tests swap both for
fakes that record what they were asked to do and send nothing.
"""

from typing import Protocol

import dns.asyncresolver
import dns.exception
import dns.resolver
import dns.reversename
import icmplib

from .protocol import Address


class ProbeError(Exception):
    """The prober cannot work at all (no CAP_NET_RAW, say): the sweep stops, and the
    job is reported incomplete with this as its error."""


class Prober(Protocol):
    async def ping(self, address: Address, timeout: float) -> bool: ...


class Resolver(Protocol):
    async def ptr(self, address: Address, servers: tuple[str, ...], timeout: float) -> str | None: ...


class IcmpProber:
    """One ICMP echo request per address, on a raw socket (`CAP_NET_RAW`)."""

    async def ping(self, address: Address, timeout: float) -> bool:
        try:
            # an address, never a name: icmplib would resolve a name first
            host = await icmplib.async_ping(
                str(address), count=1, timeout=timeout, privileged=True
            )
        except (icmplib.SocketPermissionError, icmplib.SocketUnavailableError) as exc:
            raise ProbeError(
                "ICMP needs CAP_NET_RAW: run the container with --cap-add NET_RAW "
                f"({type(exc).__name__})"
            ) from None
        except icmplib.ICMPLibError:
            # the kernel refused this one destination (no route, say): no answer
            return False
        return host.is_alive


class DnsResolver:
    """A PTR lookup against the job's DNS servers, or the host's own resolver when the
    job names none (spec §5.2)."""

    def __init__(self):
        self._resolvers: dict[tuple[str, ...], dns.asyncresolver.Resolver] = {}

    def _resolver(self, servers: tuple[str, ...]) -> dns.asyncresolver.Resolver:
        if servers not in self._resolvers:
            if servers:
                resolver = dns.asyncresolver.Resolver(configure=False)
                resolver.nameservers = list(servers)
            else:
                # reads the host's resolv.conf; raises when it has none
                resolver = dns.asyncresolver.Resolver()
            self._resolvers = {servers: resolver}
        return self._resolvers[servers]

    async def ptr(self, address: Address, servers: tuple[str, ...], timeout: float) -> str | None:
        try:
            answer = await self._resolver(servers).resolve(
                dns.reversename.from_address(str(address)), "PTR", lifetime=timeout
            )
        except (dns.exception.DNSException, OSError):
            return None
        for record in answer:
            return record.target.to_text()
        return None
