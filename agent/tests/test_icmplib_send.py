"""`IcmpProber` through the installed icmplib (#107, item 6; the briefed review of PR
#110, finding 2).

A paced probe takes its own slot (`scan._Pool.run`), and the slot ends when the step it
was granted in ends (`scan.Pacer`, #121): the next slot is measured from then. That the
echo request goes before the slot ends relies on a fact about icmplib, not about the
agent: for an IP address, `async_ping` hands the echo request to the kernel before its
first `await`, so in the step the probe's slot was granted in. The tests in
`test_pacing.py` use stand-in probers and never reach icmplib, so they would stay green
if a release of icmplib put an `await` ahead of the send. These tests run the real `IcmpProber.ping` and the
installed `icmplib.async_ping` (its socket wrapper, its packet building, its
`getaddrinfo` of the address), and replace only the raw socket it opens
(`ICMPv4Socket._create_socket`): the stand-in records the call to `sendto` and nothing
leaves the host. No test here opens a raw socket or sends a packet."""

import asyncio
import ipaddress
import socket
import time

import icmplib
import icmplib.sockets
import pytest

from tlg_ipam_agent import scan
from tlg_ipam_agent.probe import IcmpProber

PROBES = 40  # fewer than scan.PING_CONCURRENCY: none waits for another to finish
REPLY_WAIT_S = 0.05  # no reply ever comes: each probe waits this long, then gives up


class OverduePacer:
    """A pacer whose every slot is overdue, so its grants come without a sleep.
    `grants` says how many have been given, and `ended` how many of the steps they were
    granted in have ended: each slot ends with its step, as `scan.Pacer`'s does."""

    grants = 0
    ended = 0

    async def wait(self):
        self.grants += 1
        asyncio.get_running_loop().call_soon(self._end)

    def _end(self):
        self.ended += 1


class FakeRawSocket:
    """What `ICMPv4Socket._create_socket` returns in place of the raw socket. `sendto`
    records the destination and how many slots the pacer had granted and ended at that
    moment, and sends nothing. A unix socket pair that is never written to stands behind `recv`, so
    icmplib's wait for a reply is a real wait that ends in its timeout."""

    family = socket.AF_INET
    type = socket.SOCK_RAW

    def __init__(self, log, pacer):
        self._log, self._pacer = log, pacer
        self._reader, self._writer = socket.socketpair()
        self._reader.setblocking(False)

    def sendto(self, packet, destination):
        assert packet[0] == 8  # an ICMP echo request, built by icmplib
        self._log.append((destination[0], (self._pacer.grants, self._pacer.ended)))

    def fileno(self):
        return self._reader.fileno()

    def recv(self, n):
        return self._reader.recv(n)

    def bind(self, address):
        pass

    def setsockopt(self, *args):
        pass

    def setblocking(self, flag):
        pass

    def getblocking(self):
        return False

    def gettimeout(self):
        return 0.0

    def close(self):
        self._reader.close()
        self._writer.close()


async def handoffs(monkeypatch, async_ping=None):
    """Run PROBES probes through `IcmpProber` as `scan.run` does, under an overdue
    pacer, and return `(addresses sent to, (slots granted, slots ended) when each
    request was handed over)` in the order the requests went. `async_ping` replaces icmplib's, for the
    control."""
    pacer, log = OverduePacer(), []
    monkeypatch.setattr(
        icmplib.sockets.ICMPv4Socket, "_create_socket", lambda self, type: FakeRawSocket(log, pacer)
    )
    if async_ping is not None:
        monkeypatch.setattr(icmplib, "async_ping", async_ping)
    prober = IcmpProber()
    addresses = [ipaddress.ip_address(f"10.0.0.{i}") for i in range(1, PROBES + 1)]

    async def work(address, timeout):
        await prober.ping(address, timeout)

    pool = scan._Pool(scan.PING_CONCURRENCY, REPLY_WAIT_S, pacer, scan.Clock(), deadline=time.monotonic() + 60)
    assert await pool.run(addresses, work) is True
    return [destination for destination, _ in log], [slots for _, slots in log]


async def test_each_echo_request_leaves_icmplib_in_the_step_its_slot_was_granted_in(monkeypatch):
    """Every slot overdue: each probe takes its slot and runs into icmplib in the same
    step, and the pool starts the next probe only after that step. The n-th request is
    handed over when n slots have been granted and n - 1 have ended: its own is still
    open, so the next slot is measured from after the hand-off. An `await` ahead of the
    send in `icmplib.async_ping` moves it past the end of its slot, and this goes red."""
    destinations, slots = await handoffs(monkeypatch)
    assert destinations == [f"10.0.0.{i}" for i in range(1, PROBES + 1)]
    assert slots == [(n, n - 1) for n in range(1, PROBES + 1)], (
        "a request must be handed over in the step its slot was granted in"
    )


async def test_the_check_sees_an_await_ahead_of_the_send(monkeypatch):
    """The control: the same run with an `await` at the entry of `async_ping`, the
    release the check is there for. Each request goes after its slot has ended, so a
    hand-off held back there would no longer move the next slot."""
    real = icmplib.async_ping

    async def awaits_first(*args, **kwargs):
        await asyncio.sleep(0)
        return await real(*args, **kwargs)

    destinations, slots = await handoffs(monkeypatch, awaits_first)
    assert sorted(destinations) == sorted(f"10.0.0.{i}" for i in range(1, PROBES + 1))
    assert slots != [(n, n - 1) for n in range(1, PROBES + 1)]
    assert all(ended >= granted for granted, ended in slots)

