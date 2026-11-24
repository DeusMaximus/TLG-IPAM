"""How the image starts the server: `python -m app.serve` (the Dockerfile's `CMD`).

uvicorn's own command line, on `app.main:app` at `0.0.0.0:8080`, so every setting it
takes from its options or from `UVICORN_*` variables (`UVICORN_FORWARDED_ALLOW_IPS`,
`UVICORN_PROXY_HEADERS`, `UVICORN_ROOT_PATH`, `UVICORN_LOG_LEVEL` and the rest) applies
as it did when the `CMD` was `uvicorn` itself, except the ones in `PINNED`, which the
limits rest on and which nothing in the environment changes:

- **One worker** (brief §2.2), and no reloader, which would add a process.
- **The parser:** `header_limit.HeaderLimitProtocol` (#132), which uvicorn's `--http`
  cannot name (it takes only the names of its own parsers), and no HTTP/2.
- **No WebSocket.** The app serves none, so no request leaves the HTTP protocol whose
  limits these are: an `Upgrade` request is served as an ordinary HTTP request.
- **Keep-alive** (`KEEP_ALIVE_S`): a connection that sends nothing after a response is
  closed after uvicorn's default 5 s, stated here because the limits rest on it.

`--host` and `--port` given after `python -m app.serve` replace the defaults, as the
tests do to listen on loopback. The development command (`uvicorn app.main:app
--reload`, `AGENTS.md`) runs without these limits.
"""

import importlib
import sys

from .header_limit import HeaderLimitProtocol

# uvicorn's command-line module: `uvicorn.main` on the package is its command function,
# which shadows the module of the same name
cli = importlib.import_module("uvicorn.main")

# Seconds a connection may stay idle after a response before uvicorn closes it
# (uvicorn's default, `--timeout-keep-alive`).
KEEP_ALIVE_S = 5

PINNED = {
    "workers": 1,
    "reload": False,
    "http": HeaderLimitProtocol,
    "http2": False,
    "ws": "none",
    "timeout_keep_alive": KEEP_ALIVE_S,
}


def main(argv: list[str] | None = None) -> None:
    """Run uvicorn's command line with `PINNED` laid over whatever it parsed. It exits
    the process when the server stops, as `uvicorn` does."""
    args = ["app.main:app", "--host", "0.0.0.0", "--port", "8080", *(sys.argv[1:] if argv is None else argv)]
    run = cli.run

    def pinned_run(app, **settings) -> None:
        run(app, **{**settings, **PINNED})

    cli.run = pinned_run  # the name uvicorn's command calls once it has parsed
    try:
        cli.main.main(args=args, prog_name="python -m app.serve")
    finally:
        cli.run = run


if __name__ == "__main__":
    main()
