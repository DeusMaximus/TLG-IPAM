"""`tlg-ipam-agent install` and `tlg-ipam-agent uninstall`: the systemd install (spec §5.6, #22).

`install`, run as root from the virtual environment the agent was installed into:

1. creates the service user `tlg-ipam-agent` (a system user with no home and no login
   shell, and a group of its own), unless it is there;
2. creates `/etc/tlg-ipam-agent` and `/var/lib/tlg-ipam-agent`, root's, mode 0755;
3. writes `/etc/tlg-ipam-agent/agent.env` (the server's URL and the settings given; no
   secret) and the unit `/etc/systemd/system/tlg-ipam-agent.service`, both mode 0644;
4. exchanges the enrolment token itself, and writes the agent token to
   `/var/lib/tlg-ipam-agent/token`, owned by the service user, mode 0600;
5. reloads systemd, enables the unit and (re)starts it.

The enrolment token comes from the operator and goes nowhere but the request: it is in
no file this writes, and the unit's agent never sees it. `--enrol-token -` reads it
from standard input (with a prompt, and without echoing it, on a terminal), which is
the form to use under `sudo`: sudo logs the whole command line, and on a host with a
persistent journal that puts a token given as `--enrol-token tlge_…` on disk (#22
measured it on Debian 13; journald also keeps sudo's command line beside the entry). The token file exists, empty, with its
owner and mode, before the exchange, so nothing that could fail locally is left until
the token is spent; if the install fails before the exchange, or the exchange is
refused, what this run created is taken away again. Once the token is stored nothing
is taken back.

Run again without `--enrol-token`, `install` keeps the stored token and rewrites the
settings and the unit (an update: install the new release into the same environment,
then run it). With a token already stored, `--enrol-token` is refused: `uninstall`
first, then enrol again.

`uninstall` stops and disables the unit and removes everything `install` created: the
unit, both directories and the files in them, and the service user and its group. It
leaves the virtual environment, which the operator made, and the agent's record on the
server, which only the owner can remove.

The programs this runs are fixed (`useradd`, `userdel`, `groupdel`, `systemctl`), with
fixed arguments, a fixed PATH and no shell; nothing the server sends reaches them.
This module and `store.py` are the only modules allowed to start a process or write a
file (`tests/test_invariants.py`, `WRITES_WHERE`).
"""

import asyncio
import grp
import os
import pwd
import re
import stat
import subprocess
import sys
import termios
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from . import config, enrol, store
from .client import Answer, Kind, make_http

SERVICE_USER = "tlg-ipam-agent"
UNIT_NAME = "tlg-ipam-agent.service"
ETC_DIR = "/etc/tlg-ipam-agent"
UNIT_PATH = "/etc/systemd/system/" + UNIT_NAME
ENV_NAME = "agent.env"
DIR_MODE = 0o755
FILE_MODE = 0o644
# the programs `install` runs see this environment and nothing else of the caller's
COMMAND_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}
USERADD = (
    "useradd", "--system", "--user-group", "--no-create-home", "--home-dir", "/nonexistent",
    "--shell", "/usr/sbin/nologin", "--comment", "TLG-IPAM scan agent", SERVICE_USER,
)
# what a value written into agent.env or the unit may hold: nothing systemd reads
# specially in either (whitespace, quotes, backslashes, `%` specifiers, `$`, `;`, `#`)
_PLAIN = re.compile(r"[A-Za-z0-9_./:,+=@\[\]-]+")


class InstallError(Exception):
    """Why an install or uninstall stopped. Never carries a token."""


@dataclass(frozen=True)
class Paths:
    """Where the install puts things; a test points them into a directory of its own."""

    etc: str = ETC_DIR
    state: str = enrol.STATE_DIR
    unit: str = UNIT_PATH

    @property
    def env(self) -> str:
        return os.path.join(self.etc, ENV_NAME)

    @property
    def token(self) -> str:
        return enrol.token_path(self.state)


@dataclass(frozen=True)
class Options:
    url: str | None
    # as given: a token, "-" for standard input, or None
    enrol_token: str | None = None
    allowed_cidrs: str | None = None
    max_pps: str | None = None
    ca_file: str | None = None


class Host:
    """What the install does outside its files: the service user and systemd, through
    fixed programs. A test passes a fake that records the calls."""

    def euid(self) -> int:
        return os.geteuid()

    def has_systemd(self) -> bool:
        # sd_booted(3): systemd is the running init (never so off Linux)
        return sys.platform == "linux" and Path("/run/systemd/system").is_dir()

    def service_user(self) -> tuple[int, int] | None:
        try:
            entry = pwd.getpwnam(SERVICE_USER)
        except KeyError:
            return None
        return entry.pw_uid, entry.pw_gid

    def service_group(self) -> bool:
        try:
            grp.getgrnam(SERVICE_USER)
        except KeyError:
            return False
        return True

    def run(self, *argv: str) -> int:
        print("+ " + " ".join(argv), flush=True)
        return subprocess.run(list(argv), env=COMMAND_ENV, stdin=subprocess.DEVNULL, check=False).returncode

    def code_not_root_only(self, interpreter: str) -> str | None:
        return not_root_only(interpreter)


def _changeable(path: Path, owner: int) -> bool:
    info = os.stat(path)
    return info.st_uid != owner or bool(info.st_mode & (stat.S_IWGRP | stat.S_IWOTH))


def not_root_only(
    interpreter: str,
    *,
    prefix: str = sys.prefix,
    package: str = str(Path(__file__).resolve().parent),
    owner: int = 0,
    top: str = "/",
) -> str | None:
    """The first path a user other than `owner` (root) owns or could write to, among
    what the unit runs with CAP_NET_RAW, or None: the interpreter it names (followed
    to its file) and the agent's modules, and every directory from each of these up
    to `top`: the interpreter's own directory as the unit names it (a venv's `bin`,
    which a link to the system Python would otherwise skip) and as resolved, the
    virtual environment, and the package. Whoever can change one of them can run
    their own code with the capability. `owner` and `top` are for a test."""
    package_dir = Path(package)
    files = [Path(interpreter).resolve(), *sorted(package_dir.glob("*.py"))]
    for path in files:
        if _changeable(path, owner):
            return str(path)
    starts = (Path(interpreter).parent, Path(interpreter).resolve().parent, Path(prefix), package_dir)
    stop = Path(top)
    for start in starts:
        for directory in (start, *start.parents):
            if _changeable(directory, owner):
                return str(directory)
            if directory == stop:
                break
    return None


def read_token_line() -> str:
    """One line of standard input. On a terminal it prompts on stderr and turns echo
    off while the line is read, so the token is not left on the screen."""
    if not sys.stdin.isatty():
        return sys.stdin.readline()
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    quiet = termios.tcgetattr(fd)
    quiet[3] &= ~termios.ECHO
    sys.stderr.write("Enrolment token (tlge_…, not shown): ")
    sys.stderr.flush()
    try:
        termios.tcsetattr(fd, termios.TCSAFLUSH, quiet)
        return sys.stdin.readline()
    finally:
        termios.tcsetattr(fd, termios.TCSAFLUSH, saved)
        sys.stderr.write("\n")


def _say(line: str) -> None:
    print(line, flush=True)


def _check(host: Host, argv: tuple[str, ...], what: str) -> None:
    code = host.run(*argv)
    if code != 0:
        raise InstallError(f"{what} failed: `{' '.join(argv)}` exited with {code}")


def _plain(value: str, name: str) -> str:
    if not _PLAIN.fullmatch(value):
        raise InstallError(f"{name} has a character the unit's environment file cannot hold as it is")
    return value


def _ca_file(value: str | None) -> str | None:
    if value is None:
        return None
    _plain(value, "--ca-file")
    if not os.path.isabs(value):
        raise InstallError("--ca-file must be an absolute path")
    try:
        info = os.stat(value)
    except OSError:
        raise InstallError(f"--ca-file: {value} cannot be found") from None
    if not stat.S_ISREG(info.st_mode):
        raise InstallError(f"--ca-file: {value} is not a regular file")
    return value


def render_env(url: str, allowed, max_pps: int | None, ca_file: str | None, token_path: str) -> str:
    lines = [
        "# Written by `tlg-ipam-agent install`; `tlg-ipam-agent uninstall` removes it.",
        f"# No secrets: the agent token is in {token_path}, which only the service user can read.",
        f"IPAM_URL={_plain(url, '--url')}",
    ]
    if allowed is not None:
        lines.append("ALLOWED_CIDRS=" + _plain(",".join(str(n) for n in allowed), "--allowed-cidrs"))
    if max_pps is not None:
        lines.append(f"MAX_PPS={max_pps}")
    if ca_file is not None:
        lines.append(f"SSL_CERT_FILE={ca_file}")
    return "\n".join(lines) + "\n"


def render_unit(env_path: str, interpreter: str) -> str:
    """The unit. It runs the agent as the service user with CAP_NET_RAW as its one
    capability, for the raw ICMP and ARP sockets. Namespace-based sandboxing
    (`ProtectSystem=` and the like) is left out: the unit is meant for an LXC as well,
    where those can stop it from starting; add them in a drop-in where they work."""
    _plain(interpreter, "the agent's interpreter path")
    return f"""# Written by `tlg-ipam-agent install`; `tlg-ipam-agent uninstall` removes it.
[Unit]
Description=TLG-IPAM scan agent
Wants=network-online.target
After=network-online.target

[Service]
Type=exec
User={SERVICE_USER}
Group={SERVICE_USER}
EnvironmentFile={env_path}
ExecStart={interpreter} -m tlg_ipam_agent
AmbientCapabilities=CAP_NET_RAW
CapabilityBoundingSet=CAP_NET_RAW
NoNewPrivileges=yes
Restart=on-failure
RestartSec=30s

[Install]
WantedBy=multi-user.target
"""


def exchange(url: str, token: str, ca_file: str | None) -> Answer:
    """The enrolment exchange, once. A private CA is the one the agent will use."""
    if ca_file is not None:
        os.environ["SSL_CERT_FILE"] = ca_file

    async def once() -> Answer:
        async with make_http(url, token) as http:
            return await enrol.exchange(http)

    return asyncio.run(once())


def _not_enrolled(answer: Answer) -> str:
    if answer.kind is Kind.REFUSED:
        return (
            f"the server refused the enrolment token ({answer.message}): it is used, expired "
            "or revoked, or the agent is disabled. Re-enrol the agent on the Agents page for "
            "a new token"
        )
    if answer.status == 429 or (answer.status or 0) >= 500:
        return f"the server did not enrol the agent ({answer.message}); the token is not spent, so run install again"
    return (
        f"no answer the agent could use ({answer.message}). If the server enrolled the agent "
        "the token is spent, and the agent must be re-enrolled; otherwise run install again"
    )


def _preflight(host: Host) -> None:
    if host.euid() != 0:
        raise InstallError("run it as root (sudo)")
    if not host.has_systemd():
        raise InstallError("systemd is not running on this host (the agent runs on Linux)")


def _rollback(undo: list[Callable[[], object]], out: Callable[[str], None]) -> None:
    for step in reversed(undo):
        try:
            step()
        except (InstallError, store.StoreError) as exc:
            out(f"could not take back part of the install: {exc}")


def _delete_user(host: Host) -> None:
    _check(host, ("userdel", SERVICE_USER), "removing the service user")
    if host.service_group():
        _check(host, ("groupdel", SERVICE_USER), "removing the service user's group")


def install(
    options: Options,
    *,
    host: Host | None = None,
    paths: Paths = Paths(),
    exchange: Callable[[str, str, str | None], Answer] = exchange,
    read_stdin: Callable[[], str] = read_token_line,
    interpreter: str = sys.executable,
    out: Callable[[str], None] = _say,
) -> int:
    host = host or Host()
    try:
        _install(options, host, paths, exchange, read_stdin, interpreter, out)
    except (InstallError, config.ConfigError, store.StoreError) as exc:
        out(f"install failed: {exc}")
        return 1
    return 0


def _install(options, host, paths, exchange, read_stdin, interpreter, out) -> None:
    _preflight(host)
    unsafe = host.code_not_root_only(interpreter)
    if unsafe is not None:
        raise InstallError(
            f"{unsafe} can be changed by a user other than root, and the unit would run the "
            "code under it with CAP_NET_RAW. Install the agent into a virtual environment "
            "only root can write, such as /opt/tlg-ipam-agent"
        )
    url = config.parse_url(options.url, "--url")
    allowed = None if options.allowed_cidrs is None else config.parse_allowed(options.allowed_cidrs, "--allowed-cidrs")
    max_pps = None if options.max_pps is None else config.parse_max_pps(options.max_pps, "--max-pps")
    ca_file = _ca_file(options.ca_file)
    token = options.enrol_token
    if token == "-":
        token = read_stdin().rstrip("\n")
        if not token:
            raise InstallError("--enrol-token -: nothing was read from standard input")
    if token is not None:
        token = config.parse_token(token, "--enrol-token", config.ENROL_TOKEN_PREFIX)
    env = render_env(url, allowed, max_pps, ca_file, paths.token)
    unit = render_unit(paths.env, interpreter)

    stored = enrol.stored_token(paths.state)
    if stored is not None and token is not None:
        raise InstallError(
            f"an agent token is already stored in {paths.token}, so this host is enrolled. "
            "Run install without --enrol-token to keep it, or `tlg-ipam-agent uninstall` "
            "first to enrol again"
        )
    if stored is None and token is None:
        raise InstallError(
            "--enrol-token is needed: the tlge_ token the Agents page shows for this agent "
            "(--enrol-token - reads it from standard input)"
        )

    undo: list[Callable[[], object]] = []
    try:
        user = host.service_user()
        if user is None:
            _check(host, USERADD, "creating the service user")
            undo.append(partial(_delete_user, host))
            user = host.service_user()
            if user is None:
                raise InstallError(f"useradd ran but there is no user {SERVICE_USER}")
        for directory in (paths.etc, paths.state):
            if store.make_dir(directory, DIR_MODE):
                undo.append(partial(store.remove_dir, directory))
        for path, content in ((paths.env, env), (paths.unit, unit)):
            existed = os.path.lexists(path)
            store.write_file(path, content.encode("ascii"), FILE_MODE)
            if not existed:
                undo.append(partial(store.remove_file, path))
        if token is None:
            # an update: the token stays, written again so that its owner is the
            # service user and its mode 0600 whatever wrote it before (an agent run as
            # root, a service user since recreated with another uid)
            store.write_file(paths.token, stored.encode("ascii"), enrol.TOKEN_MODE, owner=user)
        else:
            with store.PendingFile(paths.token, enrol.TOKEN_MODE, owner=user) as pending:
                answer = exchange(url, token, ca_file)
                if answer.kind is not Kind.OK:
                    raise InstallError(_not_enrolled(answer))
                try:
                    pending.commit(answer.data["agent_token"].encode("ascii"))
                except store.StoreError as exc:
                    undo.clear()  # the token is spent: leave the rest for the operator
                    raise InstallError(
                        f"the agent token was issued but not stored ({exc}); fix that, run "
                        "uninstall, re-enrol the agent and install again"
                    ) from None
            undo.clear()
            out(f"enrolled as agent {answer.data['agent_id']}; its token is in {paths.token}, readable by {SERVICE_USER} alone")
    except BaseException:
        _rollback(undo, out)
        raise

    _check(host, ("systemctl", "daemon-reload"), "reloading systemd")
    _check(host, ("systemctl", "enable", UNIT_NAME), "enabling the unit")
    _check(host, ("systemctl", "restart", UNIT_NAME), "starting the unit")
    out(f"installed: `systemctl status {UNIT_NAME}` and `journalctl -u {UNIT_NAME}` show the agent")


def uninstall(*, host: Host | None = None, paths: Paths = Paths(), out: Callable[[str], None] = _say) -> int:
    host = host or Host()
    problems: list[str] = []
    if host.euid() != 0:
        out("uninstall failed: run it as root (sudo)")
        return 1

    def attempt(step: Callable[[], object]) -> None:
        try:
            step()
        except (InstallError, store.StoreError) as exc:
            problems.append(str(exc))

    systemd = host.has_systemd()
    if systemd and os.path.lexists(paths.unit):
        attempt(partial(_check, host, ("systemctl", "disable", "--now", UNIT_NAME), "stopping the unit"))
        # a unit that failed stays loaded, as failed, after its file is gone
        if host.run("systemctl", "is-failed", "--quiet", UNIT_NAME) == 0:
            attempt(partial(_check, host, ("systemctl", "reset-failed", UNIT_NAME), "forgetting the unit"))
    removed_unit = False
    for path in (paths.unit + store.PENDING_SUFFIX, paths.unit):
        try:
            removed_unit = store.remove_file(path) or removed_unit
        except store.StoreError as exc:
            problems.append(str(exc))
    if systemd and removed_unit:
        attempt(partial(_check, host, ("systemctl", "daemon-reload"), "reloading systemd"))
    for path in (
        paths.token + store.PENDING_SUFFIX, paths.token,
        paths.env + store.PENDING_SUFFIX, paths.env,
    ):
        attempt(partial(store.remove_file, path))
    for directory in (paths.state, paths.etc):
        attempt(partial(store.remove_dir, directory))
    if host.service_user() is not None or host.service_group():
        if host.service_user() is not None:
            attempt(partial(_check, host, ("userdel", SERVICE_USER), "removing the service user"))
        if host.service_group():
            attempt(partial(_check, host, ("groupdel", SERVICE_USER), "removing the service user's group"))
    for problem in problems:
        out(f"not removed: {problem}")
    out(
        f"uninstalled{' with problems (above)' if problems else ''}. Left in place: the virtual "
        f"environment ({sys.prefix}), which you made, and the agent on the server: revoke or "
        "delete it on the Agents page."
    )
    return 1 if problems else 0
