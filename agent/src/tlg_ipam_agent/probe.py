"""What sends packets: the ICMP echo prober, the ARP session and the PTR resolver
(spec §5.2).

These are the only places in the agent that put anything on the network apart from
its requests to the server, and each is handed one address at a time by `scan.run`,
which takes its addresses only from `guard.plan`. Every datagram or frame they send
comes out of the job's one packet budget (`scan.Pacer`): an echo request is one packet
per call, an ARP request one frame per call, and the resolver takes a slot before each
query it sends. Tests swap each for a fake that records what it was asked to do and
sends nothing; the ARP fake stands in for the socket only, so the frames the session
builds and the replies it takes are the real ones.
"""

import asyncio
import ipaddress
import socket
from typing import Protocol
from collections.abc import Awaitable, Callable

import dns.asyncquery
import dns.exception
import dns.flags
import dns.message
import dns.rcode
import dns.rdatatype
import dns.resolver
import dns.reversename
import icmplib

from . import arp
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
    # the servers one job's lookups ask, chosen once as the job's lookups start
    def servers(self, job_servers: tuple[str, ...]) -> tuple[str, ...]: ...
    # raises ProbeError when no query of the lookup could be sent: that is no lookup
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


# linux/if_packet.h: a frame this host is sending, which a packet socket also sees
PACKET_OUTGOING = 4
# an ARP reply is 42 bytes and padding; a longer frame is cut here, and is not one
ARP_RECV_MAX = 2048
# frames the listener takes before it lets every other task run. Reading a socket
# that already holds a frame returns without yielding, so a station that keeps the
# socket readable (a flood) would otherwise hold the event loop: the job's deadline,
# the check-ins and cancellation would all wait for the flood to end
ARP_RECEIVE_BATCH = 64


class ArpLink(Protocol):
    """A packet socket on one interface that carries ARP frames and nothing else."""

    interface: str

    async def send(self, frame: bytes) -> None: ...
    # a frame and the address recvfrom() gives for it:
    # (interface, ethertype, packet type, hardware type, source MAC)
    async def receive(self) -> tuple[bytes, tuple]: ...
    def close(self) -> None: ...


class PacketLink:
    """A raw `AF_PACKET` socket bound to one interface for ARP (Linux, `CAP_NET_RAW`,
    standard library only; spec §5.2).

    It is created with protocol 0, so it receives nothing until `bind` names ARP on
    this interface: no frame from another interface is ever queued on it."""

    def __init__(self, interface: str):
        self.interface = interface
        try:
            sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, 0)
        except PermissionError as exc:
            raise ProbeError(
                "ARP needs CAP_NET_RAW: run the container with --cap-add NET_RAW "
                f"({type(exc).__name__})"
            ) from None
        except OSError as exc:
            raise ProbeError(f"ARP socket unavailable ({type(exc).__name__})") from None
        try:
            sock.setblocking(False)
            # the ethertype in a packet address is in host order: CPython converts it
            sock.bind((interface, arp.ETH_P_ARP))
        except OSError as exc:
            sock.close()
            raise ProbeError(f"ARP cannot use interface {interface} ({type(exc).__name__})") from None
        self._sock = sock

    async def send(self, frame: bytes) -> None:
        """Hands one frame to the kernel at once. It never waits for the socket to be
        writable, so a send cannot hold the job past its deadline: a full send queue
        is a failed send, which stops the sweep."""
        try:
            self._sock.send(frame)
        except BlockingIOError:
            raise ProbeError(f"ARP send queue full on {self.interface}") from None
        except OSError as exc:
            raise ProbeError(f"ARP request not sent on {self.interface} ({type(exc).__name__})") from None

    async def receive(self) -> tuple[bytes, tuple]:
        try:
            return await asyncio.get_running_loop().sock_recvfrom(self._sock, ARP_RECV_MAX)
        except OSError as exc:
            raise ProbeError(f"ARP replies not readable on {self.interface} ({type(exc).__name__})") from None

    def close(self) -> None:
        self._sock.close()


class ArpSession:
    """One job's ARP requests and the replies it takes, on one interface.

    A request goes only to an address of `targets` (the job's planned addresses), and
    a reply is taken only from an address a request went to, once: the first reply
    for an address supplies its MAC. Anything else that arrives is dropped, whatever
    it says: a frame this host is sending (the kernel's own ARP among them), a frame
    from another interface, one that is not a well-formed reply to this interface's
    address (`arp.reply`), a gratuitous or unsolicited reply, a reply for an address
    not asked for, and a second reply for one already answered."""

    def __init__(
        self,
        link: ArpLink,
        address: ipaddress.IPv4Address,
        mac: bytes,
        targets: frozenset[Address],
    ):
        self._link = link
        self._address = address
        self._mac = mac
        self._targets = targets
        self._asked: set[Address] = set()
        self.answers: dict[Address, str] = {}

    async def request(self, address: Address) -> None:
        """Send one request for `address`. Takes no slot itself: `scan` paces it."""
        if address not in self._targets:
            raise ValueError("an ARP request goes only to an address of the job")
        self._asked.add(address)
        await self._link.send(arp.request(self._mac, self._address, address))

    def take(self, frame: bytes, source: object) -> bool:
        """Record a received frame if it answers a request; whether it did."""
        if not isinstance(source, tuple) or len(source) < 3:
            return False
        if source[0] != self._link.interface or source[2] == PACKET_OUTGOING:
            return False
        found = arp.reply(frame, self._address)
        if found is None:
            return False
        sender, mac = found
        if sender not in self._asked or sender in self.answers:
            return False
        self.answers[sender] = mac
        return True

    async def listen(self) -> None:
        """Take replies until cancelled, letting every other task run after each
        ARP_RECEIVE_BATCH frames. A ProbeError from the link ends it."""
        while True:
            for _ in range(ARP_RECEIVE_BATCH):
                frame, source = await self._link.receive()
                self.take(frame, source)
            await asyncio.sleep(0)


# servers tried for one lookup, in order; resolv.conf holds at most three as well
MAX_DNS_SERVERS = 3
# where the host's own resolvers are read, when a job names no DNS server
RESOLV_CONF = "/etc/resolv.conf"
# the error of a job whose PTR lookups had no server to ask (spec §5.2): fixed text
NO_DNS_SERVER = "rdns not run: the agent has no DNS server to ask (the job names none, and the host's resolv.conf lists none it can use)"


def no_query_sent(cause: str) -> str:
    """The error of a lookup that could send its query to none of its servers."""
    return f"rdns stopped: no DNS query could be sent to any server ({cause})"


class DnsResolver:
    """A PTR lookup against the job's DNS servers, or the host's own resolvers when the
    job names none (spec §5.2).

    The agent sends each query itself rather than through dnspython's resolver, whose
    failover, retransmission and TCP fallback would put datagrams on the wire that the
    packet budget never saw. Here a lookup is one UDP query per server, to at most
    MAX_DNS_SERVERS servers in turn, with no retransmission and no TCP; a truncated
    reply is not used. Before each
    query it awaits `slot()`, which takes one packet from the job's budget (`MAX_PPS`,
    shared with the ICMP sweep) and returns the seconds the lookup has left.

    A lookup that could not be made is not a lookup that found no name: with no server
    to ask, or when the query could be sent to none of them, `ptr` raises ProbeError
    instead of returning None, so the scan reports itself incomplete and the server
    keeps the names it has (spec §5.7)."""

    def __init__(self, port: int = 53, resolv_conf: str = RESOLV_CONF):
        self.port = port
        self.resolv_conf = resolv_conf

    def servers(self, job_servers: tuple[str, ...]) -> tuple[str, ...]:
        """The servers a job's lookups ask: the job's own, else the host's resolvers,
        read from resolv.conf each time this is called (once per job, so a corrected
        resolv.conf takes effect at the next job; nothing is cached). Only addresses are
        kept, before the list is cut to MAX_DNS_SERVERS, so an entry the agent cannot
        query (a DNS-over-HTTPS URL, say) never takes the place of one it can. Empty
        when there is none: the job's lookups then have no server to ask."""
        chosen = job_servers or self._host_servers()
        usable = []
        for server in chosen:
            try:
                ipaddress.ip_address(server)
            except ValueError:
                continue
            usable.append(server)
        return tuple(usable[:MAX_DNS_SERVERS])

    def _host_servers(self) -> tuple[str, ...]:
        try:
            # reads the host's resolv.conf; nothing is sent. dnspython refuses a file
            # it cannot read, one with no nameserver, and one with a nameserver that is
            # neither an address nor an https URL: each leaves no server to ask
            listed = dns.resolver.Resolver(filename=self.resolv_conf).nameservers
        except (dns.exception.DNSException, ValueError, OSError):
            return ()
        return tuple(s for s in listed if isinstance(s, str))

    async def ptr(self, address: Address, servers: tuple[str, ...], slot: Slot) -> str | None:
        """The PTR name of `address`, or None when a server says there is none (NXDOMAIN,
        or an answer without a PTR record), when every server asked gave no usable
        answer, or when the lookup's time ran out. Raises ProbeError when there is no
        server to ask, or when the query could be sent to none of them."""
        if not servers:
            raise ProbeError(NO_DNS_SERVER)
        name = dns.reversename.from_address(str(address))
        asked = False
        unsent: str | None = None
        for server in servers[:MAX_DNS_SERVERS]:
            left = await slot()
            if left <= 0:
                # the lookup's time is up. If no query went out, the job's deadline has
                # passed (a lookup's own time starts at its first slot), and the scan
                # reports the cut-off itself
                return None
            # A fresh query, so a fresh id, for every datagram. It carries EDNS(0)
            # advertising a 1232-byte UDP payload, so a resolver can send a PTR answer
            # whole instead of truncating it (a truncated reply is not used); without
            # EDNS a plain query allows only 512 bytes. Recursion desired, no DNSSEC.
            # The advertised payload is not a limit on what is read: dnspython
            # receives up to 65,535 bytes.
            query = dns.message.make_query(
                name, dns.rdatatype.PTR, use_edns=0, payload=1232, want_dnssec=False,
                flags=dns.flags.RD,
            )
            try:
                reply = await dns.asyncquery.udp(
                    query,
                    server,
                    timeout=left,
                    port=self.port,
                    # a reply from another source address or port is waited out until
                    # `left`; one from the server with the wrong id is a bad response
                    # (ignore_errors is off), so the next server is asked
                    ignore_unexpected=True,
                    # a truncated reply is not used, PTR record or not, and there is
                    # no TCP retry: it is treated as no answer, and the next server
                    # is asked
                    raise_on_truncation=True,
                    # dnspython's default, stated: trailing bytes make a reply bad
                    ignore_trailing=False,
                )
            except (dns.exception.DNSException, TimeoutError):
                # no answer in time, a truncated or broken one: it was asked, and the
                # next server is. (TimeoutError is an OSError: it is caught first.)
                asked = True
                continue
            except (OSError, ValueError) as exc:
                # the query was not sent: an address dnspython cannot use, no socket
                # of its family on this host, no route. With asyncio a failed send
                # surfaces here too, as an OSError, and so would an ICMP error the
                # kernel reported for the socket. The next server
                unsent = type(exc).__name__
                continue
            asked = True
            rcode = reply.rcode()
            if rcode == dns.rcode.NXDOMAIN:
                return None
            if rcode != dns.rcode.NOERROR:
                continue  # SERVFAIL, REFUSED: the next server
            for rrset in reply.answer:
                if rrset.rdtype == dns.rdatatype.PTR and len(rrset):
                    return rrset[0].target.to_text()
            return None
        if not asked:
            # every server was tried, and none could be sent the query
            raise ProbeError(no_query_sent(unsent or "unknown"))
        return None
