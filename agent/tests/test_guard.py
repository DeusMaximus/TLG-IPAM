"""The agent-side guard and job parsing (spec §5.6): refused before any packet."""

import ipaddress

import pytest

from conftest import job
from tlg_ipam_agent import guard
from tlg_ipam_agent.config import parse_allowed
from tlg_ipam_agent.protocol import OBSERVATIONS_MAX, JobRefused, job_ref, parse_job

DEFAULT = parse_allowed(None)


def plan(allowed=DEFAULT, **overrides):
    raw = job(**overrides)
    return guard.plan(parse_job(job_ref(raw), raw), allowed)


@pytest.mark.parametrize(
    "cidr,start,end",
    [
        ("10.0.0.0/24", "10.0.0.1", "10.0.0.254"),
        ("172.16.5.0/24", "172.16.5.1", "172.16.5.10"),
        ("192.168.1.0/24", "192.168.1.1", "192.168.1.1"),
        ("100.64.10.0/24", "100.64.10.1", "100.64.10.2"),
        ("10.1.0.0/20", "10.1.0.1", "10.1.15.254"),
    ],
)
def test_jobs_inside_the_default_allowlist(cidr, start, end):
    addresses = plan(cidr=cidr, ranges=[{"start": start, "end": end}])
    assert str(addresses[0]) == start and str(addresses[-1]) == end


@pytest.mark.parametrize(
    "cidr,start",
    [
        ("203.0.113.0/24", "203.0.113.1"),  # public (TEST-NET-3)
        ("8.8.8.0/24", "8.8.8.8"),
        ("172.32.0.0/24", "172.32.0.1"),  # just past 172.16/12
        ("10.0.0.0/7", "10.0.0.1"),  # wider than the allowed 10/8
        ("fd00::/120", "fd00::1"),  # IPv6 is not in the default list
    ],
)
def test_jobs_outside_the_allowlist_are_refused(cidr, start):
    with pytest.raises(JobRefused, match="outside ALLOWED_CIDRS"):
        plan(cidr=cidr, ranges=[{"start": start, "end": start}])


def test_the_owners_allowlist_replaces_the_default():
    allowed = parse_allowed("10.20.0.0/16")
    with pytest.raises(JobRefused, match="outside ALLOWED_CIDRS"):
        plan(allowed)  # 10.0.0.0/24 is in the default, not in this list
    assert plan(allowed, cidr="10.20.3.0/24", ranges=[{"start": "10.20.3.1", "end": "10.20.3.2"}])


@pytest.mark.parametrize(
    "ranges",
    [
        [{"start": "10.0.1.1", "end": "10.0.1.2"}],
        [{"start": "10.0.0.1", "end": "10.0.1.2"}],
        [{"start": "9.255.255.255", "end": "10.0.0.2"}],
        [{"start": "10.0.0.5", "end": "10.0.0.4"}],
        [{"start": "10.0.0.1", "end": "10.0.0.2"}, {"start": "192.168.0.1", "end": "192.168.0.1"}],
        [{"start": "::ffff:10.0.0.1", "end": "::ffff:10.0.0.2"}],
    ],
)
def test_ranges_outside_the_subnet_are_refused(ranges):
    with pytest.raises(JobRefused, match="not inside subnet 10.0.0.0/24"):
        plan(ranges=ranges)


def test_more_addresses_than_a_result_set_may_hold_are_refused():
    with pytest.raises(JobRefused, match=str(OBSERVATIONS_MAX)):
        plan(cidr="10.0.0.0/19", ranges=[{"start": "10.0.0.1", "end": "10.0.31.254"}])
    # counted before anything is built: a range of millions is refused as cheaply
    with pytest.raises(JobRefused):
        plan(cidr="10.0.0.0/8", ranges=[{"start": "10.0.0.0", "end": "10.255.255.255"}])


def test_only_network_and_broadcast_are_skipped_and_only_for_slash_30_and_shorter():
    assert [str(a) for a in plan(cidr="10.0.0.0/30", ranges=[{"start": "10.0.0.0", "end": "10.0.0.3"}])] == [
        "10.0.0.1",
        "10.0.0.2",
    ]
    assert len(plan(cidr="10.0.0.0/31", ranges=[{"start": "10.0.0.0", "end": "10.0.0.1"}])) == 2
    assert len(plan(cidr="10.0.0.7/32", ranges=[{"start": "10.0.0.7", "end": "10.0.0.7"}])) == 1
    with pytest.raises(JobRefused, match="no address"):
        plan(ranges=[{"start": "10.0.0.255", "end": "10.0.0.255"}])


def test_overlapping_ranges_are_swept_once():
    addresses = plan(ranges=[{"start": "10.0.0.1", "end": "10.0.0.10"}, {"start": "10.0.0.5", "end": "10.0.0.12"}])
    assert len(addresses) == len(set(addresses)) == 12


def test_ipv6_job_inside_an_ipv6_allowlist():
    addresses = plan(
        parse_allowed("fd00::/8"),
        cidr="fd00::/120",
        ranges=[{"start": "fd00::1", "end": "fd00::3"}],
    )
    assert addresses == tuple(ipaddress.ip_address(f"fd00::{i}") for i in (1, 2, 3))
    assert all(a.version == 6 for a in plan(parse_allowed("::/0"), cidr="::/120", ranges=[{"start": "::1", "end": "::2"}]))


@pytest.mark.parametrize(
    "raw",
    [None, [], "job", {"job_id": "a"}, {"subnet_id": "b"}, {"job_id": "x" * 37, "subnet_id": "b"},
     {"job_id": "a\nb", "subnet_id": "b"}, {"job_id": 5, "subnet_id": "b"}],
)
def test_a_job_without_usable_ids_cannot_be_reported(raw):
    assert job_ref(raw) is None


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"cidr": None}, "cidr"),
        ({"cidr": "10.0.0.0/24" + " " * 100}, "cidr"),
        ({"ranges": None}, "no ranges"),
        ({"ranges": ["10.0.0.1"]}, "pair of addresses"),
        ({"ranges": [{"start": "10.0.0.1"}]}, "pair of addresses"),
        ({"ranges": [{"start": "host.home.arpa", "end": "10.0.0.2"}]}, "pair of addresses"),
        ({"methods": "icmp"}, "methods"),
        ({"methods": [1]}, "methods"),
        ({"methods": ["icmp", "shell"]}, "does not know"),
        ({"methods": ["rdns", "fdns"]}, "liveness"),
        ({"dns_servers": "10.0.0.53"}, "dns_servers"),
        ({"dns_servers": ["resolver.home.arpa"]}, "DNS server"),
        ({"timeout_s": "600"}, "timeout_s"),
        ({"timeout_s": True}, "timeout_s"),
        ({"timeout_s": -1}, "timeout_s"),
    ],
)
def test_malformed_jobs_are_refused(overrides, reason):
    raw = job(**overrides)
    with pytest.raises(JobRefused, match=reason) as err:
        parse_job(job_ref(raw), raw)
    # the reason quotes nothing the agent did not parse
    assert "shell" not in err.value.reason and "home.arpa" not in err.value.reason


# --- ALLOWED_DNS_SERVERS (#93): the DNS servers a job's lookups may ask ---


def nets(*items):
    return tuple(ipaddress.ip_network(i) for i in items)


def test_unset_keeps_every_server():
    servers = ("192.0.2.1", "198.51.100.1", "2001:db8::53")
    assert guard.dns_servers(servers, None) == (servers, 0)
    assert guard.dns_servers((), None) == ((), 0)


@pytest.mark.parametrize(
    "servers, allowed, kept",
    [
        (("10.0.0.53", "1.1.1.1", "10.0.1.53"), ("10.0.0.0/8",), ("10.0.0.53", "10.0.1.53")),
        (("1.1.1.1", "10.0.0.53"), ("10.0.0.53",), ("10.0.0.53",)),
        (("1.1.1.1", "8.8.8.8"), ("10.0.0.0/8",), ()),
        (("fd00::53", "10.0.0.53"), ("fd00::/8",), ("fd00::53",)),
        (("10.0.0.53", "fd00::53"), ("10.0.0.0/8", "fd00::/8"), ("10.0.0.53", "fd00::53")),
        # an IPv4-mapped address is an IPv6 address, outside every IPv4 network
        (("::ffff:10.0.0.53",), ("10.0.0.0/8",), ()),
        (("10.0.0.53",), ("::/0",), ()),
        (("1.1.1.1",), ("0.0.0.0/0",), ("1.1.1.1",)),
    ],
)
def test_servers_outside_the_list_are_left_out_in_order(servers, allowed, kept):
    assert guard.dns_servers(servers, nets(*allowed)) == (kept, len(servers) - len(kept))


def test_an_entry_that_is_not_an_address_is_never_kept():
    assert guard.dns_servers(("dns.home.arpa", "10.0.0.53"), nets("0.0.0.0/0")) == (("10.0.0.53",), 1)


@pytest.mark.parametrize("server", ["fe80::53%2", "fe80::53%eth1", "fd00::53%1"])
@pytest.mark.parametrize("allowed", ["fe80::/10", "fe80::53", "fd00::/8", "::/0"])
def test_a_server_with_a_scope_is_never_kept(server, allowed):
    """Round 1 of the briefed review, finding 1: `ipaddress` puts `fe80::53%2` inside
    `fe80::/10` whatever its scope, but the query goes to the interface the scope
    names. The list holds no scope, so it names no interface, and the server is left
    out and counted. Unset, it is kept as before."""
    assert guard.dns_servers((server, "10.0.0.53"), nets(allowed, "10.0.0.0/8")) == (("10.0.0.53",), 1)
    assert guard.dns_servers((server,), None) == ((server,), 0)


def test_a_link_local_server_without_a_zone_is_compared_by_address_alone():
    """Round 2 of the briefed review, finding 1: the rule as it is, stated. A zone
    suffix, `%0` included, is never inside the list; a link-local address written
    without one is compared by its address like any other, and goes out on the link
    the host's routing picks, which the list does not decide."""
    assert guard.dns_servers(("fe80::53",), nets("fe80::/10")) == (("fe80::53",), 0)
    assert guard.dns_servers(("fe80::53",), nets("fe80::54")) == ((), 1)
    assert guard.dns_servers(("fe80::53%0",), nets("fe80::/10")) == ((), 1)
