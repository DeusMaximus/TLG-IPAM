"""`tlg-ipam-agent`: run the agent with its configuration from the environment."""

import argparse
import asyncio
import logging
import os
import signal
import sys

from . import __version__, config, interfaces
from .client import AgentClient, make_http
from .loop import Agent, Redact, log
from .probe import DnsResolver, IcmpProber, PacketLink


async def _run(cfg: config.Config) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    async with make_http(cfg) as http:
        agent = Agent(
            cfg,
            AgentClient(http),
            prober=IcmpProber(),
            resolver=DnsResolver(),
            arp_link=PacketLink,
            interfaces=interfaces.discover,
        )
        await agent.run(stop)


def configure_logging(token: str | None) -> None:
    """Log to stderr; once the token is known, every handler redacts it, so the agent's
    own records and every library's are covered."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if token:
        for handler in logging.getLogger().handlers:
            if not any(isinstance(f, Redact) for f in handler.filters):
                handler.addFilter(Redact(token))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tlg-ipam-agent",
        description="TLG-IPAM scan agent. Configured from the environment: IPAM_URL, "
        "IPAM_AGENT_TOKEN, ALLOWED_CIDRS, MAX_PPS.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.parse_args(argv)
    configure_logging(None)
    if sys.platform != "linux":
        log.error("the agent runs on Linux only (spec §5.6)")
        return 2
    try:
        cfg = config.load(os.environ)
    except config.ConfigError as exc:
        log.error("%s", exc)
        return 2
    configure_logging(cfg.token)
    log.info(
        "tlg-ipam-agent %s: server %s, ALLOWED_CIDRS %s, MAX_PPS %d",
        __version__,
        cfg.url,
        ",".join(str(n) for n in cfg.allowed),
        cfg.max_pps,
    )
    asyncio.run(_run(cfg))
    return 0
