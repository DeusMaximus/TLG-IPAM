"""`tlg-ipam-agent install` and `tlg-ipam-agent uninstall`: the systemd install (spec §5.6, #22).

`install`, run as root from the virtual environment the agent was installed into:

1. creates the service user `tlg-ipam-agent` (a system user with no home and no login
   shell, and a group of its own), unless it is there, and records the uid and gid of
   one it creates in `/etc/tlg-ipam-agent/created-user`;
2. creates `/etc/tlg-ipam-agent` and `/var/lib/tlg-ipam-agent`, root's, mode 0755;
3. writes `/etc/tlg-ipam-agent/agent.env` (the server's URL and the settings given; no
   secret) and the unit `/etc/systemd/system/tlg-ipam-agent.service`, both mode 0644;
4. exchanges the enrolment token itself, and writes the agent token to
   `/var/lib/tlg-ipam-agent/token`, owned by the service user, mode 0600;
5. reloads systemd, enables the unit and (re)starts it.

The enrolment token comes from the operator and goes nowhere but the request: it is in
no file this writes, and the unit's agent never sees it. It is read from standard
input alone (`--enrol-token -`; with a prompt, and without echoing it, on a
terminal), never taken as an argument: any local user can read a command line from
`/proc/<pid>/cmdline` while the exchange waits, and sudo logs it, which on a host with
a persistent journal puts it on disk (#22 measured both). A `--ca-file` must be
readable by the service user, which the kernel is asked by running fixed programs as
that user (`read_as`), once the account exists and before the exchange. The token
file exists, empty, with its owner and mode, before the exchange, so nothing that
could fail locally is left until the token is spent; if the install fails before the exchange, or the exchange is
refused, what this run created is taken away again. Once the token is stored nothing
is taken back.

Run again without `--enrol-token`, `install` keeps the stored token and rewrites the
settings and the unit (an update: install the new release into the same environment,
then run it). With a token already stored, `--enrol-token` is refused: `uninstall`
first, then enrol again.

`uninstall` stops and disables the unit and removes everything `install` created: the
unit, both directories and the files in them, and the service user and its group when
the record says `install` created them and their ids are still the recorded ones. An
account the install found there is left. The record goes last, once the account and
group it stands for are gone, so an uninstall whose `userdel` failed (the account busy)
can be run again. One window is not covered: an install stopped between `useradd` and
writing the record leaves an account nothing records; remove it by hand (`userdel
tlg-ipam-agent`) if `uninstall` reports it left in place. It leaves the virtual environment, which the
operator made, and the agent's record on the server, which only the owner can remove.

Neither command is reached until `cli` has had `trust.check` find that only root can
change the code Python could load: this module is imported after that check.

The programs this runs are fixed: `useradd`, `userdel`, `groupdel` and `systemctl` as
root, and `head` and `test` as the service user (`read_as`). Their arguments are fixed
but for the operator's `--ca-file` path, with a fixed PATH and no shell; nothing the
server sends reaches them.
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
# written when `install` creates the service user, holding its uid and gid: `uninstall`
# removes only an account this records (the review of #130: one the install found
# there is someone else's)
USER_RECORD_NAME = "created-user"
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

    @property
    def user_record(self) -> str:
        return os.path.join(self.etc, USER_RECORD_NAME)


@dataclass(frozen=True)
class Options:
    url: str | None
    # as given: "-" for standard input, or None; any other value is refused
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

    def service_group(self) -> int | None:
        try:
            return grp.getgrnam(SERVICE_USER).gr_gid
        except KeyError:
            return None

    def run(self, *argv: str) -> int:
        print("+ " + " ".join(argv), flush=True)
        return subprocess.run(list(argv), env=COMMAND_ENV, stdin=subprocess.DEVNULL, check=False).returncode

    def code_unreachable(self, interpreter: str) -> str | None:
        return unreachable(interpreter)

    def service_reads(self, path: str, uid: int, gid: int) -> str | None:
        """`read_as` for the service user, with the groups systemd gives the unit's
        process (initgroups(3): its primary group and every group the user database
        lists the account in). The line printed names the `--ca-file` by its option,
        not by its path (#140)."""
        print(f"+ {' '.join(CA_READ)} <--ca-file>, as {SERVICE_USER}", flush=True)
        return read_as(path, uid, gid, os.getgrouplist(SERVICE_USER, gid))


def unreachable(
    interpreter: str,
    *,
    prefix: str = sys.prefix,
    package: str = str(Path(__file__).resolve().parent),
    top: str = "/",
) -> str | None:
    """The first path the service user could not use, or None. It is no member of any
    group these paths have, so their bits for others are what it gets: every directory
    above the interpreter (as the unit names it and as resolved) must let others
    through (`x`) and the interpreter run (`x`); every directory of the virtual
    environment and the package must let others list and enter it (`r`, `x`: the
    import system lists them), and every file in them must be readable (`r`). A virtual
    environment made under a umask such as 077 fails here, before the install changes
    anything or spends the token (Codex on #130)."""
    resolved = Path(interpreter).resolve()
    if not os.stat(resolved).st_mode & stat.S_IXOTH:
        return str(resolved)
    stop = Path(top)
    for start in (Path(interpreter).parent, resolved.parent):
        for directory in (start, *start.parents):
            if not os.stat(directory).st_mode & stat.S_IXOTH:
                return str(directory)
            if directory == stop:
                break
    for tree in (Path(prefix), Path(package)):
        for directory, _dirs, names in os.walk(tree):
            if os.stat(directory).st_mode & (stat.S_IROTH | stat.S_IXOTH) != stat.S_IROTH | stat.S_IXOTH:
                return directory
            for name in names:
                path = os.path.join(directory, name)
                info = os.lstat(path)
                if stat.S_ISREG(info.st_mode) and not info.st_mode & stat.S_IROTH:
                    return path
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


# How `install` asks the kernel whether the service user can read the `--ca-file`
# (#136): it runs these fixed programs as that user, with the groups the unit's agent
# gets, and judges by their exit status. `head` opens the file and reads one byte;
# only when it fails, `test -e` says whether the user can reach the path at all (it
# stats it, which needs every directory and link on the way and nothing of the file),
# so the refusal can say where it stopped without naming the path. Their output and
# their errors, which name the path, go to /dev/null.
CA_READ = ("head", "-c", "1", "--")
CA_REACH = ("test", "-e")
# ample for a local file; a FIFO swapped in for the file, or a stuck network mount, is
# killed at it, and the install refused
CA_CHECK_TIMEOUT_S = 10
# where the check stopped, by its kind (#140: never by the path)
AT_THE_FILE = "the file itself"
ON_THE_WAY = "a directory or link on the way to it"
NOT_CHECKED = "not checked"


def read_as(path: str, uid: int, gid: int, groups) -> str | None:
    """None when the user `uid`, with primary group `gid` and supplementary `groups`,
    can open `path` and read from it, as the kernel decides it: POSIX ACLs, every group
    it is in, every directory and link on the way, and a security module that checks
    an open. Otherwise where it stopped: AT_THE_FILE, ON_THE_WAY, or NOT_CHECKED when a
    program could not be run as that user or did not finish in time (the review of
    #130, round 2: a model of the mode bits passed a bundle an ACL denied the account,
    and refused one it could read through a supplementary group).

    The programs run as that user from the start: `subprocess`'s `user`, `group` and
    `extra_groups` have the child call setgroups, setregid and setreuid before it
    execs, so the program holds no root privilege and none of root's groups. They get
    the fixed environment the install's other programs get, no shell, no input, and
    `/` as the working directory. Their arguments are fixed but for `path`, the
    operator's own `--ca-file`, already checked to be absolute and plain (`_ca_file`);
    nothing the server sends reaches them, and the check is made before the exchange."""

    def as_user(*argv: str) -> int:
        return subprocess.run(
            list(argv),
            env=COMMAND_ENV,
            cwd="/",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            user=uid,
            group=gid,
            extra_groups=list(groups),
            timeout=CA_CHECK_TIMEOUT_S,
            check=False,
        ).returncode

    try:
        if as_user(*CA_READ, path) == 0:
            return None
        reached = as_user(*CA_REACH, path)
    except (OSError, subprocess.SubprocessError):
        return NOT_CHECKED
    if reached == 0:
        return AT_THE_FILE
    if reached == 1:
        return ON_THE_WAY
    return NOT_CHECKED


def _ca_file(value: str | None) -> str | None:
    """The CA bundle's path, checked. No refusal repeats it: a token pasted into the
    wrong option would be shown on the terminal (#140)."""
    if value is None:
        return None
    _plain(value, "--ca-file")
    if not os.path.isabs(value):
        raise InstallError("--ca-file must be an absolute path")
    try:
        info = os.stat(value)
    except OSError:
        raise InstallError("--ca-file: there is no file at the path given (not repeated here)") from None
    if not stat.S_ISREG(info.st_mode):
        raise InstallError("--ca-file: the path given is not a regular file (not repeated here)")
    return value


# what OpenSSL reads from the environment to choose the CAs it trusts (the agent's TLS
# context is `ssl.create_default_context`, which loads OpenSSL's default paths)
TRUST_VARIABLES = ("SSL_CERT_FILE", "SSL_CERT_DIR")


def service_trust(ca_file: str | None) -> dict[str, str]:
    """The trust settings the unit's agent is given, through agent.env, and so the only
    ones the exchange is made with (the round-2 review of #130: an exchange that trusted
    what root's environment named, and a unit given nothing, spent the token on an agent
    that could not verify its server). A unit inherits nothing of root's environment,
    and the unit discards what the system manager's own environment would add
    (`discarded_trust`)."""
    return {"SSL_CERT_FILE": ca_file} if ca_file is not None else {}


def discarded_trust(ca_file: str | None) -> tuple[str, ...]:
    """The trust settings the unit unsets (`UnsetEnvironment=`): every one that
    `service_trust` does not give it. A unit also receives the system manager's
    environment (`systemctl set-environment`, `DefaultEnvironment=`), which the
    installer never sees (#138: a manager's `SSL_CERT_FILE` made a unit installed
    without `--ca-file` fail verification after the token was spent). systemd applies
    `UnsetEnvironment=` after the manager's environment, `Environment=` and
    `EnvironmentFile=`, whatever the order of the lines, so a name it lists reaches the
    agent from nowhere; and agent.env's `SSL_CERT_FILE` overrides the manager's, so it
    is not listed when `--ca-file` sets it."""
    given = service_trust(ca_file)
    return tuple(name for name in TRUST_VARIABLES if name not in given)


def inherited_trust(environ, ca_file: str | None) -> str | None:
    """The first trust setting in the installer's environment that the unit would not
    be given, or None: `SSL_CERT_DIR` always (there is no setting for a directory of
    CAs), `SSL_CERT_FILE` unless it names the `--ca-file` itself."""
    wanted = service_trust(ca_file)
    for name in TRUST_VARIABLES:
        value = environ.get(name)
        if value and wanted.get(name) != value:
            return name
    return None


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
    lines += [f"{name}={value}" for name, value in service_trust(ca_file).items()]
    return "\n".join(lines) + "\n"


def render_unit(env_path: str, interpreter: str, ca_file: str | None) -> str:
    """The unit. It runs the agent as the service user with CAP_NET_RAW as its one
    capability, for the raw ICMP and ARP sockets, and with no trust setting but
    agent.env's (`discarded_trust`). Namespace-based sandboxing (`ProtectSystem=` and
    the like) is left out: the unit is meant for an LXC as well, where those can stop
    it from starting; add them in a drop-in where they work."""
    _plain(interpreter, "the agent's interpreter path")
    discarded = " ".join(discarded_trust(ca_file))
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
# Trust only what agent.env gives: drop what the system manager's environment adds.
UnsetEnvironment={discarded}
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
    """The enrolment exchange, once, trusting exactly what the unit's agent will:
    every inherited trust setting is dropped and `service_trust` applied, before the
    TLS context is made (`client.tls_context` reads them then)."""
    for name in TRUST_VARIABLES:
        os.environ.pop(name, None)
    os.environ.update(service_trust(ca_file))

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
    if host.service_group() is not None:
        _check(host, ("groupdel", SERVICE_USER), "removing the service user's group")


def _remove_account(host: Host, recorded: tuple[int, int] | None, out, problems: list[str]) -> bool:
    """Removes the service account and its group when `recorded` (the uid and gid
    `install` wrote when it created them) still names them, and says whether nothing
    the record stands for is left to remove. With no record, an account of the name is
    someone else's and is left."""
    user, group = host.service_user(), host.service_group()
    if recorded is None:
        if user is not None or group is not None:
            out(f"left the {SERVICE_USER} account in place: install did not create it")
        return True
    gid = recorded[1]
    if user is not None and tuple(user) != recorded:
        out(f"left the {SERVICE_USER} account in place: it is not the one install created")
        return True
    if user is not None:
        try:
            _check(host, ("userdel", SERVICE_USER), "removing the service user")
        except InstallError as exc:
            problems.append(str(exc))
            return False
    group = host.service_group()
    if group is not None and group == gid:
        try:
            _check(host, ("groupdel", SERVICE_USER), "removing the service user's group")
        except InstallError as exc:
            problems.append(str(exc))
            return False
    return True


def _recorded_user(path: str) -> tuple[int, int] | None:
    """The uid and gid `install` recorded when it created the service user, or None
    when it recorded none (it found the account there) or the record is not one."""
    try:
        data = store.read_small(path)
    except store.StoreError:
        return None
    if data is None:
        return None
    try:
        uid, gid = data.decode("ascii").strip().split(":")
        return int(uid), int(gid)
    except ValueError:
        return None


def install(
    options: Options,
    *,
    host: Host | None = None,
    paths: Paths = Paths(),
    exchange: Callable[[str, str, str | None], Answer] = exchange,
    read_stdin: Callable[[], str] = read_token_line,
    interpreter: str = sys.executable,
    out: Callable[[str], None] = _say,
    environ=os.environ,
    secret: Callable[[str], None] = lambda token: None,
) -> int:
    """`secret` is given each token as the install learns it (the enrolment token read
    from standard input, the agent token from the answer), for `cli` to add to the
    log redaction (`loop.Redact`)."""
    host = host or Host()
    try:
        _install(options, host, paths, exchange, read_stdin, interpreter, out, environ, secret)
    except (InstallError, config.ConfigError, store.StoreError) as exc:
        out(f"install failed: {exc}")
        return 1
    return 0


def _install(options, host, paths, exchange, read_stdin, interpreter, out, environ, secret) -> None:
    # that only root can change the code was checked by `cli`, before it imported this
    # module (`trust.check`)
    _preflight(host)
    hidden = host.code_unreachable(interpreter)
    if hidden is not None:
        raise InstallError(
            f"{hidden} is not readable by other users, so the unit's user, {SERVICE_USER}, "
            "could not run the agent (a virtual environment made under a restrictive "
            "umask, say). Make the environment readable, as with `chmod -R o+rX "
            f"{sys.prefix}`, and run install again"
        )
    url = config.parse_url(options.url, "--url")
    allowed = None if options.allowed_cidrs is None else config.parse_allowed(options.allowed_cidrs, "--allowed-cidrs")
    max_pps = None if options.max_pps is None else config.parse_max_pps(options.max_pps, "--max-pps")
    ca_file = _ca_file(options.ca_file)
    inherited = inherited_trust(environ, ca_file)
    if inherited is not None:
        raise InstallError(
            f"{inherited} is set in this environment, and the unit would not have it: the "
            "exchange would trust a CA the agent then could not. Unset it, and give a private "
            "CA's bundle as --ca-file (a directory of CAs is not supported)"
        )
    token = options.enrol_token
    if token is not None and token != "-":
        # the review of #130: any local user can read a command line from
        # /proc/<pid>/cmdline while the exchange waits on the server
        raise InstallError(
            "--enrol-token takes - alone, and reads the token from standard input: a token "
            "on the command line can be read by any local user while install runs, and sudo "
            "logs it. It was not used, but treat it as exposed: re-enrol the agent on the "
            "Agents page for a new one, and give that on standard input"
        )
    if token == "-":
        token = read_stdin().rstrip("\n")
        if not token:
            raise InstallError("--enrol-token -: nothing was read from standard input")
    if token is not None:
        token = config.parse_token(token, "--enrol-token", config.ENROL_TOKEN_PREFIX)
        secret(token)
    env = render_env(url, allowed, max_pps, ca_file, paths.token)
    unit = render_unit(paths.env, interpreter, ca_file)

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
        created_user = user is None
        if created_user:
            _check(host, USERADD, "creating the service user")
            undo.append(partial(_delete_user, host))
            user = host.service_user()
            if user is None:
                raise InstallError(f"useradd ran but there is no user {SERVICE_USER}")
        if ca_file is not None:
            # asked of the kernel, as the service user (#136). Neither path is
            # repeated (#140): `namei -l` and `getfacl` on the path show them
            where = host.service_reads(ca_file, *user)
            if where == NOT_CHECKED:
                raise InstallError(
                    f"--ca-file: could not check that {SERVICE_USER}, which the unit runs as, "
                    f"can read the file (`{CA_READ[0]}` or `{CA_REACH[0]}` could not be run as that "
                    f"user, or did not finish within {CA_CHECK_TIMEOUT_S} s), so the install "
                    "stopped before the token was spent"
                )
            if where is not None:
                raise InstallError(
                    f"--ca-file: {SERVICE_USER}, which the unit runs as, could not read the "
                    f"file (stopped at {where}), so the agent could not verify the server. "
                    "Make the file and the directories above it readable by that user "
                    "(`namei -l` on the path shows each one's owner and mode, `getfacl` "
                    "an ACL)"
                )
        for directory in (paths.etc, paths.state):
            if store.make_dir(directory, DIR_MODE):
                undo.append(partial(store.remove_dir, directory))
        if created_user:
            store.write_file(paths.user_record, f"{user[0]}:{user[1]}\n".encode("ascii"), FILE_MODE)
            undo.append(partial(store.remove_file, paths.user_record))
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
                secret(answer.data["agent_token"])
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

    # read before the directory that holds it goes
    recorded = _recorded_user(paths.user_record)
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
        paths.user_record + store.PENDING_SUFFIX,
    ):
        attempt(partial(store.remove_file, path))
    attempt(partial(store.remove_dir, paths.state))
    # the account and its group go only if `install` created them: the uid and the
    # primary gid it recorded are both theirs still (Codex on #130: a uid alone would
    # let an account since repurposed with another group go). One it found there, or
    # one since replaced, is left. The record stays until what it stands for is gone
    # (the round-2 review of #130: removed first, a `userdel` that failed, with the
    # account busy, left the next uninstall unable to tell the account was the
    # install's), so a retry finishes the job, a user already gone and its recorded
    # group left included
    if _remove_account(host, recorded, out, problems):
        attempt(partial(store.remove_file, paths.user_record))
        attempt(partial(store.remove_dir, paths.etc))
    else:
        out(f"kept {paths.user_record}, so that uninstall, run again, can still remove the account")
    for problem in problems:
        out(f"not removed: {problem}")
    out(
        f"uninstalled{' with problems (above)' if problems else ''}. Left in place: the virtual "
        f"environment ({sys.prefix}), which you made, and the agent on the server: revoke or "
        "delete it on the Agents page."
    )
    return 1 if problems else 0
