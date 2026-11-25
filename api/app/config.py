from typing import ClassVar

from pydantic_settings import BaseSettings
from sqlalchemy.engine import make_url

from .version import VERSION

# How `app.database` names the database to SQLAlchemy: this, then `DB_PATH` as it is
SQLITE_URL_PREFIX = "sqlite+aiosqlite:///"

# The one refusal of DB_PATH; fixed, so it repeats no value
DB_PATH_REFUSED = "DB_PATH must be a filesystem path or :memory:."

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
    # The release this code is (`app/version.py`, #24): a class attribute, not a field,
    # so no environment variable can make a server report another release than it runs
    version: ClassVar[str] = VERSION


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


def load_settings() -> Settings:
    """Read the settings from the environment, refusing an unusable owner key, and a
    `DB_PATH` that is not a filesystem path or `:memory:` (`db_path_problem`).

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
    return loaded


settings = load_settings()
