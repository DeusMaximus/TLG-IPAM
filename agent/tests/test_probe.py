"""The real PTR resolver against DNS responders on loopback, with the timestamps of the
datagrams that actually arrive: every query a lookup sends, failover included, comes
out of the job's one packet budget (finding 1 of the briefed review of PR #70).
Nothing here leaves 127.0.0.1 or ::1."""

import asyncio
import errno
import ipaddress
import itertools
import os
import socket
import stat
import struct
import sys
import threading
import time

import dns.asyncquery
import dns.exception
import dns.flags
import dns.message
import dns.name
import dns.rcode
import dns.rdatatype
import dns.resolver
import dns.reversename
import dns.rrset
import pytest

from conftest import job
from tlg_ipam_agent import guard, probe, scan
from tlg_ipam_agent.config import parse_allowed
from tlg_ipam_agent.probe import (
    MAX_DNS_SERVERS,
    NO_DNS_SERVER,
    RESOLV_CONF_MAX,
    DnsResolver,
    ProbeError,
    no_query_sent,
    read_resolv_conf,
)
from tlg_ipam_agent.protocol import job_ref, parse_job

# The times these tests give a `DnsResolver` read, and the margins they hold it to. A
# read has no time of its own on a loaded machine (#115): a plain read of a small file,
# which takes a millisecond, was seen to take over 0.3 s, the thread that ran it
# not being scheduled. So a read a test expects to give servers is given PATIENT,
# and so is a call it expects to return at once, judged against half of it: a call
# that waited for its read would take all of it. Only a read a test means to see
# waited out gets BLOCKED_WAIT, and of that wait the test asserts its lower bound and
# an upper one that the stalled read, held for STALL, cannot meet: a wait with no
# bound ends only then. The event loop is judged by its longest gap against
# LOOP_GAP_MAX: load stretches a gap of milliseconds to a few hundred, and a read made
# on the loop itself holds it for the whole STALL.
BLOCKED_WAIT = 0.3
PATIENT = 5.0
LOOP_GAP_MAX = 1.0
STALL = 5.0  # the longest a stalled read in these tests is held
WAIT_MAX = 3.0  # what a wait of BLOCKED_WAIT is held to; below STALL

ANSWERS = {
    "9.0.0.10.in-addr.arpa.": "nas.home.arpa.",
    "8.0.0.10.in-addr.arpa.": "Evil\\032name<b>.home.arpa.",
}


# #144: the Responder's replies whose records the agent must read against the question
FOREIGN = {
    "foreign-soa", "cname-foreign-soa", "cname-origin-soa", "cname-out-nodata",
    "foreign-ptr", "foreign-ptr-soa", "cname-chain-ptr", "cname-chain-nodata",
}
# of those, the ones that give no usable answer, so the next configured server is asked
NOT_ANSWERING = ["foreign-soa", "cname-foreign-soa", "cname-origin-soa", "foreign-ptr", "foreign-ptr-soa"]


class Responder(asyncio.DatagramProtocol):
    """Answers PTR queries from ANSWERS. `script` gives the rcode (or "silent", or
    "truncated") for each query in turn; the last entry repeats. #100 adds "refused",
    "nxdomain", "nodata" (NOERROR with no answer, the zone's SOA in its authority),
    "empty" (NOERROR with no record in any section), "referral" (NOERROR with no
    answer, name servers in its authority and no SOA) and "malformed" (bytes that are
    no DNS message). Round 1 of the briefed review adds the CNAME of RFC 2317's
    classless delegation, `1.0-7.0.0.10.in-addr.arpa.` for 10.0.0.1, in the answer:
    with name servers for `0-7.0.0.10.in-addr.arpa.` and no SOA ("cname-referral"),
    with the target zone's SOA ("cname-nodata"), with nothing else ("cname-only"), and
    with the target's PTR record after it ("cname-ptr"). #144 adds replies whose
    records do not answer the question: an unrelated zone's SOA, with no answer
    ("foreign-soa") or after the CNAME ("cname-foreign-soa"); a CNAME to a target
    outside the reverse tree, `<label>.rev.example.com.`, with the question's own
    zone's SOA ("cname-origin-soa") or the target zone's ("cname-out-nodata"); a PTR
    record at another address's owner, alone ("foreign-ptr") or with the question's
    zone's SOA ("foreign-ptr-soa"); and a chain of two CNAMEs, from the question to
    `1.0-7.…` and on to `<label>.rev.example.com.`, with the PTR record at its end
    ("cname-chain-ptr") or the end's zone's SOA ("cname-chain-nodata"). #153 adds the
    answer padded to exactly the payload the query advertised ("at-size") and to one
    byte over it ("oversize"), and that over-size reply sent first from another port,
    the server's own answer after it ("oversize-elsewhere"). `sent` holds the length
    of every datagram it sends, from any port."""

    def __init__(self, script=("answer",)):
        self.script = list(script)
        self.arrivals: list[float] = []
        self.questions = []
        self.queries: list[dns.message.Message] = []
        self.sent: list[int] = []

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        self.arrivals.append(time.monotonic())
        query = dns.message.from_wire(data)
        self.queries.append(query)
        question = query.question[0]
        self.questions.append((question.name.to_text(), dns.rdatatype.to_text(question.rdtype)))
        action = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if action == "silent":
            return
        if action == "malformed":
            self.transport.sendto(data[:2] + b"\x81\x80\xff", addr)
            return
        reply = dns.message.make_response(query)
        if action == "servfail":
            reply.set_rcode(dns.rcode.SERVFAIL)
        elif action == "refused":
            reply.set_rcode(dns.rcode.REFUSED)
        elif action == "nxdomain":
            reply.set_rcode(dns.rcode.NXDOMAIN)
        elif action == "nodata":
            reply.authority.append(dns.rrset.from_text(
                "10.in-addr.arpa.", 60, "IN", "SOA", "ns.home.arpa. admin.home.arpa. 1 60 60 60 60"
            ))
        elif action == "empty":
            pass
        elif action == "referral":
            reply.authority.append(dns.rrset.from_text("10.in-addr.arpa.", 60, "IN", "NS", "ns.home.arpa."))
        elif action in FOREIGN:
            asked = question.name.to_text()
            label, zone = asked.split(".", 1)
            classless = "1.0-7." + zone
            outside = f"{label}.rev.example.com."
            if action in ("cname-foreign-soa", "cname-origin-soa", "cname-out-nodata"):
                target = classless if action == "cname-foreign-soa" else outside
                reply.answer.append(dns.rrset.from_text(question.name, 60, "IN", "CNAME", target))
            if action in ("cname-chain-ptr", "cname-chain-nodata"):
                reply.answer.append(dns.rrset.from_text(question.name, 60, "IN", "CNAME", classless))
                reply.answer.append(dns.rrset.from_text(classless, 60, "IN", "CNAME", outside))
            if action in ("foreign-ptr", "foreign-ptr-soa"):
                reply.answer.append(dns.rrset.from_text("99." + zone, 60, "IN", "PTR", "foreign.home.arpa."))
            if action == "cname-chain-ptr":
                reply.answer.append(dns.rrset.from_text(outside, 60, "IN", "PTR", "nas.home.arpa."))
            soa = {
                "foreign-soa": "example.com.",
                "cname-foreign-soa": "example.com.",
                "cname-origin-soa": zone,
                "foreign-ptr-soa": zone,
                "cname-out-nodata": "rev.example.com.",
                "cname-chain-nodata": "rev.example.com.",
            }.get(action)
            if soa is not None:
                reply.authority.append(dns.rrset.from_text(
                    soa, 60, "IN", "SOA", "ns.example.com. admin.example.com. 1 60 60 60 60"
                ))
        elif action.startswith("cname-"):
            target = "1.0-7." + ".".join(question.name.to_text().split(".")[1:])
            reply.answer.append(dns.rrset.from_text(question.name, 60, "IN", "CNAME", target))
            zone = target.split(".", 1)[1]
            if action == "cname-referral":
                reply.authority.append(dns.rrset.from_text(zone, 60, "IN", "NS", "ns.example.com."))
            elif action == "cname-nodata":
                reply.authority.append(dns.rrset.from_text(
                    zone, 60, "IN", "SOA", "ns.example.com. admin.example.com. 1 60 60 60 60"
                ))
            elif action == "cname-ptr":
                reply.answer.append(dns.rrset.from_text(target, 60, "IN", "PTR", "nas.home.arpa."))
        elif action == "truncated":
            reply.flags |= dns.flags.TC
        elif action in ("wrong-source", "wrong-id"):
            target = ANSWERS[question.name.to_text()]
            reply.answer.append(dns.rrset.from_text(question.name, 60, "IN", "PTR", target))
            if action == "wrong-source":
                # a forged reply from another port first, then the real one
                forged = dns.message.make_response(query)
                forged.answer.append(dns.rrset.from_text(question.name, 60, "IN", "PTR", "forged.example.com."))
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as other:
                    other.sendto(forged.to_wire(), addr)
            else:
                reply.id ^= 0x5A5A  # the right source, the wrong id
        elif action in ("at-size", "oversize", "oversize-elsewhere"):
            reply.answer.append(dns.rrset.from_text(question.name, 60, "IN", "PTR", ANSWERS[question.name.to_text()]))
            size = probe.DNS_PAYLOAD + (0 if action == "at-size" else 1)
            if action == "oversize-elsewhere":
                with socket.socket(socket.AF_INET6 if ":" in addr[0] else socket.AF_INET, socket.SOCK_DGRAM) as other:
                    other.sendto(padded(reply, size), addr)
                self.sent.append(size)
                reply = dns.message.make_response(query)
                reply.answer.append(dns.rrset.from_text(question.name, 60, "IN", "PTR", ANSWERS[question.name.to_text()]))
            else:
                wire = padded(reply, size)
                self.sent.append(len(wire))
                self.transport.sendto(wire, addr)
                return
        elif action == "truncated-ptr":
            # TC set, yet carrying a complete, usable PTR RRset
            reply.flags |= dns.flags.TC
            reply.answer.append(dns.rrset.from_text(question.name, 60, "IN", "PTR", ANSWERS[question.name.to_text()]))
        else:
            target = ANSWERS.get(question.name.to_text())
            if target is None:
                reply.set_rcode(dns.rcode.NXDOMAIN)
            else:
                reply.answer.append(dns.rrset.from_text(question.name, 60, "IN", "PTR", target))
        wire = reply.to_wire()
        self.sent.append(len(wire))
        self.transport.sendto(wire, addr)


def padded(reply: dns.message.Message, size: int) -> bytes:
    """`reply` on the wire at exactly `size` bytes, with a TXT record of filler in its
    additional section and the PTR answer untouched. dnspython would refuse to write a
    response longer than the payload its query advertised, so that limit is lifted."""
    length = 0
    for _ in range(10):
        chunks = [255] * (length // 255) + [length % 255]  # a TXT string holds 255 bytes
        text = " ".join('"' + "x" * chunk + '"' for chunk in chunks)
        reply.additional = [dns.rrset.from_text("pad.home.arpa.", 60, "IN", "TXT", text)]
        wire = reply.to_wire(max_size=65535)
        if len(wire) == size:
            return wire
        length += size - len(wire)
    raise AssertionError(f"no padding gives {size} bytes")


async def responder(host="127.0.0.1", port=0, **kwargs):
    loop = asyncio.get_running_loop()
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    transport, proto = await loop.create_datagram_endpoint(
        lambda: Responder(**kwargs), local_addr=(host, port), family=family
    )
    return transport, proto, transport.get_extra_info("sockname")[1]


async def responders_on_one_port(attempts: int = 50, make=None, **kwargs):
    """A responder on 127.0.0.1 (given `kwargs`) and a plain one on ::1, on one port
    number, for a resolver that asks both on the same port. The kernel picks the IPv4
    port; when that number is already taken on ::1, both are closed and another pair is
    tried."""
    make = make or responder
    for _ in range(attempts):
        v4_t, v4, port = await make(**kwargs)
        try:
            v6_t, v6, _ = await make(host="::1", port=port)
        except OSError as exc:
            v4_t.close()
            if exc.errno != errno.EADDRINUSE:
                raise
            continue
        return (v4_t, v4), (v6_t, v6), port
    raise AssertionError(f"no port free on both 127.0.0.1 and ::1 in {attempts} tries")


async def test_the_pair_of_responders_finds_a_port_free_on_both():
    """The collision that made the failover test fail, made on purpose: the first
    IPv4 port is one already taken on ::1, so that pair is given up and another made."""
    if not _ipv6_loopback():
        pytest.skip("no IPv6 loopback here")
    taken = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
    taken.bind(("::1", 0))
    busy = taken.getsockname()[1]
    tried: list[int] = []

    async def make(host="127.0.0.1", port=0, **kwargs):
        if host == "127.0.0.1" and not tried:
            port = busy  # the kernel's first pick, made to collide
        made = await responder(host, port, **kwargs)
        if host == "127.0.0.1":
            tried.append(made[2])
        return made

    try:
        (v4_t, _), (v6_t, _), port = await responders_on_one_port(make=make)
    finally:
        taken.close()
    v4_t.close()
    v6_t.close()
    assert tried[0] == busy and len(tried) >= 2
    assert port == tried[-1] != busy


def unpaced(seconds=2.0):
    async def slot():
        return seconds

    return slot


async def test_ptr_lookup_against_the_jobs_server():
    transport, proto, port = await responder()
    try:
        resolver = DnsResolver(port=port)
        servers = ("127.0.0.1",)
        assert await resolver.ptr(ipaddress.ip_address("10.0.0.9"), servers, unpaced()) == "nas.home.arpa."
        # NXDOMAIN is an answer, that there is no name: the next server is not asked
        assert await resolver.ptr(ipaddress.ip_address("10.0.0.7"), servers * 3, unpaced()) is probe.NO_NAME
        # the raw answer comes back; `scan` drops it with agent_protocol's rule
        assert await resolver.ptr(ipaddress.ip_address("10.0.0.8"), servers, unpaced()) == "Evil\\032name<b>.home.arpa."
        assert proto.questions[0] == ("9.0.0.10.in-addr.arpa.", "PTR")
        assert len(proto.questions) == 3
    finally:
        transport.close()


class TimedProber:
    """A fake ICMP prober that records when it 'sent', on the real clock."""

    def __init__(self, alive):
        self.alive = {ipaddress.ip_address(a) for a in alive}
        self.sent: list[float] = []

    async def ping(self, address, timeout):
        self.sent.append(time.monotonic())
        return address in self.alive


def _ipv6_loopback() -> bool:
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as s:
            s.bind(("::1", 0))
        return True
    except OSError:
        return False


@pytest.mark.parametrize("first_reply", ["servfail", "cname-foreign-soa", "foreign-ptr"])
@pytest.mark.parametrize("late", [0.0, 0.1], ids=["on-time", "first-query-late"])
@pytest.mark.parametrize("second", ["127.0.0.1", "::1"])
async def test_failover_to_the_next_server_is_paced_with_the_icmp_sweep(second, late, first_reply, monkeypatch):
    """At MAX_PPS=4 the echo request and both queries (the first server answers
    SERVFAIL, or with records that do not answer the question, #144, the second the
    name) take three slots of one budget, on the real clock.

    What is held (#87): each slot is granted at least 0.25 s after the one before, by
    the clock the pacer reads, and each packet is handed over at its grant and before
    the next grant (the echo request in its probe's first step, a query in the step its
    slot is granted, `probe.QuerySocket`). `first-query-late` wakes the first query's
    pacing sleep 0.1 s late: before #87 the second query's slot stayed where it was,
    0.15 s after the first query; now it moves with it. The arrival on loopback, which
    the responder in this same event loop records, is held only to its schedule: the
    k-th no sooner than k slots after the job started.

    Until #87 the late case delayed the hand-off to dnspython after the slot, which
    the agent's own socket no longer allows. The failure seen (once in 24 runs for
    #78, once in 200 for #99, both on macOS and both `::1`) was another dependency: the
    `::1` responder must share the port the kernel gave the 127.0.0.1 one (the resolver
    asks every server on one port), and that number can already be taken on ::1 by any
    other socket on the machine (`EADDRINUSE`). `responders_on_one_port` tries others."""
    if second == "::1" and not _ipv6_loopback():
        pytest.skip("no IPv6 loopback here")
    grants: list[float] = []
    handed: list[float] = []

    class Recording(scan.Pacer):
        async def wait(self):
            await super().wait()
            grants.append(self._last)

    real_sendto = probe.QuerySocket.sendto

    async def sendto(self, what, destination, timeout):
        handed.append(time.monotonic())
        return await real_sendto(self, what, destination, timeout)

    monkeypatch.setattr(scan, "Pacer", Recording)
    monkeypatch.setattr(probe.QuerySocket, "sendto", sendto)
    sleeps: list[float] = []

    async def sleep(seconds):
        sleeps.append(seconds)
        await asyncio.sleep(seconds + (late if len(sleeps) == 1 else 0.0))

    if second == "127.0.0.1":
        first_t, first, port = await responder(script=(first_reply, "answer"))
        second_t, second_p = first_t, first
    else:
        (first_t, first), (second_t, second_p), port = await responders_on_one_port(script=(first_reply, "answer"))
    try:
        raw = job(
            ranges=[{"start": "10.0.0.9", "end": "10.0.0.9"}],
            dns_servers=["127.0.0.1", second],
        )
        parsed = parse_job(job_ref(raw), raw)
        prober = TimedProber(alive=["10.0.0.9"])
        # no later than the pacer's first slot, which scan.run takes after this
        started = time.monotonic()
        result = await scan.run(
            parsed,
            guard.plan(parsed, parse_allowed(None)),
            prober=prober,
            resolver=DnsResolver(port=port),
            max_pps=4,
            clock=scan.Clock(sleep=sleep),
        )
        assert result["observations"] == [{"ip": "10.0.0.9", "rdns_name": "nas.home.arpa"}]
        assert result["complete"] is True
        assert len(grants) == 3 and len(prober.sent) == 1 and len(handed) == 2
        # a slot apart, by the pacer's own clock: no tolerance
        assert all(b - a >= 0.25 - 1e-9 for a, b in zip(grants, grants[1:])), grants
        if late:
            assert grants[1] - grants[0] >= 0.25 + late - 1e-9
        # each packet handed over at its grant, and before the next grant
        sends = prober.sent + handed
        assert all(g <= t for g, t in zip(grants, sends)), (grants, sends)
        assert all(t < g for t, g in zip(sends, grants[1:])), (grants, sends)
        arrivals = sorted(prober.sent + first.arrivals + (second_p.arrivals if second_p is not first else []))
        assert len(arrivals) == 3
        # 1 ms for the event loop, whose timers may fire up to its clock resolution early
        late_by = [round(t - (started + k * 0.25), 3) for k, t in enumerate(arrivals)]
        assert all(d >= -0.001 for d in late_by), late_by
    finally:
        first_t.close()
        if second_t is not first_t:
            second_t.close()


async def test_a_silent_server_gets_one_query_and_at_most_three_servers_are_tried():
    """No retransmission: one datagram per server, and no more than MAX_DNS_SERVERS
    servers however many the job names."""
    transport, proto, port = await responder(script=("silent",))
    try:
        resolver = DnsResolver(port=port)
        name = await resolver.ptr(ipaddress.ip_address("10.0.0.9"), ("127.0.0.1",) * 7, unpaced(0.2))
        assert name is None
        await asyncio.sleep(0.3)
        assert len(proto.arrivals) == MAX_DNS_SERVERS == 3
    finally:
        transport.close()


@pytest.mark.parametrize("action", ["nxdomain", "nodata", "empty", "cname-nodata", "cname-out-nodata", "cname-chain-nodata"])
async def test_an_answer_that_there_is_no_name_is_no_name_and_ends_the_lookup(action):
    """#100: NXDOMAIN, or NOERROR without a PTR record that says so (`says_no_name`:
    the SOA of the zone that holds the name the answer is about, after a CNAME chain
    or not, #144, or an empty answer and no authority), is the one answer that lets
    the server clear a stored name; no further server is asked."""
    transport, proto, port = await responder(script=(action,))
    try:
        answer = await DnsResolver(port=port).ptr(ipaddress.ip_address("10.0.0.9"), ("127.0.0.1",) * 3, unpaced())
        assert answer is probe.NO_NAME
        assert len(proto.arrivals) == 1
    finally:
        transport.close()


@pytest.mark.parametrize(
    "action",
    ["silent", "servfail", "refused", "referral", "cname-referral", "cname-only", "malformed", "truncated",
     *NOT_ANSWERING],
)
async def test_a_lookup_no_server_answered_usably_is_no_answer_not_no_name(action):
    """#100: silence, SERVFAIL, REFUSED, a referral (after a CNAME too: round 1 of the
    briefed review, finding 1), a CNAME the reply leaves unresolved, malformed bytes
    and a truncated reply say nothing about the name; nor (#144) does an SOA for a
    zone that does not hold the name the answer is about, or a PTR record at an owner
    the question does not reach. Every server is asked in turn, once each, and
    the lookup is None, which leaves a stored name as it is."""
    transport, proto, port = await responder(script=(action,))
    try:
        answer = await DnsResolver(port=port).ptr(ipaddress.ip_address("10.0.0.9"), ("127.0.0.1",) * 3, unpaced(0.3))
        assert answer is None
        await asyncio.sleep(0.05)
        assert len(proto.arrivals) == 3
    finally:
        transport.close()


UNUSABLE_FIRST = ["silent", "servfail", "refused", "referral", "cname-referral", "cname-only", "malformed",
                  *NOT_ANSWERING]


@pytest.mark.parametrize("first", UNUSABLE_FIRST)
@pytest.mark.parametrize("then, expected", [("answer", "nas.home.arpa."), ("nxdomain", probe.NO_NAME)])
async def test_a_later_servers_answer_decides_after_one_without_a_usable_one(first, then, expected):
    """The next configured server is asked after one without a usable answer, and its
    answer decides, a name or that there is none."""
    transport, proto, port = await responder(script=(first, then))
    try:
        answer = await DnsResolver(port=port).ptr(ipaddress.ip_address("10.0.0.9"), ("127.0.0.1",) * 2, unpaced(0.3))
        assert answer == expected if isinstance(expected, str) else answer is expected
        assert len(proto.arrivals) == 2
    finally:
        transport.close()


@pytest.mark.parametrize("action", ["cname-ptr", "cname-chain-ptr"])
async def test_a_cname_with_its_ptr_record_is_the_name(action):
    """The PTR record at the end of the question's CNAME chain in the reply is the
    name, one link (RFC 2317) or two, the second outside the reverse tree (#144)."""
    transport, proto, port = await responder(script=(action,))
    try:
        assert await DnsResolver(port=port).ptr(ipaddress.ip_address("10.0.0.1"), ("127.0.0.1",), unpaced()) == "nas.home.arpa."
        assert len(proto.arrivals) == 1
    finally:
        transport.close()


async def test_a_delegation_after_a_cname_goes_on_to_a_different_server():
    """Round 1 of the briefed review, finding 1, on two distinct servers: 127.0.0.1
    answers with a CNAME and a delegation, ::1 with the PTR record. At `dfae349` the
    first ended the lookup as "no name" and ::1 was never asked."""
    (v4_t, v4), (v6_t, v6), port = await responders_on_one_port(script=("cname-referral",))
    try:
        answer = await DnsResolver(port=port).ptr(ipaddress.ip_address("10.0.0.9"), ("127.0.0.1", "::1"), unpaced(0.3))
        assert answer == "nas.home.arpa."
        assert len(v4.arrivals) == 1 and len(v6.arrivals) == 1
    finally:
        v4_t.close()
        v6_t.close()


def _reply(answer=(), authority=(), rcode=dns.rcode.NOERROR):
    query = dns.message.make_query(_Q, dns.rdatatype.PTR)
    reply = dns.message.make_response(query)
    reply.set_rcode(rcode)
    for section, records in ((reply.answer, answer), (reply.authority, authority)):
        for owner, kind, data in records:
            section.append(dns.rrset.from_text(owner, 60, "IN", kind, data))
    return reply


def _soa(zone):
    return (zone, "SOA", "ns.example.com. admin.example.com. 1 60 60 60 60")


# the question, for 10.0.0.1; its zone; an RFC 2317 target inside the reverse tree and
# one outside it (RFC 2317 §5.2), with their zones
_Q = "1.0.0.10.in-addr.arpa."
_Q_ZONE = "0.0.10.in-addr.arpa."
_IN = "1.0-7.0.0.10.in-addr.arpa."
_IN_ZONE = "0-7.0.0.10.in-addr.arpa."
_OUT = "1.rev.example.com."
_OUT_ZONE = "rev.example.com."
_CNAME = (_Q, "CNAME", _IN)
_CNAME_OUT = (_Q, "CNAME", _OUT)
_CHAIN = (_CNAME, (_IN, "CNAME", _OUT))
_NS = (_IN_ZONE, "NS", "ns.example.com.")
_SOA = _soa(_IN_ZONE)
_NAME = "nas.home.arpa."


def _ptr(owner, target=_NAME):
    return (owner, "PTR", target)


# (answer, authority, rcode, what the reply says): a name, NO_NAME, or None (no usable
# answer: the stored name is kept and the next server asked)
READINGS = {
    # a PTR record is read at the name the answer is about, and only there (#144)
    "ptr": ((_ptr(_Q),), (), None, _NAME),
    "ptr-any-case": ((_ptr(_Q.upper()),), (), None, _NAME),
    "ptr-with-another-beside": ((_ptr(_Q), _ptr("99.0.0.10.in-addr.arpa.", "other.home.arpa.")), (), None, _NAME),
    "cname-ptr": ((_CNAME, _ptr(_IN)), (), None, _NAME),  # RFC 2317
    "cname-out-ptr": ((_CNAME_OUT, _ptr(_OUT)), (), None, _NAME),
    "chain-ptr": ((*_CHAIN, _ptr(_OUT)), (), None, _NAME),
    "chain-ptr-and-soa": ((*_CHAIN, _ptr(_OUT)), (_soa(_OUT_ZONE),), None, _NAME),
    "foreign-ptr": ((_ptr("99.0.0.10.in-addr.arpa.", "foreign.home.arpa."),), (), None, None),
    "foreign-ptr-soa": ((_ptr("99.0.0.10.in-addr.arpa.", "foreign.home.arpa."),), (_soa(_Q_ZONE),), None, None),
    "ptr-at-the-alias": ((_CNAME, _ptr(_Q)), (), None, None),  # the chain goes on past it
    "ptr-mid-chain": ((*_CHAIN, _ptr(_IN)), (), None, None),
    "ptr-off-the-chain": ((_CNAME, _ptr(_OUT)), (), None, None),
    "cname-loop": ((_CNAME, (_IN, "CNAME", _Q)), (_soa(_Q_ZONE),), None, None),
    # NXDOMAIN, as before #144
    "nxdomain": ((), (), dns.rcode.NXDOMAIN, probe.NO_NAME),
    "nxdomain-cname": ((_CNAME,), (_SOA,), dns.rcode.NXDOMAIN, probe.NO_NAME),
    "servfail": ((), (), dns.rcode.SERVFAIL, None),
    # no PTR record: an SOA counts only for a zone that holds the name (#144)
    "empty": ((), (), None, probe.NO_NAME),  # nothing at all: the name has no PTR record
    "soa": ((), (_soa(_Q_ZONE),), None, probe.NO_NAME),  # NODATA
    "ancestor-soa": ((), (_soa("10.in-addr.arpa."),), None, probe.NO_NAME),
    "soa-at-the-name": ((), (_soa(_Q),), None, probe.NO_NAME),
    "ns-and-soa": ((), ((_Q_ZONE, "NS", "ns.example.com."), _soa(_Q_ZONE)), None, probe.NO_NAME),
    "foreign-soa": ((), (_soa("example.com."),), None, None),
    "sibling-soa": ((), (_SOA,), None, None),  # 0-7.0.0.10.in-addr.arpa. does not hold 1.0.0.10
    "foreign-and-own-soa": ((), (_soa("example.com."), _soa(_Q_ZONE)), None, probe.NO_NAME),
    "referral": ((), (_NS,), None, None),
    "foreign-soa-and-ns": ((), (_soa("example.com."), _NS), None, None),
    "cname-soa": ((_CNAME,), (_SOA,), None, probe.NO_NAME),  # the alias's target has no PTR record
    "cname-ns-and-soa": ((_CNAME,), (_NS, _SOA), None, probe.NO_NAME),
    "cname-origin-soa-in-tree": ((_CNAME,), (_soa(_Q_ZONE),), None, probe.NO_NAME),  # that zone holds the target
    "cname-out-target-soa": ((_CNAME_OUT,), (_soa(_OUT_ZONE),), None, probe.NO_NAME),
    "cname-out-origin-soa": ((_CNAME_OUT,), (_soa(_Q_ZONE),), None, None),  # says nothing of the target
    "cname-foreign-soa": ((_CNAME,), (_soa("example.com."),), None, None),
    "chain-soa": (_CHAIN, (_soa(_OUT_ZONE),), None, probe.NO_NAME),
    "chain-first-link-soa": (_CHAIN, (_SOA,), None, None),
    "cname-referral": ((_CNAME,), (_NS,), None, None),  # a referral after the alias (RFC 2317)
    "cname-only": ((_CNAME,), (), None, None),  # an alias the reply does not resolve
    "other-type": (((_Q, "TXT", '"x"'),), (), None, None),  # an answer about something else
    "other-type-soa": (((_Q, "TXT", '"x"'),), (_soa(_Q_ZONE),), None, probe.NO_NAME),
}


@pytest.mark.parametrize("case", list(READINGS))
def test_what_a_reply_says(case):
    """#144: what one reply says, read against the question. A PTR record is the name
    only at the question's name or the end of its CNAME chain in the reply; an SOA is
    a negative answer only for a zone that holds that name. Anything else that
    does not answer the question is no usable answer. The rows for NXDOMAIN, a PTR at
    the question or the chain's end, and the right zone's SOA (with a CNAME before it
    or not) are #100's, and read as they did."""
    answer, authority, rcode, says = READINGS[case]
    reply = _reply(answer, authority, rcode if rcode is not None else dns.rcode.NOERROR)
    got = probe.read_reply(reply, dns.name.from_text(_Q))
    assert got == says if isinstance(says, str) else got is says


@pytest.mark.parametrize("chain", [(), (_CNAME,)], ids=["at-the-question", "at-the-chain-end"])
def test_of_several_ptr_records_at_the_answered_name_the_first_is_read(chain):
    """#149: a name may hold more than one PTR record (RFC 2181 §10); the one read is
    the first the reply carries there, so the same two in the other order give the
    other name. Each reply goes through the wire and back, as a server's would; the two
    records are written as rrsets of their own, since dnspython shuffles the records
    of one rrset as it writes them, and its parser joins them in the order sent."""
    owner = _IN if chain else _Q
    for first, second in (("nas.home.arpa.", "nas-2.home.arpa."), ("nas-2.home.arpa.", "nas.home.arpa.")):
        reply = _reply((*chain, _ptr(owner, first), _ptr(owner, second)))
        reply = dns.message.from_wire(reply.to_wire())
        assert len(reply.answer[-1]) == 2  # one PTR rrset of two records at that name
        assert [rdata.target.to_text() for rdata in reply.answer[-1]] == [first, second]  # wire order kept
        assert probe.read_reply(reply, dns.name.from_text(_Q)) == first


def _colliding_names(count, size=20):
    """`count` distinct names made of the same `size` bytes, split into labels at
    different places (`aaaa.`, `a.aaa.`, `aa.aa.`, …): the i-th is split after each
    byte whose bit is set in i."""
    names = []
    for split in range(count):
        labels, length = [], 1
        for at in range(size - 1):
            if split >> at & 1:
                labels.append("a" * length)
                length = 1
            else:
                length += 1
        labels.append("a" * length)
        names.append(dns.name.from_text(".".join(labels) + "."))
    return names


def _calls_reading(reply):
    """The Python function calls `read_reply` makes, all of them and those of
    `Name.fullcompare` (what a name comparison costs), counted by a profiler. The
    profiler hook that was installed before (a coverage or profiling run's, or none)
    is put back afterwards, whether or not the read raises."""
    calls = {"all": 0, "fullcompare": 0}
    fullcompare = dns.name.Name.fullcompare.__code__

    def count(frame, event, arg):
        if event == "call":
            calls["all"] += 1
            calls["fullcompare"] += frame.f_code is fullcompare

    previous = sys.getprofile()
    sys.setprofile(count)
    try:
        got = probe.read_reply(reply, dns.name.from_text(_Q))
    finally:
        sys.setprofile(previous)
    return got, calls


def test_counting_the_calls_puts_back_the_profiler_hook_it_found(monkeypatch):
    """The count above borrows the thread's profiler hook and gives back the one it
    found, after a read that returns and after one that raises, so a coverage or
    profiling run, or another test's hook, is not left switched off."""
    def hook(frame, event, arg):
        pass

    def raising(reply, question):
        raise RuntimeError("the read failed")

    found = sys.getprofile()
    sys.setprofile(hook)
    try:
        got, calls = _calls_reading(_reply((_ptr(_Q),)))
        assert got == _NAME and calls["all"] > 0
        assert sys.getprofile() is hook
        monkeypatch.setattr(probe, "read_reply", raising)
        with pytest.raises(RuntimeError):
            _calls_reading(_reply((_ptr(_Q),)))
        assert sys.getprofile() is hook
    finally:
        sys.setprofile(found)


@pytest.mark.parametrize("ends", ["ptr", "loop"])
def test_a_chain_of_colliding_names_costs_work_in_step_with_its_links(ends):
    """#149: a reply whose CNAME chain runs through distinct names that dnspython hashes
    alike costs work that grows with the number of links, not with its square. Work is
    counted as the Python calls `read_reply` makes (a dictionary keyed by such names
    compares them one by one, in `Name.fullcompare`), never timed. Each reply goes
    through the wire and back, as a server's would; that parse is dnspython's cost
    and is not counted."""
    names = _colliding_names(256)
    assert len({name.to_digestable() for name in names}) == len(names)  # distinct names
    assert len({hash(name) for name in names}) == 1  # the premise: they hash alike

    work = {}
    for links in (64, 256):
        chain = names[:links]
        query = dns.message.make_query(_Q, dns.rdatatype.PTR)
        reply = dns.message.make_response(query)
        for owner, target in zip([dns.name.from_text(_Q), *chain], chain):
            reply.answer.append(dns.rrset.from_text(owner, 60, "IN", "CNAME", target.to_text()))
        if ends == "ptr":
            reply.answer.append(dns.rrset.from_text(chain[-1], 60, "IN", "PTR", _NAME))
        else:
            reply.answer.append(dns.rrset.from_text(chain[-1], 60, "IN", "CNAME", chain[0].to_text()))
        got, calls = _calls_reading(dns.message.from_wire(reply.to_wire()))
        assert got == _NAME if ends == "ptr" else got is None
        assert calls["fullcompare"] <= 1  # the PTR record's owner against the chain's end
        work[links] = calls["all"]
    # four times the links, about four times the work (a square would be sixteen)
    assert work[256] <= 5 * work[64], work


async def test_a_truncated_answer_does_not_fall_back_to_tcp():
    transport, proto, port = await responder(script=("truncated",))
    connections = []

    async def accept(reader, writer):
        connections.append(1)
        writer.close()

    tcp = await asyncio.start_server(accept, "127.0.0.1", port)
    try:
        resolver = DnsResolver(port=port)
        assert await resolver.ptr(ipaddress.ip_address("10.0.0.9"), ("127.0.0.1",), unpaced()) is None
        await asyncio.sleep(0.1)
        assert connections == []
        assert len(proto.arrivals) == 1
    finally:
        transport.close()
        tcp.close()


async def test_no_query_once_the_lookup_has_no_time_left():
    transport, proto, port = await responder()
    try:
        assert await DnsResolver(port=port).ptr(ipaddress.ip_address("10.0.0.9"), ("127.0.0.1",), unpaced(0)) is None
        await asyncio.sleep(0.05)
        assert proto.arrivals == []
    finally:
        transport.close()


async def test_a_truncated_reply_is_not_used_even_with_a_ptr_record():
    """TC=1 with a usable PTR RRset gives no name, and no TCP: only the next server's
    reply counts (briefed round 2, finding 3)."""
    transport, proto, port = await responder(script=("truncated-ptr", "answer"))
    connections = []

    async def accept(reader, writer):
        connections.append(1)
        writer.close()

    tcp = await asyncio.start_server(accept, "127.0.0.1", port)
    try:
        resolver = DnsResolver(port=port)
        address = ipaddress.ip_address("10.0.0.9")
        assert await resolver.ptr(address, ("127.0.0.1",), unpaced()) is None
        # the same server listed twice: the second query gets a whole answer
        proto.script = ["truncated-ptr", "answer"]
        assert await resolver.ptr(address, ("127.0.0.1", "127.0.0.1"), unpaced()) == "nas.home.arpa."
        await asyncio.sleep(0.1)
        assert connections == [] and len(proto.arrivals) == 3
    finally:
        transport.close()
        tcp.close()


async def test_what_a_query_carries_on_the_wire():
    """Set explicitly, and pinned here: EDNS(0) advertising a 1232-byte UDP payload (so
    a resolver can send a PTR answer whole rather than truncate it, which the agent
    would not use), recursion desired, no DNSSEC. Since #153 the advertised payload is
    also the most the agent reads (`probe.DNS_PAYLOAD`)."""
    transport, proto, port = await responder()
    try:
        await DnsResolver(port=port).ptr(ipaddress.ip_address("10.0.0.9"), ("127.0.0.1",), unpaced())
        [query] = proto.queries
        assert query.edns == 0 and query.payload == probe.DNS_PAYLOAD == 1232
        assert query.flags & dns.flags.RD
        assert not query.ednsflags & dns.flags.DO
        assert len(query.to_wire()) == 50
    finally:
        transport.close()


async def test_a_reply_from_another_port_is_waited_out():
    """`ignore_unexpected=True`: a reply from the wrong source address or port is not
    taken as the answer; the query keeps waiting for the server's own."""
    transport, proto, port = await responder(script=("wrong-source",))
    try:
        name = await DnsResolver(port=port).ptr(ipaddress.ip_address("10.0.0.9"), ("127.0.0.1",), unpaced())
        assert name == "nas.home.arpa."
        assert len(proto.arrivals) == 1
    finally:
        transport.close()


async def test_a_reply_with_the_wrong_id_moves_to_the_next_server():
    """From the right source but the wrong id, a reply is a bad response
    (`ignore_errors` is off): it is not used, and the next server is asked."""
    transport, proto, port = await responder(script=("wrong-id", "answer"))
    try:
        resolver = DnsResolver(port=port)
        address = ipaddress.ip_address("10.0.0.9")
        assert await resolver.ptr(address, ("127.0.0.1", "127.0.0.1"), unpaced()) == "nas.home.arpa."
        assert len(proto.arrivals) == 2
        proto.script = ["wrong-id"]
        assert await resolver.ptr(address, ("127.0.0.1",), unpaced(0.5)) is None
    finally:
        transport.close()


# --- a reply longer than the payload the query advertised is not parsed (#153) ---


def parsed_sizes(monkeypatch) -> list[int]:
    """The length of every message handed to `dns.message.from_wire` from now on, the
    responders' queries included. One longer than the advertised payload fails the test
    where it would be parsed: the AssertionError is no error the resolver catches."""
    seen: list[int] = []
    real = dns.message.from_wire

    def from_wire(wire, *args, **kwargs):
        seen.append(len(wire))
        if len(wire) > probe.DNS_PAYLOAD:
            raise AssertionError(f"a {len(wire)}-byte reply reached dnspython's parser")
        return real(wire, *args, **kwargs)

    monkeypatch.setattr(dns.message, "from_wire", from_wire)
    return seen


def received(monkeypatch) -> list[tuple[int, int]]:
    """The length and source port of every datagram a query's socket receives."""
    got: list[tuple[int, int]] = []
    real = probe.QuerySocket.datagram_received

    def datagram_received(self, data, addr):
        got.append((len(data), addr[1]))
        real(self, data, addr)

    monkeypatch.setattr(probe.QuerySocket, "datagram_received", datagram_received)
    return got


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
async def test_a_reply_over_the_advertised_payload_is_not_parsed_and_the_next_server_is_asked(host, monkeypatch):
    """One byte over the payload the query advertised, from the server's own address
    and port, a reply is never parsed: the attempt ends at once as no usable answer and
    the next configured server is asked, its answer deciding. With no other server the
    lookup is None, which keeps a stored name. Each query takes its one slot, and none
    is sent after the lookup."""
    if host == "::1" and not _ipv6_loopback():
        pytest.skip("no IPv6 loopback here")
    seen = parsed_sizes(monkeypatch)
    got = received(monkeypatch)
    transport, proto, port = await responder(host=host, script=("oversize", "answer"))
    try:
        resolver = DnsResolver(port=port)
        address = ipaddress.ip_address("10.0.0.9")
        slot, taken = counted(PATIENT)
        started = time.monotonic()
        assert await resolver.ptr(address, (host, host), slot) == "nas.home.arpa."
        # ended by the over-size reply, not by waiting out the attempt's time
        assert time.monotonic() - started < PATIENT / 2
        assert proto.sent[0] == probe.DNS_PAYLOAD + 1
        assert (probe.DNS_PAYLOAD + 1, port) in got  # it did reach the query's socket
        assert len(proto.arrivals) == 2 and len(taken) == 2

        proto.script = ["oversize"]
        started = time.monotonic()
        assert await resolver.ptr(address, (host,), slot) is None
        assert time.monotonic() - started < PATIENT / 2
        await asyncio.sleep(0.1)
        assert len(proto.arrivals) == 3 and len(taken) == 3
        assert max(seen) <= probe.DNS_PAYLOAD
    finally:
        transport.close()


async def test_a_reply_of_exactly_the_advertised_payload_is_read(monkeypatch):
    seen = parsed_sizes(monkeypatch)
    transport, proto, port = await responder(script=("at-size",))
    try:
        answer = await DnsResolver(port=port).ptr(ipaddress.ip_address("10.0.0.9"), ("127.0.0.1",) * 2, unpaced())
        assert answer == "nas.home.arpa."
        assert proto.sent == [probe.DNS_PAYLOAD] and probe.DNS_PAYLOAD in seen
        assert len(proto.arrivals) == 1
    finally:
        transport.close()


async def test_an_over_size_reply_from_another_port_is_dropped_and_the_servers_own_is_read(monkeypatch):
    """A datagram over the advertised payload from any source but the query's server
    is dropped unparsed and cannot end the attempt: the server's answer that follows it
    is read, as a forged reply of any size from another port is waited out."""
    seen = parsed_sizes(monkeypatch)
    got = received(monkeypatch)
    transport, proto, port = await responder(script=("oversize-elsewhere",))
    try:
        answer = await DnsResolver(port=port).ptr(ipaddress.ip_address("10.0.0.9"), ("127.0.0.1",), unpaced())
        assert answer == "nas.home.arpa."
        assert proto.sent[0] == probe.DNS_PAYLOAD + 1
        assert [size for size, source in got if source != port] == [probe.DNS_PAYLOAD + 1]
        assert len(proto.arrivals) == 1
        assert max(seen) <= probe.DNS_PAYLOAD
    finally:
        transport.close()


@pytest.mark.parametrize(
    "destination, source, from_server",
    [
        (("192.0.2.53", 53), ("192.0.2.53", 53), True),
        (("192.0.2.53", 53), ("192.0.2.53", 5353), False),
        (("192.0.2.53", 53), ("192.0.2.54", 53), False),
        (("2001:db8::53", 53, 0, 0), ("2001:db8:0:0::53", 53, 0, 0), True),  # one address, two spellings
        (("2001:db8::53", 53, 0, 0), ("2001:db8::53", 53, 0, 2), False),  # another scope
        (("2001:db8::53", 53, 0, 0), ("2001:db8::54", 53, 0, 0), False),
        (("224.0.0.251", 53), ("192.0.2.53", 53), False),  # dnspython would take it; not here
        (None, ("192.0.2.53", 53), False),  # nothing sent yet
    ],
)
def test_only_an_over_size_datagram_from_the_querys_server_ends_the_attempt(destination, source, from_server):
    """The over-size check judges the source as dnspython's `ignore_unexpected` does,
    or more strictly: from the server, the attempt ends (ReplyTooLarge); from anywhere
    else the datagram is dropped. A datagram of exactly the payload is queued for
    dnspython as before, from any source, so the source and id checks are its own."""
    sock = probe.QuerySocket(socket.AF_INET6 if ":" in source[0] else socket.AF_INET)
    sock._destination = destination
    sock.datagram_received(b"\0" * (probe.DNS_PAYLOAD + 1), source)
    sock.datagram_received(b"\0" * probe.DNS_PAYLOAD, source)
    queued = [sock._received.get_nowait() for _ in range(sock._received.qsize())]
    if from_server:
        assert isinstance(queued[0], probe.ReplyTooLarge) and isinstance(queued[0], dns.exception.DNSException)
        queued = queued[1:]
    assert queued == [(b"\0" * probe.DNS_PAYLOAD, source)]


def test_the_largest_ordinary_ptr_answer_fits_the_advertised_payload():
    """Why 1232 bytes stays (#153): a 253-character name behind two CNAMEs of classless
    reverse delegation (RFC 2317, nested once), with four name servers and an IPv4 and
    an IPv6 address for each beside it, for an IPv6 address (the longest question), is
    well inside the payload the query advertises. dnspython compresses it as a server
    would."""
    ptr = ".".join(["h" * 63] * 3 + ["h" * 49]) + ".example.com."
    assert len(ptr) == 254  # 253 characters and the root
    question = dns.reversename.from_address("2001:db8:ffff:ffff:ffff:ffff:ffff:ffff")
    reply = dns.message.make_response(dns.message.make_query(question, "PTR", use_edns=0, payload=probe.DNS_PAYLOAD))
    owner = question
    for _ in range(2):
        target = dns.name.Name((owner.labels[0], b"128-255", *owner.labels[1:]))
        reply.answer.append(dns.rrset.from_text(owner, 3600, "IN", "CNAME", target.to_text()))
        owner = target
    reply.answer.append(dns.rrset.from_text(owner, 3600, "IN", "PTR", ptr))
    for k in range(4):
        server = f"ns{k}.a-fairly-long-name-server-hostname.example.org."
        reply.authority.append(dns.rrset.from_text(owner.parent(), 3600, "IN", "NS", server))
        reply.additional.append(dns.rrset.from_text(server, 3600, "IN", "A", f"192.0.2.{k}"))
        reply.additional.append(dns.rrset.from_text(server, 3600, "IN", "AAAA", f"2001:db8::{k}"))
    wire = reply.to_wire(max_size=65535)
    assert len(wire) < 800 < probe.DNS_PAYLOAD
    assert probe.read_reply(dns.message.from_wire(wire), question) == ptr


def _question_entries(wire_names: list[bytes]) -> bytes:
    """A reply whose question section holds one entry per name given on the wire, each
    with the same type and class, so that the entries' keys differ only by their names."""
    body = b"".join(name + struct.pack("!HH", 65280, 1) for name in wire_names)
    return struct.pack("!HHHHHH", 0, 0x8180, len(wire_names), 0, 0, 0) + body


def _comparisons(wire: bytes) -> tuple[dns.message.Message, int]:
    """The message `from_wire` reads from `wire`, and the `Name.fullcompare` calls it
    made, counted with the profiler; the hook found is put back."""
    fullcompare = dns.name.Name.fullcompare.__code__
    compared = 0

    def count(frame, event, arg):
        nonlocal compared
        if event == "call" and frame.f_code is fullcompare:
            compared += 1

    previous = sys.getprofile()
    sys.setprofile(count)
    try:
        message = dns.message.from_wire(wire)
    finally:
        sys.setprofile(previous)
    return message, compared


def test_the_parse_of_a_reply_the_agent_reads_is_bounded_by_its_size():
    """What #153 buys, counted and never timed (the PR gives the times). dnspython
    2.9.2 indexes a reply's records by keys that hold the owner name, hashed as
    h = 9*h + byte over its labels' bytes from 0, so distinct names can hash alike and
    their keys are compared one by one. The smallest entry a reply can carry is a
    question at the root: its 1-byte name, its type and class, 5 bytes (a record of any
    other section adds a TTL and RDLENGTH, 11 bytes at the least). So a message of
    DNS_PAYLOAD bytes holds at most (DNS_PAYLOAD - 12) // 5 = 244 entries.

    No bound on the comparisons follows from that alone: 29,646, one per earlier entry
    of 244, is an estimate, since the next test shows a record can be compared with an
    earlier one more than once. It is kept here as a tripwire, not a proof: a reply
    built to the limit from colliding names that passed it would send the stated
    figures back for review.

    Two replies are built to the limit. Of root entries alone, it holds exactly 244. Of
    a name of 126 zero-byte labels, its suffixes by pointer, the root (leading zero
    bytes leave h at 0, so all 127 hash alike) and then repeats of the root, it makes
    more comparisons than any reply the PR measured, and stays under the estimate."""
    most = (probe.DNS_PAYLOAD - 12) // 5
    estimate = most * (most - 1) // 2
    assert (most, estimate) == (244, 29_646)

    roots = _question_entries([b"\x00"] * most)
    assert len(roots) == probe.DNS_PAYLOAD  # the entry count is reached exactly
    message, compared = _comparisons(roots)
    assert len(message.question) == most
    assert compared == most - 1  # each repeat meets the one equal key

    labels = 126
    names = [b"\x01\x00" * labels + b"\x00"]  # spelled once, at offset 12
    names += [struct.pack("!H", 0xC000 | (12 + 2 * i)) for i in range(labels - 1, 0, -1)]  # its suffixes
    names += [b"\x00"]  # the root, last of the distinct names
    while len(_question_entries(names + [b"\x00"])) <= probe.DNS_PAYLOAD:
        names.append(b"\x00")
    wire = _question_entries(names)
    assert probe.DNS_PAYLOAD - 5 < len(wire) <= probe.DNS_PAYLOAD
    assert len({hash(dns.name.Name((b"\x00",) * i + (b"",))) for i in range(labels + 1)}) == 1  # the premise
    message, compared = _comparisons(wire)
    assert len(message.question) == len(names) and len({rrset.name for rrset in message.question}) == labels + 1
    assert 13_000 < compared <= estimate  # quadratic in the entries; under the estimate, a tripwire


def test_two_answer_records_whose_owners_hash_alike_can_be_compared_more_than_once():
    """Why the figure above is an estimate (round 1 of the briefed review of PR #155):
    each record of the answer section is looked up in dnspython's index and then
    inserted, two dictionary probes, and one probe can meet the same colliding key more
    than once. Two answer records at distinct owners of one h (two-letter labels whose
    9*a + b agree, `aj.` and `ba.` among them) make at least two comparisons, and for
    some values of h more than two, so "once per earlier record" is not a bound."""

    def record(label: bytes) -> bytes:
        return bytes((len(label),)) + label + b"\x00" + struct.pack("!HHIH", 65280, 1, 0, 0)

    counts = []
    letters = range(ord("a"), ord("z") + 1)
    for first, second in itertools.product(letters, letters):
        other = (first + 1, second - 9)  # 9*(a+1) + (b-9) == 9*a + b
        if other[0] not in letters or other[1] not in letters:
            continue
        one, two = bytes((first, second)), bytes(other)
        assert hash(dns.name.Name((one, b""))) == hash(dns.name.Name((two, b"")))  # the premise
        wire = struct.pack("!HHHHHH", 0, 0x8180, 0, 2, 0, 0) + record(one) + record(two)
        message, compared = _comparisons(wire)
        assert len(message.answer) == 2
        counts.append(compared)
    assert min(counts) >= 2  # a lookup and an insertion
    assert max(counts) > 2  # and a probe that compared one key twice


# --- no server to ask is not a completed lookup (#78) ---


def counted(seconds=2.0):
    """An unpaced slot that counts how often it was taken."""
    taken = []

    async def slot():
        taken.append(1)
        return seconds

    return slot, taken


def resolv_conf(tmp_path, text: str):
    path = tmp_path / "resolv.conf"
    path.write_text(text)
    return path


@pytest.mark.parametrize(
    "text",
    [
        "",  # what the review found in the built image: an empty /etc/resolv.conf
        "search home.arpa\noptions ndots:1\n",  # no nameserver line
        "nameserver not-an-address\nnameserver 127.0.0.1\n",  # dnspython refuses the file
        # dnspython keeps a DNS-over-HTTPS URL, which a UDP query cannot use
        "nameserver https://dns.example.com/dns-query\n",
    ],
)
async def test_a_host_with_no_usable_resolver_gives_no_server(tmp_path, text):
    resolver = DnsResolver(resolv_conf=str(resolv_conf(tmp_path, text)), read_timeout=PATIENT)
    assert await resolver.servers(()) == ()


async def test_a_host_with_no_resolv_conf_gives_no_server(tmp_path):
    resolver = DnsResolver(resolv_conf=str(tmp_path / "missing"), read_timeout=PATIENT)
    assert await resolver.servers(()) == ()


async def test_the_jobs_servers_win_over_the_hosts(tmp_path):
    resolver = DnsResolver(resolv_conf=str(resolv_conf(tmp_path, "nameserver 127.0.0.1\n")), read_timeout=PATIENT)
    assert await resolver.servers(("10.0.0.53",)) == ("10.0.0.53",)
    assert await resolver.servers(()) == ("127.0.0.1",)


async def test_only_addresses_are_kept_before_the_list_is_cut(tmp_path):
    """MAX_DNS_SERVERS cuts the list after the entries the agent cannot query are
    dropped, so three of those first cannot empty it."""
    text = "".join(f"nameserver https://dns{i}.example.com/dns-query\n" for i in range(3))
    text += "nameserver 127.0.0.1\nnameserver ::1\nnameserver 127.0.0.2\nnameserver 127.0.0.3\n"
    resolver = DnsResolver(resolv_conf=str(resolv_conf(tmp_path, text)), read_timeout=PATIENT)
    assert await resolver.servers(()) == ("127.0.0.1", "::1", "127.0.0.2")
    assert len(await resolver.servers(tuple(f"127.0.0.{i}" for i in range(1, 8)))) == MAX_DNS_SERVERS


async def test_the_hosts_resolvers_are_read_again_for_every_job(tmp_path):
    """A failed read is not kept: the next call reads resolv.conf again, so a corrected
    file takes effect at the next job, in the same process."""
    path = resolv_conf(tmp_path, "")
    resolver = DnsResolver(resolv_conf=str(path), read_timeout=PATIENT)
    assert await resolver.servers(()) == ()
    path.write_text("nameserver 127.0.0.1\n")
    assert await resolver.servers(()) == ("127.0.0.1",)
    path.write_text("nameserver ::1\n")
    assert await resolver.servers(()) == ("::1",)
    path.unlink()
    assert await resolver.servers(()) == ()


async def test_a_lookup_with_no_server_raises_and_takes_no_slot():
    slot, taken = counted()
    with pytest.raises(ProbeError) as raised:
        await DnsResolver().ptr(ipaddress.ip_address("10.0.0.9"), (), slot)
    assert str(raised.value) == NO_DNS_SERVER
    assert taken == []


async def test_a_query_sent_to_no_server_is_no_lookup():
    """Port 0 is refused by the kernel before anything is sent (macOS and Linux both
    fail the send), and a server dnspython cannot parse is never sent to: a lookup
    whose every server failed like that raises, and is not a lookup that found no
    name."""
    address = ipaddress.ip_address("10.0.0.9")
    slot, taken = counted()
    with pytest.raises(ProbeError) as raised:
        await DnsResolver(port=0).ptr(address, ("127.0.0.1", "127.0.0.1"), slot)
    assert str(raised.value) == no_query_sent("OSError")
    assert len(taken) == 2  # each attempt took its slot
    with pytest.raises(ProbeError) as raised:
        await DnsResolver().ptr(address, ("not-an-address",), unpaced())
    assert str(raised.value) == no_query_sent("ValueError")


async def test_one_server_that_cannot_be_sent_to_does_not_spoil_the_lookup():
    transport, proto, port = await responder()
    try:
        resolver = DnsResolver(port=port)
        address = ipaddress.ip_address("10.0.0.9")
        # the first cannot be sent to; the second answers with the name
        assert await resolver.ptr(address, ("not-an-address", "127.0.0.1"), unpaced()) == "nas.home.arpa."
        # one sent and silent, one that cannot be sent: it was asked, and found no name
        proto.script = ["silent"]
        assert await resolver.ptr(address, ("127.0.0.1", "not-an-address"), unpaced(0.2)) is None
        assert len(proto.arrivals) == 2
    finally:
        transport.close()


async def test_no_server_to_ask_makes_the_scan_incomplete_and_a_fixed_host_restores_it(tmp_path):
    """Acceptance for #78 at the scan: a job naming no DNS server on a host with none
    reports `complete: false` with the fixed error and sends no DNS packet; once the
    host's resolv.conf is fixed, the next job with the same resolver looks names up.
    The responder is on loopback; nothing leaves this machine."""
    transport, proto, port = await responder()
    try:
        path = resolv_conf(tmp_path, "")
        resolver = DnsResolver(port=port, resolv_conf=str(path), read_timeout=PATIENT)
        raw = job(ranges=[{"start": "10.0.0.9", "end": "10.0.0.9"}], dns_servers=[])
        parsed = parse_job(job_ref(raw), raw)
        addresses = guard.plan(parsed, parse_allowed(None))

        async def sweep():
            return await scan.run(
                parsed, addresses, prober=TimedProber(alive=["10.0.0.9"]), resolver=resolver,
                max_pps=200, clock=scan.Clock(),
            )

        first = await sweep()
        assert first["complete"] is False
        assert first["error"] == NO_DNS_SERVER
        assert first["observations"] == [{"ip": "10.0.0.9"}]
        await asyncio.sleep(0.05)
        assert proto.arrivals == []

        path.write_text("nameserver 127.0.0.1\n")
        second = await sweep()
        assert second["complete"] is True and second["error"] is None
        assert second["observations"] == [{"ip": "10.0.0.9", "rdns_name": "nas.home.arpa"}]
        assert len(proto.arrivals) == 1
    finally:
        transport.close()


# --- briefed round 1 of PR #96 ---


async def test_a_lookup_whose_own_time_runs_out_after_sending_nothing_is_no_lookup():
    """Finding 1: two unsendable attempts, then a slot past the lookup's own 2 s
    while the job still has time. No query went out, so it is not a lookup that found
    no name."""
    remaining = iter([2.0, 1.0, 0.0])

    async def slot():
        return next(remaining)

    with pytest.raises(ProbeError) as raised:
        await DnsResolver(port=0).ptr(
            ipaddress.ip_address("10.0.0.1"), ("127.0.0.1", "127.0.0.2", "127.0.0.3"), slot
        )
    assert str(raised.value) == no_query_sent("OSError")


async def test_after_a_sent_query_a_lookup_out_of_time_is_one_that_found_no_name():
    transport, proto, port = await responder(script=("silent",))
    try:
        remaining = iter([0.2, 0.0])

        async def slot():
            return next(remaining)

        name = await DnsResolver(port=port).ptr(ipaddress.ip_address("10.0.0.9"), ("127.0.0.1",) * 2, slot)
        assert name is None and len(proto.arrivals) == 1
    finally:
        transport.close()


@pytest.mark.parametrize(
    "failures, expected",
    [
        ([TimeoutError()], None),
        ([TimeoutError(), OSError()], None),
        ([OSError(), TimeoutError()], None),
        ([OSError(), OSError()], "OSError"),
    ],
)
async def test_a_built_in_timeout_counts_as_a_sent_query(monkeypatch, failures, expected):
    """dnspython turns an ordinary receive timeout into dns.exception.Timeout, but the
    built-in TimeoutError, an OSError, must be read as a sent query too, not as one
    that could not be sent (review round 1, the exception-order mutants)."""
    raised = iter(failures)

    async def udp(*args, **kwargs):
        raise next(raised)

    monkeypatch.setattr(dns.asyncquery, "udp", udp)
    servers = ("127.0.0.1",) * len(failures)
    lookup = DnsResolver().ptr(ipaddress.ip_address("10.0.0.9"), servers, unpaced())
    if expected is None:
        assert await lookup is None
    else:
        with pytest.raises(ProbeError) as error:
            await lookup
        assert str(error.value) == no_query_sent(expected)


async def test_no_query_goes_out_after_a_lookup_that_could_send_nothing(monkeypatch):
    """Finding 3: three lookups in flight against a loopback responder; the first
    query cannot be sent, and the other two would send theirs 30 ms after that. They
    are cancelled first, so no datagram reaches the responder after the failure."""
    transport, proto, port = await responder()
    real_udp = dns.asyncquery.udp
    calls = 0
    all_in, failed = asyncio.Event(), asyncio.Event()

    async def udp(query, where, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            all_in.set()
        if calls == 1:
            await all_in.wait()
            failed.set()
            raise OSError("no route")
        await failed.wait()
        await asyncio.sleep(0.03)
        return await real_udp(query, where, **kwargs)

    monkeypatch.setattr(dns.asyncquery, "udp", udp)
    try:
        live = ["10.0.0.7", "10.0.0.8", "10.0.0.9"]
        raw = job(ranges=[{"start": "10.0.0.7", "end": "10.0.0.9"}], dns_servers=["127.0.0.1"])
        parsed = parse_job(job_ref(raw), raw)
        result = await scan.run(
            parsed, guard.plan(parsed, parse_allowed(None)), prober=TimedProber(alive=live),
            resolver=DnsResolver(port=port), max_pps=1000, clock=scan.Clock(),
        )
        await asyncio.sleep(0.1)
        assert calls == 3
        assert proto.arrivals == []
        assert result["complete"] is False and result["error"] == no_query_sent("OSError")
        assert result["observations"] == [{"ip": a} for a in live]
    finally:
        transport.close()


async def test_a_lookup_the_jobs_deadline_stops_before_any_attempt_is_reported_as_the_cut_off():
    """The other side of finding 1: a lookup whose first slot comes at the job's
    deadline made no attempt, sent nothing and failed nothing. It is the deadline's
    cut-off, which the scan reports as the PTR timeout, not as a query that could not
    be sent. Three pings at 1 a second on the fake clock, a 4 s job: the first lookup
    gets 1 s and its name, the second's slot comes at the deadline."""
    from conftest import FakeClock

    transport, proto, port = await responder()
    try:
        raw = job(ranges=[{"start": "10.0.0.7", "end": "10.0.0.9"}], dns_servers=["127.0.0.1"], timeout_s=4)
        parsed = parse_job(job_ref(raw), raw)
        result = await scan.run(
            parsed, guard.plan(parsed, parse_allowed(None)),
            prober=TimedProber(alive=["10.0.0.7", "10.0.0.8", "10.0.0.9"]),
            resolver=DnsResolver(port=port), max_pps=1, clock=FakeClock(),
        )
        assert result["complete"] is False
        assert result["error"] == "timed out after 4 s during the PTR lookups"
        assert len(proto.arrivals) == 1
    finally:
        transport.close()


# --- #87: a query goes when its slot is granted, or not at all ---


class Unwritable:
    """Stands in for a client socket whose kernel send buffer is full: a datagram is
    refused (`BlockingIOError`, on which asyncio's transport would keep it and retry
    once the socket is writable) until `writable` is set, and then handed to the real
    socket. `log` records the first refusal and each send."""

    def __init__(self, sock, gate, log):
        self._sock, self._gate, self._log = sock, gate, log
        self._queued = False

    def _refuse(self) -> bool:
        if self._gate["writable"]:
            self._log.append("kernel took it")
            return False
        if not self._queued:
            self._queued = True
            self._log.append("queued")
        return True

    def sendto(self, data, addr):
        if self._refuse():
            raise BlockingIOError
        return self._sock.sendto(data, addr)

    def send(self, data):
        if self._refuse():
            raise BlockingIOError
        return self._sock.send(data)

    def __getattr__(self, name):
        return getattr(self._sock, name)


def unwritable_sockets(loop, gate, log, count=1):
    """Makes the next `count` datagram sockets the loop creates `Unwritable`: the
    stand-in replaces the transport's `_sock`, an asyncio internal. The responders are
    made before it is in place. Undo with `del loop.create_datagram_endpoint`."""
    real_endpoint = loop.create_datagram_endpoint
    made = []

    async def endpoint(*args, **kwargs):
        transport, protocol = await real_endpoint(*args, **kwargs)
        if len(made) < count:
            transport._sock = Unwritable(transport._sock, gate, log)
        made.append(transport)
        return transport, protocol

    loop.create_datagram_endpoint = endpoint
    return made


async def test_a_query_the_socket_cannot_take_at_once_never_leaves_later():
    """#87, from the review of PR #103 ("Call 1's measured cost"): a query that
    asyncio's transport kept because the socket's send buffer was full could leave
    whenever the socket became writable, after its job and during the next one. Now the
    socket that cannot take a query at once is aborted with it, and the query counts as
    not sent: the first job's one lookup could send to no server, so it is incomplete
    and keeps the stored names. Then the socket becomes writable and a second job runs:
    only its own query arrives, and the first job's never does."""
    transport, proto, port = await responder()
    loop = asyncio.get_running_loop()
    log: list[str] = []
    gate = {"writable": False}
    made = unwritable_sockets(loop, gate, log)
    try:

        async def sweep(address):
            raw = job(ranges=[{"start": address, "end": address}], dns_servers=["127.0.0.1"])
            parsed = parse_job(job_ref(raw), raw)
            return await scan.run(
                parsed, guard.plan(parsed, parse_allowed(None)), prober=TimedProber(alive=[address]),
                resolver=DnsResolver(port=port), max_pps=1000, clock=scan.Clock(),
            )

        first = await sweep("10.0.0.9")
        assert first["complete"] is False and first["error"] == no_query_sent("BlockingIOError")
        assert first["observations"] == [{"ip": "10.0.0.9"}]
        assert len(made) == 1 and made[0].is_closing()
        gate["writable"] = True
        second = await sweep("10.0.0.8")
        assert second["complete"] is True
        await asyncio.sleep(0.1)
        assert log == ["queued"]  # the kept datagram was dropped with its socket
        assert proto.questions == [("8.0.0.10.in-addr.arpa.", "PTR")]
    finally:
        del loop.create_datagram_endpoint
        transport.close()


async def test_a_server_whose_socket_cannot_take_the_query_gives_way_to_the_next():
    """The query that could not be handed over is an attempt that sent nothing, as a
    refused send is: the lookup asks the next server, which answers. One datagram
    arrives, from the second socket."""
    transport, proto, port = await responder()
    loop = asyncio.get_running_loop()
    log: list[str] = []
    gate = {"writable": False}
    unwritable_sockets(loop, gate, log)
    try:
        slot, taken = counted()
        name = await DnsResolver(port=port).ptr(ipaddress.ip_address("10.0.0.9"), ("127.0.0.1",) * 2, slot)
        assert name == "nas.home.arpa."
        assert len(taken) == 2  # the slot of the query that was dropped is spent
        gate["writable"] = True
        await asyncio.sleep(0.05)
        assert log == ["queued"] and len(proto.arrivals) == 1
    finally:
        del loop.create_datagram_endpoint
        transport.close()


async def test_the_querys_socket_has_no_port_until_the_query_goes():
    """The query's socket is made before its slot is granted, so it is open while the
    lookup waits. It is bound to no port then (the kernel picks one at the send), so
    nothing sent to the host can reach it before the query goes."""
    transport, proto, port = await responder()
    loop = asyncio.get_running_loop()
    made = []
    real_endpoint = loop.create_datagram_endpoint

    async def endpoint(*args, **kwargs):
        pair = await real_endpoint(*args, **kwargs)
        made.append(pair[0])
        return pair

    loop.create_datagram_endpoint = endpoint
    ports = []
    try:

        async def slot():
            ports.append(made[-1].get_extra_info("socket").getsockname()[1])
            return 0.5

        name = await DnsResolver(port=port).ptr(ipaddress.ip_address("10.0.0.9"), ("127.0.0.1",), slot)
        assert name == "nas.home.arpa."
        assert ports == [0] and len(proto.arrivals) == 1
    finally:
        del loop.create_datagram_endpoint
        transport.close()


# --- #99: resolv.conf is read off the event loop, bounded in time and size ---


def ticker(interval: float = 0.01):
    """A timer beside the code under test: the times it fired, and its task."""
    ticks: list[float] = []

    async def tick():
        while True:
            ticks.append(time.monotonic())
            await asyncio.sleep(interval)

    return ticks, asyncio.create_task(tick())


def longest_gap(ticks: list[float], until: float) -> float:
    return max(b - a for a, b in zip(ticks, ticks[1:] + [until], strict=True))


class FifoWriter:
    """A writer for the FIFO at `path` from `after` seconds on, so code that blocked on
    it (opened it to read without O_NONBLOCK) is freed: a test then fails on its
    assertion rather than hanging. It opens without blocking, and gives up at `until`.

    `fired` says it opened the FIFO and wrote, that is, that a reader was waiting on it
    and was freed by this writer. A test that frees its reader itself looks at the
    `fired` of the writer it kept as a safety net: if that one fired, the test passed
    only because the net caught it. `close` calls the writer off and waits for its
    thread, so no helper outlives the test to write into whatever is then at the
    path."""

    def __init__(self, path, after: float = 2.0, until: float = 4.0):
        self.fired = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._write, args=(path, after, until), daemon=True)
        self._thread.start()

    def _write(self, path, after: float, until: float) -> None:
        if self._stop.wait(after):
            return
        end = time.monotonic() + until - after
        while time.monotonic() < end and not self._stop.is_set():
            try:
                fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
            except OSError:
                time.sleep(0.01)  # no reader yet
                continue
            try:
                os.write(fd, b"nameserver 127.0.0.1\n")
                self.fired = True
            finally:
                os.close(fd)
            return

    def close(self) -> None:
        self._stop.set()
        self._thread.join(PATIENT)
        assert not self._thread.is_alive(), "the FIFO writer did not end"


def is_fifo(path) -> bool:
    try:
        return stat.S_ISFIFO(os.stat(path).st_mode)
    except OSError:
        return False


async def free_a_blocked_read(path) -> None:
    """Cleanup for a test that may stop with a read blocked in the open of the FIFO at
    `path`: while the path is still a FIFO, a writer frees the read, and the read's
    thread is waited for. Once the test has replaced the path with a file (so the read
    has ended), it starts nothing."""
    if not is_fifo(path):
        return
    writer = FifoWriter(path, after=0)
    try:
        await no_read_thread_left()
    finally:
        writer.close()


async def test_a_fifo_at_the_resolver_path_leaves_the_job_incomplete_and_the_loop_running(tmp_path):
    """Acceptance for #99: with a FIFO where resolv.conf should be, a job naming no DNS
    server ends incomplete with the fixed error and sends no DNS packet, and a timer
    beside it keeps firing. The FIFO is refused at once, without waiting for the read's
    time bound: it is not a regular file, and opening it does not wait for a writer."""
    transport, proto, port = await responder()
    fifo = tmp_path / "resolv.conf"
    os.mkfifo(fifo)
    net = FifoWriter(fifo)
    ticks, timer = ticker()
    try:
        raw = job(ranges=[{"start": "10.0.0.9", "end": "10.0.0.9"}], dns_servers=[])
        parsed = parse_job(job_ref(raw), raw)
        started = time.monotonic()
        result = await scan.run(
            parsed, guard.plan(parsed, parse_allowed(None)), prober=TimedProber(alive=["10.0.0.9"]),
            resolver=DnsResolver(port=port, resolv_conf=str(fifo), read_timeout=PATIENT),
            max_pps=200, clock=scan.Clock(),
        )
        took = time.monotonic() - started
        await asyncio.sleep(0.05)
        assert result["complete"] is False
        assert result["error"] == NO_DNS_SERVER
        assert result["observations"] == [{"ip": "10.0.0.9"}]
        assert proto.arrivals == []
        assert longest_gap(ticks, time.monotonic()) < LOOP_GAP_MAX, ticks
        assert took < PATIENT / 2  # refused at once: a read waited out would take all of PATIENT
        assert not net.fired  # nothing opened the FIFO to read
    finally:
        net.close()
        timer.cancel()
        transport.close()


async def test_a_read_that_does_not_finish_gives_no_server_and_holds_one_thread_at_most(monkeypatch, tmp_path):
    """A stalled read (a network mount that does not answer, stood in for by a read
    that waits on an event) is waited for `read_timeout`, on a thread, while the event
    loop runs on; it gives no server. While it is still blocked, the next job starts no
    second read and waits for none. Once it ends, the next job reads again."""
    release = threading.Event()
    calls: list[str] = []

    def stalled(path):
        calls.append(path)
        release.wait(STALL)  # freed by the test; STALL at most, so a failure cannot hang
        return ("127.0.0.1",)

    monkeypatch.setattr(probe, "read_resolv_conf", stalled)
    resolver = DnsResolver(resolv_conf=str(tmp_path / "resolv.conf"), read_timeout=BLOCKED_WAIT)
    ticks, timer = ticker()
    try:
        started = time.monotonic()
        assert await resolver.servers(()) == ()
        waited = time.monotonic() - started
        assert 0.25 <= waited < WAIT_MAX  # it waited its bound, and no longer than that
        assert longest_gap(ticks, time.monotonic()) < LOOP_GAP_MAX, ticks
        assert len(ticks) >= 3
        assert await until(lambda: calls)  # the read's thread has run, if a loaded machine was slow to
        assert len(calls) == 1
        resolver.read_timeout = PATIENT  # a call that waited would now take all of it
        started = time.monotonic()
        assert await resolver.servers(()) == ()
        assert time.monotonic() - started < PATIENT / 2
        assert len(calls) == 1  # no second read while the first is blocked
        # a job's own servers need no read
        assert await resolver.servers(("10.0.0.53",)) == ("10.0.0.53",)
        assert len(calls) == 1
        release.set()
        assert await no_read_thread_left()  # the read ended, and took its lock with it
        assert await resolver.servers(()) == ("127.0.0.1",)
        assert len(calls) == 2
    finally:
        release.set()
        timer.cancel()
        await no_read_thread_left()


async def test_a_resolver_built_with_no_bound_gives_up_on_a_read_after_the_specs_second(monkeypatch, tmp_path):
    """The production bound on the host's resolv.conf read (#118, spec §5.2: "a read
    that has not finished within 1 second" gives no server). Every other test here
    passes its own bound, so none runs a blocked read against the default, and raising
    RESOLV_CONF_TIMEOUT_S to 4.0 passed them all.

    The second is written out here as a number, not read from the constant, which is
    what is held to it; and the resolver is built as `loop.py` builds it, with no
    `read_timeout`, and given a read that does not finish. It gives no server after
    about that second: not at once (the read was waited for) and not several (a
    bound that was raised). The upper limit, WAIT_MAX, is the margin a loaded machine
    is given, below the 4 s that raised bound would wait. It is a smoke test: it cannot
    tell 1 s from 2, which the next test does without the host's scheduler, on a clock it freezes. Its read
    ends after the wait, with its thread waited for, and nothing is read again after:
    that recovery is held by the tests above and below it, and a read made against the
    one-second default would only add a way to fail on a descheduled thread."""
    assert probe.RESOLV_CONF_TIMEOUT_S == 1.0
    release = threading.Event()
    calls: list[str] = []

    def stalled(path):
        calls.append(path)
        release.wait(STALL)  # freed by the test; STALL at most, so a failure cannot hang
        return ("127.0.0.1",)

    monkeypatch.setattr(probe, "read_resolv_conf", stalled)
    resolver = DnsResolver(resolv_conf=str(tmp_path / "resolv.conf"))
    assert resolver.read_timeout == 1.0
    try:
        started = time.monotonic()
        assert await resolver.servers(()) == ()
        waited = time.monotonic() - started
        assert 0.9 <= waited < WAIT_MAX, waited
        assert len(calls) == 1  # the read had started: this was its wait running out
        release.set()
        assert await no_read_thread_left()
    finally:
        release.set()
        await no_read_thread_left()


@pytest.mark.parametrize("late_s", [0.0, 1.5], ids=["thread-on-time", "thread-1.5s-late"])
async def test_the_default_bound_is_a_timer_of_one_second_on_the_loops_clock(monkeypatch, tmp_path, late_s):
    """The other half of the one-second bound (#118, review of PR #119), which the
    wall-clock test above cannot hold: it accepts any wait from 0.9 s to 3 s, so a bound
    doubled to 2 s passes it. Here the event loop's clock is the test's, frozen at an
    instant read before the resolver is called plus `skew`, and the loop is only turned
    (`asyncio.sleep(0)`): the read's wait is a timer set at that instant for
    `read_timeout`, and it is due when the clock has passed it. Real time counts for
    nothing, so neither a loaded machine nor a read thread that starts late (`late_s`:
    the delay #115 was about, held before the read says it has begun) moves the clock
    or the timer: the wait is pending at +0.999 s and done at +1.0 s, for the bound
    the spec names. A bound of 1.001 s, 1.2 s or 2 s is not done at +1.0 s; one of
    0.999 s is done at +0.999 s.

    A timed sleep never fires on a frozen clock, so the test waits for the thread with
    turns of the loop and puts the real clock back before its clean-up."""
    release = threading.Event()
    entered = threading.Event()

    def stalled(path):
        time.sleep(late_s)
        entered.set()
        release.wait(STALL)  # freed by the test; STALL at most, so a failure cannot hang
        return ("127.0.0.1",)

    async def turns(n=50):
        for _ in range(n):
            await asyncio.sleep(0)  # a turn of the loop; no timer of real time is involved

    loop = asyncio.get_running_loop()
    real_time = loop.time
    frozen = real_time()
    skew = [0.0]
    monkeypatch.setattr(loop, "time", lambda: frozen + skew[0])
    monkeypatch.setattr(probe, "read_resolv_conf", stalled)
    resolver = DnsResolver(resolv_conf=str(tmp_path / "resolv.conf"))  # no bound given
    waiting = asyncio.create_task(resolver.servers(()))
    try:
        end = time.monotonic() + PATIENT
        while not entered.is_set() and time.monotonic() < end:
            await asyncio.sleep(0)  # the read is blocked, and its wait is a timer set at `frozen`
        assert entered.is_set()
        skew[0] = 0.999
        await turns()
        assert not waiting.done(), "the wait ended before one second on the loop's clock"
        skew[0] = 1.0
        await turns()
        assert waiting.done(), "the wait did not end after one second on the loop's clock"
        assert waiting.result() == ()
        assert resolv_conf_threads()  # the read is still held, as it was
    finally:
        monkeypatch.setattr(loop, "time", real_time)  # the clean-up polls with timed sleeps
        release.set()
        if not waiting.done():
            waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
        await no_read_thread_left()


def test_a_fifo_or_a_device_is_not_read_and_not_waited_on(tmp_path):
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    full = tmp_path / "full"
    os.mkfifo(full)
    nets = [FifoWriter(fifo), FifoWriter(full)]
    try:
        started = time.monotonic()
        assert read_resolv_conf(str(fifo)) == ()
        assert time.monotonic() - started < 0.5
        assert read_resolv_conf(os.devnull) == ()  # a character device
        assert read_resolv_conf(str(tmp_path)) == ()  # a directory
        # a FIFO that holds a whole file's worth and then its end (the writer has gone,
        # another reader keeps the data): it could be read like a file, and is not
        reader = os.open(full, os.O_RDONLY | os.O_NONBLOCK)
        try:
            writer = os.open(full, os.O_WRONLY | os.O_NONBLOCK)
            os.write(writer, b"nameserver 127.0.0.1\n")
            os.close(writer)
            assert read_resolv_conf(str(full)) == ()
        finally:
            os.close(reader)
    finally:
        for net in nets:
            net.close()


def test_at_most_resolv_conf_max_bytes_are_taken(tmp_path):
    """A file of RESOLV_CONF_MAX bytes is read whole; one byte more and it gives no
    server, rather than a list cut short in the middle of an address."""
    line = b"nameserver 127.0.0.1\n"
    at_cap = tmp_path / "at-cap"
    at_cap.write_bytes(b"#" * (RESOLV_CONF_MAX - len(line) - 1) + b"\n" + line)
    assert at_cap.stat().st_size == RESOLV_CONF_MAX
    assert read_resolv_conf(str(at_cap)) == ("127.0.0.1",)
    over = tmp_path / "over"
    over.write_bytes(b"#" * (RESOLV_CONF_MAX - len(line)) + b"\n" + line)
    assert over.stat().st_size == RESOLV_CONF_MAX + 1
    assert read_resolv_conf(str(over)) == ()


@pytest.mark.parametrize(
    "content, expected",
    [
        (b"nameserver 127.0.0.1\r\nnameserver ::1\r\n", ("127.0.0.1", "::1")),
        (b"nameserver 127.0.0.1\rnameserver ::1\r", ("127.0.0.1", "::1")),
        (b"# comment\n; comment\nsearch home.arpa\nnameserver 127.0.0.1\n", ("127.0.0.1",)),
        (b"nameserver 127.0.0.1\n\xff\n", ()),  # not UTF-8, as dnspython refuses it
        # not line breaks in a text file, though str.splitlines cuts at each (round 1
        # of #103): one line, so one nameserver
        ("nameserver 127.0.0.1\x85nameserver 127.0.0.2\n".encode(), ("127.0.0.1",)),
        ("nameserver 127.0.0.1\u2028nameserver 127.0.0.2\n".encode(), ("127.0.0.1",)),
        (b"nameserver 127.0.0.1\fnameserver 127.0.0.2\n", ("127.0.0.1",)),
        (b"nameserver 127.0.0.1\vnameserver 127.0.0.2\n", ("127.0.0.1",)),
        # a byte-order mark spoils the first line's keyword
        ("\ufeffnameserver 127.0.0.1\nnameserver 127.0.0.2\n".encode(), ("127.0.0.2",)),
        (b"nameserver 127.0.0.1\x00\nnameserver 127.0.0.2\n", ()),  # dnspython refuses the NUL
        # the rest of the sixteen round 2 of #103 compared
        (b"nameserver 127.0.0.1\nnameserver ::1\r\nnameserver 127.0.0.2\rnameserver 127.0.0.3",
         ("127.0.0.1", "::1", "127.0.0.2", "127.0.0.3")),  # mixed line ends, the last unterminated
        (b"nameserver 127.0.0.1", ("127.0.0.1",)),
        (b"", ()),
        (b"nameserver 127.0.0.1 trailing text\n", ("127.0.0.1",)),
        # four: the file's parse keeps them all, `usable_servers` takes three
        (b"nameserver 127.0.0.1\nnameserver 127.0.0.2\nnameserver 127.0.0.3\nnameserver 127.0.0.4\n",
         ("127.0.0.1", "127.0.0.2", "127.0.0.3", "127.0.0.4")),
        (b"nameserver fe80::1%lo0\n", ("fe80::1%lo0",)),
        (b"nameserver https://dns.example.com/dns-query\nnameserver 127.0.0.1\n",
         ("https://dns.example.com/dns-query", "127.0.0.1")),
    ],
)
def test_the_file_is_parsed_as_dnspython_parses_it(tmp_path, content, expected):
    """dnspython opens and parses the file (#104; #99 had read it and handed dnspython
    its lines): each case's answer is the one dnspython gives for the same file when
    it is made with `filename=`, as the agent made it before #99."""
    path = tmp_path / "resolv.conf"
    path.write_bytes(content)
    assert read_resolv_conf(str(path)) == expected
    # the same answer dnspython gives when it opens the file itself
    try:
        theirs = tuple(dns.resolver.Resolver(filename=str(path)).nameservers)
    except (dns.exception.DNSException, ValueError):
        theirs = ()
    assert theirs == expected


def test_a_symlinked_resolv_conf_is_followed(tmp_path):
    """systemd-resolved makes /etc/resolv.conf a link to a file of its own."""
    target = tmp_path / "stub-resolv.conf"
    target.write_text("nameserver 127.0.0.53\n")
    link = tmp_path / "resolv.conf"
    link.symlink_to(target)
    assert read_resolv_conf(str(link)) == ("127.0.0.53",)


def resolv_conf_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == "resolv.conf"]


async def until(condition, within: float = PATIENT) -> bool:
    """Whether `condition()` came true within `within` seconds, polled on the loop."""
    end = time.monotonic() + within
    while time.monotonic() < end:
        if condition():
            return True
        await asyncio.sleep(0.01)
    return bool(condition())


def returns_without_suspending(coro):
    """What `coro` returns when it is driven by hand and ends in its first step: having
    waited for nothing, no sleep, no future and no turn of the event loop. It fails the
    test if the coroutine suspends. A wait cannot hide from this in a bound: it does not
    ask how long the call took, which a loaded machine answers for it."""
    try:
        coro.send(None)
    except StopIteration as done:
        return done.value
    coro.close()
    pytest.fail("the call suspended: it waited for something")


async def no_read_thread_left(within: float = PATIENT) -> bool:
    return await until(lambda: not resolv_conf_threads(), within)


@pytest.mark.parametrize("kind", ["fifo", "device", "directory", "oversized"])
async def test_what_is_not_an_ordinary_file_gives_no_server_and_leaves_no_thread(tmp_path, kind):
    """Acceptance for #104: a FIFO, a device (a link to /dev/null), a directory or a file
    over RESOLV_CONF_MAX at the resolver path gives no server at once, well inside the
    read's time bound, while a timer beside it keeps firing. No read thread is left
    behind, and once the path holds an ordinary file again the next job reads it."""
    path = tmp_path / "resolv.conf"
    net = None
    if kind == "fifo":
        os.mkfifo(path)
        net = FifoWriter(path)  # frees a read that opened it and waited, so a failure cannot hang
    elif kind == "device":
        path.symlink_to(os.devnull)
    elif kind == "directory":
        path.mkdir()
    else:
        path.write_bytes(b"nameserver 127.0.0.1\n" + b"#" * RESOLV_CONF_MAX)
    resolver = DnsResolver(resolv_conf=str(path), read_timeout=PATIENT)
    ticks, timer = ticker()
    try:
        started = time.monotonic()
        assert await resolver.servers(()) == ()
        assert time.monotonic() - started < PATIENT / 2  # at once, not the read's bound
        await asyncio.sleep(0.05)
        assert longest_gap(ticks, time.monotonic()) < LOOP_GAP_MAX, ticks
        assert await no_read_thread_left()
        if kind == "directory":
            path.rmdir()
        else:
            path.unlink()
        if net is not None:
            net.close()
            assert not net.fired  # the FIFO was refused, not opened
        path.write_text("nameserver 127.0.0.1\n")
        assert await resolver.servers(()) == ("127.0.0.1",)
        assert await no_read_thread_left()
    finally:
        if net is not None:
            net.close()
        timer.cancel()


async def test_a_fifo_put_at_the_path_after_the_look_holds_one_thread_until_a_writer_comes(monkeypatch, tmp_path):
    """What #104 gives up, held to its bound: the path is looked at (`os.stat`) before
    dnspython opens it, so a FIFO swapped in between those two blocks the open until a
    writer comes. The swap is stood in for by a `stat` that calls the FIFO a regular
    file. The job waits `read_timeout` and finds no server, the loop runs on, the next
    job starts no second read; once a writer comes the thread ends, and the next job
    reads the file then at the path.

    Only the first wait is meant to run out its bound (BLOCKED_WAIT). The read at the
    end, which must succeed, was made with that same 0.3 s and failed once under load
    (#115); it has PATIENT now. A writer set to free the FIFO at STALL keeps a read with
    no time bound from hanging the test: it is freed, finds a server, and the first
    assertion fails. If that net fired in a run that got to the end, the run fails: it
    was held up for STALL between the first wait and the test's own writer, and what it
    saw after that was not its own doing. Whatever happens, the cleanup ends the
    helpers: the nets are joined, and a read still blocked on the FIFO is freed and its
    thread waited for."""

    class LooksRegular:  # what `os.stat` saw before the swap
        @staticmethod
        def S_ISREG(mode):
            return True

    fifo = tmp_path / "resolv.conf"
    os.mkfifo(fifo)
    monkeypatch.setattr(probe, "stat", LooksRegular)
    resolver = DnsResolver(resolv_conf=str(fifo), read_timeout=BLOCKED_WAIT)
    ticks, timer = ticker()
    net = FifoWriter(fifo, after=STALL, until=STALL + 2)
    freeing = None
    try:
        started = time.monotonic()
        assert await resolver.servers(()) == ()
        assert 0.25 <= time.monotonic() - started < WAIT_MAX
        assert longest_gap(ticks, time.monotonic()) < LOOP_GAP_MAX, ticks
        # blocked in dnspython's open: the thread has run, if the machine was slow to
        assert await until(lambda: len(resolv_conf_threads()) == 1)
        # a call made while the read is blocked makes no wait at all: it ends in the
        # first step, having suspended nowhere (no sleep, no turn of the loop)
        assert returns_without_suspending(resolver.servers(())) == ()
        resolver.read_timeout = PATIENT  # a call that waited would now take all of it
        started = time.monotonic()
        assert await resolver.servers(()) == ()  # no second read while it is blocked
        assert time.monotonic() - started < PATIENT / 2
        assert len(resolv_conf_threads()) == 1
        monkeypatch.undo()
        net.close()
        assert not net.fired, f"the net freed the read: this run was held up for {STALL} s"
        freeing = FifoWriter(fifo, after=0)
        assert await no_read_thread_left()
        freeing.close()
        assert freeing.fired  # the read ended because this writer came
        fifo.unlink()
        fifo.write_text("nameserver 127.0.0.1\n")
        assert await resolver.servers(()) == ("127.0.0.1",)
    finally:
        net.close()
        if freeing is not None:
            freeing.close()
        timer.cancel()
        await free_a_blocked_read(fifo)  # only if the test stopped before it freed the read


async def test_a_call_made_while_a_read_is_stuck_makes_no_wait(monkeypatch, tmp_path):
    """While a read holds the resolver's lock, a call that needs the host's servers
    refuses at once, and a call that brings its own servers never looks at the lock:
    neither suspends, which is judged by driving the call by hand (no bound, so not
    the host's scheduler). A delay before the refusal, a sleep or a turn of the loop,
    fails here, which the loose timing checks of the tests beside it do not tell."""
    release = threading.Event()
    entered = threading.Event()

    def stalled(path):
        entered.set()
        release.wait(STALL)
        return ("127.0.0.1",)

    monkeypatch.setattr(probe, "read_resolv_conf", stalled)
    resolver = DnsResolver(resolv_conf=str(tmp_path / "resolv.conf"), read_timeout=PATIENT)
    first = asyncio.create_task(resolver.servers(()))
    try:
        assert await until(entered.is_set)  # the read holds the lock
        assert returns_without_suspending(resolver.servers(())) == ()
        assert returns_without_suspending(resolver.servers(("10.0.0.53",))) == ("10.0.0.53",)
        release.set()
        assert await first == ("127.0.0.1",)  # the first call was waiting all along
        assert await resolver.servers(()) == ("127.0.0.1",)  # and the lock is free again
    finally:
        release.set()
        if not first.done():
            first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        await no_read_thread_left()


STALL_TRIES = 6
LOOP_STALL = 0.25


async def test_the_loop_turns_while_the_servers_are_chosen(monkeypatch, tmp_path):
    """The loop is not held up while the choice of servers waits for a read held on an
    event: check-ins, the job's deadline and the scan's backstop run on it. A stall a
    regression puts into the call is in every wait it makes; one the host puts there
    (a loaded machine, a late wake) is in few. So the test passes at the first wait in
    which no gap between the timer's ticks reaches LOOP_STALL, and fails when none of
    STALL_TRIES waits is clean: a stall of the code's own, like a block of 0.4 s before
    the wait, shows in all of them, and a loaded machine would have to stall the loop
    in every one."""
    release = threading.Event()

    def stalled(path):
        release.wait(STALL)
        return ("127.0.0.1",)

    monkeypatch.setattr(probe, "read_resolv_conf", stalled)
    resolver = DnsResolver(resolv_conf=str(tmp_path / "resolv.conf"), read_timeout=0.2)
    gaps: list[float] = []
    try:
        for _ in range(STALL_TRIES):
            release.clear()
            ticks, timer = ticker()
            try:
                assert await resolver.servers(()) == ()  # waited its 0.2 s for the held read
                assert ticks, "the loop did not turn once during the wait"
                gaps.append(longest_gap(ticks, time.monotonic()))
            finally:
                timer.cancel()
                release.set()  # the read ends, and the next wait starts a read of its own
            assert await no_read_thread_left()
            if gaps[-1] < LOOP_STALL:
                break
        assert gaps[-1] < LOOP_STALL, f"the loop was held up in every wait, gaps {gaps}"
    finally:
        release.set()
        await no_read_thread_left()


@pytest.mark.parametrize("late_s", [0.0, 1.5], ids=["thread-on-time", "thread-1.5s-late"])
async def test_the_choice_of_servers_ends_with_the_jobs_deadline(monkeypatch, tmp_path, late_s):
    """Order against the deadline, not a time taken: the job has one second, the read of
    resolv.conf is held on an event, and the loop's clock is made to pass the job's
    deadline (`skew`) while the scan waits for the choice of servers. The scan must
    then end within a few turns of the loop, the read still held. A wait that goes on
    after the cancellation (a sleep in the cancelled call) would need the clock to pass
    again, and the scan is still running after those turns. The loop's timers and the
    turns are the whole clock here, so the host's scheduler does not decide it; the
    job's own clock is untouched.

    The wait for the choice is a timer of the job's time left, set on the loop's clock,
    so that clock is frozen from before the scan starts and moved only once the read is
    held (#120): a read thread that starts late (`late_s`, held before the read says it
    has begun; 1.5 s is more than the job's whole second) moves neither the clock nor
    the timer, and the scan is still waiting when it enters. A timed sleep never fires
    on a frozen clock, so the test waits for the thread with turns of the loop and puts
    the real clock back before its clean-up."""
    release = threading.Event()
    entered = threading.Event()

    def stalled(path):
        time.sleep(late_s)
        entered.set()
        release.wait(STALL)
        return ("127.0.0.1",)

    async def turns(n=50):
        for _ in range(n):
            await asyncio.sleep(0)  # a turn of the loop; no timer of real time is involved

    loop = asyncio.get_running_loop()
    real_time = loop.time
    frozen = real_time()
    skew = [0.0]
    monkeypatch.setattr(loop, "time", lambda: frozen + skew[0])
    monkeypatch.setattr(probe, "read_resolv_conf", stalled)
    resolver = DnsResolver(resolv_conf=str(tmp_path / "resolv.conf"), read_timeout=PATIENT)
    raw = job(ranges=[{"start": "10.0.0.9", "end": "10.0.0.9"}], dns_servers=[], timeout_s=1)
    parsed = parse_job(job_ref(raw), raw)
    run = asyncio.create_task(scan.run(
        parsed, guard.plan(parsed, parse_allowed(None)), prober=TimedProber(alive=["10.0.0.9"]),
        resolver=resolver, max_pps=200, clock=scan.Clock(),
    ))
    try:
        end = time.monotonic() + PATIENT
        while not entered.is_set() and time.monotonic() < end:
            await asyncio.sleep(0)  # the scan is waiting for the choice of servers
        assert entered.is_set()
        await turns()
        assert not run.done(), "the scan ended before the job's deadline on the loop's clock"
        skew[0] += 0.5  # half of the job's second
        await turns()
        assert not run.done(), "the scan ended before the job's deadline on the loop's clock"
        skew[0] += 1.0  # the job's second has passed on the loop's clock; PATIENT has not
        await turns()
        assert run.done(), "the scan did not end with the job's deadline"
        result = run.result()
        assert result["complete"] is False
        assert result["error"] == "timed out after 1 s during the PTR lookups"
        assert result["observations"] == [{"ip": "10.0.0.9"}]
        assert resolv_conf_threads()  # the read is still held, as it was
    finally:
        monkeypatch.setattr(loop, "time", real_time)  # the clean-up polls with timed sleeps
        release.set()
        if not run.done():
            run.cancel()
        await asyncio.gather(run, return_exceptions=True)
        await no_read_thread_left()


async def test_choosing_the_servers_ends_at_the_jobs_deadline(monkeypatch, tmp_path):
    """Round 1 of #103, finding 2, on the real clock: liveness takes 0.8 s of a 1 s job
    and resolv.conf's read stalls. Choosing the servers waits only for the 0.2 s the
    job has left, not the read's own second, and the job ends at its deadline with the
    PTR stage's timeout, not as a job with no server. The stalled read keeps its thread
    and its lock: the next job starts no second read, and once the read ends one does."""
    release = threading.Event()
    calls: list[str] = []

    def stalled(path):
        calls.append(path)
        release.wait(STALL)  # freed by the test; STALL at most, so a failure cannot hang
        return ("127.0.0.1",)

    class SlowProber:
        async def ping(self, address, timeout):
            await asyncio.sleep(0.8)
            return True

    monkeypatch.setattr(probe, "read_resolv_conf", stalled)
    resolver = DnsResolver(resolv_conf=str(tmp_path / "resolv.conf"), read_timeout=PATIENT)
    assert resolver.read_timeout > 1.0  # the read's own bound is longer than the job has
    try:
        raw = job(ranges=[{"start": "10.0.0.9", "end": "10.0.0.9"}], dns_servers=[], timeout_s=1)
        parsed = parse_job(job_ref(raw), raw)
        started = time.monotonic()
        result = await scan.run(
            parsed, guard.plan(parsed, parse_allowed(None)), prober=SlowProber(),
            resolver=resolver, max_pps=200, clock=scan.Clock(),
        )
        took = time.monotonic() - started
        assert result["complete"] is False
        assert result["error"] == "timed out after 1 s during the PTR lookups"
        assert result["observations"] == [{"ip": "10.0.0.9"}]
        # the job's deadline, not the read's bound: with the read's own PATIENT after
        # the 0.8 s of liveness the job would end after nearly 6 s
        assert 0.95 <= took < 3.0, took
        assert await until(lambda: calls)  # the read's thread has run, if a loaded machine was slow to
        assert len(calls) == 1
        assert await resolver.servers(()) == ()  # still blocked: no second read
        assert len(calls) == 1
        release.set()
        assert await no_read_thread_left()  # the read ended, and took its lock with it
        assert await resolver.servers(()) == ("127.0.0.1",)
        assert len(calls) == 2
    finally:
        release.set()
        await no_read_thread_left()
