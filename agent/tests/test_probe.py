"""The real PTR resolver against a DNS responder on loopback: the one packet path
these tests exercise for real, and it never leaves 127.0.0.1."""

import asyncio
import ipaddress

import dns.message
import dns.rcode
import dns.rdatatype
import dns.rrset

from tlg_ipam_agent.probe import DnsResolver

ANSWERS = {
    "9.0.0.10.in-addr.arpa.": "nas.home.arpa.",
    "8.0.0.10.in-addr.arpa.": "Evil\\032name<b>.home.arpa.",
}


class Responder(asyncio.DatagramProtocol):
    def __init__(self):
        self.questions = []

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        query = dns.message.from_wire(data)
        reply = dns.message.make_response(query)
        question = query.question[0]
        self.questions.append((question.name.to_text(), dns.rdatatype.to_text(question.rdtype)))
        target = ANSWERS.get(question.name.to_text())
        if target is None:
            reply.set_rcode(dns.rcode.NXDOMAIN)
        else:
            reply.answer.append(dns.rrset.from_text(question.name, 60, "IN", "PTR", target))
        self.transport.sendto(reply.to_wire(), addr)


async def test_ptr_lookup_against_the_jobs_server():
    loop = asyncio.get_running_loop()
    transport, responder = await loop.create_datagram_endpoint(Responder, local_addr=("127.0.0.1", 0))
    port = transport.get_extra_info("sockname")[1]
    try:
        resolver = DnsResolver()
        servers = ("127.0.0.1",)
        resolver._resolver(servers).port = port  # the job's server, on a test port
        assert await resolver.ptr(ipaddress.ip_address("10.0.0.9"), servers, 2.0) == "nas.home.arpa."
        assert await resolver.ptr(ipaddress.ip_address("10.0.0.7"), servers, 2.0) is None
        # the raw answer comes back; `scan` drops it with agent_protocol's rule
        assert await resolver.ptr(ipaddress.ip_address("10.0.0.8"), servers, 2.0) == "Evil\\032name<b>.home.arpa."
        assert responder.questions[0] == ("9.0.0.10.in-addr.arpa.", "PTR")
    finally:
        transport.close()


async def test_a_server_that_does_not_answer_gives_no_name():
    loop = asyncio.get_running_loop()
    # bound, never answering
    transport, _ = await loop.create_datagram_endpoint(asyncio.DatagramProtocol, local_addr=("127.0.0.1", 0))
    port = transport.get_extra_info("sockname")[1]
    try:
        resolver = DnsResolver()
        resolver._resolver(("127.0.0.1",)).port = port
        assert await resolver.ptr(ipaddress.ip_address("10.0.0.9"), ("127.0.0.1",), 0.3) is None
    finally:
        transport.close()
