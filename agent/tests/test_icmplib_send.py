"""`IcmpProber` through the installed icmplib (#107, item 6; the briefed review of PR
#110, finding 2).

`scan._Pool.run` yields once after it starts each paced probe, so that the probe's first
step runs before the pacer grants the next slot. That relies on a fact about icmplib,
not about the agent: for an IP address, `async_ping` hands the echo request to the
kernel before its first `await`. The ordering test in `test_pacing.py` uses a stand-in
coroutine and never reaches icmplib, so it would stay green if a release of icmplib put
an `await` ahead of the send. These tests run the real `IcmpProber.ping` and the
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
    """A pacer whose every slot is overdue, so its grants come without a sleep and
    `grants` says how many have been given."""

    grants = 0

    async def wait(self):
        self.grants += 1


class FakeRawSocket:
    """What `ICMPv4Socket._create_socket` returns in place of the raw socket. `sendto`
    records the destination and how many grants the pacer had given at that moment, and
    sends nothing. A unix socket pair that is never written to stands behind `recv`, so
    icmplib's wait for a reply is a real wait that ends in its timeout."""

    family = socket.AF_INET
    type = socket.SOCK_RAW

    def __init__(self, log, pacer):
        self._log, self._pacer = log, pacer
        self._reader, self._writer = socket.socketpair()
        self._reader.setblocking(False)

    def sendto(self, packet, destination):
        assert packet[0] == 8  # an ICMP echo request, built by icmplib
        self._log.append((destination[0], self._pacer.grants))

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
    pacer, and return `(addresses sent to, grants given when each request was handed
    over)` in the order the requests went. `async_ping` replaces icmplib's, for the
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
    return [destination for destination, _ in log], [grants for _, grants in log]


async def test_each_echo_request_leaves_icmplib_before_the_next_slot_is_granted(monkeypatch):
    """Every slot overdue: the pool starts a probe, lets its first step run, and only
    then asks for the next slot. The request is handed over in that first step, so the
    n-th request goes when n slots have been granted. An `await` ahead of the send in
    `icmplib.async_ping` moves it past the next grant, and this goes red."""
    destinations, grants = await handoffs(monkeypatch)
    assert destinations == [f"10.0.0.{i}" for i in range(1, PROBES + 1)]
    assert grants == list(range(1, PROBES + 1)), "a request must be handed over before the next slot is granted"


async def test_the_check_sees_an_await_ahead_of_the_send(monkeypatch):
    """The control: the same run with an `await` at the entry of `async_ping`, the
    release the check is there for. The requests go late: the first not at the first
    grant, and the last at the last one or after it (the review measured
    `[2, 3, ..., 9, 9]` for nine)."""
    real = icmplib.async_ping

    async def awaits_first(*args, **kwargs):
        await asyncio.sleep(0)
        return await real(*args, **kwargs)

    destinations, grants = await handoffs(monkeypatch, awaits_first)
    assert sorted(destinations) == sorted(f"10.0.0.{i}" for i in range(1, PROBES + 1))
    assert grants != list(range(1, PROBES + 1))
    assert grants[0] > 1 and grants[-1] == PROBES

