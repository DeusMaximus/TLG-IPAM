"""`tlg-ipam-agent install` and `uninstall` (#22), in a directory of the test's own.

The host is a fake: it records the programs the install would run and plays the user
database, and its service user is the test's own user, so the token file's owner can
be checked without root. The files are real, written by `store`, under a temporary
root. The enrolment exchange is made against the server's real routes, in process.

What this cannot show (a real `useradd`, a real systemd, the file's owner as another
user) was shown in Linux containers; the PR records how."""

import asyncio
import os
import stat
from pathlib import Path

import httpx
import pytest
from app.database import SessionLocal, engine
from app.main import app
from app.models import Agent as AgentRow
from app.models import Base
from app.services import enrolment
from app.services.device_types import seed_defaults

from conftest import ENROL_TOKEN, TOKEN
from tlg_ipam_agent import enrol, install, store
from tlg_ipam_agent.client import Answer, Kind
from tlg_ipam_agent.install import USERADD, Options, Paths

PYTHON = "/opt/tlg-ipam-agent/bin/python"
URL = "https://ipam.home.arpa"
ANSWER = Answer(Kind.OK, 200, None, "HTTP 200", {"agent_id": "0b6f3c1e-1111-4222-8333-444455556666", "agent_token": TOKEN})


class FakeHost(install.Host):
    def __init__(
        self, *, root=True, systemd=True, user=False, group_stays=False, fail=None, failed_unit=False, ids=None
    ):
        self.root, self.systemd = root, systemd
        # the service user's uid and gid: the test's own unless given (a token file can
        # be given only to the test's own user without root)
        self.ids = ids or (os.getuid(), os.getgid())
        self.failed_unit = failed_unit
        self.user = user
        self.group = os.getgid() if user else None
        self.group_stays = group_stays
        self.fail = fail or {}
        self.hidden = None
        self.commands: list[tuple[str, ...]] = []

    def euid(self):
        return 0 if self.root else 1000

    def has_systemd(self):
        return self.systemd

    def service_user(self):
        return self.ids if self.user else None

    def service_group(self):
        return self.group

    def code_unreachable(self, interpreter):
        assert interpreter == PYTHON
        return self.hidden

    def run(self, *argv):
        self.commands.append(argv)
        if argv[:2] == ("systemctl", "is-failed"):
            return 0 if self.failed_unit else 1
        code = self.fail.get(argv[0] if argv[0] != "systemctl" else argv[1], 0)
        if code:
            return code
        if argv[0] == "useradd":
            self.user, self.group = True, os.getgid()
        elif argv[0] == "userdel":
            self.user = False
            self.group = os.getgid() if self.group_stays else None
        elif argv[0] == "groupdel":
            self.group = None
        return 0


@pytest.fixture
def root(tmp_path):
    for directory in ("etc/systemd/system", "var/lib"):
        (tmp_path / directory).mkdir(parents=True)
    return tmp_path


def paths_in(root: Path) -> Paths:
    return Paths(
        etc=str(root / "etc/tlg-ipam-agent"),
        state=str(root / "var/lib/tlg-ipam-agent"),
        unit=str(root / "etc/systemd/system/tlg-ipam-agent.service"),
    )


def tree(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


def every_byte(root: Path) -> bytes:
    return b"".join(p.read_bytes() for p in root.rglob("*") if p.is_file())


def run_install(root, host, options=None, *, exchange=None, stdin=ENROL_TOKEN + "\n", out=None):
    said = [] if out is None else out
    code = install.install(
        options or Options(url=URL, enrol_token="-"),
        host=host,
        paths=paths_in(root),
        exchange=exchange or (lambda url, token, ca: ANSWER),
        read_stdin=lambda: stdin,
        interpreter=PYTHON,
        out=said.append,
    )
    return code, said


SYSTEMD_START = [
    ("systemctl", "daemon-reload"),
    ("systemctl", "enable", "tlg-ipam-agent.service"),
    ("systemctl", "restart", "tlg-ipam-agent.service"),
]


# --- install ---


def test_install_writes_the_token_for_the_service_user_alone_and_starts_the_unit(root):
    host = FakeHost()
    asked = []
    code, said = run_install(root, host, exchange=lambda url, token, ca: asked.append((url, token, ca)) or ANSWER)
    assert code == 0, said
    assert asked == [(URL, ENROL_TOKEN, None)]
    assert host.commands == [USERADD, *SYSTEMD_START]
    token = root / "var/lib/tlg-ipam-agent/token"
    info = os.stat(token)
    # acceptance 2: owned by the service user, mode 0600, in a directory only root writes
    assert stat.S_IMODE(info.st_mode) == 0o600
    assert (info.st_uid, info.st_gid) == (os.getuid(), os.getgid())
    assert token.read_bytes() == TOKEN.encode()
    for directory in ("etc/tlg-ipam-agent", "var/lib/tlg-ipam-agent"):
        assert stat.S_IMODE(os.stat(root / directory).st_mode) == 0o755
    for written in ("etc/tlg-ipam-agent/agent.env", "etc/systemd/system/tlg-ipam-agent.service"):
        assert stat.S_IMODE(os.stat(root / written).st_mode) == 0o644
    assert tree(root) == {
        "etc", "etc/systemd", "etc/systemd/system", "etc/systemd/system/tlg-ipam-agent.service",
        "etc/tlg-ipam-agent", "etc/tlg-ipam-agent/agent.env", "etc/tlg-ipam-agent/created-user",
        "var", "var/lib", "var/lib/tlg-ipam-agent", "var/lib/tlg-ipam-agent/token",
    }
    # acceptance 3: the enrolment token is in no file; the agent token only in its own
    everything = every_byte(root)
    assert ENROL_TOKEN.encode() not in everything
    assert everything.count(TOKEN.encode()) == 1
    assert not any(ENROL_TOKEN in line or TOKEN in line for line in said)


def test_the_settings_file_holds_the_settings_and_no_secret(root):
    ca = root / "ca.pem"
    ca.write_bytes(b"-----BEGIN CERTIFICATE-----\n")
    options = Options(
        url="https://ipam.home.arpa:8443/",
        enrol_token="-",
        allowed_cidrs="10.20.0.0/16 192.168.1.0/24",
        max_pps="50",
        ca_file=str(ca),
    )
    asked = []
    code, said = run_install(root, FakeHost(), options, exchange=lambda u, t, c: asked.append(c) or ANSWER)
    assert code == 0, said
    env = (root / "etc/tlg-ipam-agent/agent.env").read_text()
    settings = [line for line in env.splitlines() if not line.startswith("#")]
    assert settings == [
        "IPAM_URL=https://ipam.home.arpa:8443",
        "ALLOWED_CIDRS=10.20.0.0/16,192.168.1.0/24",
        "MAX_PPS=50",
        f"SSL_CERT_FILE={ca}",
    ]
    assert asked == [str(ca)]  # the exchange trusts the same CA


def test_the_unit_runs_the_agent_as_the_service_user_with_cap_net_raw_alone(root):
    run_install(root, FakeHost())
    unit = (root / "etc/systemd/system/tlg-ipam-agent.service").read_text()
    lines = set(unit.splitlines())
    assert {
        "User=tlg-ipam-agent",
        "Group=tlg-ipam-agent",
        f"EnvironmentFile={root}/etc/tlg-ipam-agent/agent.env",
        f"ExecStart={PYTHON} -m tlg_ipam_agent",
        "AmbientCapabilities=CAP_NET_RAW",
        "CapabilityBoundingSet=CAP_NET_RAW",
        "NoNewPrivileges=yes",
        "WantedBy=multi-user.target",
    } <= lines
    assert "IPAM_AGENT_TOKEN" not in unit and "IPAM_ENROL_TOKEN" not in unit


def test_the_enrolment_token_can_come_from_standard_input(root):
    asked = []
    code, _ = run_install(
        root,
        FakeHost(),
        Options(url=URL, enrol_token="-"),
        exchange=lambda u, t, c: asked.append(t) or ANSWER,
        stdin=ENROL_TOKEN + "\n",
    )
    assert code == 0 and asked == [ENROL_TOKEN]


@pytest.mark.parametrize(
    "host, message",
    [
        (FakeHost(root=False), "run it as root"),
        (FakeHost(systemd=False), "systemd is not running"),
    ],
)
def test_install_refuses_before_it_changes_anything(root, host, message):
    before = tree(root)
    code, said = run_install(root, host, exchange=lambda *a: pytest.fail("no exchange"))
    assert code == 1 and message in said[-1]
    assert host.commands == [] and tree(root) == before


@pytest.mark.parametrize(
    "options, message",
    [
        (Options(url="http://ipam.home.arpa", enrol_token="-"), "--url must use https"),
        (Options(url=None, enrol_token="-"), "--url is not set"),
        (Options(url=URL, enrol_token=ENROL_TOKEN), "--enrol-token takes - alone"),
        (Options(url=URL, enrol_token=TOKEN), "--enrol-token takes - alone"),
        (Options(url=URL), "--enrol-token is needed"),
        (Options(url=URL, enrol_token="-", allowed_cidrs="10.0.0.1/8"), "--allowed-cidrs"),
        (Options(url=URL, enrol_token="-", max_pps="0"), "--max-pps"),
        (Options(url=URL, enrol_token="-", ca_file="relative.pem"), "--ca-file must be an absolute path"),
        (Options(url=URL, enrol_token="-", ca_file="/no/such/ca.pem"), "cannot be found"),
        (Options(url=URL, enrol_token="-", ca_file="/a b.pem"), "--ca-file has a character"),
        (Options(url="https://ipam.home.arpa/%61", enrol_token="-"), "--url has a character"),
    ],
)
def test_bad_options_are_refused_before_anything_changes(root, options, message):
    host = FakeHost()
    before = tree(root)
    code, said = run_install(root, host, options, exchange=lambda *a: pytest.fail("no exchange"))
    assert code == 1 and message in said[-1], said
    assert ENROL_TOKEN not in said[-1]
    assert host.commands == [] and tree(root) == before


@pytest.mark.parametrize(
    "line, message",
    [
        ("", "nothing was read from standard input"),
        (TOKEN + "\n", "--enrol-token is not an enrolment token"),
        ("tlge_a b\n", "--enrol-token contains characters"),
    ],
)
def test_a_bad_token_on_standard_input_is_refused_before_anything_changes(root, line, message):
    host = FakeHost()
    before = tree(root)
    code, said = run_install(root, host, stdin=line, exchange=lambda *a: pytest.fail("no exchange"))
    assert code == 1 and message in said[-1], said
    assert host.commands == [] and tree(root) == before


def test_a_token_on_the_command_line_is_refused_and_not_repeated(root):
    """The review of #130: another local user can read an argument from
    /proc/<pid>/cmdline while the exchange waits, so `--enrol-token` takes `-` alone.
    The refusal comes before standard input is read and does not repeat the value."""
    host = FakeHost()
    read = []
    code, said = install.install(
        Options(url=URL, enrol_token=ENROL_TOKEN),
        host=host,
        paths=paths_in(root),
        exchange=lambda *a: pytest.fail("no exchange"),
        read_stdin=lambda: read.append(1) or "",
        interpreter=PYTHON,
        out=(said := []).append,
    ), said
    assert code == 1 and "--enrol-token takes - alone" in said[-1]
    assert ENROL_TOKEN not in said[-1] and read == [] and host.commands == []


def test_a_ca_file_the_service_user_cannot_read_stops_the_install_before_the_exchange(root):
    """The review of #130: root could read the bundle and the service user could not,
    and the install spent the token on a unit that could not verify the server. The
    file is checked as the service user, after the account exists, before the
    exchange, and the install is taken back."""
    ca = root / "ca.pem"
    ca.write_bytes(b"-----BEGIN CERTIFICATE-----\n")
    ca.chmod(0o600)  # root's (here, the test's) alone
    host = FakeHost(ids=(os.getuid() + 1000, os.getgid() + 1000))
    before = tree(root)
    code, said = run_install(
        root, host, Options(url=URL, enrol_token="-", ca_file=str(ca)), exchange=lambda *a: pytest.fail("no exchange")
    )
    assert code == 1 and "could not read" in said[-1] and str(ca) in said[-1], said
    assert tree(root) == before and host.commands == [USERADD, ("userdel", "tlg-ipam-agent")]


def test_a_ca_file_behind_a_directory_the_service_user_cannot_enter_is_refused(tmp_path):
    """Every directory on the way is judged, and a link's directories too."""
    tmp_path.chmod(0o755)
    hidden = tmp_path / "private"
    hidden.mkdir()
    ca = hidden / "ca.pem"
    ca.write_bytes(b"-----BEGIN CERTIFICATE-----\n")
    ca.chmod(0o644)
    hidden.chmod(0o700)  # the installer's alone: others cannot enter it
    other = (os.getuid() + 1000, os.getgid() + 1000)
    top = str(tmp_path)
    assert install.blocked_for(str(ca), *other, top=top) == str(hidden)
    # a link from a readable place into it is followed, and refused at the same place
    (tmp_path / "ca-link.pem").symlink_to(ca)
    assert install.blocked_for(str(tmp_path / "ca-link.pem"), *other, top=top) == str(hidden)
    hidden.chmod(0o711)
    assert install.blocked_for(str(ca), *other, top=top) is None
    assert install.blocked_for(str(tmp_path / "missing.pem"), *other, top=top) == str(tmp_path / "missing.pem")


@pytest.mark.parametrize(
    "mode, uid, gid, readable",
    [
        (0o600, "me", "other", True),  # the owner's bits
        (0o060, "other", "mine", True),  # the group's, for a file of the user's group
        (0o604, "other", "other", True),  # others' bits
        (0o640, "other", "other", False),  # group bits of a group it is not in
        (0o600, "other", "mine", False),
    ],
)
def test_a_file_is_judged_by_the_bits_that_apply_to_the_service_user(tmp_path, mode, uid, gid, readable):
    path = tmp_path / "ca.pem"
    path.write_bytes(b"")
    path.chmod(mode)
    me, mine = os.getuid(), os.getgid()
    as_uid = me if uid == "me" else me + 1000
    as_gid = mine if gid == "mine" else mine + 1000
    # the directories above are the test's own: judged from the test's user
    blocked = install.blocked_for(str(path), as_uid, as_gid, top=str(path))
    assert (blocked is None) is readable


@pytest.mark.parametrize(
    "answer, message",
    [
        (Answer(Kind.REFUSED, 403, "FORBIDDEN", "HTTP 403 FORBIDDEN"), "refused the enrolment token (HTTP 403"),
        (Answer(Kind.RETRY, 429, "RATE_LIMITED", "HTTP 429 RATE_LIMITED"), "the token is not spent"),
        (Answer(Kind.RETRY, 503, "UNAVAILABLE", "HTTP 503 UNAVAILABLE"), "the token is not spent"),
        (Answer(Kind.RETRY, None, None, "no answer (ConnectError)"), "If the server enrolled the agent"),
        (Answer(Kind.RETRY, 200, None, "HTTP 200 answer not in the expected shape"), "If the server enrolled"),
    ],
)
def test_an_exchange_that_does_not_enrol_takes_the_install_back(root, answer, message):
    """Acceptance 4 on the failure path: nothing this run created is left."""
    host = FakeHost()
    before = tree(root)
    code, said = run_install(root, host, exchange=lambda *a: answer)
    assert code == 1 and message in said[-1]
    assert tree(root) == before
    assert host.commands == [USERADD, ("userdel", "tlg-ipam-agent")]
    assert host.user is False


def test_a_service_user_that_was_there_is_kept_when_the_install_is_taken_back(root):
    host = FakeHost(user=True)
    code, _ = run_install(root, host, exchange=lambda *a: Answer(Kind.REFUSED, 403, "FORBIDDEN", "HTTP 403"))
    assert code == 1 and host.commands == [] and host.user is True


def test_an_exception_in_the_exchange_takes_the_install_back_too(root):
    host = FakeHost()
    before = tree(root)

    def boom(*a):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_install(root, host, exchange=boom)
    assert tree(root) == before and host.user is False


def test_a_failed_useradd_stops_the_install(root):
    host = FakeHost(fail={"useradd": 9})
    code, said = run_install(root, host, exchange=lambda *a: pytest.fail("no exchange"))
    assert code == 1 and "creating the service user failed" in said[-1] and "exited with 9" in said[-1]
    assert host.commands == [USERADD]


def test_a_systemctl_failure_after_the_exchange_leaves_the_install_in_place(root):
    """The token is spent once stored, so nothing is taken back after it."""
    host = FakeHost(fail={"restart": 1})
    code, said = run_install(root, host)
    assert code == 1 and "starting the unit failed" in said[-1]
    assert (root / "var/lib/tlg-ipam-agent/token").read_bytes() == TOKEN.encode()
    assert host.user is True


def test_a_second_install_with_a_token_stored_is_refused_and_changes_nothing(root):
    run_install(root, FakeHost())
    host = FakeHost(user=True)
    before = {p: (root / p).read_bytes() for p in tree(root) if (root / p).is_file()}
    code, said = run_install(root, host, exchange=lambda *a: pytest.fail("no exchange"))
    assert code == 1 and "already stored" in said[-1]
    assert host.commands == []
    assert {p: (root / p).read_bytes() for p in tree(root) if (root / p).is_file()} == before


def test_an_install_without_an_enrolment_token_keeps_the_stored_one_and_updates_the_rest(root):
    run_install(root, FakeHost())
    host = FakeHost(user=True)
    code, said = run_install(
        root, host, Options(url=URL, max_pps="20"), exchange=lambda *a: pytest.fail("no exchange")
    )
    assert code == 0, said
    assert host.commands == SYSTEMD_START
    assert "MAX_PPS=20" in (root / "etc/tlg-ipam-agent/agent.env").read_text()
    assert (root / "var/lib/tlg-ipam-agent/token").read_bytes() == TOKEN.encode()


def test_an_update_gives_the_stored_token_to_the_service_user_again(root, monkeypatch):
    """Codex on #130: a token written by something else (an agent run as root, a
    service user recreated with another uid) is rewritten with the service user as its
    owner and mode 0600, its value unchanged."""
    state = root / "var/lib/tlg-ipam-agent"
    state.mkdir()
    (state / "token").write_bytes(TOKEN.encode())
    (state / "token").chmod(0o644)
    owners = []
    real = store.write_file
    monkeypatch.setattr(
        install.store, "write_file", lambda path, data, mode, owner=None: owners.append((path, owner)) or real(path, data, mode, owner)
    )
    host = FakeHost(user=True)
    code, said = run_install(root, host, Options(url=URL), exchange=lambda *a: pytest.fail("no exchange"))
    assert code == 0, said
    assert stat.S_IMODE(os.stat(state / "token").st_mode) == 0o600
    assert (state / "token").read_bytes() == TOKEN.encode()
    assert (str(state / "token"), (os.getuid(), os.getgid())) in owners


def test_a_state_directory_left_owner_only_is_opened_to_the_service_user(root):
    """Codex on #130: a `0700` directory (state from a container, say) kept as it was
    would leave the service user unable to reach its token."""
    state = root / "var/lib/tlg-ipam-agent"
    state.mkdir(mode=0o700)
    state.chmod(0o700)
    code, said = run_install(root, FakeHost())
    assert code == 0, said
    assert stat.S_IMODE(os.stat(state).st_mode) == 0o755


def test_a_state_directory_others_can_write_is_refused_not_repaired(root):
    state = root / "var/lib/tlg-ipam-agent"
    state.mkdir()
    state.chmod(0o777)
    host = FakeHost()
    code, said = run_install(root, host, exchange=lambda *a: pytest.fail("no exchange"))
    assert code == 1 and "writable by users other than its owner" in said[-1]
    assert stat.S_IMODE(os.stat(state).st_mode) == 0o777


def _venv(root: Path) -> dict:
    """A virtual environment's layout: `bin/python3` a link to an interpreter outside
    it, and a package with a module."""
    system = root / "usr/bin"
    system.mkdir(parents=True)
    (system / "python3.13").write_bytes(b"")
    venv = root / "opt/tlg-ipam-agent"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin/python3").symlink_to(system / "python3.13")
    package = venv / "lib/python3.13/site-packages/tlg_ipam_agent"
    package.mkdir(parents=True)
    (package / "cli.py").write_bytes(b"")
    for directory in (root, *[p for p in root.rglob("*") if p.is_dir()]):
        directory.chmod(0o755)
    return dict(interpreter=str(venv / "bin/python3"), prefix=str(venv), package=str(package))


def _unreachable(root, layout):
    return install.unreachable(layout["interpreter"], prefix=layout["prefix"], package=layout["package"], top=str(root))


def test_code_the_service_user_can_reach_passes(tmp_path):
    layout = _venv(tmp_path)
    for path in tmp_path.rglob("*"):
        if path.is_file():
            path.chmod(0o755 if path.name.startswith("python") else 0o644)
    assert _unreachable(tmp_path, layout) is None


@pytest.mark.parametrize(
    "path, mode",
    [
        ("opt/tlg-ipam-agent", 0o700),  # a venv made under umask 077
        ("opt/tlg-ipam-agent/bin", 0o711),  # enterable but not listable, inside the venv
        ("opt/tlg-ipam-agent/lib/python3.13/site-packages/tlg_ipam_agent", 0o750),
        ("opt/tlg-ipam-agent/lib/python3.13/site-packages/tlg_ipam_agent/cli.py", 0o600),
        ("usr/bin/python3.13", 0o744),  # the interpreter others cannot run
        ("usr/bin", 0o700),
        ("opt", 0o700),
    ],
)
def test_code_the_service_user_cannot_reach_is_refused(tmp_path, path, mode):
    """Codex on #130: root's check alone passed a venv the service user could not
    enter, and the install spent the token on a unit that could not start."""
    layout = _venv(tmp_path)
    for each in tmp_path.rglob("*"):
        if each.is_file():
            each.chmod(0o755 if each.name.startswith("python") else 0o644)
    (tmp_path / path).chmod(mode)
    assert _unreachable(tmp_path, layout) == str(tmp_path / path)


def test_an_environment_the_service_user_cannot_reach_stops_the_install_first(root):
    host = FakeHost()
    host.hidden = "/opt/tlg-ipam-agent"
    before = tree(root)
    code, said = run_install(root, host, exchange=lambda *a: pytest.fail("no exchange"))
    assert code == 1 and "/opt/tlg-ipam-agent is not readable by other users" in said[-1]
    assert "chmod -R o+rX" in said[-1]
    assert host.commands == [] and tree(root) == before


# --- uninstall ---


def test_install_then_uninstall_leaves_nothing_behind(root):
    """Acceptance 4: no user, no unit, no file, no directory of the install's."""
    before = tree(root)
    host = FakeHost()
    assert run_install(root, host)[0] == 0
    host.commands.clear()
    said = []
    assert install.uninstall(host=host, paths=paths_in(root), out=said.append) == 0, said
    assert tree(root) == before
    assert host.user is False and host.group is None
    assert host.commands == [
        ("systemctl", "disable", "--now", "tlg-ipam-agent.service"),
        ("systemctl", "is-failed", "--quiet", "tlg-ipam-agent.service"),
        ("systemctl", "daemon-reload"),
        ("userdel", "tlg-ipam-agent"),
    ]
    assert "Left in place: the virtual environment" in said[-1]


def test_uninstall_forgets_a_unit_that_failed(root):
    host = FakeHost()
    run_install(root, host)
    host.commands.clear()
    host.failed_unit = True
    assert install.uninstall(host=host, paths=paths_in(root), out=lambda s: None) == 0
    assert host.commands[1:3] == [
        ("systemctl", "is-failed", "--quiet", "tlg-ipam-agent.service"),
        ("systemctl", "reset-failed", "tlg-ipam-agent.service"),
    ]


def test_uninstall_removes_a_group_userdel_left(root):
    host = FakeHost(group_stays=True)
    run_install(root, host)
    host.commands.clear()
    assert install.uninstall(host=host, paths=paths_in(root), out=lambda s: None) == 0
    assert host.commands[-2:] == [("userdel", "tlg-ipam-agent"), ("groupdel", "tlg-ipam-agent")]
    assert host.group is None


def test_an_account_the_install_found_is_used_and_left_by_uninstall(root):
    """The review of #130: `uninstall` removed an account of the same name that the
    install had not created. Now only an account it recorded creating is removed."""
    host = FakeHost(user=True)
    code, said = run_install(root, host)
    assert code == 0, said
    assert USERADD not in host.commands
    assert not (root / "etc/tlg-ipam-agent/created-user").exists()
    host.commands.clear()
    said = []
    assert install.uninstall(host=host, paths=paths_in(root), out=said.append) == 0
    assert not any(c[0] in ("userdel", "groupdel") for c in host.commands)
    assert host.user is True and host.group == os.getgid()
    assert any("left the tlg-ipam-agent account in place" in line for line in said)
    assert tree(root) == {"etc", "etc/systemd", "etc/systemd/system", "var", "var/lib"}


def test_uninstall_with_only_a_same_name_account_leaves_it(root):
    """The reviewer's reproduction: no unit, no files, an account of that name."""
    host = FakeHost(user=True)
    assert install.uninstall(host=host, paths=paths_in(root), out=lambda s: None) == 0
    assert host.commands == [] and host.user is True


def test_an_account_replaced_since_the_install_is_left(root):
    host = FakeHost()
    run_install(root, host)
    record = root / "etc/tlg-ipam-agent/created-user"
    assert record.read_text() == f"{os.getuid()}:{os.getgid()}\n"
    record.write_text(f"{os.getuid() + 1}:{os.getgid()}\n")  # the uid it created is gone
    host.commands.clear()
    assert install.uninstall(host=host, paths=paths_in(root), out=lambda s: None) == 0
    assert not any(c[0] == "userdel" for c in host.commands) and host.user is True


def test_uninstall_with_nothing_installed_does_nothing(root):
    host = FakeHost()
    before = tree(root)
    assert install.uninstall(host=host, paths=paths_in(root), out=lambda s: None) == 0
    assert host.commands == [] and tree(root) == before


def test_uninstall_removes_what_a_stopped_install_left(root):
    run_install(root, FakeHost())
    for pending in ("var/lib/tlg-ipam-agent/token.new", "etc/tlg-ipam-agent/agent.env.new"):
        (root / pending).write_bytes(b"")
    host = FakeHost(user=True)
    assert install.uninstall(host=host, paths=paths_in(root), out=lambda s: None) == 0
    assert not (root / "var/lib/tlg-ipam-agent").exists() and not (root / "etc/tlg-ipam-agent").exists()


def test_uninstall_leaves_a_file_it_did_not_write_and_says_so(root):
    run_install(root, FakeHost())
    (root / "etc/tlg-ipam-agent/notes").write_bytes(b"the operator's")
    host = FakeHost(user=True)
    said = []
    assert install.uninstall(host=host, paths=paths_in(root), out=said.append) == 1
    assert (root / "etc/tlg-ipam-agent/notes").read_bytes() == b"the operator's"
    assert any("not removed" in line and "tlg-ipam-agent" in line for line in said)
    # the rest is gone all the same
    assert not (root / "var/lib/tlg-ipam-agent").exists() and host.user is False


def test_uninstall_needs_root(root):
    run_install(root, FakeHost())
    before = tree(root)
    host = FakeHost(root=False, user=True)
    said = []
    assert install.uninstall(host=host, paths=paths_in(root), out=said.append) == 1
    assert "run it as root" in said[-1] and host.commands == [] and tree(root) == before


# --- the exchange against the server's real routes ---


@pytest.fixture
async def owner():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as session:
        await seed_defaults(session)
    enrolment.throttle.reset()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"X-API-Key": "test-key"}
    ) as client:
        yield client
    enrolment.throttle.reset()


def real_exchange(loop):
    """`install.exchange`'s part, against the app in process: the install runs in a
    thread, the request on the test's event loop, where the database engine lives."""

    async def once(token):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as http:
            return await enrol.exchange(http)

    return lambda url, token, ca: asyncio.run_coroutine_threadsafe(once(token), loop).result()


async def test_install_enrols_against_the_real_route(owner, root):
    created = (await owner.post("/api/v1/agents", json={"name": "lxc-agent"})).json()["data"]
    host = FakeHost()
    exchange = real_exchange(asyncio.get_running_loop())
    options = Options(url=URL, enrol_token="-")
    stdin = created["enrol_token"] + "\n"
    code, said = await asyncio.to_thread(run_install, root, host, options, exchange=exchange, stdin=stdin)
    assert code == 0, said
    stored = (root / "var/lib/tlg-ipam-agent/token").read_text()
    assert stored.startswith("tlga_")
    assert created["enrol_token"].encode() not in every_byte(root)
    async with SessionLocal() as session:
        assert (await session.get(AgentRow, created["id"])).state == "active"
    # the agent the unit starts reads it back
    assert enrol.stored_token(paths_in(root).state) == stored

    # a second host given the same, spent, token is refused and keeps nothing
    other = root / "other"
    for directory in ("etc/systemd/system", "var/lib"):
        (other / directory).mkdir(parents=True)
    host = FakeHost()
    code, said = await asyncio.to_thread(run_install, other, host, options, exchange=exchange, stdin=stdin)
    assert code == 1 and "HTTP 403 FORBIDDEN" in said[-1]
    assert tree(other) == {"etc", "etc/systemd", "etc/systemd/system", "var", "var/lib"}


def test_the_real_host_runs_fixed_programs_with_a_fixed_environment(monkeypatch):
    seen = {}

    class Done:
        returncode = 0

    def fake_run(argv, **kwargs):
        seen.update(argv=argv, **kwargs)
        return Done()

    monkeypatch.setattr(install.subprocess, "run", fake_run)
    assert install.Host().run("systemctl", "daemon-reload") == 0
    assert seen["argv"] == ["systemctl", "daemon-reload"]
    assert seen["env"] == install.COMMAND_ENV and "shell" not in seen
    assert seen["stdin"] is install.subprocess.DEVNULL


def test_a_token_read_from_a_terminal_is_not_echoed(monkeypatch):
    """`--enrol-token -` on a terminal: echo is off while the line is read, and back on
    after it."""
    import io
    import termios
    import threading
    import time

    leader, follower = os.openpty()
    stdin = io.TextIOWrapper(io.FileIO(follower, "r", closefd=False))
    monkeypatch.setattr(install.sys, "stdin", stdin)
    monkeypatch.setattr(install.sys, "stderr", io.StringIO())
    got = []
    reader = threading.Thread(target=lambda: got.append(install.read_token_line()), daemon=True)
    reader.start()
    deadline = time.monotonic() + 5
    while termios.tcgetattr(follower)[3] & termios.ECHO:
        assert time.monotonic() < deadline, "echo was never turned off"
        time.sleep(0.01)
    os.write(leader, ENROL_TOKEN.encode() + b"\n")
    reader.join(5)
    assert got == [ENROL_TOKEN + "\n"]
    assert termios.tcgetattr(follower)[3] & termios.ECHO
    assert "Enrolment token" in install.sys.stderr.getvalue()
    os.close(leader)
    os.close(follower)


def test_the_token_file_mode_is_the_store_s_and_owner_only():
    assert enrol.TOKEN_MODE == 0o600
    assert store.PENDING_SUFFIX == ".new"
