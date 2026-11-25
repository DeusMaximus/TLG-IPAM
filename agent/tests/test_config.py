"""Configuration from the environment (spec §5.6)."""

import ipaddress

import pytest

from conftest import ENROL_TOKEN, TOKEN
from tlg_ipam_agent.config import DEFAULT_MAX_PPS, ConfigError, load, parse_allowed, parse_allowed_dns

GOOD = {"IPAM_URL": "https://ipam.home.arpa", "IPAM_AGENT_TOKEN": TOKEN}


def test_defaults():
    settings = load(GOOD)
    assert settings.url == "https://ipam.home.arpa"
    assert settings.agent_token == TOKEN and settings.enrol_token is None
    cfg = settings.config(settings.agent_token)
    assert cfg.token == TOKEN and cfg.url == settings.url
    assert cfg.max_pps == DEFAULT_MAX_PPS == 200
    # unset: no limit on the DNS servers, as before #93
    assert settings.allowed_dns is None and cfg.allowed_dns is None
    assert [str(n) for n in cfg.allowed] == [
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
    ]


def test_repr_never_shows_the_token():
    assert TOKEN not in repr(load(GOOD))
    assert TOKEN not in str(load(GOOD))
    assert TOKEN not in repr(load(GOOD).config(TOKEN))
    enrolling = load({"IPAM_URL": "https://ipam.home.arpa", "IPAM_ENROL_TOKEN": ENROL_TOKEN})
    assert ENROL_TOKEN not in repr(enrolling) and ENROL_TOKEN not in str(enrolling)


@pytest.mark.parametrize("value", [None, ""])
def test_neither_token_is_needed_to_load_the_settings(value):
    """Where the token comes from when neither is set (a stored one) is `enrol`'s to
    decide, after the settings are read; set but empty counts as unset."""
    env = {"IPAM_URL": "https://ipam.home.arpa"}
    if value is not None:
        env |= {"IPAM_AGENT_TOKEN": value, "IPAM_ENROL_TOKEN": value}
    settings = load(env)
    assert settings.agent_token is None and settings.enrol_token is None


def test_an_enrolment_token_is_read_from_its_own_variable():
    settings = load({"IPAM_URL": "https://ipam.home.arpa", "IPAM_ENROL_TOKEN": ENROL_TOKEN})
    assert settings.enrol_token == ENROL_TOKEN and settings.agent_token is None


def test_both_tokens_set_is_refused():
    with pytest.raises(ConfigError, match="IPAM_AGENT_TOKEN and IPAM_ENROL_TOKEN are both set") as err:
        load({**GOOD, "IPAM_ENROL_TOKEN": ENROL_TOKEN})
    assert TOKEN not in str(err.value) and ENROL_TOKEN not in str(err.value)


@pytest.mark.parametrize(
    "token",
    [TOKEN, "not-a-token", f" {ENROL_TOKEN}", f"{ENROL_TOKEN}\n", "tlge_a b", "tlge_é", "tlge_" + "x" * 300],
)
def test_refused_enrolment_tokens_never_echo_the_value(token):
    with pytest.raises(ConfigError, match="IPAM_ENROL_TOKEN") as err:
        load({"IPAM_URL": "https://ipam.home.arpa", "IPAM_ENROL_TOKEN": token})
    assert token.strip() not in str(err.value)


def test_a_token_of_the_other_kind_is_named_as_such():
    with pytest.raises(ConfigError, match="it is an enrolment token"):
        load({**GOOD, "IPAM_AGENT_TOKEN": ENROL_TOKEN})
    with pytest.raises(ConfigError, match="it is an agent token"):
        load({"IPAM_URL": "https://ipam.home.arpa", "IPAM_ENROL_TOKEN": TOKEN})


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://ipam.home.arpa/", "https://ipam.home.arpa"),
        ("https://ipam.home.arpa:8443/ipam/", "https://ipam.home.arpa:8443/ipam"),
        ("http://localhost:8080", "http://localhost:8080"),
        ("http://127.0.0.1:8080", "http://127.0.0.1:8080"),
        ("http://[::1]:8080", "http://[::1]:8080"),
    ],
)
def test_accepted_urls(url, expected):
    assert load({**GOOD, "IPAM_URL": url}).url == expected


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "http://ipam.home.arpa",
        "http://10.0.0.2:8080",
        "ftp://ipam.home.arpa",
        "ipam.home.arpa",
        "https://",
        "https://user:pw@ipam.home.arpa",
        "https://ipam.home.arpa/?x=1",
        "https://ipam.home.arpa:99999",
        " https://ipam.home.arpa",
    ],
)
def test_refused_urls(url):
    env = dict(GOOD)
    if url is None:
        del env["IPAM_URL"]
    else:
        env["IPAM_URL"] = url
    with pytest.raises(ConfigError, match="IPAM_URL"):
        load(env)


@pytest.mark.parametrize(
    "token",
    ["tlge_enrolment", "not-a-token", f" {TOKEN}", f"{TOKEN}\n", "tlga_a b", "tlga_é", "tlga_" + "x" * 300],
)
def test_refused_tokens_never_echo_the_value(token):
    with pytest.raises(ConfigError, match="IPAM_AGENT_TOKEN") as err:
        load({**GOOD, "IPAM_AGENT_TOKEN": token})
    assert token.strip() not in str(err.value)


def test_allowed_cidrs_parsed():
    cfg = load({**GOOD, "ALLOWED_CIDRS": "10.20.0.0/16, 192.168.1.0/24  fd00::/8"})
    assert cfg.allowed == (
        ipaddress.ip_network("10.20.0.0/16"),
        ipaddress.ip_network("192.168.1.0/24"),
        ipaddress.ip_network("fd00::/8"),
    )


@pytest.mark.parametrize("value", ["", "  ", "10.0.0.1/8", "10.0.0.0/33", "ten", "10.0.0.0/8,"])
def test_bad_allowed_cidrs(value):
    if value == "10.0.0.0/8,":
        # a trailing separator is harmless
        assert len(load({**GOOD, "ALLOWED_CIDRS": value}).allowed) == 1
        return
    with pytest.raises(ConfigError, match="ALLOWED_CIDRS"):
        load({**GOOD, "ALLOWED_CIDRS": value})


@pytest.mark.parametrize("value,expected", [("1", 1), ("200", 200), (" 1000 ", 1000)])
def test_max_pps(value, expected):
    assert load({**GOOD, "MAX_PPS": value}).max_pps == expected


@pytest.mark.parametrize("value", ["0", "1001", "-5", "1.5", "", "fast", "٣"])
def test_bad_max_pps(value):
    with pytest.raises(ConfigError, match="MAX_PPS"):
        load({**GOOD, "MAX_PPS": value})


@pytest.mark.parametrize("name", ["ALLOWED_CIDRS", "--allowed-cidrs"])
@pytest.mark.parametrize(
    "value, place",
    [
        ("tlge_" + "y" * 43, "item 1 of 1"),
        ("10.0.0.0/8,tlge_" + "y" * 43, "item 2 of 2"),
        ("10.0.0.0/8 192.168.0.0/16 tlga_" + "x" * 43 + " 100.64.0.0/10", "item 3 of 4"),
        ("10.0.0.1/8", "item 1 of 1"),
    ],
)
def test_an_item_that_is_not_a_network_is_named_by_its_place(name, value, place):
    """Never by its value (#140): a token pasted into the wrong option, on install's
    command line or a container's `-e`, is not repeated. The running agent and the
    installer share the rule."""
    with pytest.raises(ConfigError) as refused:
        parse_allowed(value, name)
    message = str(refused.value)
    assert message == f"{name}: {place} is not a network (host bits must be zero); it is not repeated here"
    assert "y" * 43 not in message and "x" * 43 not in message and "10.0.0.1" not in message


# --- ALLOWED_DNS_SERVERS (#93) ---


def test_allowed_dns_servers_parsed_and_carried_into_the_config():
    """Addresses and networks, separated as ALLOWED_CIDRS's are: an address alone is
    the network of that one address."""
    settings = load({**GOOD, "ALLOWED_DNS_SERVERS": "10.0.0.53, 192.168.1.0/24  fd00::53"})
    expected = (
        ipaddress.ip_network("10.0.0.53/32"),
        ipaddress.ip_network("192.168.1.0/24"),
        ipaddress.ip_network("fd00::53/128"),
    )
    assert settings.allowed_dns == expected
    assert settings.config(TOKEN).allowed_dns == expected
    assert "10.0.0.53/32" in repr(settings.config(TOKEN)) and TOKEN not in repr(settings.config(TOKEN))
    assert parse_allowed_dns(None) is None


@pytest.mark.parametrize("value", ["", "  ", ",", "10.0.0.53/24", "10.0.0.0/33", "dns.home.arpa", "https://dns.example.com/dns-query"])
def test_bad_allowed_dns_servers_stop_the_agent(value):
    """Set but empty is refused, not read as "ask no server" or "any": a job that should
    make no lookup is one whose target does not issue rdns. A name or a URL is refused,
    since the agent asks addresses only, and so is a network with host bits set."""
    with pytest.raises(ConfigError, match="ALLOWED_DNS_SERVERS") as refused:
        load({**GOOD, "ALLOWED_DNS_SERVERS": value})
    assert "dns.home.arpa" not in str(refused.value) and "example.com" not in str(refused.value)


def test_an_allowed_dns_item_that_is_not_a_network_is_named_by_its_place():
    with pytest.raises(ConfigError) as refused:
        load({**GOOD, "ALLOWED_DNS_SERVERS": "10.0.0.53 tlge_" + "y" * 43})
    assert str(refused.value) == (
        "ALLOWED_DNS_SERVERS: item 2 of 2 is not a network (host bits must be zero); it is not repeated here"
    )
