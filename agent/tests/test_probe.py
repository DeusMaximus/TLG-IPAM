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
from tlg_ipam_agent.probe import MAX_DNS_SERVERS, NO_DNS_SERVER, DnsResolver, ProbeError, no_query_sent
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
        self.queries: list[dns.message.Message] = []

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
        reply = dns.message.make_response(query)
        if action == "servfail":
            reply.set_rcode(dns.rcode.SERVFAIL)
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


async def test_what_a_query_carries_on_the_wire():
    """Set explicitly, and pinned here: EDNS(0) advertising a 1232-byte UDP payload (so
    a resolver can send a PTR answer whole rather than truncate it, which the agent
    would not use), recursion desired, no DNSSEC. The advertised payload is not a limit
    on what the agent reads: dnspython receives up to 65,535 bytes."""
    transport, proto, port = await responder()
    try:
        await DnsResolver(port=port).ptr(ipaddress.ip_address("10.0.0.9"), ("127.0.0.1",), unpaced())
        [query] = proto.queries
        assert query.edns == 0 and query.payload == 1232
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
def test_a_host_with_no_usable_resolver_gives_no_server(tmp_path, text):
    resolver = DnsResolver(resolv_conf=str(resolv_conf(tmp_path, text)))
    assert resolver.servers(()) == ()


def test_a_host_with_no_resolv_conf_gives_no_server(tmp_path):
    assert DnsResolver(resolv_conf=str(tmp_path / "missing")).servers(()) == ()


def test_the_jobs_servers_win_over_the_hosts(tmp_path):
    resolver = DnsResolver(resolv_conf=str(resolv_conf(tmp_path, "nameserver 127.0.0.1\n")))
    assert resolver.servers(("10.0.0.53",)) == ("10.0.0.53",)
    assert resolver.servers(()) == ("127.0.0.1",)


def test_only_addresses_are_kept_before_the_list_is_cut(tmp_path):
    """MAX_DNS_SERVERS cuts the list after the entries the agent cannot query are
    dropped, so three of those first cannot empty it."""
    text = "".join(f"nameserver https://dns{i}.example.com/dns-query\n" for i in range(3))
    text += "nameserver 127.0.0.1\nnameserver ::1\nnameserver 127.0.0.2\nnameserver 127.0.0.3\n"
    resolver = DnsResolver(resolv_conf=str(resolv_conf(tmp_path, text)))
    assert resolver.servers(()) == ("127.0.0.1", "::1", "127.0.0.2")
    assert len(resolver.servers(tuple(f"127.0.0.{i}" for i in range(1, 8)))) == MAX_DNS_SERVERS


def test_the_hosts_resolvers_are_read_again_for_every_job(tmp_path):
    """A failed read is not kept: the next call reads resolv.conf again, so a corrected
    file takes effect at the next job, in the same process."""
    path = resolv_conf(tmp_path, "")
    resolver = DnsResolver(resolv_conf=str(path))
    assert resolver.servers(()) == ()
    path.write_text("nameserver 127.0.0.1\n")
    assert resolver.servers(()) == ("127.0.0.1",)
    path.write_text("nameserver ::1\n")
    assert resolver.servers(()) == ("::1",)
    path.unlink()
    assert resolver.servers(()) == ()


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
        resolver = DnsResolver(port=port, resolv_conf=str(path))
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
