"""The agent's copy of the wire contract against the server's own module
(`api/app/agent_protocol.py`), so neither can change without the other noticing."""

import importlib
import tomllib
from pathlib import Path

import pytest
from app import agent_protocol as server
from app.services import jobs

from conftest import FakeClock, FakeProber, FakeResolver, job
from tlg_ipam_agent import guard, loop, protocol, scan
from tlg_ipam_agent.config import parse_allowed

AGENT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "name",
    [
        "PROTOCOL_VERSION",
        "CHECKIN_INTERVAL_S",
        "JOB_TIMEOUT_S",
        "CHECKIN_RETRY_MIN_S",
        "CHECKIN_RETRY_MAX_S",
        "CHECKIN_REFUSED_RETRY_S",
        "VERSION_MAX",
        "PLATFORM_MAX",
        "HOSTNAME_MAX",
        "INTERFACE_NAME_MAX",
        "INTERFACES_MAX",
        "OBSERVATIONS_MAX",
        "RDNS_NAME_MAX",
        "SCAN_ERROR_MAX",
    ],
)
def test_constants_match_the_server(name):
    assert getattr(protocol, name) == getattr(server, name)


def test_the_shortest_retry_is_not_inside_the_servers_checkin_gap():
    assert protocol.CHECKIN_RETRY_MIN_S >= jobs.DEFAULT_CHECKIN_GAP.total_seconds()


def test_the_lease_matches_the_server():
    assert protocol.LEASE_S == jobs.LEASE.total_seconds()
    assert protocol.JOB_TIMEOUT_S < protocol.LEASE_S


def test_the_checkin_body_is_what_the_server_accepts():
    body = loop.checkin_body([{"name": "eth0", "cidr": "10.0.0.5/24", "mac": "02:00:00:00:00:05"}])
    parsed = server.CheckinRequest.model_validate(body)
    assert parsed.protocol == server.PROTOCOL_VERSION
    assert parsed.capabilities == ["icmp", "rdns"]


def _server_job(**overrides) -> dict:
    """A job exactly as the server's own model serialises it."""
    return server.Job.model_validate(job(**overrides)).model_dump(mode="json")


async def test_the_servers_job_parses_and_the_result_set_is_what_the_server_accepts():
    raw = _server_job(dns_servers=["10.0.0.53"])
    parsed = protocol.parse_job(protocol.job_ref(raw), raw)
    addresses = guard.plan(parsed, parse_allowed(None))
    result = await scan.run(
        parsed,
        addresses,
        prober=FakeProber(alive=["10.0.0.1", "10.0.0.7"]),
        resolver=FakeResolver({"10.0.0.7": "NAS.Home.Arpa."}),
        max_pps=200,
        clock=FakeClock(),
    )
    request = server.ResultsRequest.model_validate(result)
    assert request.complete is True
    for record in request.observations:
        observation, reason = server.parse_observation(record)
        assert reason is None
        assert observation.model_dump(exclude_none=True) == record


def test_a_refusal_is_a_result_set_the_server_accepts():
    raw = _server_job()
    result = scan.refusal(protocol.job_ref(raw), "refused: subnet x is outside ALLOWED_CIDRS", FakeClock())
    request = server.ResultsRequest.model_validate(result)
    assert request.complete is False and request.observations == [] and request.error


NAMES = [
    "nas.home.arpa", "NAS.Home.Arpa.", "a", "a-b.c", "-a.b", "a-.b", "a..b", "", ".", "..",
    "a" * 63, "a" * 64, ".".join(["a" * 63] * 4), "x_y.home.arpa", "é.home.arpa",
    "xn--bcher-kva.example", "a b", "a\n", "<b>", None, 5, ["a"],
]


@pytest.mark.parametrize("value", NAMES)
def test_rdns_rule_matches_the_server(value):
    assert protocol.rdns_name(value) == server.rdns_name(value)


@pytest.mark.parametrize(
    "value", [None, "", "  ", "a\nb", "x" * 900, "tab\there", "‮evil", "ok", "a\x00b  c"]
)
def test_scan_error_rule_matches_the_server(value):
    assert protocol.scan_error(value) == server.scan_error(value)


def test_the_package_has_its_entry_point_and_declared_dependencies():
    meta = tomllib.loads((AGENT / "pyproject.toml").read_text())["project"]
    module, _, func = meta["scripts"]["tlg-ipam-agent"].partition(":")
    assert callable(getattr(importlib.import_module(module), func))
    declared = sorted(meta["dependencies"])
    pinned_from = sorted(
        line.strip()
        for line in (AGENT / "requirements.in").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    )
    # spec §5.6: an HTTP client, icmplib and dnspython, and the lock built from the same
    assert declared == pinned_from
    assert [d.split(">")[0] for d in declared] == ["dnspython", "httpx", "icmplib"]
