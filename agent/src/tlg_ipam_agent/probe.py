"""What sends packets: the ICMP echo prober and the PTR resolver (spec §5.2).

These are the only two places in the agent that put anything on the network apart
from its requests to the server, and each is handed one address at a time by
`scan.run`, which takes its addresses only from `guard.plan`. Every datagram either
sends comes out of the job's one packet budget (`scan.Pacer`): an echo request is one
packet per call, and the resolver takes a slot before each query it sends. Tests swap
both for fakes that record what they were asked to do and send nothing.
"""

from typing import Protocol
from collections.abc import Awaitable, Callable

import dns.asyncquery
import dns.exception
import dns.message
import dns.rcode
import dns.rdatatype
import dns.resolver
import dns.reversename
import icmplib

from .protocol import Address


class ProbeError(Exception):
    """The prober cannot work at all (no CAP_NET_RAW, say): the sweep stops, and the
    job is reported incomplete with this as its error."""


class Prober(Protocol):
    async def ping(self, address: Address, timeout: float) -> bool: ...


# awaited before every datagram a lookup sends: takes one packet from the job's budget
# and returns the seconds the lookup has left (none left: send nothing more)
Slot = Callable[[], Awaitable[float]]


class Resolver(Protocol):
    async def ptr(self, address: Address, servers: tuple[str, ...], slot: Slot) -> str | None: ...


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


# servers tried for one lookup, in order; resolv.conf holds at most three as well
MAX_DNS_SERVERS = 3


class DnsResolver:
    """A PTR lookup against the job's DNS servers, or the host's own resolvers when the
    job names none (spec §5.2).

    The agent sends each query itself rather than through dnspython's resolver, whose
    failover, retransmission and TCP fallback would put datagrams on the wire that the
    packet budget never saw. Here a lookup is one UDP query per server, to at most
    MAX_DNS_SERVERS servers in turn, with no retransmission and no TCP. Before each
    query it awaits `slot()`, which takes one packet from the job's budget (`MAX_PPS`,
    shared with the ICMP sweep) and returns the seconds the lookup has left."""

    def __init__(self, port: int = 53):
        self.port = port
        self._system: tuple[str, ...] | None = None

    def _system_servers(self) -> tuple[str, ...]:
        if self._system is None:
            try:
                # reads the host's resolv.conf; nothing is sent
                self._system = tuple(dns.resolver.Resolver().nameservers)
            except dns.exception.DNSException:
                self._system = ()
        return self._system

    async def ptr(self, address: Address, servers: tuple[str, ...], slot: Slot) -> str | None:
        name = dns.reversename.from_address(str(address))
        for server in (servers or self._system_servers())[:MAX_DNS_SERVERS]:
            left = await slot()
            if left <= 0:
                return None
            # a fresh query, so a fresh id, for every datagram
            query = dns.message.make_query(name, dns.rdatatype.PTR)
            try:
                reply = await dns.asyncquery.udp(
                    query, server, timeout=left, port=self.port, ignore_unexpected=True
                )
            except (dns.exception.DNSException, OSError, ValueError):
                continue  # no answer in time, or a broken one: the next server
            rcode = reply.rcode()
            if rcode == dns.rcode.NXDOMAIN:
                return None
            if rcode != dns.rcode.NOERROR:
                continue  # SERVFAIL, REFUSED: the next server
            for rrset in reply.answer:
                if rrset.rdtype == dns.rdatatype.PTR and len(rrset):
                    return rrset[0].target.to_text()
            return None
        return None
