"""`tlg-ipam-agent`: run the agent with its configuration from the environment, or
install it as a systemd unit (`install`, `uninstall`; spec §5.6)."""

import argparse
import asyncio
import logging
import os
import signal
import sys

from . import __version__, config, enrol, install, interfaces
from .client import AgentClient, make_http
from .loop import Agent, Redact, log
from .probe import DnsResolver, IcmpProber, PacketLink


async def _run(settings: config.Settings) -> int:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        token = await enrol.agent_token(settings, stop=stop)
    except config.ConfigError as exc:
        log.error("%s", exc)
        return 2
    if token is None:  # stopped while waiting to enrol
        return 0
    configure_logging(token)
    cfg = settings.config(token)
    async with make_http(cfg.url, cfg.token) as http:
        agent = Agent(
            cfg,
            AgentClient(http),
            prober=IcmpProber(),
            resolver=DnsResolver(),
            arp_link=PacketLink,
            interfaces=interfaces.discover,
        )
        await agent.run(stop)
    return 0


def configure_logging(*tokens: str | None) -> None:
    """Log to stderr; every handler redacts each token the agent holds, so the agent's
    own records and every library's are covered. Called again as a token becomes
    known, it adds it to the same filter."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    known = [t for t in tokens if t]
    if not known:
        return
    for handler in logging.getLogger().handlers:
        redact = next((f for f in handler.filters if isinstance(f, Redact)), None)
        if redact is None:
            handler.addFilter(Redact(*known))
        else:
            redact.add(*known)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tlg-ipam-agent",
        description="TLG-IPAM scan agent. With no command it runs, configured from the "
        "environment: IPAM_URL, IPAM_ENROL_TOKEN (first start) or IPAM_AGENT_TOKEN, "
        "ALLOWED_CIDRS, MAX_PPS.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", metavar="{install,uninstall}")
    setup = commands.add_parser(
        "install",
        help="install the agent as a systemd unit and enrol it (as root)",
        description="Creates the service user, enrols the agent, writes "
        f"{install.ETC_DIR}/{install.ENV_NAME} and the agent token, and installs, "
        "enables and starts the unit. Run from the virtual environment the agent was "
        "installed into, as root.",
    )
    setup.add_argument("--url", required=True, help="the server's URL (https)")
    setup.add_argument(
        "--enrol-token",
        metavar="TOKEN",
        help="the tlge_ token the Agents page shows, or - to read it from standard input; "
        "leave it out to keep the agent token already stored (an update)",
    )
    setup.add_argument("--allowed-cidrs", metavar="CIDRS", help="ALLOWED_CIDRS for the agent")
    setup.add_argument("--max-pps", metavar="N", help="MAX_PPS for the agent")
    setup.add_argument(
        "--ca-file",
        metavar="PATH",
        help="a CA bundle for a server certificate from a private CA (SSL_CERT_FILE)",
    )
    commands.add_parser(
        "uninstall",
        help="stop the unit and remove everything install created (as root)",
        description="Stops and disables the unit and removes it, the settings, the agent "
        "token and the service user. The virtual environment is left in place.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "install":
        options = install.Options(
            url=args.url,
            enrol_token=args.enrol_token,
            allowed_cidrs=args.allowed_cidrs,
            max_pps=args.max_pps,
            ca_file=args.ca_file,
        )
        configure_logging(None if options.enrol_token == "-" else options.enrol_token)
        return install.install(options)
    if args.command == "uninstall":
        return install.uninstall()
    configure_logging(None)
    if sys.platform != "linux":
        log.error("the agent runs on Linux only (spec §5.6)")
        return 2
    try:
        settings = config.load(os.environ)
    except config.ConfigError as exc:
        log.error("%s", exc)
        return 2
    configure_logging(settings.agent_token, settings.enrol_token)
    log.info(
        "tlg-ipam-agent %s: server %s, ALLOWED_CIDRS %s, MAX_PPS %d",
        __version__,
        settings.url,
        ",".join(str(n) for n in settings.allowed),
        settings.max_pps,
    )
    return asyncio.run(_run(settings))
