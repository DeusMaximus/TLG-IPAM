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
from tlg_ipam_agent import client, config, enrol, guard, loop, probe, protocol, scan
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
        "ENROL_TOO_OLD",
        "ENROL_TOO_NEW",
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
    stored. A change to the server's form fails here before it fails an enrolment, and
    `api/tests/test_agents.py` holds the server to `agent_protocol.AGENT_TOKEN_FORM`."""
    for _ in range(500):
        assert client.ISSUED_AGENT_TOKEN.fullmatch(_issued("agent"))
    assert not client.ISSUED_AGENT_TOKEN.fullmatch(_issued("enrol"))


def test_the_agents_rule_for_an_issued_agent_token_is_the_wire_contracts_form():
    """`client.ISSUED_AGENT_TOKEN` is `agent_protocol.AGENT_TOKEN_FORM` written out (the
    agent does not import pydantic's module), to the character (#140)."""
    assert client.ISSUED_AGENT_TOKEN.pattern == server.AGENT_TOKEN_FORM.pattern
    assert client.ISSUED_AGENT_TOKEN.flags == server.AGENT_TOKEN_FORM.flags


@pytest.mark.parametrize(
    "token",
    [
        "tlga_" + "A" * 43,
        "tlga_" + "-_" * 21 + "z",
        "tlga_" + "A" * 42,
        "tlga_" + "A" * 44,
        "tlga_" + "A" * 42 + "=",
        "tlga_" + "A" * 42 + "+",
        "tlga_" + "A" * 42 + "/",
        "tlga_" + "A" * 42 + "é",
        "tlga_" + "A" * 42 + "\n",
        "tlge_" + "A" * 43,
        "TLGA_" + "A" * 43,
        "",
    ],
)
def test_the_agent_and_the_contract_take_the_same_tokens(token):
    assert bool(client.ISSUED_AGENT_TOKEN.fullmatch(token)) == bool(server.AGENT_TOKEN_FORM.fullmatch(token))


def test_the_tokens_the_tests_spell_by_hand_are_of_the_issued_form():
    """The agent's tests write their tokens out (`conftest`, and `answer_memory`, which
    runs in a fresh interpreter); each is one the server could issue."""
    import answer_memory
    import conftest

    for token in (conftest.TOKEN, answer_memory.TOKEN):
        assert server.AGENT_TOKEN_FORM.fullmatch(token)
    assert server.ENROL_TOKEN_FORM.fullmatch(conftest.ENROL_TOKEN)


def test_the_enrol_body_is_what_the_server_accepts_and_stores_unchanged():
    """The report as check-in sends it, and the protocol (#165), which is the field the
    server judges and does not store."""
    body = enrol.enrol_body()
    parsed = server.EnrolRequest.model_validate(body)
    assert parsed.model_dump() == body
    assert {k: v for k, v in body.items() if k != "protocol"} == loop.reported()


def test_the_enrol_body_states_the_protocol_the_server_answers():
    """`agent_protocol` states the field and the rule (#165): the agent sends its
    PROTOCOL_VERSION as `protocol`, an integer, which the server of the same protocol
    takes and one of any other refuses with the code the agent reads."""
    body = enrol.enrol_body()
    assert "protocol" in server.EnrolRequest.model_fields
    assert type(body["protocol"]) is int and body["protocol"] == server.PROTOCOL_VERSION
    assert server.enrol_protocol_refusal(body) is None
    assert server.enrol_protocol(body) == protocol.PROTOCOL_VERSION
    newer = {**body, "protocol": protocol.PROTOCOL_VERSION + 1}
    older = {**body, "protocol": protocol.PROTOCOL_VERSION - 1}
    assert server.enrol_protocol_refusal(newer) == protocol.ENROL_TOO_NEW
    assert server.enrol_protocol_refusal(older) == protocol.ENROL_TOO_OLD
    # the codes are of the vocabulary shape the agent reads (`client._code`)
    for code in (protocol.ENROL_TOO_OLD, protocol.ENROL_TOO_NEW):
        assert client._CODE.fullmatch(code)


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
    """Each of the three things a lookup can find (#100): a name, an answer that there
    is none (`rdns_no_name`), and no usable answer (neither), each a record the server
    reads back field for field, in a result set of the server's protocol."""
    raw = _server_job(dns_servers=["10.0.0.53"])
    parsed = protocol.parse_job(protocol.job_ref(raw), raw)
    addresses = guard.plan(parsed, parse_allowed(None))
    result = await scan.run(
        parsed,
        addresses,
        prober=FakeProber(alive=["10.0.0.1", "10.0.0.7", "10.0.0.9"]),
        resolver=FakeResolver({"10.0.0.7": "NAS.Home.Arpa."}, unanswered=["10.0.0.9"]),
        max_pps=200,
        clock=FakeClock(),
    )
    request = server.ResultsRequest.model_validate(result)
    assert request.protocol == server.PROTOCOL_VERSION in server.RESULTS_PROTOCOLS
    assert request.complete is True and request.liveness_complete is True
    assert request.marks_dead() is True
    assert result["observations"] == [
        {"ip": "10.0.0.1", "rdns_no_name": True},
        {"ip": "10.0.0.7", "rdns_name": "nas.home.arpa"},
        {"ip": "10.0.0.9"},
    ]
    for record in request.observations:
        observation, reason = server.parse_observation(record)
        assert reason is None
        assert observation.model_dump(exclude_defaults=True) == record


async def test_an_agent_result_set_whose_lookups_did_not_run_still_marks_the_dead():
    """No DNS server to ask: the result set is incomplete, its liveness sweep is not,
    and the server reads it that way (#100)."""
    raw = _server_job()
    parsed = protocol.parse_job(protocol.job_ref(raw), raw)
    result = await scan.run(
        parsed,
        guard.plan(parsed, parse_allowed(None)),
        prober=FakeProber(alive=["10.0.0.1"]),
        resolver=FakeResolver(host=()),
        max_pps=200,
        clock=FakeClock(),
    )
    request = server.ResultsRequest.model_validate(result)
    assert request.complete is False and request.liveness_complete is True
    assert request.marks_dead() is True
    assert request.error == probe.NO_DNS_SERVER


def test_a_refusal_is_a_result_set_the_server_accepts():
    raw = _server_job()
    result = scan.refusal(protocol.job_ref(raw), "refused: subnet x is outside ALLOWED_CIDRS", FakeClock())
    request = server.ResultsRequest.model_validate(result)
    assert request.protocol == server.PROTOCOL_VERSION
    assert request.complete is False and request.observations == [] and request.error
    assert request.liveness_complete is False and request.marks_dead() is False


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
