from typing import ClassVar
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, ValidationError
from pydantic_settings import BaseSettings
from sqlalchemy.engine import make_url

from .version import VERSION

# How `app.database` names the database to SQLAlchemy: this, then `DB_PATH` as it is
SQLITE_URL_PREFIX = "sqlite+aiosqlite:///"

# The one refusal of DB_PATH; fixed, so it repeats no value
DB_PATH_REFUSED = "DB_PATH must be a filesystem path or :memory:."

# The one refusal of PUBLIC_BASE_URL; fixed, so it repeats no value
PUBLIC_BASE_URL_REFUSED = (
    "PUBLIC_BASE_URL must be the server's public origin and nothing else, written as "
    "clients will use it: `https://` and a lowercase host name, with a port only when "
    "it is not 443 (https://ipam.example.com). No path, no trailing slash, no query, "
    "no fragment, no user name and no spaces. Plain `http://` is taken only for "
    "`localhost`, `127.0.0.1` or `[::1]`, for testing on one machine. Leave it unset "
    "to keep remote MCP (the built-in OAuth server) off."
)

# The one refusal of MCP_REMOTE_WRITES; fixed, so it repeats no value
MCP_REMOTE_WRITES_REFUSED = (
    "MCP_REMOTE_WRITES must be `true` or `false` (in any case), or unset, which is "
    "`false`. Nothing else is read as either: not `1`, `yes`, `on`, and no spaces."
)

# The value `.env.example` ships. Anyone who has read the repository knows it.
PLACEHOLDER_API_KEY = "change-me"

GENERATE_KEY_HINT = (
    "Generate one with `openssl rand -hex 32` (or "
    "`python3 -c 'import secrets; print(secrets.token_hex(32))'`) "
    "and set API_KEY to it."
)


class Settings(BaseSettings):
    # No usable default: `load_settings` refuses an unset or empty key.
    api_key: str = ""
    db_path: str = "./ipam.db"
    static_dir: str = "./static"
    # The server's public origin (v0.2 spec §6.1). Empty, the default, leaves the
    # built-in OAuth server off: `/mcp` takes the owner key alone, as before M3.
    public_base_url: str = ""
    # Whether a linked client may be given write access (v0.2 spec §6.5, #28): `true` or
    # `false`, read once here (`remote_writes`) and handed to the OAuth provider, from
    # which the token rule and the link window both take it. Unset is `false`.
    mcp_remote_writes: str = ""
    # The release this code is (`app/version.py`, #24): a class attribute, not a field,
    # so no environment variable can make a server report another release than it runs
    version: ClassVar[str] = VERSION

    @property
    def remote_writes(self) -> bool:
        """`MCP_REMOTE_WRITES` as `load_settings` has checked it: on only for `true`."""
        return self.mcp_remote_writes.lower() == "true"


def api_key_problem(value: str) -> str | None:
    """Why `value` cannot be the owner key, or None when it can.

    Each reason is a fixed string: none of them contains the key.
    """
    if not value.strip():
        return "API_KEY is not set, or is empty."
    if value.strip() == PLACEHOLDER_API_KEY:
        return f"API_KEY is the placeholder `{PLACEHOLDER_API_KEY}` from .env.example."
    # Refused, not stripped: the configured credential stays the one exact value.
    # Most HTTP clients cannot send a header value with whitespace at either end,
    # so a padded key would start a server its owner cannot sign in to.
    if value != value.strip():
        return "API_KEY starts or ends with whitespace (a space, tab or newline); remove it."
    return None


def db_path_problem(value: str) -> str | None:
    """Why `value` cannot be `DB_PATH`, or None when it can (#173, round 2 of #181's
    briefed review).

    `DB_PATH` is a filesystem path, or `:memory:` (the empty name is SQLite's in-memory
    database too), so that `database.storage_problem` checks the file the server opens.
    A `file:` name in any case is refused: with `uri=true` it is a URI naming any file,
    and without it a file whose name starts `file:`. So is anything the engine's URL
    would read otherwise than as that path, a query (`?mode=ro&uri=true`) or a `%`
    escape (`make_url` decodes it), since the path checked would not be the one opened.
    """
    if value.lower().startswith("file:"):
        return DB_PATH_REFUSED
    url = make_url(SQLITE_URL_PREFIX + value)
    if url.query or url.database != value:
        return DB_PATH_REFUSED
    return None


# The hosts plain `http` is taken for: the MCP SDK's own list (`validate_issuer_url`),
# which refuses any other issuer that is not `https`
_HTTP_HOSTS = ("localhost", "127.0.0.1", "[::1]")


def public_base_url_problem(value: str) -> str | None:
    """Why `value` cannot be `PUBLIC_BASE_URL`, or None when it can (spec §6.7, #26).

    The empty string is "unset": OAuth off. Any other value is the installation's
    identity: the issuer, the resource (`<value>/mcp`) and every endpoint URL a client is
    told come from it, and clients compare those as strings. So it is taken only in the
    one spelling the server publishes it in, and never trimmed or normalised here: an
    origin, `https://host[:port]`, lowercase, the host in its ASCII form, the port absent
    when it is the scheme's default. A path, a trailing slash, a query, a fragment,
    credentials or whitespace is refused, as is plain `http` to anything but this
    machine (the MCP SDK refuses to build routes for such an issuer, and a link over
    plain HTTP would send tokens in the clear). One fixed message, which repeats nothing
    of the value.
    """
    if value == "":
        return None
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return PUBLIC_BASE_URL_REFUSED
    host = parts.netloc.rpartition(":")[0] if port is not None else parts.netloc
    # The exact text an origin has: nothing before the scheme or after the authority,
    # which rules out a path (a lone `/` included), `?`, `#` and surrounding whitespace
    if value != f"{parts.scheme}://{parts.netloc}" or not host or "@" in parts.netloc:
        return PUBLIC_BASE_URL_REFUSED
    if port == 0:
        # no server listens on it: a typo, not an origin
        return PUBLIC_BASE_URL_REFUSED
    if parts.scheme not in ("https", "http") or (parts.scheme == "http" and host not in _HTTP_HOSTS):
        return PUBLIC_BASE_URL_REFUSED
    # What the URL type FastMCP and the MCP SDK publish it through makes of it: only a
    # value that comes back unchanged is the one clients will be told (an upper-case
    # host, `:443`, an internationalised name and `127.1` all come back in another
    # spelling)
    try:
        published = str(AnyHttpUrl(value))
    except ValidationError:
        return PUBLIC_BASE_URL_REFUSED
    if published != value + "/":
        return PUBLIC_BASE_URL_REFUSED
    return None


def mcp_remote_writes_problem(value: str) -> str | None:
    """Why `value` cannot be `MCP_REMOTE_WRITES`, or None when it can (#28).

    `true` turns remote writes on and `false` or unset leaves them off, in any case. Any
    other spelling stops the start rather than being read as either: a value meant as
    on (`yes`, `1`) is not silently off, and one meant as off is never on.
    """
    if value.isascii() and value.lower() in ("", "true", "false"):
        return None
    return MCP_REMOTE_WRITES_REFUSED


def load_settings() -> Settings:
    """Read the settings from the environment, refusing an unusable owner key, a
    `DB_PATH` that is not a filesystem path or `:memory:` (`db_path_problem`), and a
    `PUBLIC_BASE_URL` that is not the public origin in the spelling the server would
    publish (`public_base_url_problem`), and an `MCP_REMOTE_WRITES` that is not `true`
    or `false` (`mcp_remote_writes_problem`).

    Runs when this module is imported, so uvicorn (by hand or as the container's
    CMD) exits before it serves anything. Under `--reload` the reloader stays up and
    the server process it starts exits. An empty key would let an empty `X-API-Key`
    or bearer in, the placeholder is public, and a key padded with whitespace is
    one most clients cannot present.
    """
    loaded = Settings()
    problem = api_key_problem(loaded.api_key)
    if problem is not None:
        raise SystemExit(
            f"TLG-IPAM will not start: {problem} The owner key guards the REST API and /mcp. "
            f"{GENERATE_KEY_HINT}"
        )
    if db_path_problem(loaded.db_path) is not None:
        raise SystemExit(f"TLG-IPAM will not start: {DB_PATH_REFUSED}")
    if public_base_url_problem(loaded.public_base_url) is not None:
        raise SystemExit(f"TLG-IPAM will not start: {PUBLIC_BASE_URL_REFUSED}")
    if mcp_remote_writes_problem(loaded.mcp_remote_writes) is not None:
        raise SystemExit(f"TLG-IPAM will not start: {MCP_REMOTE_WRITES_REFUSED}")
    return loaded


settings = load_settings()
