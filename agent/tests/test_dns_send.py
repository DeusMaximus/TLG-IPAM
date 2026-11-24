"""`DnsResolver.ptr` through the installed dnspython (#125, the briefed review of PR #123,
finding 1).

A PTR lookup takes its slot (`scan.Pacer`) after its socket is made, and the slot ends
when the step it was granted in ends (#121): the next slot is measured from then. That
the query goes before the slot ends relies on a fact about the path from `ptr` to the
kernel, not about the pacer: `probe.DnsResolver.ptr` calls `dns.asyncquery.udp`, which
awaits nothing before `QuerySocket.sendto` when it is handed a socket, and `sendto`
hands the datagram to the transport in its own first step. A yield anywhere on that path
(in `ptr`, in a release of dnspython) moves the query past the end of its slot, so a
delay there puts two packets closer than `1/MAX_PPS` again. `test_probe.py` compares each
query with the grants, the next one included, and not with the end of its own slot, so
nothing there sees it.

This is the counterpart of `test_icmplib_send.py`: the real `DnsResolver.ptr`, the
installed dnspython and `probe.QuerySocket`, and only the transport it is given replaced
(`create_datagram_endpoint` on the running loop). The stand-in transport records whether
the slot of the grant is still open when it is handed the datagram, and answers through
`datagram_received`. No socket is opened and no packet is sent.

The ARP request needs no such test. `ArpSession.request` hands the frame to the
`PacketLink` the agent owns (`probe.PacketLink`, a raw socket it made itself) with no
library and no `await` between the slot and `sendto`, and that is what the existing
ARP tests in `test_probe.py` and `test_pacing.py` check by their stand-in links."""

import asyncio
import ipaddress

import dns.asyncquery
import dns.message
import dns.reversename
import dns.rrset

from tlg_ipam_agent import scan
from tlg_ipam_agent.probe import DnsResolver

LOOKUPS = 12
PPS = 100  # no slot waits: the clock is always overdue
SERVER = "127.0.0.53"
OPEN = "a query must be handed to the transport while the slot of its grant is open"


class CountingPacer(scan.Pacer):
    """The real pacer, with the grants it has given and the slots that have ended counted.
    A slot is open between the grant and the callback the event loop runs after the step
    that holds it. Slots end in the order they were granted, so slot k is open while
    fewer than k slots have ended."""

    grants = 0
    ended = 0

    async def wait(self):
        await super().wait()
        self.grants += 1

    def _ended(self):
        self.ended += 1
        super()._ended()


def overdue_clock():
    """A clock that is always past the next slot, so grants come without a sleep: only
    the step ends, as with a scan that has fallen behind its budget."""
    now = 0.0

    def monotonic():
        nonlocal now
        now += 10.0
        return now

    async def sleep(seconds):
        raise AssertionError("an overdue slot waits for nothing")

    return scan.Clock(monotonic=monotonic, sleep=sleep)


class FakeTransport(asyncio.DatagramTransport):
    """What `create_datagram_endpoint` returns in place of a UDP transport. `sendto`
    records the destination and whether the slot of the lookup that sent the datagram (the
    query names its address; `granted` says which slot that lookup was given) is still
    open, and sends nothing: the reply is handed to the protocol in the next step of the
    loop, as the transport would on receiving it."""

    def __init__(self, protocol, pacer, granted, log):
        super().__init__()
        self._protocol, self._pacer, self._granted, self._log = protocol, pacer, granted, log

    def sendto(self, data, addr=None):
        query = dns.message.from_wire(data)
        question = query.question[0]
        slot = self._granted[dns.reversename.to_address(question.name)]
        self._log.append((addr, self._pacer.ended < slot))
        reply = dns.message.make_response(query)
        reply.answer.append(dns.rrset.from_text(question.name, 300, "IN", "PTR", "host.home.arpa."))
        asyncio.get_running_loop().call_soon(self._protocol.datagram_received, reply.to_wire(), addr)

    def get_write_buffer_size(self):
        return 0

    def abort(self):
        pass

    def close(self):
        pass


async def handoffs(monkeypatch):
    """Run LOOKUPS lookups at once through the real `DnsResolver.ptr`, all taking their
    slots from one pacer as `scan.run`'s do, and return `(destinations, whether the
    slot was open)` in the order the queries reached the transport."""
    pacer, granted, log = CountingPacer(PPS, overdue_clock()), {}, []

    async def create_datagram_endpoint(protocol_factory, **kwargs):
        protocol = protocol_factory()
        transport = FakeTransport(protocol, pacer, granted, log)
        protocol.connection_made(transport)
        return transport, protocol

    monkeypatch.setattr(asyncio.get_running_loop(), "create_datagram_endpoint", create_datagram_endpoint)
    resolver = DnsResolver()

    async def lookup(address):
        async def slot():
            await pacer.wait()
            granted[str(address)] = pacer.grants  # the step of the grant: no other has run
            return 5.0

        return await resolver.ptr(address, (SERVER,), slot)

    addresses = [ipaddress.ip_address(f"10.0.0.{i}") for i in range(1, LOOKUPS + 1)]
    names = await asyncio.gather(*(lookup(address) for address in addresses))
    assert names == ["host.home.arpa."] * LOOKUPS  # each lookup ran to its answer
    assert pacer.grants == pacer.ended == LOOKUPS
    return [addr for addr, _ in log], [slot_open for _, slot_open in log]


async def test_each_dns_query_reaches_the_transport_in_the_step_its_slot_was_granted_in(monkeypatch):
    """Every slot overdue: each lookup takes its slot, and in that step `dns.asyncquery.udp`
    and `QuerySocket.sendto` hand the datagram to the transport. The slot is still open
    then, so the next one is measured from after the hand-off. A yield ahead of the send,
    in `DnsResolver.ptr` or in dnspython, moves it past the end of its slot and this goes
    red."""
    destinations, slot_open = await handoffs(monkeypatch)
    assert destinations == [(SERVER, 53)] * LOOKUPS
    assert slot_open == [True] * LOOKUPS, OPEN


async def test_the_check_sees_an_await_ahead_of_the_send(monkeypatch):
    """The control: the same run with an `await` at the entry of `dns.asyncquery.udp`, the
    change the check is there for. Each query reaches the transport after its slot has
    ended, which is what the check reports."""
    real = dns.asyncquery.udp

    async def awaits_first(*args, **kwargs):
        await asyncio.sleep(0)
        return await real(*args, **kwargs)

    monkeypatch.setattr(dns.asyncquery, "udp", awaits_first)
    destinations, slot_open = await handoffs(monkeypatch)
    assert destinations == [(SERVER, 53)] * LOOKUPS
    assert slot_open == [False] * LOOKUPS
