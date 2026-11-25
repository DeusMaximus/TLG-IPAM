"""Configuration from the environment (spec §5.6): `IPAM_URL`, `IPAM_AGENT_TOKEN` or
`IPAM_ENROL_TOKEN`, `ALLOWED_CIDRS`, `MAX_PPS`. A bad value stops the agent before it
sends anything, with a message that names the variable and never contains the token.

Where the agent token comes from, when `IPAM_AGENT_TOKEN` is not set, is `enrol`'s:
the token stored by an earlier exchange, or an exchange of `IPAM_ENROL_TOKEN`.
"""

import ipaddress
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from .protocol import Network

# RFC 1918 and the RFC 6598 shared (CGNAT) range
DEFAULT_ALLOWED_CIDRS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10")
DEFAULT_MAX_PPS = 200
MAX_PPS_CEILING = 1000
TOKEN_PREFIX = "tlga_"
# agent_protocol.ENROL_TOKEN_PREFIX: the single-use token the Agents page shows
ENROL_TOKEN_PREFIX = "tlge_"
# the server's tokens are 48 characters; a longer value is not one of them
TOKEN_MAX = 256
_LOOPBACK_NAMES = {"localhost"}


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    url: str
    token: str
    allowed: tuple[Network, ...]
    max_pps: int

    def __repr__(self) -> str:  # never print the token
        return f"Config(url={self.url!r}, allowed={self.allowed!r}, max_pps={self.max_pps})"


@dataclass(frozen=True)
class Settings:
    """The environment's settings, before the agent token is known. At most one of the
    two tokens is set (`load` refuses both)."""

    url: str
    allowed: tuple[Network, ...]
    max_pps: int
    agent_token: str | None = None
    enrol_token: str | None = None

    def __repr__(self) -> str:  # never print a token
        return f"Settings(url={self.url!r}, allowed={self.allowed!r}, max_pps={self.max_pps})"

    def config(self, token: str) -> Config:
        return Config(url=self.url, token=token, allowed=self.allowed, max_pps=self.max_pps)


def _is_loopback(host: str) -> bool:
    if host.lower() in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def parse_url(value: str | None, name: str = "IPAM_URL") -> str:
    """The server's base URL. `https` always, so the token never travels in clear and
    the server's certificate is always checked; plain `http` only to this host's own
    loopback, where nothing crosses a network."""
    if not value or not value.strip():
        raise ConfigError(f"{name} is not set: the server's URL, such as https://ipam.home.arpa")
    if value != value.strip():
        raise ConfigError(f"{name} has whitespace at either end")
    try:
        parts = urlsplit(value)
        _ = parts.port  # raises on a bad port
    except ValueError:
        raise ConfigError(f"{name} is not a URL") from None
    host = parts.hostname or ""
    if not host:
        raise ConfigError(f"{name} has no host")
    if parts.username is not None or parts.password is not None:
        raise ConfigError(f"{name} must not carry a user name or password")
    if parts.query or parts.fragment:
        raise ConfigError(f"{name} must not carry a query or a fragment")
    if parts.scheme == "https":
        pass
    elif parts.scheme == "http" and _is_loopback(host):
        pass
    elif parts.scheme == "http":
        raise ConfigError(
            f"{name} must use https: the agent token is a credential and the server's "
            "certificate is always verified. Plain http is accepted only for a server on "
            "this host's loopback address."
        )
    else:
        raise ConfigError(f"{name} must start with https://")
    return value.rstrip("/")


def parse_token(value: str, name: str = "IPAM_AGENT_TOKEN", prefix: str = TOKEN_PREFIX) -> str:
    """A token of the kind `prefix` names, or ConfigError naming `name` (the variable,
    flag or file it came from) and never the value."""
    kind = "an agent token" if prefix == TOKEN_PREFIX else "an enrolment token"
    if value != value.strip():
        raise ConfigError(f"{name} has whitespace at either end")
    if not value.startswith(prefix):
        other = (
            "an enrolment token" if value.startswith(ENROL_TOKEN_PREFIX)
            else "an agent token" if value.startswith(TOKEN_PREFIX)
            else None
        )
        raise ConfigError(
            f"{name} is not {kind}, which starts with {prefix}" + (f": it is {other}" if other else "")
        )
    if (
        len(value) > TOKEN_MAX
        or not (value.isascii() and value.isprintable())
        or any(c.isspace() for c in value)
    ):
        raise ConfigError(f"{name} contains characters a token never has")
    return value


def _optional_token(env: Mapping[str, str], name: str, prefix: str) -> str | None:
    # unset and set-but-empty are the same: a compose file often carries `NAME=`
    value = env.get(name)
    return parse_token(value, name, prefix) if value else None


def parse_allowed(value: str | None, name: str = "ALLOWED_CIDRS") -> tuple[Network, ...]:
    """Networks separated by commas or whitespace. Unset means the default; a value that
    is set but empty is refused rather than read as "allow nothing" or "the default".
    A network with host bits set is refused too, since it is most likely a typo."""
    if value is None:
        items = list(DEFAULT_ALLOWED_CIDRS)
    else:
        items = value.replace(",", " ").split()
        if not items:
            raise ConfigError(f"{name} is set but empty; unset it for the default")
    networks = []
    for place, item in enumerate(items, 1):
        try:
            networks.append(ipaddress.ip_network(item, strict=True))
        except ValueError:
            # named by its place, never by its value: a token pasted into the wrong
            # option (`install --allowed-cidrs tlge_…`, `docker run -e …`) would be
            # repeated on the terminal (#140)
            raise ConfigError(
                f"{name}: item {place} of {len(items)} is not a network (host bits must be "
                "zero); it is not repeated here"
            ) from None
    return tuple(networks)


def parse_max_pps(value: str | None, name: str = "MAX_PPS") -> int:
    if value is None:
        return DEFAULT_MAX_PPS
    value = value.strip()
    if not (value.isascii() and value.isdigit()) or not 1 <= int(value) <= MAX_PPS_CEILING:
        raise ConfigError(f"{name} must be a whole number from 1 to {MAX_PPS_CEILING}")
    return int(value)


def load(env: Mapping[str, str]) -> Settings:
    url = parse_url(env.get("IPAM_URL"))
    agent_token = _optional_token(env, "IPAM_AGENT_TOKEN", TOKEN_PREFIX)
    enrol_token = _optional_token(env, "IPAM_ENROL_TOKEN", ENROL_TOKEN_PREFIX)
    settings = Settings(
        url=url,
        allowed=parse_allowed(env.get("ALLOWED_CIDRS")),
        max_pps=parse_max_pps(env.get("MAX_PPS")),
        agent_token=agent_token,
        enrol_token=enrol_token,
    )
    if agent_token and enrol_token:
        raise ConfigError(
            "IPAM_AGENT_TOKEN and IPAM_ENROL_TOKEN are both set: set IPAM_ENROL_TOKEN "
            "alone to enrol, or IPAM_AGENT_TOKEN alone to run with a token you hold"
        )
    return settings
