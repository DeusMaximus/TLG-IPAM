import ipaddress
from types import SimpleNamespace

import pytest

from app.errors import ApiError
from app.ipnet import (
    canonical_cidr,
    exclusion_intervals,
    format_intervals,
    interval_size,
    iter_free,
    subtract_intervals,
)


def test_large_exclusions_are_merged_without_address_expansion():
    subnet = SimpleNamespace(
        cidr="10.0.0.0/8",
        gateway="10.0.0.1",
        dhcp_start="10.0.0.2",
        dhcp_end="10.255.255.253",
    )
    reserved = [
        SimpleNamespace(start_int=167772170, end_int=167772180),
        SimpleNamespace(start_int=184549360, end_int=184549373),
    ]

    intervals = exclusion_intervals(subnet, reserved)

    assert intervals == [(167772161, 184549373)]
    assert interval_size(intervals) == 16_777_213
    assert iter_free(subnet, set(), reserved, limit=2) == ["10.255.255.254"]


def test_free_iteration_skips_allocated_and_disjoint_intervals():
    subnet = SimpleNamespace(
        cidr="192.0.2.0/29",
        gateway="192.0.2.1",
        dhcp_start=None,
        dhcp_end=None,
    )
    reserved = [SimpleNamespace(start_int=3221225987, end_int=3221225988)]

    assert iter_free(subnet, {3221225986}, reserved, limit=10) == ["192.0.2.5", "192.0.2.6"]


@pytest.mark.parametrize(
    "removed, expected",
    [
        ([], [(1, 254)]),
        ([(10, 19)], [(1, 9), (20, 254)]),
        ([(0, 5)], [(6, 254)]),  # clipped to the base
        ([(250, 300)], [(1, 249)]),
        ([(1, 254)], []),
        ([(10, 30), (20, 40)], [(1, 9), (41, 254)]),  # overlapping removals merge
        ([(10, 19), (20, 29)], [(1, 9), (30, 254)]),  # adjacent removals merge
        ([(100, 120), (10, 19)], [(1, 9), (20, 99), (121, 254)]),  # any order
        ([(300, 400)], [(1, 254)]),  # outside the base
    ],
)
def test_subtract_intervals(removed, expected):
    assert subtract_intervals((1, 254), removed) == expected


def test_subtract_from_an_empty_interval():
    assert subtract_intervals((5, 4), []) == []


def test_subtract_does_not_expand_addresses():
    # a /8 minus one address: two intervals, never 16 million list entries
    assert subtract_intervals((1, 2**24 - 2), [(100, 100)]) == [(1, 99), (101, 2**24 - 2)]


def test_format_intervals_uses_the_network_family():
    net = ipaddress.ip_network("10.0.0.0/24")
    base = int(net.network_address)
    assert format_intervals(net, [(base + 1, base + 9)]) == [
        {"start": "10.0.0.1", "end": "10.0.0.9"}
    ]


@pytest.mark.parametrize(
    ("typed", "canonical"),
    [
        ("10.0.0.0/24", "10.0.0.0/24"),
        ("10.0.0.0/255.255.255.0", "10.0.0.0/24"),
        ("10.0.0.0/0.0.0.255", "10.0.0.0/24"),
        ("10.0.0.0/0024", "10.0.0.0/24"),
        ("192.168.240.0/" + "0" * 4298 + "20", "192.168.240.0/20"),
        ("10.0.0.5", "10.0.0.5/32"),
        ("255.255.255.255/255.255.255.255", "255.255.255.255/32"),
    ],
)
def test_canonical_cidr_is_one_spelling_per_network(typed, canonical):
    assert canonical_cidr(typed) == canonical
    assert ipaddress.ip_network(typed) == ipaddress.ip_network(canonical)
    # the longest an IPv4 network can be spelled canonically (#97)
    assert len(canonical_cidr(typed)) <= len("255.255.255.255/32")


@pytest.mark.parametrize("bad", ["10.0.0.1/24", " 10.0.0.0/24", "10.0.0.0/33", "fd00::/64"])
def test_canonical_cidr_refuses_what_parse_cidr_refuses(bad):
    with pytest.raises(ApiError):
        canonical_cidr(bad)
