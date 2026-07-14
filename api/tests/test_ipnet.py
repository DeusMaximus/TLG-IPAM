from types import SimpleNamespace

from app.ipnet import exclusion_intervals, interval_size, iter_free


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
