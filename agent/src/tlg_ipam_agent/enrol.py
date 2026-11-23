"""Where the agent's token comes from, and the enrolment exchange (spec §5.6, §5.7; #22).

In this order:

1. `IPAM_AGENT_TOKEN`, when it is set: a token the operator holds (the M2a install).
   Nothing is read from the state directory and nothing is stored.
2. The token stored in the state directory (`/var/lib/tlg-ipam-agent/token`) by an
   earlier exchange. A stored token always wins over `IPAM_ENROL_TOKEN`, which is then
   ignored: that is what lets a container be recreated, with the same volume and the
   same, now spent, enrolment token in its environment, and start without enrolling.
3. An exchange of `IPAM_ENROL_TOKEN` (`POST /api/v1/agent/enrol`), whose agent token
   is stored before the agent uses it.

A stored file that is not a token stops the agent: it is never enrolled over.

The exchange (`enrol_and_store`, the container's first start) never ends in a tight
loop. The enrol route has one throttle for every caller (10 attempts that did not
enrol per minute), so an agent that repeats a refused exchange would hold every other
enrolment at `429`. A refused exchange (`401`, `403`, any other 4xx) waits the
refusal rule's 13.5-15 minutes before it tries again, as a refused check-in does
(§5.7): a spent token stays spent, but a disabled agent can be enabled. A `429`, a
5xx or no answer waits the bounded back-off. The process keeps running throughout, so
a container's restart policy does not turn a refusal into a restart loop.

`tlg-ipam-agent install` makes its exchange once, itself (`install.py`): the
enrolment token there comes from the operator and never reaches the disk.
"""

import asyncio
import logging
import os
import random
from collections.abc import Callable
from typing import Any

from . import store
from .backoff import Backoff, refused_wait
from .client import AgentClient, Answer, Kind, make_http
from .config import ConfigError, Settings, parse_token
from .loop import reported
from .scan import Clock

log = logging.getLogger("tlg_ipam_agent")

# the container's volume and the systemd install's state directory: the same path
STATE_DIR = "/var/lib/tlg-ipam-agent"
TOKEN_NAME = "token"
# the token file's mode: its owner reads it, nobody else can
TOKEN_MODE = 0o600


def token_path(state_dir: str) -> str:
    return os.path.join(state_dir, TOKEN_NAME)


def stored_token(state_dir: str) -> str | None:
    """The agent token stored in `state_dir`, or None when there is no file. A file
    that is not an agent token, or that cannot be read, raises ConfigError naming the
    path, never the content. One newline at the end is allowed (a file written by
    hand), nothing else around the token."""
    path = token_path(state_dir)
    try:
        data = store.read_small(path)
    except store.StoreError as exc:
        raise ConfigError(str(exc)) from None
    if data is None:
        return None
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError:
        raise ConfigError(f"{path} contains characters a token never has") from None
    if text.endswith("\n"):
        text = text[:-1]
    return parse_token(text, path)


async def exchange(http: Any) -> Answer:
    """One enrolment exchange; `http` (an httpx client, `client.make_http`) presents
    the enrolment token."""
    return await AgentClient(http).enrol(reported())


def _store(state_dir: str, token: str, pending: store.PendingFile) -> None:
    try:
        pending.commit(token.encode("ascii"))
    except store.StoreError as exc:
        # the enrolment token is spent and this token is in no file: the agent cannot
        # keep it across a restart, so it does not start on it either
        raise ConfigError(
            f"the agent token was issued but not stored ({exc}); re-enrol the agent on the "
            "Agents page and start again with the new enrolment token"
        ) from None


async def enrol_and_store(
    settings: Settings,
    state_dir: str,
    *,
    stop: asyncio.Event,
    http: Callable[[str, str], Any] = make_http,
    clock: Clock | None = None,
    rng: random.Random | None = None,
) -> str | None:
    """Exchanges `settings.enrol_token` until the server accepts it, stores the agent
    token in `state_dir`, and returns it; None if `stop` is set first.

    The token file is created, empty and with its mode, before each request, so a
    state directory that cannot take it (none mounted, read-only, not the agent's own)
    stops the agent before the enrolment token is spent (ConfigError)."""
    assert settings.enrol_token
    clock = clock or Clock()
    rng = rng or random.SystemRandom()
    backoff = Backoff(rng)
    try:
        store.check_dir(state_dir, os.geteuid())
    except store.StoreError as exc:
        raise ConfigError(
            f"the agent token cannot be stored: {exc}. Mount a volume at {STATE_DIR} "
            "(-v tlg-ipam-agent:/var/lib/tlg-ipam-agent)"
        ) from None
    if not os.path.ismount(state_dir):
        # a container run without the volume: the token would go into its own layer
        log.warning(
            "%s is not a mount point: in a container, the agent token stored there is lost "
            "when the container is recreated, and the agent must then be re-enrolled. Mount a "
            "volume there (-v tlg-ipam-agent:/var/lib/tlg-ipam-agent)",
            state_dir,
        )
    while not stop.is_set():
        try:
            pending = store.PendingFile(token_path(state_dir), TOKEN_MODE)
        except store.StoreError as exc:
            raise ConfigError(f"the agent token cannot be stored: {exc}") from None
        with pending:
            async with http(settings.url, settings.enrol_token) as client:
                answer = await exchange(client)
            if answer.kind is Kind.OK:
                token = answer.data["agent_token"]
                _store(state_dir, token, pending)
                log.info(
                    "enrolled as agent %s; the agent token is stored in %s",
                    answer.data["agent_id"],
                    token_path(state_dir),
                )
                return token
        if answer.kind is Kind.RETRY:
            wait = backoff.next()
            log.warning("enrolment not accepted (%s); retrying in %.0f s", answer.message, wait)
        else:
            backoff.reset()
            wait = refused_wait(rng)
            log.error(
                "enrolment refused (%s): the enrolment token is used, expired or revoked, "
                "or the agent is disabled. Re-enrol the agent on the Agents page and start "
                "the agent with the new token; trying again in %.0f s",
                answer.message,
                wait,
            )
        await _pause(clock, wait, stop)
    return None


async def agent_token(
    settings: Settings,
    state_dir: str = STATE_DIR,
    *,
    stop: asyncio.Event,
    http: Callable[[str, str], Any] = make_http,
    clock: Clock | None = None,
    rng: random.Random | None = None,
) -> str | None:
    """The agent token to run with, by the order in this module's docstring; None if
    `stop` is set while the agent waits to enrol. ConfigError when there is none."""
    if settings.agent_token:
        return settings.agent_token
    stored = stored_token(state_dir)
    if stored is not None:
        if settings.enrol_token:
            log.warning(
                "IPAM_ENROL_TOKEN is ignored: the agent token stored in %s is used. To "
                "enrol this agent again, remove that file (or the container's volume) first",
                state_dir,
            )
        return stored
    if not settings.enrol_token:
        raise ConfigError(
            "no agent token: set IPAM_ENROL_TOKEN to the tlge_ token the Agents page shows, "
            f"with a volume at {STATE_DIR} to keep the agent token it is exchanged for "
            "(or set IPAM_AGENT_TOKEN to an agent token you hold)"
        )
    return await enrol_and_store(settings, state_dir, stop=stop, http=http, clock=clock, rng=rng)


async def _pause(clock: Clock, seconds: float, stop: asyncio.Event) -> None:
    sleeping = asyncio.ensure_future(clock.sleep(seconds))
    stopping = asyncio.ensure_future(stop.wait())
    try:
        await asyncio.wait({sleeping, stopping}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        sleeping.cancel()
        stopping.cancel()
