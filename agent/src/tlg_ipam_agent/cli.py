"""`tlg-ipam-agent`: run the agent with its configuration from the environment, or
install it as a systemd unit (`install`, `uninstall`; spec §5.6).

This module imports only `argparse` and `sys` at its top, and nothing else of the
agent's but the package itself. `install` and `uninstall` run as root, so before they
import `install` (and through it the agent's other modules and their dependencies),
`trust.check` must find that only root can change the code Python could load (#22,
the review of #130). Running the agent imports the rest only once it has been asked to
run.
"""

import argparse
import sys

from . import __version__

# the install's own paths, written out here so that the parser is built without
# importing `install` (its tests hold them to install.ETC_DIR and install.ENV_NAME)
ETC_ENV = "/etc/tlg-ipam-agent/agent.env"


async def _run(settings) -> int:
    import asyncio
    import signal

    from . import enrol, interfaces
    from .client import AgentClient, make_http
    from .config import ConfigError
    from .loop import Agent, log
    from .probe import DnsResolver, IcmpProber, PacketLink

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        token = await enrol.agent_token(settings, stop=stop)
    except ConfigError as exc:
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
    import logging

    from .loop import Redact

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
        f"{ETC_ENV} and the agent token, and installs, enables and starts the unit. Run "
        "from the virtual environment the agent was installed into, as root.",
    )
    setup.add_argument("--url", required=True, help="the server's URL (https)")
    setup.add_argument(
        "--enrol-token",
        metavar="-",
        help="- to read the tlge_ token the Agents page shows from standard input (asked "
        "for, not echoed, on a terminal); never the token itself, which any local user "
        "could read from the command line. Leave it out to keep the agent token already "
        "stored (an update)",
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
        "token and the service user if install created it. The virtual environment is "
        "left in place.",
    )
    return parser


def _root_only(command: str) -> bool:
    """`trust.check`, before anything else of the agent's is imported."""
    from . import trust

    # no later import may come from the working directory, which `python -m` puts on
    # the path and which may be anyone's (or `/`, which the check would walk whole)
    sys.path[:] = trust.without_working_directory(sys.path)
    try:
        trust.check()
    except trust.Untrusted as exc:
        print(
            f"{command} refused: {exc} can be changed by a user other than root, and "
            "this command runs the code Python loads from there as root (the unit, with "
            "CAP_NET_RAW). Install the agent into a virtual environment only root can "
            "write, such as /opt/tlg-ipam-agent, and run it from there",
            file=sys.stderr,
            flush=True,
        )
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command in ("install", "uninstall"):
        if not _root_only(args.command):
            return 1
        from . import install

        if args.command == "uninstall":
            return install.uninstall()
        configure_logging(None)
        return install.install(
            install.Options(
                url=args.url,
                enrol_token=args.enrol_token,
                allowed_cidrs=args.allowed_cidrs,
                max_pps=args.max_pps,
                ca_file=args.ca_file,
            )
        )
    return _main_run()


def _main_run() -> int:
    import asyncio
    import os

    from . import config
    from .loop import log

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
