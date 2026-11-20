"""ARP frames (RFC 826) for the active sweep (spec §5.2): building a request and reading
a reply, as bytes only.

Nothing here opens a socket or sends a frame: `probe.ArpSession` does both, on the one
interface whose network contains the job's subnet. Ethernet and IPv4 only, which is
what ARP resolves; a frame of any other kind, or one cut short, is not a reply.
"""

import ipaddress
import struct

ETH_P_ARP = 0x0806
ETH_P_IP = 0x0800
ARPHRD_ETHER = 1
ARPOP_REQUEST = 1
ARPOP_REPLY = 2
BROADCAST = b"\xff" * 6
_ZERO_MAC = b"\x00" * 6

_ETHER = struct.Struct("!6s6sH")  # destination, source, ethertype
_ARP = struct.Struct("!HHBBH6s4s6s4s")  # htype, ptype, hlen, plen, oper, sha, spa, tha, tpa
# what a request is on the wire; the driver pads it to Ethernet's 60-byte minimum
FRAME_LEN = _ETHER.size + _ARP.size


def mac_bytes(text: str) -> bytes:
    """`aa:bb:cc:dd:ee:ff` (the form `interfaces.discover` reports) as six bytes.
    Raises ValueError for anything else, and for a MAC no frame can come from."""
    parts = text.split(":")
    if len(parts) != 6 or not all(len(p) == 2 for p in parts):
        raise ValueError("not a MAC address")
    raw = bytes.fromhex("".join(parts))
    if not is_unicast(raw):
        raise ValueError("not a unicast MAC address")
    return raw


def mac_text(raw: bytes) -> str:
    return ":".join(f"{b:02x}" for b in raw)


def is_unicast(raw: bytes) -> bool:
    """A MAC one station can own: not all zeroes, and the group bit clear (broadcast
    and multicast both set it)."""
    return len(raw) == 6 and raw != _ZERO_MAC and not raw[0] & 1


def request(sender_mac: bytes, sender_ip: ipaddress.IPv4Address, target: ipaddress.IPv4Address) -> bytes:
    """A broadcast "who has `target`? tell `sender_ip`" from this interface."""
    return _ETHER.pack(BROADCAST, sender_mac, ETH_P_ARP) + _ARP.pack(
        ARPHRD_ETHER, ETH_P_IP, 6, 4, ARPOP_REQUEST,
        sender_mac, sender_ip.packed, _ZERO_MAC, target.packed,
    )


def reply(frame: bytes, own_ip: ipaddress.IPv4Address) -> tuple[ipaddress.IPv4Address, str] | None:
    """`(address, MAC)` from an Ethernet ARP reply sent to `own_ip`, or None for any
    other frame: too short, another ethertype or hardware type, a request, a reply to
    some other host, or one whose sender MAC no station can own.

    Says nothing about whether the agent asked for that address: `probe.ArpSession`
    takes a reply only from an address it sent a request to. Trailing bytes (Ethernet
    padding) are ignored."""
    if len(frame) < FRAME_LEN:
        return None
    _dst, _src, ethertype = _ETHER.unpack_from(frame)
    if ethertype != ETH_P_ARP:
        return None
    htype, ptype, hlen, plen, oper, sha, spa, _tha, tpa = _ARP.unpack_from(frame, _ETHER.size)
    if (htype, ptype, hlen, plen, oper) != (ARPHRD_ETHER, ETH_P_IP, 6, 4, ARPOP_REPLY):
        return None
    if tpa != own_ip.packed or not is_unicast(sha):
        return None
    return ipaddress.IPv4Address(spa), mac_text(sha)
