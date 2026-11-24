from typing import ClassVar

from pydantic_settings import BaseSettings

from .version import VERSION

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


def load_settings() -> Settings:
    """Read the settings from the environment, refusing an unusable owner key.

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
    return loaded


settings = load_settings()
