"""The agent's copy of the wire contract against the server's own module
(`api/app/agent_protocol.py`), so neither can change without the other noticing."""

import importlib
import tomllib
from pathlib import Path

import uuid
from datetime import datetime, timezone

import pytest
from app import agent_protocol as server
from app.models import Agent as AgentRow
from app.services import agents, jobs

from conftest import FakeClock, FakeProber, FakeResolver, job
from tlg_ipam_agent import client, config, guard, loop, probe, protocol, scan
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


def test_the_agent_token_prefix_is_the_servers():
    """The agent refuses an `IPAM_AGENT_TOKEN` without the prefix the server issues,
    which is not an enrolment token's (#21)."""
    assert config.TOKEN_PREFIX == server.AGENT_TOKEN_PREFIX
    assert not server.ENROL_TOKEN_PREFIX.startswith(config.TOKEN_PREFIX)


def test_the_enrolment_token_prefix_is_the_servers():
    """`IPAM_ENROL_TOKEN` and `install --enrol-token` take what the server issues (#22)."""
    assert config.ENROL_TOKEN_PREFIX == server.ENROL_TOKEN_PREFIX
    assert not server.AGENT_TOKEN_PREFIX.startswith(config.ENROL_TOKEN_PREFIX)


class _Session:
    def add(self, row):
        pass


def _issued(kind: str) -> str:
    """A plain token as the server's own `agents.issue_token` makes it."""
    _row, plain = agents.issue_token(_Session(), AgentRow(name="x"), kind, datetime.now(timezone.utc))
    return plain


@pytest.mark.parametrize("kind, prefix", [("agent", config.TOKEN_PREFIX), ("enrol", config.ENROL_TOKEN_PREFIX)])
def test_every_token_the_server_issues_has_the_shape_the_agent_takes(kind, prefix):
    """The agent checks a token's shape (`config.parse_token`) before it uses one, or
    writes one to disk; a token the server issues always passes."""
    for _ in range(200):
        plain = _issued(kind)
        assert config.parse_token(plain, "token", prefix) == plain
        assert len(plain) <= config.TOKEN_MAX


def test_every_agent_token_the_server_issues_is_the_form_the_enrol_answer_must_carry():
    """`client.ISSUED_AGENT_TOKEN` is the agent's copy of what `agents.issue_token`
    makes (finding 4 of the review of #130): an answer carrying anything else is not
    stored. A change to the server's form fails here before it fails an enrolment."""
    for _ in range(500):
        assert client.ISSUED_AGENT_TOKEN.fullmatch(_issued("agent"))
    assert not client.ISSUED_AGENT_TOKEN.fullmatch(_issued("enrol"))


def test_the_enrol_body_is_what_the_server_accepts_and_stores_unchanged():
    body = loop.reported()
    parsed = server.EnrolRequest.model_validate(body)
    assert parsed.model_dump() == body


def test_the_servers_enrol_answer_is_in_the_shape_the_agent_takes():
    plain = _issued("agent")
    answer = server.EnrolResponse(agent_id=str(uuid.uuid4()), agent_token=plain).model_dump(mode="json")
    assert client.enrol_data(answer) == answer


def test_the_shortest_retry_is_not_inside_the_servers_checkin_gap():
    assert protocol.CHECKIN_RETRY_MIN_S >= jobs.DEFAULT_CHECKIN_GAP.total_seconds()


def test_the_lease_matches_the_server():
    assert protocol.LEASE_S == jobs.LEASE.total_seconds()
    assert protocol.JOB_TIMEOUT_S < protocol.LEASE_S


def test_a_job_carries_no_more_dns_servers_than_the_agent_asks():
    """The server cuts a job's servers to what the agent asks per lookup (#75): more
    would be bytes of the check-in answer the agent never uses."""
    assert probe.MAX_DNS_SERVERS == server.DNS_SERVERS_MAX


def test_the_checkin_body_is_what_the_server_accepts():
    body = loop.checkin_body([{"name": "eth0", "cidr": "10.0.0.5/24", "mac": "02:00:00:00:00:05"}])
    parsed = server.CheckinRequest.model_validate(body)
    assert parsed.protocol == server.PROTOCOL_VERSION
    assert parsed.capabilities == ["arp", "icmp", "rdns"]


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


# names of exactly the cap and one past it, with and without the trailing dot the
# rule removes first: a cut-off moved by one either way fails here
_253 = ".".join(["a" * 63] * 3 + ["b" * 61])
_254 = ".".join(["a" * 63] * 3 + ["b" * 62])
assert (len(_253), len(_254)) == (253, 254)
NAMES = [
    "nas.home.arpa", "NAS.Home.Arpa.", "a", "a-b.c", "-a.b", "a-.b", "a..b", "", ".", "..",
    "a" * 63, "a" * 64, ".".join(["a" * 63] * 4), "x_y.home.arpa", "é.home.arpa",
    "xn--bcher-kva.example", "a b", "a\n", "<b>", None, 5, ["a"],
    _253, _253 + ".", _253 + "..", _254, _254 + ".", "a" * 63 + "." + "b" * 63,
    "0", "9.9", "a-" * 31 + "a", "-", "a.-", "A" * 63, "a.b.", ".a", "a\u00a0b",
]


def test_the_boundary_names_sit_either_side_of_the_cap():
    assert server.rdns_name(_253) == _253 and server.rdns_name(_253 + ".") == _253
    assert server.rdns_name(_254) is None and server.rdns_name(_254 + ".") is None


@pytest.mark.parametrize("value", NAMES)
def test_rdns_rule_matches_the_server(value):
    assert protocol.rdns_name(value) == server.rdns_name(value)


@pytest.mark.parametrize(
    "value",
    [
        None, "", "  ", "a\nb", "x" * 900, "tab\there", "\u202eevil", "ok", "a\x00b  c",
        # the 500-character cut, at it and either side, and a cut that lands on a space
        "x" * 499, "x" * 500, "x" * 501, "x" * 499 + " y", " " * 600 + "z", "é" * 501,
    ],
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
