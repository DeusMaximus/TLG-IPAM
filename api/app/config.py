from pydantic_settings import BaseSettings

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
    version: str = "0.1.0"


def api_key_problem(value: str) -> str | None:
    """Why `value` cannot be the owner key, or None when it can."""
    if not value.strip():
        return "API_KEY is not set, or is empty."
    if value.strip() == PLACEHOLDER_API_KEY:
        return f"API_KEY is the placeholder `{PLACEHOLDER_API_KEY}` from .env.example."
    return None


def load_settings() -> Settings:
    """Read the settings from the environment, refusing an unusable owner key.

    Runs when this module is imported, so uvicorn (by hand or as the container's
    CMD) exits before it serves anything. Under `--reload` the reloader stays up and
    the server process it starts exits. An empty key would let an empty `X-API-Key`
    or bearer in, and the placeholder is public.
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
