"""Configuration from the environment (spec §5.6): `IPAM_URL`, `IPAM_AGENT_TOKEN`,
`ALLOWED_CIDRS`, `MAX_PPS`. A bad value stops the agent before it sends anything, with
a message that names the variable and never contains the token.
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


def _is_loopback(host: str) -> bool:
    if host.lower() in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def parse_url(value: str | None) -> str:
    """The server's base URL. `https` always, so the token never travels in clear and
    the server's certificate is always checked; plain `http` only to this host's own
    loopback, where nothing crosses a network."""
    if not value or not value.strip():
        raise ConfigError("IPAM_URL is not set: the server's URL, such as https://ipam.home.arpa")
    if value != value.strip():
        raise ConfigError("IPAM_URL has whitespace at either end")
    try:
        parts = urlsplit(value)
        _ = parts.port  # raises on a bad port
    except ValueError:
        raise ConfigError("IPAM_URL is not a URL") from None
    host = parts.hostname or ""
    if not host:
        raise ConfigError("IPAM_URL has no host")
    if parts.username is not None or parts.password is not None:
        raise ConfigError("IPAM_URL must not carry a user name or password")
    if parts.query or parts.fragment:
        raise ConfigError("IPAM_URL must not carry a query or a fragment")
    if parts.scheme == "https":
        pass
    elif parts.scheme == "http" and _is_loopback(host):
        pass
    elif parts.scheme == "http":
        raise ConfigError(
            "IPAM_URL must use https: the agent token is a credential and the server's "
            "certificate is always verified. Plain http is accepted only for a server on "
            "this host's loopback address."
        )
    else:
        raise ConfigError("IPAM_URL must start with https://")
    return value.rstrip("/")


def parse_token(value: str | None) -> str:
    if not value:
        raise ConfigError("IPAM_AGENT_TOKEN is not set: the tlga_ token from the Agents page")
    if value != value.strip():
        raise ConfigError("IPAM_AGENT_TOKEN has whitespace at either end")
    if not value.startswith(TOKEN_PREFIX):
        raise ConfigError(
            "IPAM_AGENT_TOKEN is not an agent token: it starts with tlga_ (the Agents page "
            "shows it once, when the agent is created)"
        )
    if not (value.isascii() and value.isprintable()) or any(c.isspace() for c in value):
        raise ConfigError("IPAM_AGENT_TOKEN contains characters a token never has")
    return value


def parse_allowed(value: str | None) -> tuple[Network, ...]:
    """Networks separated by commas or whitespace. Unset means the default; a value that
    is set but empty is refused rather than read as "allow nothing" or "the default".
    A network with host bits set is refused too, since it is most likely a typo."""
    if value is None:
        items = list(DEFAULT_ALLOWED_CIDRS)
    else:
        items = value.replace(",", " ").split()
        if not items:
            raise ConfigError("ALLOWED_CIDRS is set but empty; unset it for the default")
    networks = []
    for item in items:
        try:
            networks.append(ipaddress.ip_network(item, strict=True))
        except ValueError:
            shown = item if len(item) <= 64 and item.isprintable() else "a value"
            raise ConfigError(
                f"ALLOWED_CIDRS: {shown} is not a network (host bits must be zero)"
            ) from None
    return tuple(networks)


def parse_max_pps(value: str | None) -> int:
    if value is None:
        return DEFAULT_MAX_PPS
    value = value.strip()
    if not (value.isascii() and value.isdigit()) or not 1 <= int(value) <= MAX_PPS_CEILING:
        raise ConfigError(f"MAX_PPS must be a whole number from 1 to {MAX_PPS_CEILING}")
    return int(value)


def load(env: Mapping[str, str]) -> Config:
    return Config(
        url=parse_url(env.get("IPAM_URL")),
        token=parse_token(env.get("IPAM_AGENT_TOKEN")),
        allowed=parse_allowed(env.get("ALLOWED_CIDRS")),
        max_pps=parse_max_pps(env.get("MAX_PPS")),
    )
