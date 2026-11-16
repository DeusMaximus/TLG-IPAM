"""The real PTR resolver against DNS responders on loopback, with the timestamps of the
datagrams that actually arrive: every query a lookup sends, failover included, comes
out of the job's one packet budget (finding 1 of the briefed review of PR #70).
Nothing here leaves 127.0.0.1 or ::1."""

import asyncio
import ipaddress
import socket
import time

import dns.flags
import dns.message
import dns.rcode
import dns.rdatatype
import dns.rrset
import pytest

from conftest import job
from tlg_ipam_agent import guard, scan
from tlg_ipam_agent.config import parse_allowed
from tlg_ipam_agent.probe import MAX_DNS_SERVERS, DnsResolver
from tlg_ipam_agent.protocol import job_ref, parse_job

ANSWERS = {
    "9.0.0.10.in-addr.arpa.": "nas.home.arpa.",
    "8.0.0.10.in-addr.arpa.": "Evil\\032name<b>.home.arpa.",
}


class Responder(asyncio.DatagramProtocol):
    """Answers PTR queries from ANSWERS. `script` gives the rcode (or "silent", or
    "truncated") for each query in turn; the last entry repeats."""

    def __init__(self, script=("answer",)):
        self.script = list(script)
        self.arrivals: list[float] = []
        self.questions = []

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        self.arrivals.append(time.monotonic())
        query = dns.message.from_wire(data)
        question = query.question[0]
        self.questions.append((question.name.to_text(), dns.rdatatype.to_text(question.rdtype)))
        action = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if action == "silent":
            return
        reply = dns.message.make_response(query)
        if action == "servfail":
            reply.set_rcode(dns.rcode.SERVFAIL)
        elif action == "truncated":
            reply.flags |= dns.flags.TC
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
        self.transport.sendto(reply.to_wire(), addr)


async def responder(host="127.0.0.1", port=0, **kwargs):
    loop = asyncio.get_running_loop()
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    transport, proto = await loop.create_datagram_endpoint(
        lambda: Responder(**kwargs), local_addr=(host, port), family=family
    )
    return transport, proto, transport.get_extra_info("sockname")[1]


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
        # NXDOMAIN is an answer: the next server is not asked
        assert await resolver.ptr(ipaddress.ip_address("10.0.0.7"), servers * 3, unpaced()) is None
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


@pytest.mark.parametrize("second", ["127.0.0.1", "::1"])
async def test_failover_to_the_next_server_is_paced_with_the_icmp_sweep(second):
    """At MAX_PPS=4 the echo request and both queries (the first server answers
    SERVFAIL, the second the name) are at least 0.25 s apart on the wire."""
    if second == "::1" and not _ipv6_loopback():
        pytest.skip("no IPv6 loopback here")
    first_t, first, port = await responder(script=("servfail", "answer"))
    second_t, second_p = first_t, first
    if second != "127.0.0.1":
        second_t, second_p, _ = await responder(host="::1", port=port)
    try:
        raw = job(
            ranges=[{"start": "10.0.0.9", "end": "10.0.0.9"}],
            dns_servers=["127.0.0.1", second],
        )
        parsed = parse_job(job_ref(raw), raw)
        prober = TimedProber(alive=["10.0.0.9"])
        result = await scan.run(
            parsed,
            guard.plan(parsed, parse_allowed(None)),
            prober=prober,
            resolver=DnsResolver(port=port),
            max_pps=4,
            clock=scan.Clock(),
        )
        assert result["observations"] == [{"ip": "10.0.0.9", "rdns_name": "nas.home.arpa"}]
        assert result["complete"] is True
        arrivals = sorted(prober.sent + first.arrivals + (second_p.arrivals if second_p is not first else []))
        assert len(arrivals) == 3
        gaps = [b - a for a, b in zip(arrivals, arrivals[1:], strict=False)]
        assert all(g >= 0.25 - 0.03 for g in gaps), gaps
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
