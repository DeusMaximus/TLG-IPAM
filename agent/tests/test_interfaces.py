"""Reading the host's addresses from an rtnetlink dump (spec §5.2)."""

import socket
import struct

import pytest

from tlg_ipam_agent import interfaces as nl


def _attr(kind: int, data: bytes) -> bytes:
    raw = struct.pack("=HH", 4 + len(data), kind) + data
    return raw + b"\0" * (nl._align(len(raw)) - len(raw))


def _addr(family, prefixlen, scope, index, *attrs) -> bytes:
    body = struct.pack("=BBBBI", family, prefixlen, 0, scope, index) + b"".join(attrs)
    return struct.pack("=IHHII", 16 + len(body), nl.RTM_NEWADDR, 2, 1, 0) + body


def _done() -> bytes:
    return struct.pack("=IHHII", 20, nl.NLMSG_DONE, 2, 1, 0) + b"\0" * 4


V4 = socket.inet_pton(socket.AF_INET, "10.0.0.5")
PEER = socket.inet_pton(socket.AF_INET, "10.0.0.1")
V6 = socket.inet_pton(socket.AF_INET6, "fd00::5")
LO = socket.inet_pton(socket.AF_INET, "127.0.0.1")


def test_addresses_and_the_end_of_the_dump():
    data = (
        _addr(socket.AF_INET, 24, 0, 2, _attr(nl.IFA_ADDRESS, V4), _attr(nl.IFA_LOCAL, V4), _attr(3, b"eth0\0"))
        + _addr(socket.AF_INET6, 64, 0, 3, _attr(nl.IFA_ADDRESS, V6))
        + _done()
    )
    assert nl.parse_addresses(data) == ([(2, "10.0.0.5/24"), (3, "fd00::5/64")], True)


def test_point_to_point_reports_the_local_address_not_the_peer():
    data = _addr(socket.AF_INET, 32, 0, 4, _attr(nl.IFA_ADDRESS, PEER), _attr(nl.IFA_LOCAL, V4))
    assert nl.parse_addresses(data) == ([(4, "10.0.0.5/32")], False)


def test_host_and_link_scope_are_left_out():
    data = (
        _addr(socket.AF_INET, 8, 254, 1, _attr(nl.IFA_LOCAL, LO))
        + _addr(socket.AF_INET6, 64, 253, 2, _attr(nl.IFA_ADDRESS, V6))
        + _done()
    )
    assert nl.parse_addresses(data) == ([], True)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\x01\x02",
        # a length shorter than the header
        struct.pack("=IHHII", 4, nl.RTM_NEWADDR, 0, 0, 0),
        # a length past the end of the read
        struct.pack("=IHHII", 400, nl.RTM_NEWADDR, 0, 0, 0) + b"\0" * 8,
        # an address of the wrong size, a prefix too long, an attribute running over
        _addr(socket.AF_INET, 24, 0, 2, _attr(nl.IFA_LOCAL, V6)),
        _addr(socket.AF_INET, 40, 0, 2, _attr(nl.IFA_LOCAL, V4)),
        _addr(socket.AF_INET, 24, 0, 2, struct.pack("=HH", 200, nl.IFA_LOCAL) + V4),
    ],
)
def test_malformed_input_yields_nothing(data):
    found, _ = nl.parse_addresses(data)
    assert found == []


def test_an_error_message_is_raised():
    data = struct.pack("=IHHII", 36, nl.NLMSG_ERROR, 0, 1, 0) + b"\0" * 20
    with pytest.raises(OSError):
        nl.parse_addresses(data)


def test_discover_is_empty_off_linux(monkeypatch):
    monkeypatch.setattr(nl.sys, "platform", "darwin")
    assert nl.discover() == []


def test_discover_builds_the_checkin_entries(monkeypatch, tmp_path):
    monkeypatch.setattr(nl.sys, "platform", "linux")
    monkeypatch.setattr(nl, "_dump", lambda: [(2, "10.0.0.5/24"), (1, "127.0.0.1/8"), (3, "fd00::5/64"), (9, "10.9.0.1/24")])
    names = {2: "eth0", 1: "lo", 3: "wg0"}

    def indextoname(i):
        if i not in names:
            raise OSError("gone")
        return names[i]

    monkeypatch.setattr(nl.socket, "if_indextoname", indextoname)
    (tmp_path / "eth0").mkdir()
    (tmp_path / "eth0" / "address").write_text("02:00:00:00:00:05\n")
    (tmp_path / "wg0").mkdir()
    (tmp_path / "wg0" / "address").write_text("\n")
    monkeypatch.setattr(nl, "_SYSFS", tmp_path)
    assert nl.discover() == [
        {"name": "eth0", "cidr": "10.0.0.5/24", "mac": "02:00:00:00:00:05"},
        {"name": "wg0", "cidr": "fd00::5/64", "mac": None},
    ]


def test_discover_survives_a_kernel_that_refuses(monkeypatch):
    monkeypatch.setattr(nl.sys, "platform", "linux")

    def refuse():
        raise OSError("no netlink")

    monkeypatch.setattr(nl, "_dump", refuse)
    assert nl.discover() == []


def test_the_dump_stops_holding_addresses_past_its_bound(monkeypatch):
    """A host with a huge address list: the dump keeps DUMP_MAX, not all of them."""
    one = _addr(socket.AF_INET, 24, 0, 2, _attr(nl.IFA_LOCAL, V4))

    class Sock:
        def __init__(self, *args):
            self.reads = 0

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def settimeout(self, t):
            pass

        def bind(self, addr):
            pass

        def send(self, data):
            pass

        def recv(self, n):
            self.reads += 1
            return one * 100  # never DONE

    monkeypatch.setattr(nl.socket, "socket", Sock)
    monkeypatch.setattr(nl.socket, "AF_NETLINK", 16, raising=False)
    found = nl._dump()
    assert len(found) == nl.DUMP_MAX
