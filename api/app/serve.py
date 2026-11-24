"""How the image starts the server: `python -m app.serve` (the Dockerfile's `CMD`).

uvicorn, one worker, on the app, with the limits on a request's head and on a
connection waiting for one (`header_limit.HeaderLimitProtocol`, #132), which uvicorn's
command line cannot set: its `--http` takes only the names of its own parsers. Every
other setting is uvicorn's default, as the command line started it before.

- **One worker** (brief §2.2), passed here, so `WEB_CONCURRENCY` in the environment
  cannot raise it; uvicorn reads `UVICORN_*` variables only on its command line.
- **No WebSocket.** The app serves none, so uvicorn's WebSocket support is off and no
  request leaves the HTTP protocol whose limits these are: an `Upgrade` request is
  served as an ordinary HTTP request.
- **Keep-alive** (`KEEP_ALIVE_S`): a connection that sends nothing after a response is
  closed after uvicorn's default 5 s, stated here because the limits rest on it.

The development command (`uvicorn app.main:app --reload`, `AGENTS.md`) runs without
these limits.
"""

import argparse

import uvicorn

from .header_limit import HeaderLimitProtocol

# Seconds a connection may stay idle after a response before uvicorn closes it
# (uvicorn's default, `--timeout-keep-alive`).
KEEP_ALIVE_S = 5


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.serve", description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        workers=1,
        http=HeaderLimitProtocol,
        ws="none",
        timeout_keep_alive=KEEP_ALIVE_S,
    )


if __name__ == "__main__":
    main()
