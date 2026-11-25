"""What sends packets: the ICMP echo prober, the ARP session and the DNS resolver, for
PTR lookups and the forward-DNS check's A lookups (spec §5.2).

These are the only places in the agent that put anything on the network apart from
its requests to the server, and each is handed one address at a time by `scan.run`,
which takes its addresses only from `guard.plan`. Every datagram or frame they send
comes out of the agent's one packet budget (`scan.Pacer`): an echo request is one
packet per call, an ARP request one frame per call, and the resolver takes a slot
before each query it sends. Each is handed to the kernel at its grant or not at all:
an ARP frame or a DNS query the socket cannot take at once is a failed send, never
kept to go later. The ARP the kernel sends itself, to reach an on-link address before
an echo request, is not the agent's and takes no slot (spec §5.6). Tests swap each for a fake that records what it was asked to do and
sends nothing; the ARP fake stands in for the socket only, so the frames the session
builds and the replies it takes are the real ones.
"""

import asyncio
import ipaddress
import os
import socket
import stat
import threading
from typing import NamedTuple, Protocol
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


# awaited before every datagram a lookup sends: takes one packet from the budget
# and returns the seconds the lookup has left (none left: send nothing more)
Slot = Callable[[], Awaitable[float]]


class NoName:
    """What `ptr` returns when a server answered that the address has no name: NXDOMAIN,
    or NOERROR without a PTR record that says so (`says_no_name`, for the name the
    answer is about). Unlike None, which is a lookup no server gave a
    usable answer to, it is the one answer that lets the server clear a stored name
    (spec §5.7). `NO_NAME` is its one instance."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "NO_NAME"


NO_NAME = NoName()


class UnreadReplies(NamedTuple):
    """What `ptr` may return in place of None for a lookup no server gave a usable
    answer to, when at least one of its attempts ended with a reply from the query's
    own server that the agent refused unread (#164): `too_large` for one longer than
    DNS_PAYLOAD (ReplyTooLarge), `too_many_records` for one whose header claims more
    than a PTR answer needs (ReplyTooManyRecords), both when its attempts met both.
    It is no usable answer, as None is, and the stored name stays: it only says which
    of the agent's own limits refused a reply, so that the job's error can count them
    (`scan.no_answer`). It holds nothing of the reply, its sender or the address. A
    lookup a later server answered returns that answer, wrapped in AnsweredPastRefusal
    when an earlier server's reply was refused (#170)."""

    too_large: bool
    too_many_records: bool


# What an A lookup (`DnsResolver.a`) found: the IPv4 addresses at the name the answer
# is about, sorted and each once, as text
Addresses = tuple[str, ...]


class AnsweredPastRefusal(NamedTuple):
    """What `ptr` returns in place of its answer for a lookup a later server answered
    (`answer`: a name, or NO_NAME) after an earlier attempt ended with a reply from
    that attempt's own server that the agent refused unread (#170), saying which
    limits refused one, as UnreadReplies does. The answer decides the record exactly
    as it would alone; the flags only let the job's error say that an earlier server's
    replies cost a query more (`scan.answered_later`). It holds nothing of the reply,
    its sender or the address."""

    answer: "str | NoName | Addresses"
    too_large: bool
    too_many_records: bool


def no_usable_answer(too_large: bool, too_many_records: bool) -> UnreadReplies | None:
    """What `ptr` returns for a lookup no server gave a usable answer to: UnreadReplies
    when any attempt's reply was refused unread, None otherwise."""
    if too_large or too_many_records:
        return UnreadReplies(too_large, too_many_records)
    return None


def answered(
    answer: "str | NoName | Addresses", too_large: bool, too_many_records: bool
) -> "str | NoName | Addresses | AnsweredPastRefusal":
    """What `ptr` returns for a lookup a server answered: the answer itself, as before
    #170, when no earlier attempt's reply was refused unread, and AnsweredPastRefusal
    otherwise."""
    if too_large or too_many_records:
        return AnsweredPastRefusal(answer, too_large, too_many_records)
    return answer


class Resolver(Protocol):
    # the servers one job's lookups ask, chosen once as the job's lookups start
    async def servers(self, job_servers: tuple[str, ...]) -> tuple[str, ...]: ...
    # the name, NO_NAME when a server said there is none, either wrapped in
    # AnsweredPastRefusal when an earlier server's reply was refused unread for the
    # agent's own limits, or, when no server gave a usable answer, None, or
    # UnreadReplies when a server's reply was so refused; raises ProbeError when no
    # query of the lookup could be sent: that is no lookup
    async def ptr(
        self, address: Address, servers: tuple[str, ...], slot: Slot
    ) -> str | NoName | AnsweredPastRefusal | UnreadReplies | None: ...
    # the forward-DNS check (#20): `name`'s IPv4 addresses, or NO_NAME when a server
    # said it has none, and otherwise as `ptr` answers, the errors saying fdns
    async def a(
        self, name: str, servers: tuple[str, ...], slot: Slot
    ) -> Addresses | NoName | AnsweredPastRefusal | UnreadReplies | None: ...


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
# how long a job waits for that read, and the largest file it parses: a read that
# has not finished by then, or a file larger when it is looked at, gives no server
RESOLV_CONF_TIMEOUT_S = 1.0
RESOLV_CONF_MAX = 64 * 1024
def no_dns_server(what: str = "rdns") -> str:
    """The error of a job whose lookups had no server to ask (spec §5.2): fixed text,
    naming the lookups that were not run (`rdns`, `fdns`, or `rdns and fdns`)."""
    return (
        f"{what} not run: the agent has no DNS server to ask (the job names none, and the "
        "host's resolv.conf lists none it can use)"
    )


NO_DNS_SERVER = no_dns_server()
# the UDP payload a PTR query advertises (EDNS(0), RFC 6891 §6.2.5), and the largest
# reply the agent reads (#153). 1232 bytes is the size DNS Flag Day 2020 settled on to
# keep a reply out of IP fragments; the largest ordinary PTR answer measured (a
# 253-character name behind two CNAMEs of RFC 2317 delegation, for an IPv6 address,
# with four name servers and their addresses beside it) is 710 bytes. A server that
# honours EDNS truncates a longer answer instead, and a truncated reply is not used:
# a larger datagram is one the agent did not ask for.
DNS_PAYLOAD = 1232
# the most a reply's 12-byte header may claim (#160): the one question a PTR query
# asks, and records in its answer, authority and additional sections together (the
# OPT record of EDNS among them). The largest legitimate PTR answers built hold 16
# (the 710-byte answer above: two CNAMEs and the PTR record, four name servers, an A
# and an AAAA record for each, and OPT) and 5 (four PTR records of 253 characters at
# one name, and OPT); 32 is twice the larger. A reply that claims more is not parsed:
# dnspython's parse of one whose owner names hash alike costs work that grows with
# the square of its records, and holds the event loop while it runs. Under these
# counts it makes at most (32 - 1) * (32 + 24) = 1,736 name comparisons on a 64-bit
# CPython whose dictionary probes as 3.13's does (spec §5.2, which says when that is
# rechecked).
DNS_QUESTIONS_MAX = 1
DNS_RECORDS_MAX = 32
# a DNS message's header: id, flags, then the four section counts, 2 bytes each
DNS_HEADER = 12


class ReplyTooLarge(dns.exception.DNSException):
    """A datagram from the query's server longer than DNS_PAYLOAD: it is not parsed
    (#153). The attempt ends as one that got no usable answer: the query was sent, and
    the next configured server is asked."""


class ReplyTooManyRecords(dns.exception.DNSException):
    """A datagram from the query's server whose header claims more than
    DNS_QUESTIONS_MAX questions, or more than DNS_RECORDS_MAX records in its other
    sections: it is not parsed (#160), and the attempt ends as ReplyTooLarge's does."""


def unread(data: bytes) -> dns.exception.DNSException | None:
    """Why a datagram is not handed to dnspython's parser, or None when it is: longer
    than DNS_PAYLOAD (ReplyTooLarge), or a header that claims more than a PTR answer
    needs (ReplyTooManyRecords). Only the length and the header's counts are read; the
    counts are the sender's claim. dnspython reads by them, so a reply that holds more
    than it claims is parsed only as far as the counts and then refused for its
    trailing bytes (`ignore_trailing` is off), and one that holds less runs out and is
    refused as malformed. A datagram shorter than a header has no counts to read, and
    dnspython refuses it as malformed, at no cost that grows."""
    if len(data) > DNS_PAYLOAD:
        return ReplyTooLarge(f"a {len(data)}-byte reply, over {DNS_PAYLOAD}")
    if len(data) < DNS_HEADER:
        return None
    questions = int.from_bytes(data[4:6], "big")
    records = sum(int.from_bytes(data[at:at + 2], "big") for at in (6, 8, 10))
    if questions > DNS_QUESTIONS_MAX or records > DNS_RECORDS_MAX:
        return ReplyTooManyRecords(
            f"a reply claiming {questions} questions and {records} records, over "
            f"{DNS_QUESTIONS_MAX} and {DNS_RECORDS_MAX}"
        )
    return None


class QuerySocket(asyncio.DatagramProtocol):
    """The UDP socket of one DNS query, made before the query takes its slot so that
    the query is handed to the kernel in the step its slot is granted (`sendto`), as an
    ARP request is. `dns.asyncquery.udp` is given it as `sock`: it has what dnspython's
    `DatagramSocket` offers that call (`family`, `sendto`, `recvfrom`). It is bound to
    no port until the query is sent (the kernel picks one then), so nothing can reach
    it while the lookup waits for its slot.

    The query goes when the slot is granted or not at all. A socket that cannot take it
    at once (its send buffer full) does not keep it: asyncio's transport would hold it
    and send it whenever the socket became writable, after the lookup had ended, after
    the job, or during the next one. The transport is aborted instead, the datagram
    with it, and the send fails, so the query counts as not sent. `close` aborts the
    transport as well: nothing of a lookup's leaves after it. A send the kernel refuses
    (no route, say) is reported by asyncio to `error_received`, and `recvfrom` raises
    it, as dnspython's own socket does.

    No datagram longer than DNS_PAYLOAD, the payload the query advertised, reaches
    dnspython (#153): `dns.asyncquery.receive_udp` hands every datagram `recvfrom`
    returns from the server's address and port straight to `dns.message.from_wire`,
    whose cost grows with the square of a reply's records when their names hash alike
    (dnspython 2.9.2 hashes a name by arithmetic that distinct names share), and the
    parse holds the event loop until it ends. Nor does one whose header claims more
    than one question, or more than DNS_RECORDS_MAX records (#160), which bounds that
    cost at a size the agent reads (`unread`). One from the address and port the query
    went to (`_from_server`) is answered with ReplyTooLarge or ReplyTooManyRecords,
    which `recvfrom` raises and dnspython passes on unparsed, so the attempt ends at
    once and the next server is asked. Whatever its id: one from there with the wrong
    id ends the attempt too, once parsed (a bad response), so no sender gains anything
    it did not have. One from anywhere else is dropped, as dnspython would ignore it
    (`ignore_unexpected`): no other sender can end the attempt, whatever it holds."""

    def __init__(self, family: int):
        self.family = family
        self._transport: asyncio.DatagramTransport | None = None
        self._received: asyncio.Queue = asyncio.Queue()
        # where the query went (dnspython's address tuple), once it has gone
        self._destination: tuple | None = None

    @classmethod
    async def made(cls, family: int) -> "QuerySocket":
        made = cls(family)
        await asyncio.get_running_loop().create_datagram_endpoint(lambda: made, family=family)
        return made

    def connection_made(self, transport) -> None:
        self._transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        refused = unread(data)
        if refused is not None:
            if self._from_server(addr):
                self._received.put_nowait(refused)
            return
        self._received.put_nowait((data, addr))

    def _from_server(self, addr) -> bool:
        """Whether a datagram came from the address and port the query was sent to,
        judged as strictly as dnspython judges it (`dns.query._addresses_equal`: the
        address as a value, not as text, and the rest of the tuple equal), or more
        strictly: a source dnspython would take from a multicast destination, or a
        scoped IPv6 address, is not the server here, so its over-size datagram is
        dropped rather than ending the attempt."""
        if self._destination is None or not isinstance(addr, tuple) or len(addr) < 2:
            return False
        try:
            same = ipaddress.ip_address(addr[0]) == ipaddress.ip_address(self._destination[0])
        except (TypeError, ValueError):
            return False
        return same and tuple(addr[1:]) == tuple(self._destination[1:])

    def error_received(self, exc: Exception) -> None:
        self._received.put_nowait(exc)

    def connection_lost(self, exc: Exception | None) -> None:
        self._received.put_nowait(exc or EOFError("the query's socket closed"))

    async def sendto(self, what: bytes, destination, timeout) -> int:
        """Hands the query to the kernel now, or raises: it is never kept for later."""
        self._destination = tuple(destination)
        self._transport.sendto(what, destination)
        if self._transport.get_write_buffer_size():
            # the socket could not take it, and the transport kept it: it goes with
            # the transport, unsent
            self.close()
            raise BlockingIOError("DNS send buffer full")
        return len(what)

    async def recvfrom(self, size: int, timeout: float | None) -> tuple:
        try:
            item = await asyncio.wait_for(self._received.get(), timeout)
        except TimeoutError:
            raise dns.exception.Timeout(timeout=timeout) from None
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self) -> None:
        if self._transport is not None:
            self._transport.abort()


def answered_name(reply: dns.message.Message, question: "dns.name.Name") -> "dns.name.Name | None":
    """The name a reply's answer is about (spec §5.2): the question's name, followed
    along the CNAME records the answer section holds, each at the name the chain has
    reached so far, to the last target (RFC 1034 §3.6.2; RFC 2317's classless
    delegation is such a chain). Records at any other owner are not part of it. None
    when the chain loops. dnspython keeps one CNAME per owner (the last a reply
    carries), so a link has one target. The agent follows the chain only within the
    reply; it sends no query for a target.

    The map and the visited set are keyed by each name's case-folded wire form
    (`Name.to_digestable`, label lengths included), never by the `Name`: dnspython
    hashes a name by its labels' bytes without their boundaries, so distinct names
    made of the same bytes split at different places (`aaaa.`, `a.aaa.`, `aa.aa.`, …)
    hash alike, and a reply whose chain is built from them would be compared name by
    name, at a cost that grows with the square of its links (#149). Two names in a
    reply are the same name exactly when their keys are equal, so the walk reads as it
    did; the cost of reading the reply off the wire is dnspython's."""
    cnames = {
        rrset.name.to_digestable(): rrset
        for rrset in reply.answer
        if rrset.rdtype == dns.rdatatype.CNAME and len(rrset)
    }
    name = question
    key = name.to_digestable()
    seen = {key}
    while key in cnames:
        name = cnames[key][0].target
        key = name.to_digestable()
        if key in seen:
            return None
        seen.add(key)
    return name


def says_no_name(reply: dns.message.Message, name: "dns.name.Name") -> bool:
    """Whether a NOERROR reply that carries no PTR record for `name`, the name its
    answer is about (`answered_name`), says that name has no PTR record (spec §5.2,
    RFC 2308 §2.2), rather than nothing about it.

    - An SOA in the authority section for a zone that contains `name` (its owner is
      `name` or an ancestor of it) is a negative answer (NODATA), whether or not the
      answer section holds the CNAMEs that led to `name`, and whether or not name
      servers stand beside it: no name.
    - An SOA there for any other zone (one unrelated to the question, or the
      question's own zone beside a CNAME whose target lies outside it) says nothing
      about `name`.
    - Name servers there and no SOA is a referral, from a server that does not
      recurse, pointing at the ones that know: nothing about the name, even after a
      CNAME (RFC 2317's classless reverse delegation is exactly that). The agent does
      not follow it; the next configured server is asked.
    - Neither, with an empty answer: the server answered the name itself, with no
      PTR record: no name, as the owner decided for a NOERROR without one (#100).
    - Neither, with records in the answer (a CNAME whose target the reply does not
      resolve, or records of another type): the name is an alias, or the reply is
      about something else, and the target's own answer is missing: nothing about
      the name."""
    soas = [rrset.name for rrset in reply.authority if rrset.rdtype == dns.rdatatype.SOA]
    if any(name.is_subdomain(zone) for zone in soas):
        return True
    if soas:
        return False
    if any(rrset.rdtype == dns.rdatatype.NS for rrset in reply.authority):
        return False
    return not reply.answer


def read_reply(reply: dns.message.Message, question: "dns.name.Name") -> str | NoName | None:
    """What a reply to the PTR query for `question` says (spec §5.2): the name, NO_NAME
    when it says there is none, or None when it gives no usable answer and the next
    configured server is asked.

    - NXDOMAIN: NO_NAME. Any rcode but NOERROR: None.
    - A PTR record is read only at the name the answer is about (`answered_name`: the
      question's name, or the last target of its CNAME chain in this reply). One
      there is the name (its first record, when it holds more than one), whatever
      else the reply holds, PTR records at other owners included (#149).
    - A PTR record at any other owner, the chain's earlier links included (a name
      with a CNAME holds no other record), answers some other question and is never
      the name. With none at the chain's end, it makes the reply None, never
      NO_NAME, even beside an SOA that `says_no_name` would count.
    - No PTR record at all: NO_NAME if `says_no_name` for that name, else None."""
    rcode = reply.rcode()
    if rcode == dns.rcode.NXDOMAIN:
        return NO_NAME
    if rcode != dns.rcode.NOERROR:
        return None  # SERVFAIL, REFUSED: no answer about the name
    name = answered_name(reply, question)
    if name is None:
        return None  # a CNAME chain that loops
    ptrs = [rrset for rrset in reply.answer if rrset.rdtype == dns.rdatatype.PTR and len(rrset)]
    for rrset in ptrs:
        if rrset.name == name:
            return rrset[0].target.to_text()
    if ptrs:
        return None  # a PTR record at an owner the question does not reach
    return NO_NAME if says_no_name(reply, name) else None


def read_a_reply(reply: dns.message.Message, question: "dns.name.Name") -> Addresses | NoName | None:
    """What a reply to the A query for `question` says (the forward-DNS check, #20): the
    addresses, NO_NAME when it says the name has none, or None when it gives no usable
    answer and the next configured server is asked. Read as `read_reply` reads a PTR
    reply, by the same rules:

    - NXDOMAIN: NO_NAME. Any rcode but NOERROR: None.
    - The A records are read only at the name the answer is about (`answered_name`: the
      question's name, or the last target of its CNAME chain in this reply), and all of
      them there are the answer, whatever else the reply holds.
    - A records at any other owner and none there: None, never NO_NAME.
    - No A record at all: NO_NAME if `says_no_name` for that name (NODATA: an SOA of a
      zone that holds it, or nothing at all), else None (a referral, an alias left
      unresolved, an SOA of another zone).

    Only A records are asked for and read: v1 is IPv4-only, and a host's address is an
    IPv4 address, so an AAAA record could only ever say "not this host". A name with
    only AAAA records answers NODATA here, and is a name that does not resolve to an
    IPv4 address."""
    rcode = reply.rcode()
    if rcode == dns.rcode.NXDOMAIN:
        return NO_NAME
    if rcode != dns.rcode.NOERROR:
        return None
    name = answered_name(reply, question)
    if name is None:
        return None  # a CNAME chain that loops
    records = [rrset for rrset in reply.answer if rrset.rdtype == dns.rdatatype.A and len(rrset)]
    found = {ipaddress.IPv4Address(rd.address) for rrset in records if rrset.name == name for rd in rrset}
    if found:
        return tuple(str(a) for a in sorted(found))
    if records:
        return None  # A records at an owner the question does not reach
    return NO_NAME if says_no_name(reply, name) else None


def no_query_sent(cause: str, what: str = "rdns") -> str:
    """The error of a lookup that could send its query to none of its servers."""
    return f"{what} stopped: no DNS query could be sent to any server ({cause})"


def usable_servers(chosen: tuple[str, ...]) -> tuple[str, ...]:
    """The servers a lookup may ask, of those `chosen`: only IP addresses are kept, and
    only then is the list cut to MAX_DNS_SERVERS, so an entry the agent cannot query (a
    DNS-over-HTTPS URL, say) never takes the place of one it can."""
    usable = []
    for server in chosen:
        try:
            ipaddress.ip_address(server)
        except ValueError:
            continue
        usable.append(server)
    return tuple(usable[:MAX_DNS_SERVERS])


def read_resolv_conf(path: str) -> tuple[str, ...]:
    """The `nameserver` entries of the resolv.conf at `path`, as dnspython parses the
    file; empty when there is none. It blocks: `DnsResolver` calls it in its thread.

    The path is looked at first (`os.stat`, following a link as an open would), and
    only a regular file of at most RESOLV_CONF_MAX bytes is handed to dnspython, which
    opens it to read and parses it: a FIFO is refused without being opened, so its
    read never waits for a writer, and so are a device, a directory and a larger file.
    That is a look before the open, not a bound on it: a file that grows between the
    two is parsed whole, and a path swapped for a FIFO in that moment blocks the read
    until a writer comes. Both need control of the host, which already decides what
    the path holds, and the thread and its time bound hold the job and the event loop
    to a second as they do for a stalled network mount (`DnsResolver._host_servers`).
    dnspython refuses a file it cannot open, one that is not UTF-8, one with no
    nameserver, and one with a nameserver that is neither an address nor an https URL:
    each gives none. Nothing is sent."""
    try:
        status = os.stat(path)
    except (OSError, ValueError):
        return ()
    if not stat.S_ISREG(status.st_mode) or status.st_size > RESOLV_CONF_MAX:
        return ()
    resolver = dns.resolver.Resolver(configure=False)
    try:
        resolver.read_resolv_conf(path)
    except (dns.exception.DNSException, OSError, ValueError):
        return ()
    return tuple(s for s in resolver.nameservers if isinstance(s, str))


class DnsResolver:
    """A PTR lookup against the job's DNS servers, or the host's own resolvers when the
    job names none (spec §5.2).

    The agent sends each query itself rather than through dnspython's resolver, whose
    failover, retransmission and TCP fallback would put datagrams on the wire that the
    packet budget never saw. Here a lookup is one UDP query per server, to at most
    MAX_DNS_SERVERS servers in turn, with no retransmission and no TCP; a truncated
    reply is not used, nor one longer than the DNS_PAYLOAD bytes the query advertised
    or one whose header claims more questions or records than a PTR answer needs,
    neither of which is parsed. Before each query it awaits `slot()`, which takes one
    packet from the budget (`MAX_PPS`, shared with the ICMP and ARP sweeps) and returns
    the seconds the lookup has left; the query goes on its own `QuerySocket`, made
    first.

    A lookup that could not be made is not a lookup that found no name: with no server
    to ask, or when every attempt failed before the query went out (until the servers
    or the lookup's own time ran out), `ptr` raises ProbeError instead of returning
    None, so the scan reports itself incomplete and the server keeps the names it has
    (spec §5.7). Nor is a lookup that got no usable answer one that found no name:
    `ptr` returns NO_NAME only for a server's answer that there is none, and None for
    the rest, and only the first lets the server clear a stored name (#100). Of the
    rest, a lookup one of whose attempts met a reply refused unread (ReplyTooLarge,
    ReplyTooManyRecords) is UnreadReplies instead of None, saying which, so the job's
    error can say the agent's own limit refused a server's reply (#164); it clears no
    name either. A lookup a later server answered after such a refusal returns its
    answer wrapped in AnsweredPastRefusal, so the job's error can say that too (#170)."""

    def __init__(
        self,
        port: int = 53,
        resolv_conf: str = RESOLV_CONF,
        read_timeout: float = RESOLV_CONF_TIMEOUT_S,
    ):
        self.port = port
        self.resolv_conf = resolv_conf
        self.read_timeout = read_timeout
        # held by the thread that reads resolv.conf, for as long as its read runs
        self._reading = threading.Lock()

    async def servers(self, job_servers: tuple[str, ...]) -> tuple[str, ...]:
        """The servers a job's lookups ask (`usable_servers`): the job's own, else the
        host's resolvers, read from resolv.conf each time this is called (once per job,
        so a corrected resolv.conf takes effect at the next job; nothing is cached).
        Empty when there is none: the job's lookups then have no server to ask."""
        return usable_servers(job_servers or await self._host_servers())

    async def _host_servers(self) -> tuple[str, ...]:
        """resolv.conf's servers (`read_resolv_conf`), read off the event loop, in a
        daemon thread of its own: a read that blocks (a stalled network mount, or a FIFO
        put at the path after `read_resolv_conf` looked at it) holds
        neither the loop (check-ins, the job's deadline, the scan's backstop) nor the
        agent's exit. The job waits for it at most `read_timeout`, and a read that has
        not finished by then gives no server. While a read is still running no other
        starts, and every job until it ends finds no server, so at most one thread per
        resolver (the agent makes one) is ever held. Correcting the file does not end
        that: the blocked operation must return, or the agent be restarted. A job that
        names its own DNS servers reads no file, and is not affected. `scan.run` waits
        for this no longer than the job has left."""
        if not self._reading.acquire(blocking=False):
            return ()
        loop = asyncio.get_running_loop()
        done: asyncio.Future = loop.create_future()

        def settle(servers: tuple[str, ...]) -> None:
            if not done.done():
                done.set_result(servers)

        def read() -> None:
            servers: tuple[str, ...] = ()
            try:
                servers = read_resolv_conf(self.resolv_conf)
            except Exception:
                pass  # whatever went wrong, the read gave no server to ask
            finally:
                self._reading.release()
            try:
                loop.call_soon_threadsafe(settle, servers)
            except RuntimeError:
                pass  # the loop has closed: nothing waits for this read any more

        try:
            threading.Thread(target=read, name="resolv.conf", daemon=True).start()
        except RuntimeError:
            # no thread to be had: no read, so no server
            self._reading.release()
            return ()
        try:
            return await asyncio.wait_for(done, self.read_timeout)
        except TimeoutError:
            return ()

    async def ptr(
        self, address: Address, servers: tuple[str, ...], slot: Slot
    ) -> str | NoName | AnsweredPastRefusal | UnreadReplies | None:
        """The PTR name of `address`, or NO_NAME when a server says there is none
        (NXDOMAIN, or NOERROR without a PTR record that `says_no_name`), as one reply
        is read by `read_reply`, or None when
        no server asked gave a usable answer (silence, SERVFAIL, REFUSED, another rcode,
        a referral, a CNAME the reply leaves unresolved, a PTR record at an owner the
        question does not reach, an SOA for a zone that does not hold the name, a
        truncated, malformed or mismatched reply, a reply longer than DNS_PAYLOAD or one
        claiming more questions or records than a PTR answer needs, neither of which is
        parsed), or when the lookup's time ran out after a query was sent (or before its first attempt, which only
        the job's deadline does). In place of None it returns UnreadReplies when any of
        those attempts ended with a reply refused unread for its size or its header's
        counts (`no_usable_answer`), and in place of a name or NO_NAME it returns
        AnsweredPastRefusal when an attempt before the answer ended so (`answered`,
        #170). Raises ProbeError when there is no server to ask, or when no query was
        sent before the servers or the lookup's time ran out."""
        return await self._ask(
            dns.reversename.from_address(str(address)), dns.rdatatype.PTR, servers, slot, read_reply, "rdns"
        )

    async def a(
        self, name: str, servers: tuple[str, ...], slot: Slot
    ) -> Addresses | NoName | AnsweredPastRefusal | UnreadReplies | None:
        """The IPv4 addresses of `name` (the forward-DNS check, #20), or NO_NAME when a
        server says it has none (NXDOMAIN, or NOERROR without an A record that
        `says_no_name`), as one reply is read by `read_a_reply`; otherwise exactly as
        `ptr`: the same queries (one per server, paced, EDNS at DNS_PAYLOAD, no
        retransmission or TCP), the same replies refused unread, None or UnreadReplies
        for no usable answer, AnsweredPastRefusal for a later server's answer, and
        ProbeError when no query could be sent, its error saying fdns. `name` is a
        hostname (`protocol.fdns_name`), asked as an absolute name for its A records
        only."""
        # dnspython makes the text an absolute name (`make_query`); the question it holds
        # is what the reply is read against
        return await self._ask(name, dns.rdatatype.A, servers, slot, read_a_reply, "fdns")

    async def _ask(self, name, rdtype, servers: tuple[str, ...], slot: Slot, read, what: str):
        """One lookup: the query for `name` and `rdtype` to each of `servers` in turn,
        until a reply `read` finds usable (`ptr` says how each attempt ends)."""
        if not servers:
            raise ProbeError(no_dns_server(what))
        asked = False
        unsent: str | None = None
        # whether an attempt ended with a reply from its server refused unread: only
        # the query's own server can end an attempt so (QuerySocket), so a datagram
        # from anywhere else is never counted here
        too_large = too_many_records = False
        for server in servers[:MAX_DNS_SERVERS]:
            # the query's socket is made before its slot is taken, so the query is
            # handed to the kernel in the step its slot is granted (QuerySocket). An
            # address that is not one, or no socket of its family on this host, is an
            # attempt that sent nothing, and takes no slot: the next server
            try:
                family = socket.AF_INET6 if ipaddress.ip_address(server).version == 6 else socket.AF_INET
                sock = await QuerySocket.made(family)
            except (OSError, ValueError) as exc:
                unsent = type(exc).__name__
                continue
            try:
                left = await slot()
                if left <= 0:
                    # the lookup's time is up. After a query was sent, that is a lookup
                    # that asked and got no answer. After only attempts that sent
                    # nothing, it is not a lookup at all. With no attempt yet, the
                    # job's deadline has passed (a lookup's own time starts at its first
                    # slot, capped by the deadline), and the scan reports that cut-off
                    if not asked and unsent is not None:
                        raise ProbeError(no_query_sent(unsent, what))
                    return no_usable_answer(too_large, too_many_records)
                # A fresh query, so a fresh id, for every datagram. It carries EDNS(0)
                # advertising a DNS_PAYLOAD (1232-byte) UDP payload, so a resolver can
                # send a PTR answer whole instead of truncating it (a truncated reply
                # is not used); without EDNS a plain query allows only 512 bytes.
                # Recursion desired, no DNSSEC. The advertised payload is also the
                # most the agent reads: a longer reply from the server is not parsed
                # (QuerySocket, #153), nor one whose header claims more than a PTR
                # answer needs (#160), and the next server is asked.
                query = dns.message.make_query(
                    name, rdtype, use_edns=0, payload=DNS_PAYLOAD, want_dnssec=False,
                    flags=dns.flags.RD,
                )
                try:
                    reply = await dns.asyncquery.udp(
                        query,
                        server,
                        timeout=left,
                        port=self.port,
                        sock=sock,
                        # a reply from another source address or port is waited out
                        # until `left`; one from the server with the wrong id is a bad
                        # response (ignore_errors is off), so the next server is asked
                        ignore_unexpected=True,
                        # a truncated reply is not used, PTR record or not, and there
                        # is no TCP retry: it is treated as no answer, and the next
                        # server is asked
                        raise_on_truncation=True,
                        # dnspython's default, stated: trailing bytes make a reply bad
                        ignore_trailing=False,
                    )
                except (dns.exception.DNSException, TimeoutError) as exc:
                    # no answer in time, a truncated, broken or over-size one
                    # (ReplyTooLarge), or one claiming too many records
                    # (ReplyTooManyRecords): the query was sent, and the next server
                    # is asked. (TimeoutError is an OSError: caught first.) The two
                    # refusals are remembered for the job's error, whether a later
                    # server answers or not (#164, #170)
                    asked = True
                    too_large = too_large or isinstance(exc, ReplyTooLarge)
                    too_many_records = too_many_records or isinstance(exc, ReplyTooManyRecords)
                    continue
                except (OSError, ValueError) as exc:
                    # the query was not sent: the kernel refused it (no route, a port
                    # it will not send to), or the socket could not take it at once
                    # (QuerySocket.sendto). An ICMP error the kernel reported for the
                    # socket after the query went would surface here too. The next
                    # server
                    unsent = type(exc).__name__
                    continue
            finally:
                sock.close()
            asked = True
            answer = read(reply, query.question[0].name)
            if answer is None:
                # SERVFAIL, REFUSED, a referral, an alias left unresolved, or records
                # that do not answer the question: the next server
                continue
            return answered(answer, too_large, too_many_records)
        if not asked:
            # every server was tried, and none could be sent the query
            raise ProbeError(no_query_sent(unsent or "unknown", what))
        return no_usable_answer(too_large, too_many_records)
