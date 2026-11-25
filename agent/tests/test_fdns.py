"""The forward-DNS check on the agent (#20, spec §5.2): the names a job may carry, how
an A reply is read, the real resolver's A lookups against a DNS responder on loopback,
and how a job's forward lookups are run and reported. Nothing here leaves 127.0.0.1."""

import asyncio
import socket

import dns.message
import dns.name
import dns.rcode
import dns.rdatatype
import dns.rrset
import pytest

from conftest import FakeClock, FakeProber, FakeResolver, job
from test_probe import unpaced, with_records
from test_scan import planned, sweep
from tlg_ipam_agent import probe, scan
from tlg_ipam_agent.config import parse_allowed_dns
from tlg_ipam_agent.probe import NO_NAME, DnsResolver, ProbeError, UnreadReplies, no_query_sent
from tlg_ipam_agent.protocol import FDNS_JOB_MAX, FDNS_MAX, JobRefused, fdns_name, job_ref, parse_job

HOST_A = "00000000-0000-4000-8000-00000000000a"
HOST_B = "00000000-0000-4000-8000-00000000000b"
HOST_C = "00000000-0000-4000-8000-00000000000c"


def names(*pairs):
    return [{"host_id": h, "fqdn": f} for h, f in pairs]


# --- What a job may carry ---


def test_a_jobs_names_are_parsed_and_kept_only_for_a_job_issued_fdns():
    raw = job(methods=["icmp", "fdns"], fdns=names((HOST_A, "nas.home.arpa"), (HOST_B, "a.b.home.arpa")))
    parsed = parse_job(job_ref(raw), raw)
    assert parsed.fdns == ((HOST_A, "nas.home.arpa"), (HOST_B, "a.b.home.arpa"))
    # without the method, nothing is asked whatever the list holds
    raw = job(methods=["icmp", "rdns"], fdns=names((HOST_A, "nas.home.arpa")))
    assert parse_job(job_ref(raw), raw).fdns == ()
    # an older server's job, which had no list
    raw = job(methods=["icmp", "fdns"])
    del raw["fdns"]
    assert parse_job(job_ref(raw), raw).fdns == ()


@pytest.mark.parametrize(
    "entries",
    [
        "nas.home.arpa",
        [["x", "nas.home.arpa"]],
        names((HOST_A, "NAS.home.arpa")),  # not in the one form the server sends
        names((HOST_A, "nas.home.arpa.")),
        names((HOST_A, "nas_1.home.arpa")),
        names((HOST_A, "nas home.arpa")),
        names((HOST_A, "-nas.home.arpa")),
        names((HOST_A, "näs.home.arpa")),
        names((HOST_A, "a" * 64 + ".home.arpa")),
        names((HOST_A, ".".join(["a" * 63] * 4))),  # 255 characters
        names((HOST_A, "")),
        [{"host_id": HOST_A}],
        [{"host_id": HOST_A, "fqdn": 7}],
        names(("", "nas.home.arpa")),
        names(("x" * 37, "nas.home.arpa")),
        names(("id\n", "nas.home.arpa")),
        names((HOST_A, "nas.home.arpa"), (HOST_A, "other.home.arpa")),  # one host twice
    ],
)
def test_a_job_naming_anything_but_one_host_id_and_a_dns_hostname_is_refused(entries):
    """The agent cannot tell whether a name is under the subnet's domain; it holds that
    every name it would ask is a DNS hostname, so a server (or whoever holds it) can
    make it send nothing else. The job is refused whole, before any packet."""
    raw = job(methods=["icmp", "fdns"], fdns=entries)
    with pytest.raises(JobRefused) as refused:
        parse_job(job_ref(raw), raw)
    assert refused.value.reason.startswith("refused: ")


def test_a_job_naming_more_hosts_than_the_server_sends_is_refused():
    """Round 1 of #185's briefed review, finding 2: the server sends at most
    FDNS_JOB_MAX names a job, so 1,025 distinct valid names are refused before a query,
    though a result set may carry up to FDNS_MAX answers."""
    assert FDNS_JOB_MAX == 1024 < FDNS_MAX
    entries = [{"host_id": f"{i:036d}", "fqdn": f"h{i}.home.arpa"} for i in range(FDNS_JOB_MAX + 1)]
    raw = job(methods=["icmp", "fdns"], fdns=entries)
    with pytest.raises(JobRefused, match=f"more than {FDNS_JOB_MAX}"):
        parse_job(job_ref(raw), raw)
    raw["fdns"] = entries[:FDNS_JOB_MAX]
    assert len(parse_job(job_ref(raw), raw).fdns) == FDNS_JOB_MAX


def test_the_longest_dns_name_is_a_name_a_job_may_carry():
    longest = ".".join(["a" * 63] * 3 + ["b" * 61])
    assert len(longest) == 253 and fdns_name(longest) == longest
    assert fdns_name(longest + "b") is None


# --- How an A reply is read ---

_Q = "nas.home.arpa."
_ALIAS = "alias.home.arpa."
_OUT = "nas.example.com."


def _reply(answer=(), authority=(), rcode=dns.rcode.NOERROR):
    query = dns.message.make_query(_Q, dns.rdatatype.A)
    reply = dns.message.make_response(query)
    reply.set_rcode(rcode)
    for section, records in ((reply.answer, answer), (reply.authority, authority)):
        for owner, kind, data in records:
            section.append(dns.rrset.from_text(owner, 60, "IN", kind, data))
    return reply


def _soa(zone):
    return (zone, "SOA", "ns.example.com. admin.example.com. 1 60 60 60 60")


def _a(owner, *addresses):
    return [(owner, "A", a) for a in addresses]


READINGS = {
    "one": (_a(_Q, "10.0.0.9"), (), None, ("10.0.0.9",)),
    "several-sorted-once": (_a(_Q, "10.0.0.20", "10.0.0.3", "10.0.0.20"), (), None, ("10.0.0.3", "10.0.0.20")),
    "any-case": (_a(_Q.upper(), "10.0.0.9"), (), None, ("10.0.0.9",)),
    "after-a-cname": (((_Q, "CNAME", _ALIAS), *_a(_ALIAS, "10.0.0.9")), (), None, ("10.0.0.9",)),
    "with-another-beside": ((*_a(_Q, "10.0.0.9"), *_a("other.home.arpa.", "10.0.0.8")), (), None, ("10.0.0.9",)),
    "at-another-owner": (_a("other.home.arpa.", "10.0.0.8"), (), None, None),
    "at-the-alias": (((_Q, "CNAME", _ALIAS), *_a(_Q, "10.0.0.9")), (), None, None),
    "cname-loop": (((_Q, "CNAME", _ALIAS), (_ALIAS, "CNAME", _Q)), (_soa("home.arpa."),), None, None),
    "cname-only": (((_Q, "CNAME", _OUT),), (), None, None),
    "nxdomain": ((), (_soa("home.arpa."),), dns.rcode.NXDOMAIN, NO_NAME),
    "servfail": ((), (), dns.rcode.SERVFAIL, None),
    "refused": ((), (), dns.rcode.REFUSED, None),
    "nodata": ((), (_soa("home.arpa."),), None, NO_NAME),
    "empty": ((), (), None, NO_NAME),
    "foreign-soa": ((), (_soa("example.com."),), None, None),
    "referral": ((), (("home.arpa.", "NS", "ns.example.com."),), None, None),
    "cname-nodata": (((_Q, "CNAME", _OUT),), (_soa("example.com."),), None, NO_NAME),
    "cname-origin-soa": (((_Q, "CNAME", _OUT),), (_soa("home.arpa."),), None, None),
    "aaaa-only": (((_Q, "AAAA", "2001:db8::9"),), (), None, None),
}


@pytest.mark.parametrize("case", list(READINGS))
def test_what_an_a_reply_says(case):
    """Read as a PTR reply is (#144, #149): A records only at the name the answer is
    about, the question's or its CNAME chain's end; NXDOMAIN, or NODATA for that name,
    is a name that does not resolve; anything else is no usable answer."""
    answer, authority, rcode, says = READINGS[case]
    reply = _reply(answer, authority, rcode if rcode is not None else dns.rcode.NOERROR)
    got = probe.read_a_reply(reply, dns.name.from_text(_Q))
    assert got == says if isinstance(says, tuple) else got is says


# --- The real resolver, against a responder on loopback ---


class AResponder(asyncio.DatagramProtocol):
    """Answers A queries from `zone` (name -> addresses; a name not there is NXDOMAIN).
    `script` gives the action for each query in turn, the last repeating: "answer",
    "silent", "servfail", "over-budget" (the answer with records past the budget a
    header may claim)."""

    def __init__(self, zone, script=("answer",)):
        self.zone = dict(zone)
        self.script = list(script)
        self.queries: list[dns.message.Message] = []

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        query = dns.message.from_wire(data)
        self.queries.append(query)
        action = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if action == "silent":
            return
        question = query.question[0]
        reply = dns.message.make_response(query)
        if action == "servfail":
            reply.set_rcode(dns.rcode.SERVFAIL)
        elif question.name.to_text() not in self.zone:
            reply.set_rcode(dns.rcode.NXDOMAIN)
        else:
            addresses = self.zone[question.name.to_text()]
            if addresses:
                reply.answer.append(dns.rrset.from_text_list(question.name, 60, "IN", "A", addresses))
            else:
                reply.authority.append(dns.rrset.from_text(
                    "home.arpa.", 60, "IN", "SOA", "ns.home.arpa. admin.home.arpa. 1 60 60 60 60"
                ))
        if action == "over-budget":
            self.transport.sendto(with_records(reply, probe.DNS_RECORDS_MAX + 1), addr)
            return
        self.transport.sendto(reply.to_wire(), addr)


async def a_responder(zone, **kwargs):
    loop = asyncio.get_running_loop()
    transport, proto = await loop.create_datagram_endpoint(
        lambda: AResponder(zone, **kwargs), local_addr=("127.0.0.1", 0), family=socket.AF_INET
    )
    return transport, proto, transport.get_extra_info("sockname")[1]


ZONE = {"nas.home.arpa.": ["10.0.0.9", "10.0.0.19"], "printer.home.arpa.": []}


async def test_an_a_lookup_against_the_jobs_server():
    """One query per name, for its A records only, as an absolute name, with recursion
    desired and the payload advertised: the addresses, a name with no A record and one
    that does not exist both as a name that does not resolve."""
    transport, proto, port = await a_responder(ZONE)
    try:
        resolver = DnsResolver(port=port)
        servers = ("127.0.0.1",)
        assert await resolver.a("nas.home.arpa", servers, unpaced()) == ("10.0.0.9", "10.0.0.19")
        assert await resolver.a("printer.home.arpa", servers * 3, unpaced()) is NO_NAME
        assert await resolver.a("gone.home.arpa", servers * 3, unpaced()) is NO_NAME
        assert len(proto.queries) == 3
        for query, asked in zip(proto.queries, ("nas.home.arpa.", "printer.home.arpa.", "gone.home.arpa.")):
            [question] = query.question
            assert question.name.to_text() == asked and question.rdtype == dns.rdatatype.A
            assert query.flags & dns.flags.RD and query.payload == probe.DNS_PAYLOAD
    finally:
        transport.close()


async def test_an_a_lookup_takes_a_slot_for_every_query_and_goes_on_past_no_usable_answer():
    """The hardened path is the PTR lookups' own: a slot of the budget before each
    query, the next server after silence, SERVFAIL or a reply refused unread, which is
    said beside the answer a later server gave (#170), or in place of None when no
    server gave one (#164)."""
    transport, proto, port = await a_responder(ZONE, script=["silent", "servfail", "silent", "over-budget", "answer"])
    taken = []

    def counting():
        async def slot():
            taken.append(1)
            return 0.3

        return slot

    try:
        resolver = DnsResolver(port=port)
        servers = ("127.0.0.1",) * 3
        assert await resolver.a("nas.home.arpa", servers, counting()) is None
        assert len(taken) == len(proto.queries) == 3
        got = await resolver.a("nas.home.arpa", servers, counting())
        assert got == probe.AnsweredPastRefusal(("10.0.0.9", "10.0.0.19"), False, True)
        assert len(taken) == len(proto.queries) == 5
        proto.script = ["over-budget"]
        assert await resolver.a("nas.home.arpa", servers, counting()) == UnreadReplies(False, True)
        assert len(taken) == len(proto.queries) == 8
    finally:
        transport.close()


async def test_an_a_lookup_with_no_server_or_no_query_sent_is_no_lookup_and_says_fdns():
    resolver = DnsResolver()
    with pytest.raises(ProbeError) as raised:
        await resolver.a("nas.home.arpa", (), unpaced())
    assert str(raised.value) == probe.no_dns_server("fdns")
    assert str(raised.value).startswith("fdns not run: the agent has no DNS server to ask")
    with pytest.raises(ProbeError) as raised:
        await resolver.a("nas.home.arpa", ("not-an-address",), unpaced())
    assert str(raised.value) == no_query_sent("ValueError", "fdns")


# --- A job's forward lookups ---


def fdns_job(**overrides):
    entries = names((HOST_A, "nas.home.arpa"), (HOST_B, "printer.home.arpa"), (HOST_C, "gone.home.arpa"))
    return planned(**{"methods": ["icmp", "rdns", "fdns"], "fdns": entries, **overrides})


async def test_each_name_a_server_answered_has_an_entry_and_one_no_server_answered_has_none():
    """The result set's `fdns`: the addresses for a name that resolves, an empty list
    for one a server answered does not, and no entry for one no server gave a usable
    answer for, so the server keeps what it has. The scan stays complete; the error
    counts the names without an answer."""
    resolver = FakeResolver(addresses={"nas.home.arpa": ("10.0.0.9",), "printer.home.arpa": NO_NAME,
                                       "gone.home.arpa": None})
    parsed, addresses = fdns_job()
    result = await sweep(parsed, addresses, prober=FakeProber(alive=["10.0.0.9"]), resolver=resolver)
    assert result["fdns"] == [
        {"host_id": HOST_A, "addresses": ["10.0.0.9"]},
        {"host_id": HOST_B, "addresses": []},
    ]
    assert result["complete"] is True and result["liveness_complete"] is True
    assert result["error"] == scan.fdns_no_answer(1, 3)
    assert result["error"] == "fdns: no DNS server gave a usable answer for 1 of 3 names; their stored answers are kept"
    # after the PTR lookups, through the same servers
    assert [n for n, _ in resolver.resolved] == ["nas.home.arpa", "printer.home.arpa", "gone.home.arpa"]
    assert {s for _, s in resolver.resolved} == {s for _, s in resolver.asked}


async def test_the_forward_lookups_run_without_a_live_address_and_without_rdns():
    resolver = FakeResolver(addresses={"nas.home.arpa": ("10.0.0.9", "10.0.0.10")})
    parsed, addresses = fdns_job(methods=["icmp", "fdns"])
    result = await sweep(parsed, addresses, prober=FakeProber(), resolver=resolver)
    assert resolver.asked == [] and len(resolver.resolved) == 3
    assert result["fdns"][0] == {"host_id": HOST_A, "addresses": ["10.0.0.9", "10.0.0.10"]}
    assert result["complete"] is True and result["error"] is None


async def test_refusals_and_a_later_answer_are_counted_in_the_forward_lookups_own_words():
    resolver = FakeResolver(addresses={
        "nas.home.arpa": probe.AnsweredPastRefusal(("10.0.0.9",), True, False),
        "printer.home.arpa": UnreadReplies(False, True),
        "gone.home.arpa": UnreadReplies(True, True),
    })
    parsed, addresses = fdns_job(methods=["icmp", "fdns"])
    result = await sweep(parsed, addresses, prober=FakeProber(), resolver=resolver)
    assert result["fdns"] == [{"host_id": HOST_A, "addresses": ["10.0.0.9"]}]
    assert result["error"] == "; ".join([
        "fdns: no DNS server gave a usable answer for 2 of 3 names, answers kept; the agent refused a reply "
        "over 1232 bytes in 1 lookup, over 1 question or 32 records in 2 lookups",
        "fdns: a later server answered after the agent refused a reply over 1232 bytes in 1 lookup",
    ])
    assert result["complete"] is True


@pytest.mark.parametrize(
    "methods, alive, said",
    [
        (["icmp", "fdns"], [], "fdns"),
        (["icmp", "rdns", "fdns"], [], "fdns"),  # no live address: no PTR lookup to make
        (["icmp", "rdns", "fdns"], ["10.0.0.9"], "rdns and fdns"),
    ],
)
async def test_with_no_dns_server_the_error_names_the_lookups_not_made(methods, alive, said):
    resolver = FakeResolver(host=())
    parsed, addresses = fdns_job(methods=methods)
    result = await sweep(parsed, addresses, prober=FakeProber(alive=alive), resolver=resolver)
    assert result["error"] == probe.no_dns_server(said)
    assert result["complete"] is False and result["liveness_complete"] is True
    assert result["fdns"] == [] and resolver.resolved == [] and resolver.asked == []


async def test_allowed_dns_servers_bounds_the_forward_lookups_too():
    """#93's limit holds for every DNS query the agent sends: a server outside it is
    asked nothing, and with none left neither lookup is made."""
    parsed, addresses = fdns_job(dns_servers=["192.0.2.53", "127.0.0.1"])
    resolver = FakeResolver(addresses={"nas.home.arpa": ("10.0.0.9",)})
    result = await sweep(
        parsed, addresses, prober=FakeProber(alive=["10.0.0.9"]), resolver=resolver,
        allowed_dns=parse_allowed_dns("127.0.0.1"),
    )
    assert {s for _, s in resolver.resolved} == {("127.0.0.1",)}
    assert result["error"].startswith(scan.outside_allowed(1, 2, "rdns and fdns"))
    assert result["fdns"][0] == {"host_id": HOST_A, "addresses": ["10.0.0.9"]}
    resolver = FakeResolver()
    result = await sweep(
        parsed, addresses, prober=FakeProber(alive=["10.0.0.9"]), resolver=resolver,
        allowed_dns=parse_allowed_dns("10.9.9.9"),
    )
    assert result["error"] == "rdns and fdns not run: all 2 DNS servers to ask are outside ALLOWED_DNS_SERVERS"
    assert resolver.resolved == [] and resolver.asked == [] and result["complete"] is False


async def test_no_forward_query_follows_ptr_lookups_that_could_send_nothing():
    class Stops(FakeResolver):
        async def ptr(self, address, servers, slot):
            await slot()
            raise ProbeError(no_query_sent("OSError"))

    resolver = Stops()
    parsed, addresses = fdns_job()
    result = await sweep(parsed, addresses, prober=FakeProber(alive=["10.0.0.9"]), resolver=resolver)
    assert resolver.resolved == []
    assert result["error"] == f"{no_query_sent('OSError')}; {scan.FDNS_AFTER_RDNS_STOPPED}"
    assert result["complete"] is False and result["fdns"] == []


async def test_forward_lookups_that_could_send_nothing_stop_and_keep_what_was_answered():
    class Stops(FakeResolver):
        async def a(self, name, servers, slot):
            await slot()
            if name == "gone.home.arpa":
                for _ in range(50):
                    await asyncio.sleep(0)
                raise ProbeError(no_query_sent("OSError", "fdns"))
            self.resolved.append((name, servers))
            return ("10.0.0.9",)

    parsed, addresses = fdns_job(methods=["icmp", "fdns"])
    result = await sweep(parsed, addresses, prober=FakeProber(), resolver=Stops())
    assert result["error"] == no_query_sent("OSError", "fdns")
    assert result["complete"] is False and result["liveness_complete"] is True
    assert [e["host_id"] for e in result["fdns"]] == [HOST_A, HOST_B]


async def test_a_ptr_timeout_ends_the_job_before_its_forward_lookups():
    clock = FakeClock()
    resolver = FakeResolver(clock=clock, delay=700.0)
    parsed, addresses = fdns_job()
    result = await sweep(parsed, addresses, prober=FakeProber(alive=["10.0.0.9"]), resolver=resolver, clock=clock)
    assert result["error"] == "timed out after 600 s during the PTR lookups"
    assert resolver.resolved == [] and result["complete"] is False


async def test_forward_lookups_cut_short_by_the_deadline_say_so():
    clock = FakeClock()

    class Slow(FakeResolver):
        async def a(self, name, servers, slot):
            await slot()
            await clock.sleep(700.0)
            return ("10.0.0.9",)

    parsed, addresses = fdns_job(methods=["icmp", "fdns"])
    result = await sweep(parsed, addresses, prober=FakeProber(), resolver=Slow(), clock=clock)
    assert result["error"] == "timed out after 600 s during the forward lookups"
    assert result["complete"] is False and result["liveness_complete"] is True


async def test_every_forward_query_takes_a_slot_of_the_budget():
    """Paced with the sweep: at 10 packets a second, 254 echo requests and 3 forward
    queries take 25.6 s of the clock, and no query goes sooner than its slot."""
    clock = FakeClock()
    resolver = FakeResolver(clock=clock)
    times = []

    class Timed(FakeResolver):
        async def a(self, name, servers, slot):
            await slot()
            times.append(clock.t)
            return ("10.0.0.9",)

    parsed, addresses = fdns_job(methods=["icmp", "fdns"])
    result = await sweep(parsed, addresses, prober=FakeProber(clock=clock), resolver=Timed(), clock=clock, max_pps=10)
    assert len(times) == 3 and result["complete"] is True
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert all(g >= 0.1 - 1e-9 for g in gaps)
    assert times[0] >= 254 / 10 - 1e-9
    del resolver
