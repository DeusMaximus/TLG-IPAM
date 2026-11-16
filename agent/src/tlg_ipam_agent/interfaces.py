"""The host's interfaces, as reported at check-in (spec §5.2, §5.7): the server reads
them to decide which subnets are on the agent's own link.

Read from the kernel over rtnetlink (an `RTM_GETADDR` dump), standard library only,
so that every address is seen, secondary ones included. Nothing is sent on any
network: a netlink socket talks to the local kernel. MACs come from sysfs.
"""

import ipaddress
import socket
import struct
import sys
from pathlib import Path

from .protocol import INTERFACE_NAME_MAX, INTERFACES_MAX

NETLINK_ROUTE = 0
RTM_NEWADDR = 20
RTM_GETADDR = 22
NLM_F_REQUEST = 0x1
NLM_F_DUMP = 0x300
NLMSG_ERROR = 2
NLMSG_DONE = 3
IFA_ADDRESS = 1
IFA_LOCAL = 2
RT_SCOPE_UNIVERSE = 0

_NLMSGHDR = struct.Struct("=IHHII")  # len, type, flags, seq, pid
_IFADDRMSG = struct.Struct("=BBBBI")  # family, prefixlen, flags, scope, index
_RTATTR = struct.Struct("=HH")  # len, type
_SYSFS = Path("/sys/class/net")


def _align(n: int) -> int:
    return (n + 3) & ~3


def parse_addresses(data: bytes) -> tuple[list[tuple[int, str]], bool]:
    """`(interface index, "address/prefix")` for each global-scope address in one
    netlink read, and whether the dump is done. Malformed input ends the parse."""
    found = []
    offset = 0
    while offset + _NLMSGHDR.size <= len(data):
        length, kind, _flags, _seq, _pid = _NLMSGHDR.unpack_from(data, offset)
        if length < _NLMSGHDR.size or offset + length > len(data):
            return found, True
        if kind == NLMSG_DONE:
            return found, True
        if kind == NLMSG_ERROR:
            raise OSError("netlink refused the address dump")
        body = offset + _NLMSGHDR.size
        if kind == RTM_NEWADDR and body + _IFADDRMSG.size <= offset + length:
            family, prefixlen, _f, scope, index = _IFADDRMSG.unpack_from(data, body)
            attrs: dict[int, bytes] = {}
            at = body + _IFADDRMSG.size
            while at + _RTATTR.size <= offset + length:
                alen, atype = _RTATTR.unpack_from(data, at)
                if alen < _RTATTR.size or at + alen > offset + length:
                    break
                attrs[atype] = data[at + _RTATTR.size : at + alen]
                at += _align(alen)
            # IPv4 carries the interface's own address in IFA_LOCAL (IFA_ADDRESS is the
            # peer on a point-to-point link); IPv6 only in IFA_ADDRESS
            raw = attrs.get(IFA_LOCAL) or attrs.get(IFA_ADDRESS)
            if scope == RT_SCOPE_UNIVERSE and raw is not None:
                size = {socket.AF_INET: 4, socket.AF_INET6: 16}.get(family)
                if size == len(raw) and prefixlen <= size * 8:
                    found.append((index, f"{ipaddress.ip_address(raw)}/{prefixlen}"))
        offset += _align(length)
    return found, False


def _dump() -> list[tuple[int, str]]:
    with socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, NETLINK_ROUTE) as sock:
        sock.settimeout(2.0)
        sock.bind((0, 0))
        header = _NLMSGHDR.pack(
            _NLMSGHDR.size + _IFADDRMSG.size, RTM_GETADDR, NLM_F_REQUEST | NLM_F_DUMP, 1, 0
        )
        sock.send(header + _IFADDRMSG.pack(socket.AF_UNSPEC, 0, 0, 0, 0))
        found: list[tuple[int, str]] = []
        while True:
            part, done = parse_addresses(sock.recv(65536))
            found += part
            if done:
                return found


def _mac(name: str) -> str | None:
    try:
        text = (_SYSFS / name / "address").read_text().strip().lower()
    except OSError:
        return None
    parts = text.split(":")
    if len(parts) != 6 or text == "00:00:00:00:00:00":
        return None
    if not all(len(p) == 2 and all(c in "0123456789abcdef" for c in p) for p in parts):
        return None
    return text


def discover() -> list[dict]:
    """The check-in's `interfaces`: each global-scope address with its interface's name
    and MAC. Loopback is left out. Empty when the kernel cannot be asked, which the
    server reads as "on no subnet's link"."""
    if sys.platform != "linux":
        return []
    try:
        addresses = _dump()
    except OSError:
        return []
    interfaces = []
    for index, cidr in addresses:
        if ipaddress.ip_interface(cidr).ip.is_loopback:
            continue
        try:
            name = socket.if_indextoname(index)
        except OSError:
            continue
        if not name or len(name) > INTERFACE_NAME_MAX or not name.isprintable():
            continue
        interfaces.append({"name": name, "cidr": cidr, "mac": _mac(name)})
        if len(interfaces) == INTERFACES_MAX:
            break
    return interfaces
