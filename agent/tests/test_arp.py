"""The active ARP sweep (spec §5.2, #14): the request frame, reading replies, which
replies are taken, and the sweep's retry, pacing, scope and deadline.

No test opens a raw socket. The frames are checked against RFC 826's layout written
out in `conftest`, the session runs against `FakeArp`, and `PacketLink` against a
stand-in for the socket module or a local socket pair, which carries no frame onto
any network.
"""

import asyncio
import bisect
import ipaddress
import random
import re
import socket

import pytest

from conftest import (
    ARP,
    ETH0,
    ETHER,
    HOST_IP,
    HOST_MAC,
    PACKET_BROADCAST,
    PACKET_OUTGOING,
    FakeArp,
    FakeClock,
    FakeProber,
    FakeResolver,
    arp_frame,
    job,
    mac,
    source,
)
from tlg_ipam_agent import arp, guard, probe, scan
from tlg_ipam_agent.config import parse_allowed
from tlg_ipam_agent.probe import ArpSession, PacketLink, ProbeError
from tlg_ipam_agent.protocol import job_ref, parse_job

ip = ipaddress.IPv4Address
NAS = "10.0.0.7"
NAS_MAC = "02:11:22:33:44:07"


# --- the request ---


def test_the_request_is_an_rfc_826_broadcast_who_has():
    frame = arp.request(HOST_MAC, HOST_IP, ip(NAS))
    # captured by hand from the layout: broadcast, from eth0, ARP; Ethernet/IPv4,
    # request; sender eth0 at 10.0.0.5; target unknown at 10.0.0.7
    assert frame.hex() == (
        "ffffffffffff" "020000000005" "0806"
        "0001" "0800" "06" "04" "0001"
        "020000000005" "0a000005" "000000000000" "0a000007"
    )
    assert len(frame) == arp.FRAME_LEN == 42


@pytest.mark.parametrize(
    "text",
    ["", "02:00:00:00:00", "02:00:00:00:00:05:06", "02-00-00-00-00-05", "0200.0000.0005",
     "02:00:00:00:00:0g", "2:0:0:0:0:5", "00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff",
     "01:00:5e:00:00:01", "33:33:00:00:00:01"],
)
def test_a_mac_no_frame_can_come_from_is_refused(text):
    with pytest.raises(ValueError):
        arp.mac_bytes(text)


def test_a_unicast_mac_round_trips():
    assert arp.mac_text(arp.mac_bytes("02:ab:CD:00:00:05")) == "02:ab:cd:00:00:05"


# --- reading replies ---


def test_a_reply_to_this_host_gives_the_sender_and_its_mac():
    frame = arp_frame(NAS, NAS_MAC, HOST_IP)
    assert arp.reply(frame, HOST_IP) == (ip(NAS), NAS_MAC)


def test_ethernet_padding_is_ignored():
    frame = arp_frame(NAS, NAS_MAC, HOST_IP, pad=18)
    assert len(frame) == 60
    assert arp.reply(frame, HOST_IP) == (ip(NAS), NAS_MAC)


def test_a_broadcast_reply_to_this_host_is_still_a_reply():
    frame = arp_frame(NAS, NAS_MAC, HOST_IP, dst=b"\xff" * 6)
    assert arp.reply(frame, HOST_IP) == (ip(NAS), NAS_MAC)


@pytest.mark.parametrize("length", [0, 1, 13, 14, 27, 41])
def test_a_frame_cut_short_is_not_a_reply(length):
    assert arp.reply(arp_frame(NAS, NAS_MAC, HOST_IP)[:length], HOST_IP) is None


@pytest.mark.parametrize(
    "fields",
    [
        {"ethertype": 0x0800},  # IPv4, not ARP
        {"ethertype": 0x8100},  # a VLAN tag in front of the ARP
        {"ethertype": 0x8035},  # RARP
        {"htype": 6},  # IEEE 802 hardware
        {"ptype": 0x86DD},
        {"hlen": 8},
        {"plen": 16},
        {"oper": 1},  # a request: someone asking, not answering
        {"oper": 3},
        {"oper": 0},
    ],
)
def test_a_frame_that_is_not_an_ethernet_ipv4_arp_reply_is_dropped(fields):
    assert arp.reply(arp_frame(NAS, NAS_MAC, HOST_IP, **fields), HOST_IP) is None


def test_a_reply_to_another_host_is_dropped():
    frame = arp_frame(NAS, NAS_MAC, "10.0.0.6", mac("02:00:00:00:00:06"))
    assert arp.reply(frame, HOST_IP) is None


def test_a_gratuitous_reply_is_dropped():
    # an announcement: sender and target the same address, broadcast
    frame = arp_frame(NAS, NAS_MAC, NAS, b"\xff" * 6, dst=b"\xff" * 6)
    assert arp.reply(frame, HOST_IP) is None


@pytest.mark.parametrize("sender", ["00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff", "01:00:5e:00:00:07"])
def test_a_reply_from_a_mac_no_station_owns_is_dropped(sender):
    assert arp.reply(arp_frame(NAS, sender, HOST_IP), HOST_IP) is None


def test_no_frame_makes_the_parser_fail():
    """Every frame is a reply or None, never an exception, and a reply is always an
    address and a unicast MAC. The inputs are built to reach past each check in turn:
    every value of every byte of a good reply (42 x 256), every truncation, random
    tails behind a valid header, and random values in each header field; random
    frames alone almost never get past the ethertype."""
    rng = random.Random(14)
    good = arp_frame(NAS, NAS_MAC, HOST_IP)
    frames = [good[:at] + bytes([value]) + good[at + 1 :] for at in range(len(good)) for value in range(256)]
    frames += [good[:n] for n in range(len(good))]
    frames += [good[:14 + n] + rng.randbytes(rng.randrange(0, 60)) for n in range(29) for _ in range(20)]
    for _ in range(3000):
        fields = {
            "ethertype": rng.choice([0x0806, 0x0806, 0x0800, 0x8100, rng.randrange(65536)]),
            "htype": rng.choice([1, 1, 6, rng.randrange(65536)]),
            "ptype": rng.choice([0x0800, 0x0800, 0x86DD, rng.randrange(65536)]),
            "hlen": rng.choice([6, 6, rng.randrange(256)]),
            "plen": rng.choice([4, 4, rng.randrange(256)]),
            "oper": rng.choice([2, 2, 1, rng.randrange(65536)]),
            "pad": rng.randrange(0, 30),
        }
        target = rng.choice([HOST_IP, ip(rng.randrange(2**32))])
        frames.append(arp_frame(ip(rng.randrange(2**32)), rng.randbytes(6), target, rng.randbytes(6), **fields))
    taken = 0
    for frame in frames:
        found = arp.reply(frame, HOST_IP)
        if found is not None:
            taken += 1
            address, text = found
            assert isinstance(address, ipaddress.IPv4Address)
            assert arp.is_unicast(arp.mac_bytes(text))
            assert frame[12:14] == b"\x08\x06" and frame[20:22] == b"\x00\x02" and frame[38:42] == HOST_IP.packed
    # the inputs reach the end of the parser, both ways
    assert 100 < taken < len(frames) - 1000


# --- which replies the session takes ---


def session(targets=(NAS, "10.0.0.8")) -> tuple[ArpSession, FakeArp]:
    link = FakeArp()("eth0")
    return ArpSession(link, HOST_IP, HOST_MAC, frozenset(ip(a) for a in targets)), link


async def test_a_request_goes_only_to_an_address_of_the_job():
    s, link = session()
    with pytest.raises(ValueError):
        await s.request(ip("10.0.0.9"))
    assert link.requests == []
    await s.request(ip(NAS))
    assert link.requests == [ip(NAS)]


async def test_a_reply_is_taken_only_from_an_address_asked():
    s, _ = session()
    reply = arp_frame(NAS, NAS_MAC, HOST_IP)
    # unsolicited: the job's address, but no request has gone to it yet
    assert s.take(reply, source()) is False
    await s.request(ip(NAS))
    assert s.take(reply, source()) is True
    assert s.answers == {ip(NAS): NAS_MAC}


async def test_a_reply_from_outside_the_job_is_dropped():
    s, _ = session()
    await s.request(ip(NAS))
    for sender in ("10.0.0.9", "192.168.1.1", "10.0.0.5", "0.0.0.0", "255.255.255.255"):
        assert s.take(arp_frame(sender, NAS_MAC, HOST_IP), source()) is False
    assert s.answers == {}


async def test_the_first_reply_for_an_address_is_kept():
    s, _ = session()
    await s.request(ip(NAS))
    assert s.take(arp_frame(NAS, NAS_MAC, HOST_IP), source()) is True
    assert s.take(arp_frame(NAS, "02:66:66:66:66:66", HOST_IP), source()) is False
    assert s.answers == {ip(NAS): NAS_MAC}


async def test_a_frame_this_host_is_sending_is_dropped():
    """A packet socket also sees the host's outgoing frames: the kernel's own ARP
    replies among them."""
    s, _ = session()
    await s.request(ip(NAS))
    assert s.take(arp_frame(NAS, NAS_MAC, HOST_IP), source(kind=PACKET_OUTGOING)) is False
    assert s.answers == {}


async def test_a_frame_from_another_interface_is_dropped():
    s, _ = session()
    await s.request(ip(NAS))
    assert s.take(arp_frame(NAS, NAS_MAC, HOST_IP), source(interface="eth1")) is False
    assert s.take(arp_frame(NAS, NAS_MAC, HOST_IP), None) is False
    assert s.take(arp_frame(NAS, NAS_MAC, HOST_IP), ("eth0",)) is False
    assert s.take(arp_frame(NAS, NAS_MAC, HOST_IP), source(kind=PACKET_BROADCAST)) is True


# --- the socket ---


class FakeSocketModule:
    """Stands in for `socket` inside `probe`: records how the socket is made and
    bound, and fails where it is told to."""

    AF_PACKET, SOCK_RAW = 17, 3

    def __init__(self, fail_create=None, fail_bind=None):
        self.fail_create, self.fail_bind = fail_create, fail_bind
        self.made: list[tuple] = []
        self.bound: list[tuple] = []
        self.closed = 0

    def socket(self, *args):
        if self.fail_create:
            raise self.fail_create
        self.made.append(args)
        return self

    def setblocking(self, flag):
        assert flag is False

    def bind(self, address):
        if self.fail_bind:
            raise self.fail_bind
        self.bound.append(address)

    def close(self):
        self.closed += 1


def test_the_link_is_bound_to_arp_on_its_one_interface(monkeypatch):
    fake = FakeSocketModule()
    monkeypatch.setattr(probe, "socket", fake)
    link = PacketLink("eth0")
    # protocol 0 until bind names ARP on eth0: nothing from elsewhere is queued
    assert fake.made == [(17, 3, 0)]
    assert fake.bound == [("eth0", 0x0806)]
    link.close()
    assert fake.closed == 1


def test_without_cap_net_raw_the_link_says_so(monkeypatch):
    monkeypatch.setattr(probe, "socket", FakeSocketModule(fail_create=PermissionError()))
    with pytest.raises(ProbeError, match="CAP_NET_RAW"):
        PacketLink("eth0")


def test_an_interface_that_is_gone_closes_the_socket(monkeypatch):
    fake = FakeSocketModule(fail_bind=OSError(19, "No such device"))
    monkeypatch.setattr(probe, "socket", fake)
    with pytest.raises(ProbeError, match="eth0"):
        PacketLink("eth0")
    assert fake.closed == 1


async def test_the_link_sends_and_receives_through_the_event_loop():
    """`send` and `receive` on a local socket pair in place of the packet socket:
    the frame goes to the other end of the pair, and nowhere else."""
    ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    ours.setblocking(False)
    link = object.__new__(PacketLink)
    link.interface, link._sock = "eth0", ours
    try:
        await link.send(b"request")
        assert theirs.recv(64) == b"request"
        theirs.send(b"reply")
        frame, _ = await asyncio.wait_for(link.receive(), 1)
        assert frame == b"reply"
        theirs.close()
        ours.close()
        with pytest.raises(ProbeError, match="not sent on eth0"):
            await link.send(b"request")
    finally:
        ours.close()
        theirs.close()


# --- the sweep ---


def planned(**overrides):
    raw = job(**{"methods": ["arp"], **overrides})
    parsed = parse_job(job_ref(raw), raw)
    return parsed, guard.plan(parsed, parse_allowed(None))


async def sweep(parsed, addresses, *, arp_link, prober=None, resolver=None, interfaces=ETH0, clock=None, max_pps=200):
    return await scan.run(
        parsed,
        addresses,
        prober=prober or FakeProber(),
        resolver=resolver or FakeResolver(),
        arp_link=arp_link,
        interfaces=interfaces,
        max_pps=max_pps,
        clock=clock or FakeClock(),
    )


async def test_a_host_that_drops_ping_is_found_with_its_mac():
    prober = FakeProber(alive=["10.0.0.3"])
    link = FakeArp({NAS: NAS_MAC, "10.0.0.3": "02:00:00:00:00:03"})
    parsed, addresses = planned(methods=["icmp", "arp"])
    result = await sweep(parsed, addresses, arp_link=link, prober=prober)
    assert result["complete"] is True and result["error"] is None
    assert result["observations"] == [
        {"ip": "10.0.0.3", "mac": "02:00:00:00:00:03"},
        {"ip": "10.0.0.5", "mac": "02:00:00:00:00:05"},
        {"ip": NAS, "mac": NAS_MAC},
    ]
    assert link.opened == ["eth0"] and link.closed == 1


async def test_an_address_that_answers_ping_alone_has_no_mac():
    prober = FakeProber(alive=["10.0.0.3"])
    parsed, addresses = planned(methods=["icmp", "arp"])
    result = await sweep(parsed, addresses, arp_link=FakeArp(), prober=prober)
    assert {"ip": "10.0.0.3"} in result["observations"]


async def test_one_request_and_one_retry_per_address():
    clock = FakeClock()
    link = FakeArp({NAS: NAS_MAC, "10.0.0.8": "02:00:00:00:00:08"}, clock, deaf_first=["10.0.0.8"])
    parsed, addresses = planned(ranges=[{"start": "10.0.0.1", "end": "10.0.0.20"}])
    result = await sweep(parsed, addresses, arp_link=link, clock=clock)
    assert result["complete"] is True
    # answered at once: one request; answered only the retry, or never: two
    assert link.asked(NAS) == 1
    assert link.asked("10.0.0.8") == 2
    assert link.asked("10.0.0.1") == 2
    assert max(link.asked(a) for a in addresses) == scan.ARP_ATTEMPTS == 2
    # the agent's own address gets no frame, and is reported with its own MAC
    assert link.asked("10.0.0.5") == 0
    assert result["observations"] == [
        {"ip": "10.0.0.5", "mac": "02:00:00:00:00:05"},
        {"ip": NAS, "mac": NAS_MAC},
        {"ip": "10.0.0.8", "mac": "02:00:00:00:00:08"},
    ]
    # each retry comes at least ARP_TIMEOUT_S after the address's first request
    first: dict = {}
    for address, at in zip(link.requests, link.times, strict=True):
        if address in first:
            assert at - first[address] >= scan.ARP_TIMEOUT_S
        first.setdefault(address, at)


async def test_requests_go_only_to_the_jobs_addresses():
    link = FakeArp()
    parsed, addresses = planned(
        ranges=[{"start": "10.0.0.10", "end": "10.0.0.12"}, {"start": "10.0.0.40", "end": "10.0.0.41"}]
    )
    await sweep(parsed, addresses, arp_link=link)
    assert set(link.requests) == set(addresses) == {
        ip(a) for a in ("10.0.0.10", "10.0.0.11", "10.0.0.12", "10.0.0.40", "10.0.0.41")
    }


async def test_unsolicited_and_malformed_frames_are_not_reported():
    """Each frame arrives after the address it names has been asked, so only the
    check it is meant to meet can drop it."""
    after = {
        # a reply to another host, a request, a frame this host is sending, one from
        # another interface, and a sender MAC no station owns: each about an address
        # the sweep has just asked
        "10.0.0.10": [
            (arp_frame("10.0.0.10", "02:00:00:00:00:10", "10.0.0.6", mac("02:00:00:00:00:06")), source()),
            (arp_frame("10.0.0.10", "02:00:00:00:00:10", HOST_IP, oper=1), source()),
            (arp_frame("10.0.0.10", "02:00:00:00:00:10", HOST_IP), source(kind=PACKET_OUTGOING)),
        ],
        "10.0.0.11": [
            (arp_frame("10.0.0.11", "02:00:00:00:00:11", HOST_IP), source(interface="eth1")),
            (arp_frame("10.0.0.11", "ff:ff:ff:ff:ff:ff", HOST_IP), source()),
            (arp_frame("10.0.0.11", "02:00:00:00:00:11", HOST_IP, ethertype=0x0800), source()),
        ],
        "10.0.0.12": [
            # from outside the ranges and the subnet, and frames cut short
            (arp_frame("10.0.0.99", "02:00:00:00:00:99", HOST_IP), source()),
            (arp_frame("192.168.1.1", "02:00:00:00:01:01", HOST_IP), source()),
            (arp_frame("10.0.0.12", "02:00:00:00:00:12", HOST_IP)[:41], source()),
            (b"", source()),
        ],
    }
    link = FakeArp(after=after)
    parsed, addresses = planned(ranges=[{"start": "10.0.0.10", "end": "10.0.0.12"}])
    result = await sweep(parsed, addresses, arp_link=link)
    assert result["observations"] == [] and result["complete"] is True
    assert sorted(link.requests) == sorted(addresses * 2)  # each silent, so retried


async def test_a_reply_that_arrives_after_its_request_is_reported():
    """The control for the test above: the same delivery path does carry a reply."""
    after = {"10.0.0.11": [(arp_frame("10.0.0.11", "02:00:00:00:00:11", HOST_IP), source())]}
    parsed, addresses = planned(ranges=[{"start": "10.0.0.10", "end": "10.0.0.12"}])
    result = await sweep(parsed, addresses, arp_link=FakeArp(after=after))
    assert result["observations"] == [{"ip": "10.0.0.11", "mac": "02:00:00:00:00:11"}]


def _most_in_one_second(times) -> int:
    times = sorted(times)
    return max(bisect.bisect_left(times, t + 1.0 - 1e-9) - i for i, t in enumerate(times))


@pytest.mark.parametrize(("pps", "last"), [(1, 40), (20, 254), (200, 254)])
async def test_requests_and_echo_requests_share_max_pps(pps, last):
    """Each phase holds more than a second of sends at its rate (the whole /24 at 20
    and 200 a second): an ARP sweep that skipped the pacer, or paced itself apart
    from the ICMP sweep, would put more than `pps` in the second where one phase
    meets the next."""
    clock = FakeClock()
    prober = FakeProber(clock=clock)
    link = FakeArp(clock=clock)
    parsed, addresses = planned(methods=["icmp", "arp"], ranges=[{"start": "10.0.0.1", "end": f"10.0.0.{last}"}])
    await sweep(parsed, addresses, arp_link=link, prober=prober, clock=clock, max_pps=pps)
    # an echo request to each, then an ARP request and a retry to each but 10.0.0.5
    assert len(prober.times) == last and len(link.times) == 2 * (last - 1)
    assert _most_in_one_second(prober.times + link.times) == pps
    assert _most_in_one_second(link.times) == pps


async def test_the_deadline_stops_the_sweep_and_reports_incomplete():
    clock = FakeClock()
    link = FakeArp({"10.0.0.1": "02:00:00:00:00:01"}, clock)
    # 253 requests at 10 a second take 25 s; the job allows 5
    parsed, addresses = planned(timeout_s=5)
    result = await sweep(parsed, addresses, arp_link=link, clock=clock, max_pps=10)
    assert result["complete"] is False
    assert "during the ARP sweep" in result["error"]
    assert 40 <= len(link.requests) <= 52
    assert all(t < 1000 + 5 for t in link.times)
    # what answered in time is still reported
    assert {"ip": "10.0.0.1", "mac": "02:00:00:00:00:01"} in result["observations"]
    assert link.closed == 1


async def test_a_reply_wait_cut_by_the_deadline_makes_the_result_incomplete():
    clock = FakeClock()
    link = FakeArp(clock=clock)
    # two requests in the first half-second of a 1 s job: the 1 s wait for their
    # replies runs into the deadline
    parsed, addresses = planned(ranges=[{"start": "10.0.0.1", "end": "10.0.0.2"}], timeout_s=1)
    result = await sweep(parsed, addresses, arp_link=link, clock=clock, max_pps=4)
    assert link.asked("10.0.0.1") == 1
    assert result["complete"] is False


async def test_with_every_address_answered_there_is_no_retry_round():
    clock = FakeClock()
    link = FakeArp({"10.0.0.1": "02:00:00:00:00:01", "10.0.0.2": "02:00:00:00:00:02"}, clock)
    parsed, addresses = planned(ranges=[{"start": "10.0.0.1", "end": "10.0.0.2"}])
    result = await sweep(parsed, addresses, arp_link=link, clock=clock)
    assert link.requests == [ip("10.0.0.1"), ip("10.0.0.2")]
    assert result["complete"] is True
    assert clock.t - 1000 < scan.ARP_TIMEOUT_S


async def test_a_socket_that_cannot_be_opened_keeps_the_icmp_results():
    prober = FakeProber(alive=["10.0.0.3"])
    link = FakeArp(fail_open=ProbeError("ARP needs CAP_NET_RAW: run the container with --cap-add NET_RAW"))
    parsed, addresses = planned(methods=["icmp", "arp"])
    result = await sweep(parsed, addresses, arp_link=link, prober=prober)
    assert result["complete"] is False
    assert "CAP_NET_RAW" in result["error"]
    assert {"ip": "10.0.0.3"} in result["observations"]


async def test_a_socket_that_fails_mid_sweep_stops_it():
    link = FakeArp({"10.0.0.1": "02:00:00:00:00:01"}, fail_receive_after=3)
    parsed, addresses = planned()
    result = await sweep(parsed, addresses, arp_link=link)
    assert result["complete"] is False
    assert "not readable on eth0" in result["error"]
    # it stops at the next send rather than sweeping on deaf
    assert len(link.requests) <= 4
    assert {"ip": "10.0.0.1", "mac": "02:00:00:00:00:01"} in result["observations"]
    assert link.closed == 1


async def test_a_failed_send_stops_the_sweep():
    class Unsendable(FakeArp):
        async def send(self, frame):
            self.requests.append(frame)
            raise ProbeError("ARP request not sent on eth0 (OSError)")

    link = Unsendable()
    parsed, addresses = planned()
    result = await sweep(parsed, addresses, arp_link=link)
    assert result["complete"] is False and "not sent on eth0" in result["error"]
    assert len(link.requests) == 1 and link.closed == 1


@pytest.mark.parametrize(
    "interfaces",
    [
        [],
        [{"name": "eth0", "cidr": "10.0.0.5/24", "mac": None}],  # no MAC to send from
        [{"name": "eth0", "cidr": "10.0.0.5/24", "mac": "00:00:00:00:00:00"}],
        [{"name": "eth0", "cidr": "10.0.0.5/28", "mac": "02:00:00:00:00:05"}],  # part of the subnet
        [{"name": "eth0", "cidr": "10.1.0.5/24", "mac": "02:00:00:00:00:05"}],  # another subnet
        [{"name": "eth0", "cidr": "fd00::5/64", "mac": "02:00:00:00:00:05"}],
        [{"cidr": "10.0.0.5/24", "mac": "02:00:00:00:00:05"}],
        [{"name": "eth0", "cidr": "not an address", "mac": "02:00:00:00:00:05"}],
        ["eth0"],
    ],
)
async def test_without_an_interface_on_the_subnet_nothing_is_sent(interfaces):
    prober = FakeProber(alive=["10.0.0.3"])
    link = FakeArp({NAS: NAS_MAC})
    parsed, addresses = planned(methods=["icmp", "arp"])
    result = await sweep(parsed, addresses, arp_link=link, prober=prober, interfaces=interfaces)
    assert link.opened == [] and link.requests == []
    assert result["complete"] is False
    assert result["error"] == "arp not run: no interface of this host with a MAC is on 10.0.0.0/24"
    assert result["observations"] == [{"ip": "10.0.0.3"}]


async def test_the_interface_whose_network_holds_the_subnet_is_used():
    interfaces = [
        {"name": "lan", "cidr": "192.168.1.5/24", "mac": "02:00:00:00:01:05"},
        {"name": "vlan10", "cidr": "10.0.0.200/16", "mac": "02:00:00:00:0a:c8"},
        {"name": "vlan10", "cidr": "10.0.0.201/16", "mac": "02:00:00:00:0a:c8"},
    ]
    sent: list[bytes] = []

    class Recording(FakeArp):
        async def send(self, frame):
            sent.append(frame)

    link = Recording()
    parsed, addresses = planned(ranges=[{"start": "10.0.0.199", "end": "10.0.0.202"}])
    result = await sweep(parsed, addresses, arp_link=link, interfaces=interfaces)
    assert link.opened == ["vlan10"]
    # from the interface's first address and its MAC; its two addresses are its own
    _, src, _ = ETHER.unpack_from(sent[0])
    *_, sha, spa, _tha, _tpa = ARP.unpack_from(sent[0], ETHER.size)
    assert src == sha == mac("02:00:00:00:0a:c8") and spa == ip("10.0.0.200").packed
    assert {a for a in link.opened} == {"vlan10"}
    assert result["observations"] == [
        {"ip": "10.0.0.200", "mac": "02:00:00:00:0a:c8"},
        {"ip": "10.0.0.201", "mac": "02:00:00:00:0a:c8"},
    ]


async def test_ptr_lookups_cover_addresses_found_by_arp():
    resolver = FakeResolver({NAS: "nas.home.arpa"})
    link = FakeArp({NAS: NAS_MAC})
    parsed, addresses = planned(methods=["icmp", "arp", "rdns"])
    result = await sweep(parsed, addresses, arp_link=link, resolver=resolver)
    assert {"ip": NAS, "mac": NAS_MAC, "rdns_name": "nas.home.arpa"} in result["observations"]


async def test_a_reply_that_lands_while_a_retry_waits_for_its_slot_stops_that_retry():
    """Both silent at first, at 1 a second. While the retry of .2 waits for its slot,
    .2's late first-round reply arrives: .2 gets no retry (briefed review of PR #84,
    finding 4)."""
    clock = FakeClock()

    class Late(FakeArp):
        async def send(self, frame):
            await super().send(frame)
            if self.requests == [ip("10.0.0.1"), ip("10.0.0.2"), ip("10.0.0.1")]:
                self._queue.put_nowait((arp_frame("10.0.0.2", "02:00:00:00:00:02", HOST_IP), source()))

    link = Late(clock=clock)
    parsed, addresses = planned(ranges=[{"start": "10.0.0.1", "end": "10.0.0.2"}])
    result = await sweep(parsed, addresses, arp_link=link, clock=clock, max_pps=1)
    assert link.requests == [ip("10.0.0.1"), ip("10.0.0.2"), ip("10.0.0.1")]
    assert result["observations"] == [{"ip": "10.0.0.2", "mac": "02:00:00:00:00:02"}]
    assert result["complete"] is True


class Flood(FakeArp):
    """A link that is always readable, as under an ARP flood: `receive` returns a
    frame at once and never yields. Each read records how many times a task beside
    the sweep has run, so a stretch of reads with no other task running shows."""

    def __init__(self, ticks, **kwargs):
        super().__init__(**kwargs)
        self.ticks = ticks
        self.reads = 0
        self.longest = 0
        self._run = 0
        self._seen = None

    async def receive(self):
        self.reads += 1
        if self.ticks[0] == self._seen:
            self._run += 1
            self.longest = max(self.longest, self._run)
        else:
            self._seen, self._run = self.ticks[0], 1
        if self.reads > 2_000_000:  # a listener that never yields would read for ever
            raise ProbeError("the flood outlasted the test")
        return arp_frame("10.0.0.99", "02:00:00:00:00:99", "10.0.0.6", mac("02:00:00:00:00:06")), source()


async def test_a_flood_of_frames_does_not_hold_the_event_loop():
    """The listener lets other tasks run after each batch, so the sweep, its deadline
    and the agent's check-ins go on while the socket stays readable (briefed review
    of PR #84, finding 1)."""
    ticks = [0]

    async def beside():
        while True:
            ticks[0] += 1
            await asyncio.sleep(0)

    other = asyncio.create_task(beside())
    try:
        clock = FakeClock()
        link = Flood(ticks, clock=clock)
        parsed, addresses = planned(ranges=[{"start": "10.0.0.1", "end": "10.0.0.20"}])
        result = await sweep(parsed, addresses, arp_link=link, clock=clock, max_pps=10)
    finally:
        other.cancel()
    assert result["complete"] is True and result["observations"] == [{"ip": "10.0.0.5", "mac": "02:00:00:00:00:05"}]
    assert link.reads > 0
    assert link.longest <= probe.ARP_RECEIVE_BATCH
    assert len(link.requests) == 2 * 19  # every address but 10.0.0.5, and its retry


async def test_the_deadline_holds_under_a_flood():
    """A 2 s job at 1 a second, with the socket always readable: the sweep stops at
    its deadline and says so."""
    clock = FakeClock()
    link = Flood([0], clock=clock)
    parsed, addresses = planned(timeout_s=2)
    result = await sweep(parsed, addresses, arp_link=link, clock=clock, max_pps=1)
    assert result["complete"] is False and "during the ARP sweep" in result["error"]
    assert len(link.requests) <= 3 and link.closed == 1


def _full_socket_pair():
    """A local datagram socket pair whose sending end is full: the next send would
    block (Linux says EAGAIN; macOS says ENOBUFS). Nothing reads the other end, and
    nothing reaches any network."""
    ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    ours.setblocking(False)
    while True:
        try:
            ours.send(b"\x00" * 42)
        except OSError:
            return ours, theirs


FULL = r"(send queue full|request not sent) on eth0"


async def test_a_full_send_queue_fails_the_send_at_once():
    """`send` never waits for the socket to be writable, so it cannot hold the job
    past its deadline (briefed review of PR #84, finding 2)."""
    ours, theirs = _full_socket_pair()
    link = object.__new__(PacketLink)
    link.interface, link._sock = "eth0", ours
    try:
        with pytest.raises(ProbeError, match=FULL):
            await asyncio.wait_for(link.send(b"request"), 0.5)
    finally:
        ours.close()
        theirs.close()


async def test_a_send_that_would_block_fails_without_waiting():
    """The same on every platform: a socket whose send would block (EAGAIN)."""

    class WouldBlock:
        def send(self, frame):
            raise BlockingIOError

    link = object.__new__(PacketLink)
    link.interface, link._sock = "eth0", WouldBlock()
    with pytest.raises(ProbeError, match="send queue full on eth0"):
        await asyncio.wait_for(link.send(b"request"), 0.5)


async def test_a_sweep_on_a_full_send_queue_ends_incomplete_at_once():
    ours, theirs = _full_socket_pair()

    def opener(interface):
        link = object.__new__(PacketLink)
        link.interface, link._sock = interface, ours
        return link

    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        parsed, addresses = planned(timeout_s=1)
        # a send that waited for the socket would never end: fail instead of hanging
        result = await asyncio.wait_for(sweep(parsed, addresses, arp_link=opener, clock=scan.Clock()), 5)
    finally:
        ours.close()
        theirs.close()
    assert loop.time() - started < 0.5
    assert result["complete"] is False and re.search(FULL, result["error"])
    # the agent's own address is still reported
    assert result["observations"] == [{"ip": "10.0.0.5", "mac": "02:00:00:00:00:05"}]
