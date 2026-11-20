"""The real PTR resolver against DNS responders on loopback, with the timestamps of the
datagrams that actually arrive: every query a lookup sends, failover included, comes
out of the job's one packet budget (finding 1 of the briefed review of PR #70).
Nothing here leaves 127.0.0.1 or ::1."""

import asyncio
import errno
import ipaddress
import os
import socket
import threading
import time

import dns.asyncquery
import dns.exception
import dns.flags
import dns.message
import dns.rcode
import dns.rdatatype
import dns.resolver
import dns.rrset
import pytest

from conftest import job
from tlg_ipam_agent import guard, probe, scan
from tlg_ipam_agent.config import parse_allowed
from tlg_ipam_agent.probe import (
    MAX_DNS_SERVERS,
    NO_DNS_SERVER,
    RESOLV_CONF_MAX,
    RESOLV_CONF_TIMEOUT_S,
    DnsResolver,
    ProbeError,
    no_query_sent,
    read_resolv_conf,
)
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


@pytest.mark.parametrize("late", [0.0, 0.1], ids=["on-time", "first-query-late"])
@pytest.mark.parametrize("second", ["127.0.0.1", "::1"])
async def test_failover_to_the_next_server_is_paced_with_the_icmp_sweep(second, late, monkeypatch):
    """At MAX_PPS=4 the echo request and both queries (the first server answers
    SERVFAIL, the second the name) take three slots of one budget: the k-th packet on
    the wire comes no sooner than k slots (0.25 s each) after the job started.

    That is the pacer's promise: each send waits for its slot on a schedule, and a slot
    is never earlier than k intervals from the first. It is not a minimum gap between two
    sends: a send that leaves late is followed by one that leaves on schedule, sooner
    than 0.25 s after it (#87 owns whether that should change). Until #99 this test
    asserted every gap was at least 0.22 s, so it would fail whenever one packet left
    more than 30 ms after its slot and the next did not. `first-query-late` makes that
    happen on purpose, handing the first query to dnspython 0.1 s after its slot.

    The failure seen (once in 24 runs for #78, once in 200 for #99, both on macOS and
    both `::1`) was another dependency: the `::1` responder must share the port the
    kernel gave the 127.0.0.1 one (the resolver asks every server on one port), and
    that number can already be taken on ::1 by any other socket on the machine
    (`EADDRINUSE`). `responders_on_one_port` now tries other ports."""
    if second == "::1" and not _ipv6_loopback():
        pytest.skip("no IPv6 loopback here")
    if late:
        real_udp, calls = dns.asyncquery.udp, []

        async def udp(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                await asyncio.sleep(late)
            return await real_udp(*args, **kwargs)

        monkeypatch.setattr(dns.asyncquery, "udp", udp)
    if second == "127.0.0.1":
        first_t, first, port = await responder(script=("servfail", "answer"))
        second_t, second_p = first_t, first
    else:
        (first_t, first), (second_t, second_p), port = await responders_on_one_port(script=("servfail", "answer"))
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
            clock=scan.Clock(),
        )
        assert result["observations"] == [{"ip": "10.0.0.9", "rdns_name": "nas.home.arpa"}]
        assert result["complete"] is True
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
async def test_a_host_with_no_usable_resolver_gives_no_server(tmp_path, text):
    resolver = DnsResolver(resolv_conf=str(resolv_conf(tmp_path, text)))
    assert await resolver.servers(()) == ()


async def test_a_host_with_no_resolv_conf_gives_no_server(tmp_path):
    assert await DnsResolver(resolv_conf=str(tmp_path / "missing")).servers(()) == ()


async def test_the_jobs_servers_win_over_the_hosts(tmp_path):
    resolver = DnsResolver(resolv_conf=str(resolv_conf(tmp_path, "nameserver 127.0.0.1\n")))
    assert await resolver.servers(("10.0.0.53",)) == ("10.0.0.53",)
    assert await resolver.servers(()) == ("127.0.0.1",)


async def test_only_addresses_are_kept_before_the_list_is_cut(tmp_path):
    """MAX_DNS_SERVERS cuts the list after the entries the agent cannot query are
    dropped, so three of those first cannot empty it."""
    text = "".join(f"nameserver https://dns{i}.example.com/dns-query\n" for i in range(3))
    text += "nameserver 127.0.0.1\nnameserver ::1\nnameserver 127.0.0.2\nnameserver 127.0.0.3\n"
    resolver = DnsResolver(resolv_conf=str(resolv_conf(tmp_path, text)))
    assert await resolver.servers(()) == ("127.0.0.1", "::1", "127.0.0.2")
    assert len(await resolver.servers(tuple(f"127.0.0.{i}" for i in range(1, 8)))) == MAX_DNS_SERVERS


async def test_the_hosts_resolvers_are_read_again_for_every_job(tmp_path):
    """A failed read is not kept: the next call reads resolv.conf again, so a corrected
    file takes effect at the next job, in the same process."""
    path = resolv_conf(tmp_path, "")
    resolver = DnsResolver(resolv_conf=str(path))
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


# --- #99: what a cancelled lookup's datagram does ---


class Unwritable:
    """Stands in for a client socket whose kernel send buffer is full: a datagram is
    refused (`BlockingIOError`, so asyncio's transport keeps it and retries once the
    socket is writable) until `writable` is set, and then handed to the real socket.
    `log` records the first refusal and each send."""

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
            self._gate["queued"].set()
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


async def test_a_datagram_a_cancelled_lookup_had_handed_over_is_not_recalled(monkeypatch):
    """Briefed round 2 of PR #96, finding 1, held as what the spec now says. Two
    lookups, real resolver, loopback responder. 10.0.0.9's query is handed to its
    socket, which cannot take it yet, so asyncio's transport keeps it. Then 10.0.0.8's
    lookup could send its query to no server: 10.0.0.9's lookup is cancelled, its socket
    closed, and the scan returns its (incomplete) result set. No query is handed to a
    socket after the failure. When the socket becomes writable, asyncio still sends the
    datagram it kept: after the cancellation, after the result set was made. It took
    its slot, and goes to the job's server.

    The stand-in replaces the transport's `_sock`, an asyncio internal, on the client's
    sockets only; the responder's is made before it is in place."""
    transport, proto, port = await responder()
    loop = asyncio.get_running_loop()
    real_endpoint = loop.create_datagram_endpoint
    log: list[str] = []
    gate = {"writable": False, "queued": asyncio.Event()}

    async def endpoint(*args, **kwargs):
        made, protocol = await real_endpoint(*args, **kwargs)
        made._sock = Unwritable(made._sock, gate, log)
        return made, protocol

    real_udp = dns.asyncquery.udp
    handed: list[str] = []

    async def udp(query, where, **kwargs):
        name = query.question[0].name.to_text()
        handed.append(name)
        if name.startswith("8."):
            await gate["queued"].wait()
            log.append("failure")
            raise OSError("no route")
        return await real_udp(query, where, **kwargs)

    monkeypatch.setattr(dns.asyncquery, "udp", udp)
    loop.create_datagram_endpoint = endpoint
    try:
        raw = job(ranges=[{"start": "10.0.0.8", "end": "10.0.0.9"}], dns_servers=["127.0.0.1"])
        parsed = parse_job(job_ref(raw), raw)
        result = await scan.run(
            parsed, guard.plan(parsed, parse_allowed(None)), prober=TimedProber(alive=["10.0.0.8", "10.0.0.9"]),
            resolver=DnsResolver(port=port), max_pps=1000, clock=scan.Clock(),
        )
        log.append("result")
        assert result["complete"] is False and result["error"] == no_query_sent("OSError")
        assert result["observations"] == [{"ip": "10.0.0.8"}, {"ip": "10.0.0.9"}]
        await asyncio.sleep(0.05)
        assert proto.arrivals == []  # the kernel has not taken it yet
        gate["writable"] = True
        for _ in range(100):
            if proto.arrivals:
                break
            await asyncio.sleep(0.01)
        # no query handed over after the failure; the one kept went out after the result
        assert sorted(handed) == ["8.0.0.10.in-addr.arpa.", "9.0.0.10.in-addr.arpa."]
        assert log == ["queued", "failure", "result", "kernel took it"]
        assert len(proto.arrivals) == 1
        assert proto.questions == [("9.0.0.10.in-addr.arpa.", "PTR")]
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


def unblock_later(path, after: float = 2.0, until: float = 4.0) -> None:
    """A writer for the FIFO at `path` from `after` seconds on, so code that blocked on
    it (opened it to read without O_NONBLOCK) is freed: a test then fails on its
    assertion rather than hanging. It opens without blocking, and gives up at `until`."""

    def write():
        time.sleep(after)
        end = time.monotonic() + until - after
        while time.monotonic() < end:
            try:
                fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
            except OSError:
                time.sleep(0.01)  # no reader yet
                continue
            try:
                os.write(fd, b"nameserver 127.0.0.1\n")
            finally:
                os.close(fd)
            return

    threading.Thread(target=write, daemon=True).start()


async def test_a_fifo_at_the_resolver_path_leaves_the_job_incomplete_and_the_loop_running(tmp_path):
    """Acceptance for #99: with a FIFO where resolv.conf should be, a job naming no DNS
    server ends incomplete with the fixed error and sends no DNS packet, and a timer
    beside it keeps firing. The FIFO is refused at once, without waiting for the read's
    time bound: it is not a regular file, and opening it does not wait for a writer."""
    transport, proto, port = await responder()
    fifo = tmp_path / "resolv.conf"
    os.mkfifo(fifo)
    unblock_later(fifo)
    ticks, timer = ticker()
    try:
        raw = job(ranges=[{"start": "10.0.0.9", "end": "10.0.0.9"}], dns_servers=[])
        parsed = parse_job(job_ref(raw), raw)
        started = time.monotonic()
        result = await scan.run(
            parsed, guard.plan(parsed, parse_allowed(None)), prober=TimedProber(alive=["10.0.0.9"]),
            resolver=DnsResolver(port=port, resolv_conf=str(fifo)), max_pps=200, clock=scan.Clock(),
        )
        took = time.monotonic() - started
        await asyncio.sleep(0.05)
        assert result["complete"] is False
        assert result["error"] == NO_DNS_SERVER
        assert result["observations"] == [{"ip": "10.0.0.9"}]
        assert proto.arrivals == []
        assert longest_gap(ticks, time.monotonic()) < 0.2, ticks
        assert took < RESOLV_CONF_TIMEOUT_S
    finally:
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
        release.wait(5)  # freed by the test; 5 s at most, so a failure cannot hang
        return ("127.0.0.1",)

    monkeypatch.setattr(probe, "read_resolv_conf", stalled)
    resolver = DnsResolver(resolv_conf=str(tmp_path / "resolv.conf"), read_timeout=0.3)
    ticks, timer = ticker()
    try:
        started = time.monotonic()
        assert await resolver.servers(()) == ()
        waited = time.monotonic() - started
        assert 0.25 <= waited < 1.0
        assert longest_gap(ticks, time.monotonic()) < 0.2, ticks
        assert len(ticks) >= 10
        started = time.monotonic()
        assert await resolver.servers(()) == ()
        assert time.monotonic() - started < 0.1
        assert len(calls) == 1
        # a job's own servers need no read
        assert await resolver.servers(("10.0.0.53",)) == ("10.0.0.53",)
        release.set()
        for _ in range(100):
            if await resolver.servers(()) == ("127.0.0.1",):
                break
            await asyncio.sleep(0.01)
        assert await resolver.servers(()) == ("127.0.0.1",)
        assert len(calls) >= 2
    finally:
        release.set()
        timer.cancel()


def test_a_fifo_or_a_device_is_not_read_and_not_waited_on(tmp_path):
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    unblock_later(fifo)
    started = time.monotonic()
    assert read_resolv_conf(str(fifo)) == ()
    assert time.monotonic() - started < 0.5
    assert read_resolv_conf(os.devnull) == ()  # a character device
    assert read_resolv_conf(str(tmp_path)) == ()  # a directory
    # a FIFO that holds a whole file's worth and then its end (the writer has gone,
    # another reader keeps the data): it could be read like a file, and is not
    full = tmp_path / "full"
    os.mkfifo(full)
    unblock_later(full)
    reader = os.open(full, os.O_RDONLY | os.O_NONBLOCK)
    try:
        writer = os.open(full, os.O_WRONLY | os.O_NONBLOCK)
        os.write(writer, b"nameserver 127.0.0.1\n")
        os.close(writer)
        assert read_resolv_conf(str(full)) == ()
    finally:
        os.close(reader)


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


async def no_read_thread_left() -> bool:
    for _ in range(100):
        if not resolv_conf_threads():
            return True
        await asyncio.sleep(0.01)
    return False


@pytest.mark.parametrize("kind", ["fifo", "device", "directory", "oversized"])
async def test_what_is_not_an_ordinary_file_gives_no_server_and_leaves_no_thread(tmp_path, kind):
    """Acceptance for #104: a FIFO, a device (a link to /dev/null), a directory or a file
    over RESOLV_CONF_MAX at the resolver path gives no server at once, well inside the
    read's time bound, while a timer beside it keeps firing. No read thread is left
    behind, and once the path holds an ordinary file again the next job reads it."""
    path = tmp_path / "resolv.conf"
    if kind == "fifo":
        os.mkfifo(path)
        unblock_later(path)  # frees a read that opened it and waited, so a failure cannot hang
    elif kind == "device":
        path.symlink_to(os.devnull)
    elif kind == "directory":
        path.mkdir()
    else:
        path.write_bytes(b"nameserver 127.0.0.1\n" + b"#" * RESOLV_CONF_MAX)
    resolver = DnsResolver(resolv_conf=str(path))
    ticks, timer = ticker()
    try:
        started = time.monotonic()
        assert await resolver.servers(()) == ()
        assert time.monotonic() - started < RESOLV_CONF_TIMEOUT_S / 2
        await asyncio.sleep(0.05)
        assert longest_gap(ticks, time.monotonic()) < 0.2, ticks
        assert await no_read_thread_left()
        if kind == "directory":
            path.rmdir()
        else:
            path.unlink()
        path.write_text("nameserver 127.0.0.1\n")
        assert await resolver.servers(()) == ("127.0.0.1",)
        assert await no_read_thread_left()
    finally:
        timer.cancel()


async def test_a_fifo_put_at_the_path_after_the_look_holds_one_thread_until_a_writer_comes(monkeypatch, tmp_path):
    """What #104 gives up, held to its bound: the path is looked at (`os.stat`) before
    dnspython opens it, so a FIFO swapped in between those two blocks the open until a
    writer comes. The swap is stood in for by a `stat` that calls the FIFO a regular
    file. The job waits `read_timeout` and finds no server, the loop runs on, the next
    job starts no second read; once a writer comes the thread ends, and the next job
    reads the file then at the path."""

    class LooksRegular:  # what `os.stat` saw before the swap
        @staticmethod
        def S_ISREG(mode):
            return True

    fifo = tmp_path / "resolv.conf"
    os.mkfifo(fifo)
    monkeypatch.setattr(probe, "stat", LooksRegular)
    resolver = DnsResolver(resolv_conf=str(fifo), read_timeout=0.3)
    ticks, timer = ticker()
    freed = False
    try:
        started = time.monotonic()
        assert await resolver.servers(()) == ()
        assert 0.25 <= time.monotonic() - started < 1.0
        assert longest_gap(ticks, time.monotonic()) < 0.2, ticks
        assert len(resolv_conf_threads()) == 1  # blocked in dnspython's open
        started = time.monotonic()
        assert await resolver.servers(()) == ()  # no second read while it is blocked
        assert time.monotonic() - started < 0.1
        assert len(resolv_conf_threads()) == 1
        monkeypatch.undo()
        unblock_later(fifo, after=0)
        freed = True
        assert await no_read_thread_left()
        fifo.unlink()
        fifo.write_text("nameserver 127.0.0.1\n")
        assert await resolver.servers(()) == ("127.0.0.1",)
    finally:
        if not freed:
            unblock_later(fifo, after=0)
        timer.cancel()


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
        release.wait(5)  # freed by the test; 5 s at most, so a failure cannot hang
        return ("127.0.0.1",)

    class SlowProber:
        async def ping(self, address, timeout):
            await asyncio.sleep(0.8)
            return True

    monkeypatch.setattr(probe, "read_resolv_conf", stalled)
    resolver = DnsResolver(resolv_conf=str(tmp_path / "resolv.conf"))
    assert resolver.read_timeout > 0.5  # the read's own bound is longer than the job has left
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
        assert 0.95 <= took < 1.4, took
        assert len(calls) == 1
        assert await resolver.servers(()) == ()  # still blocked: no second read
        assert len(calls) == 1
        release.set()
        for _ in range(100):
            if await resolver.servers(()) == ("127.0.0.1",):
                break
            await asyncio.sleep(0.01)
        assert len(calls) >= 2
    finally:
        release.set()
