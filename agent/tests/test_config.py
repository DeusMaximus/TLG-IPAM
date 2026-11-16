"""Configuration from the environment (spec §5.6)."""

import ipaddress

import pytest

from conftest import TOKEN
from tlg_ipam_agent.config import DEFAULT_MAX_PPS, ConfigError, load

GOOD = {"IPAM_URL": "https://ipam.home.arpa", "IPAM_AGENT_TOKEN": TOKEN}


def test_defaults():
    cfg = load(GOOD)
    assert cfg.url == "https://ipam.home.arpa"
    assert cfg.token == TOKEN
    assert cfg.max_pps == DEFAULT_MAX_PPS == 200
    assert [str(n) for n in cfg.allowed] == [
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
    ]


def test_repr_never_shows_the_token():
    assert TOKEN not in repr(load(GOOD))
    assert TOKEN not in str(load(GOOD))


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
    [None, "", "tlge_enrolment", "not-a-token", f" {TOKEN}", f"{TOKEN}\n", "tlga_a b", "tlga_é"],
)
def test_refused_tokens_never_echo_the_value(token):
    env = dict(GOOD)
    if token is None:
        del env["IPAM_AGENT_TOKEN"]
    else:
        env["IPAM_AGENT_TOKEN"] = token
    with pytest.raises(ConfigError, match="IPAM_AGENT_TOKEN") as err:
        load(env)
    if token:
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
