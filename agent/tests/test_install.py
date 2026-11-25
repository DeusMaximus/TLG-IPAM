"""`tlg-ipam-agent install` and `uninstall` (#22), in a directory of the test's own.

The host is a fake: it records the programs the install would run and plays the user
database, and its service user is the test's own user, so the token file's owner can
be checked without root. The files are real, written by `store`, under a temporary
root. The enrolment exchange is made against the server's real routes, in process.

What this cannot show (a real `useradd`, a real systemd, the file's owner as another
user) was shown in Linux containers; the PR records how. The CA bundle's check
(`install.read_as`) runs its real programs here, as the test's own user, since without
root a process can take on no other user's ids or groups; the cases that need root (an
ACL, a supplementary group, another user's ids) run only as root on Linux, which the
PR of #136 records."""

import asyncio
import os
import stat
import struct
import subprocess
import sys
from pathlib import Path
from unittest import mock

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


def as_the_test_user(asked: list):
    """`subprocess.run` for `read_as`'s programs, run as the test's own user: what
    `read_as` asked for is recorded, and its `user`, `group` and `extra_groups` taken
    off, since without root a process can set none of them (setgroups needs
    CAP_SETGID even for the groups it holds)."""
    real = subprocess.run

    def run(argv, **keywords):
        asked.append((argv, dict(keywords)))
        for name in ("user", "group", "extra_groups"):
            keywords.pop(name)
        return real(argv, **keywords)

    return run


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
        # what the CA bundle's check answers: None runs the real `read_as`, as the
        # test's own user; a string is that answer
        self.reads = None
        self.read_checks: list[tuple] = []

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

    def service_reads(self, path, uid, gid):
        assert (uid, gid) == self.ids  # asked for the service user found or created
        self.read_checks.append((path, uid, gid))
        if self.reads is not None:
            return self.reads
        asked = []
        with mock.patch.object(install.subprocess, "run", as_the_test_user(asked)):
            return install.read_as(path, uid, gid, [gid])

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


def run_install(root, host, options=None, *, exchange=None, stdin=ENROL_TOKEN + "\n", out=None, environ=None, secret=None):
    said = [] if out is None else out
    extra = {"environ": {} if environ is None else environ}
    if secret is not None:
        extra["secret"] = secret
    code = install.install(
        options or Options(url=URL, enrol_token="-"),
        host=host,
        paths=paths_in(root),
        exchange=exchange or (lambda url, token, ca: ANSWER),
        read_stdin=lambda: stdin,
        interpreter=PYTHON,
        out=said.append,
        **extra,
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


def unit_environment(unit: str, manager: dict[str, str]) -> dict[str, str]:
    """The environment systemd gives the unit's process, as systemd 257 was measured to
    build it on Debian 13 (#138; the PR records the runs): the manager's environment,
    then each `EnvironmentFile=`'s assignments over it, then every name the
    `UnsetEnvironment=` lines list removed, after all of those and whatever the order
    of the lines (an empty `UnsetEnvironment=` empties the list). A stand-in for
    systemd: the unit's own text decides the outcome, so a unit that stops discarding a
    setting fails here."""
    env = dict(manager)
    unset: list[str] = []
    for line in unit.splitlines():
        key, _, value = line.partition("=")
        assert key != "Environment", "the model does not order Environment= against EnvironmentFile="
        if key == "EnvironmentFile":
            for assignment in Path(value.removeprefix("-")).read_text().splitlines():
                if assignment and not assignment.startswith("#"):
                    name, _, setting = assignment.partition("=")
                    env[name] = setting
        elif key == "UnsetEnvironment":
            unset = unset + value.split() if value else []
    for name in unset:
        env.pop(name, None)
    return env


MANAGER_TRUST = [
    {},
    {"SSL_CERT_FILE": "/nonexistent-manager-file"},
    {"SSL_CERT_DIR": "/nonexistent-manager-dir"},
    {"SSL_CERT_FILE": "/nonexistent-manager-file", "SSL_CERT_DIR": "/nonexistent-manager-dir"},
]


@pytest.mark.parametrize("manager", MANAGER_TRUST, ids=["none", "file", "dir", "both"])
@pytest.mark.parametrize("ca", [False, True], ids=["no-ca-file", "ca-file"])
def test_the_unit_discards_the_trust_settings_of_the_system_managers_environment(root, manager, ca):
    """#138, the third round on #130: a unit also receives the system manager's
    environment (`systemctl set-environment`), which the installer never sees. With
    `SSL_CERT_FILE` set there and no `--ca-file`, install spent the token and the unit
    could not verify the server. The unit is given agent.env's trust and nothing else:
    `SSL_CERT_DIR` is always unset, `SSL_CERT_FILE` unless `--ca-file` sets it, and
    then agent.env's value is the one the agent sees."""
    bundle = root / "ca.pem"
    bundle.write_bytes(b"-----BEGIN CERTIFICATE-----\n")
    ca_file = str(bundle) if ca else None
    code, said = run_install(root, FakeHost(), Options(url=URL, enrol_token="-", ca_file=ca_file))
    assert code == 0, said
    unit = (root / "etc/systemd/system/tlg-ipam-agent.service").read_text()
    lines = unit.splitlines()
    discarded = "SSL_CERT_DIR" if ca else "SSL_CERT_FILE SSL_CERT_DIR"
    assert [line for line in lines if line.startswith("UnsetEnvironment")] == [f"UnsetEnvironment={discarded}"]
    env = unit_environment(unit, {"PATH": "/usr/bin", **manager})
    assert {name: env[name] for name in install.TRUST_VARIABLES if name in env} == install.service_trust(ca_file)
    assert env["PATH"] == "/usr/bin"  # nothing else of the manager's is dropped


def test_the_unit_discards_exactly_the_trust_settings_agent_env_does_not_give():
    for ca_file in (None, "/etc/ipam-ca.pem"):
        given = install.service_trust(ca_file)
        discarded = install.discarded_trust(ca_file)
        assert set(given) | set(discarded) == set(install.TRUST_VARIABLES)
        assert not set(given) & set(discarded)
        unit = install.render_unit("/etc/tlg-ipam-agent/agent.env", PYTHON, ca_file)
        assert f"\nUnsetEnvironment={' '.join(discarded)}\n" in unit
        # the line holds fixed names alone: the bundle's path is agent.env's, not the unit's
        assert ca_file is None or ca_file not in unit


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
        (Options(url=URL, enrol_token="-", ca_file="/no/such/ca.pem"), "--ca-file: there is no file at the path given"),
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
    assert "treat it as exposed: re-enrol the agent" in said[-1]
    assert ENROL_TOKEN not in said[-1] and read == [] and host.commands == []


@pytest.mark.parametrize(
    "inherited, ca, refused",
    [
        ({"SSL_CERT_FILE": "/etc/root-ca.pem"}, False, "SSL_CERT_FILE"),  # the reviewer's case
        ({"SSL_CERT_DIR": "/etc/root-cas"}, False, "SSL_CERT_DIR"),
        ({"SSL_CERT_DIR": "/etc/root-cas"}, True, "SSL_CERT_DIR"),  # a directory beside --ca-file
        ({"SSL_CERT_FILE": "/etc/root-ca.pem"}, True, "SSL_CERT_FILE"),  # another file than --ca-file
        ({"SSL_CERT_FILE": "CA"}, True, None),  # the --ca-file itself
        ({"SSL_CERT_FILE": ""}, False, None),  # set but empty names nothing
        ({}, False, None),
    ],
)
def test_trust_inherited_from_roots_environment_is_refused_before_anything_changes(root, inherited, ca, refused):
    """Round 2 of the review of #130: the exchange trusted what root's environment
    named, the unit was given nothing, and the token was spent on an agent that could
    not verify its server. Only what the unit is given may be trusted."""
    bundle = root / "ca.pem"
    bundle.write_bytes(b"-----BEGIN CERTIFICATE-----\n")
    environ = {name: (str(bundle) if value == "CA" else value) for name, value in inherited.items()}
    host = FakeHost()
    before = tree(root)
    asked = []
    code, said = run_install(
        root, host, Options(url=URL, enrol_token="-", ca_file=str(bundle) if ca else None),
        exchange=lambda *a: asked.append(a) or ANSWER, environ=environ,
    )
    if refused:
        assert code == 1 and f"{refused} is set in this environment" in said[-1] and "--ca-file" in said[-1]
        assert asked == [] and host.commands == [] and tree(root) == before
    else:
        assert code == 0, said


def test_the_exchange_is_made_with_exactly_the_trust_the_unit_is_given(root, monkeypatch):
    """What `install.exchange` hands the TLS context (OpenSSL reads both variables when
    it is made) is what agent.env gives the unit, whatever the installer's own
    environment held."""
    bundle = root / "ca.pem"
    bundle.write_bytes(b"")
    for name in install.TRUST_VARIABLES:
        monkeypatch.setenv(name, "/inherited")
    seen = []

    class Http:
        def __init__(self, url, token):
            seen.append({name: os.environ.get(name) for name in install.TRUST_VARIABLES})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    async def exchange(http):
        return ANSWER

    monkeypatch.setattr(install, "make_http", Http)
    monkeypatch.setattr(install.enrol, "exchange", exchange)
    for ca in (str(bundle), None):
        install.exchange(URL, ENROL_TOKEN, ca)
        env = install.render_env(URL, None, None, ca, "/var/lib/tlg-ipam-agent/token")
        given = dict(line.split("=", 1) for line in env.splitlines() if line.startswith("SSL_"))
        assert seen[-1] == {name: given.get(name) for name in install.TRUST_VARIABLES}


def test_install_learns_each_token_for_the_log_redaction(root):
    learnt = []
    code, _ = run_install(root, FakeHost(), secret=learnt.append)
    assert code == 0 and learnt == [ENROL_TOKEN, TOKEN]


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a file of mode 0000")
def test_a_ca_file_the_service_user_cannot_read_stops_the_install_before_the_exchange(root):
    """The review of #130: root could read the bundle and the service user could not,
    and the install spent the token on a unit that could not verify the server. The
    file is checked as the service user (here, the test's own user, through the real
    programs), after the account exists, before the exchange, and the install is
    taken back."""
    ca = root / "ca.pem"
    ca.write_bytes(b"-----BEGIN CERTIFICATE-----\n")
    ca.chmod(0o000)
    host = FakeHost()
    before = tree(root)
    code, said = run_install(
        root, host, Options(url=URL, enrol_token="-", ca_file=str(ca)), exchange=lambda *a: pytest.fail("no exchange")
    )
    assert code == 1 and "could not read the file (stopped at the file itself)" in said[-1], said
    assert str(ca) not in said[-1] and ca.name not in said[-1]  # #140: no path is repeated
    assert tree(root) == before and host.commands == [USERADD, ("userdel", "tlg-ipam-agent")]
    assert host.read_checks == [(str(ca), *host.ids)]


def test_a_ca_file_is_checked_on_an_update_too(root):
    """Run again without `--enrol-token`, the bundle is checked all the same, and a
    refusal leaves the stored token and the files as they were."""
    ca = root / "ca.pem"
    ca.write_bytes(b"-----BEGIN CERTIFICATE-----\n")
    assert run_install(root, FakeHost(), Options(url=URL, enrol_token="-", ca_file=str(ca)))[0] == 0
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    host = FakeHost(user=True)
    host.reads = install.AT_THE_FILE
    code, said = run_install(root, host, Options(url=URL, ca_file=str(ca)), exchange=lambda *a: pytest.fail("no exchange"))
    assert code == 1 and "could not read the file" in said[-1], said
    assert {path: path.read_bytes() for path in root.rglob("*") if path.is_file()} == before
    assert host.commands == []


@pytest.mark.parametrize("where", [install.AT_THE_FILE, install.ON_THE_WAY])
def test_a_ca_file_the_service_user_cannot_read_is_placed_without_its_path(root, where):
    """The refusal says where the check stopped by its kind, never by the path (#140)."""
    real = root / "real.pem"
    real.write_bytes(b"-----BEGIN CERTIFICATE-----\n")
    ca = root / "ca.pem"
    ca.symlink_to(real)
    host = FakeHost()
    host.reads = where
    code, said = run_install(
        root, host, Options(url=URL, enrol_token="-", ca_file=str(ca)), exchange=lambda *a: pytest.fail("no exchange")
    )
    assert code == 1 and f"could not read the file (stopped at {where})" in said[-1], said
    assert "getfacl" in said[-1] and "namei -l" in said[-1]
    assert str(root) not in said[-1] and "pem" not in said[-1]


def test_a_ca_file_whose_check_could_not_be_made_stops_the_install_before_the_exchange(root):
    """A program that could not be run as the service user, or that did not finish in
    time, is not taken for a readable file: the install stops with the token unspent."""
    ca = root / "ca.pem"
    ca.write_bytes(b"-----BEGIN CERTIFICATE-----\n")
    host = FakeHost()
    host.reads = install.NOT_CHECKED
    before = tree(root)
    code, said = run_install(
        root, host, Options(url=URL, enrol_token="-", ca_file=str(ca)), exchange=lambda *a: pytest.fail("no exchange")
    )
    assert code == 1 and "could not check that tlg-ipam-agent" in said[-1] and "before the token was spent" in said[-1]
    assert str(root) not in said[-1] and "pem" not in said[-1]
    assert tree(root) == before


def _misplaced(token):
    """A token typed as the value of each of install's options that takes one, in each
    form a slip of order or of paste would give it (#140)."""
    return [
        {"url": token},
        {"url": f"https://{token}"},
        {"url": f"{URL}/{token}"},
        {"url": f"{URL}?{token}"},
        {"url": f" {token}"},
        {"allowed_cidrs": token},
        {"allowed_cidrs": f"10.0.0.0/8,{token}"},
        {"allowed_cidrs": f"10.0.0.0/8 192.168.0.0/16 {token} 100.64.0.0/10"},
        {"max_pps": token},
        {"ca_file": token},
        {"ca_file": f"/{token}"},
        {"ca_file": f"/etc/ssl/{token}"},
        {"ca_file": f"/etc/ssl/{token}.pem"},
        {"enrol_token": token},
    ]


@pytest.mark.parametrize("token", [ENROL_TOKEN, TOKEN])
@pytest.mark.parametrize("place", range(len(_misplaced(TOKEN))))
def test_a_token_given_as_any_options_value_is_in_no_output(root, capsys, token, place):
    """Through install's real validation, a fake host and an exchange that does not
    enrol: whatever the outcome, neither what install says nor what it prints holds
    the token or its secret part. A `--url` with the token as its host gets as far as
    the exchange, and is in agent.env as the URL given, which is no output."""
    options = Options(**{"url": URL, "enrol_token": "-", **_misplaced(token)[place]})
    refused = Answer(Kind.RETRY, None, None, "no answer (ConnectError)")
    code, said = run_install(root, FakeHost(), options, exchange=lambda *a: refused)
    out, err = capsys.readouterr()
    assert code == 1 and said
    for shown in (*said, out, err):
        assert token[5:] not in shown, shown


# --- the CA bundle's check, `read_as`, as the test's own user ---


def _read_as_me(path, asked=None):
    asked = [] if asked is None else asked
    with mock.patch.object(install.subprocess, "run", as_the_test_user(asked)):
        return install.read_as(str(path), os.getuid(), os.getgid(), [os.getgid(), 4242])


def test_the_check_runs_its_fixed_programs_as_the_service_user_and_nothing_else(tmp_path):
    """`read_as` asks for the service user's ids and every group it is given, the
    install's fixed environment, `/` as the working directory, no input, its output
    discarded (`head`'s errors name the path), a deadline and no shell; the arguments
    are the fixed programs' and the path."""
    hidden = tmp_path / "hidden"
    hidden.mkdir()
    asked = []
    assert _read_as_me(hidden / "ca.pem", asked) == install.ON_THE_WAY
    path = str(hidden / "ca.pem")
    assert [argv for argv, _ in asked] == [["head", "-c", "1", "--", path], ["test", "-e", path]]
    for _, keywords in asked:
        assert keywords == {
            "env": install.COMMAND_ENV,
            "cwd": "/",
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "user": os.getuid(),
            "group": os.getgid(),
            "extra_groups": [os.getgid(), 4242],
            "timeout": install.CA_CHECK_TIMEOUT_S,
            "check": False,
        }


def test_a_readable_file_is_read_once_and_nothing_more_is_run(tmp_path):
    ca = tmp_path / "ca.pem"
    ca.write_bytes(b"-----BEGIN CERTIFICATE-----\n")
    asked = []
    assert _read_as_me(ca, asked) is None
    assert [argv[0] for argv, _ in asked] == ["head"]
    # an empty file is readable too: what it holds is the TLS handshake's to judge
    ca.write_bytes(b"")
    assert _read_as_me(ca) is None


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads and enters whatever the mode bits say")
def test_the_check_says_where_it_stopped_by_what_the_kernel_answered(tmp_path):
    """The file itself when the user reaches it and cannot read it; on the way when it
    cannot reach it (a directory it cannot enter, followed through a link too, or
    nothing there)."""
    tmp_path.chmod(0o755)
    hidden = tmp_path / "private"
    hidden.mkdir()
    ca = hidden / "ca.pem"
    ca.write_bytes(b"-----BEGIN CERTIFICATE-----\n")
    (tmp_path / "ca-link.pem").symlink_to(ca)
    ca.chmod(0o000)
    assert _read_as_me(ca) == install.AT_THE_FILE
    assert _read_as_me(tmp_path / "ca-link.pem") == install.AT_THE_FILE
    ca.chmod(0o644)
    hidden.chmod(0o600)  # listable, not enterable
    try:
        assert _read_as_me(ca) == install.ON_THE_WAY
        assert _read_as_me(tmp_path / "ca-link.pem") == install.ON_THE_WAY
    finally:
        hidden.chmod(0o755)
    assert _read_as_me(ca) is None
    assert _read_as_me(tmp_path / "missing.pem") == install.ON_THE_WAY
    (tmp_path / "dangling.pem").symlink_to(tmp_path / "missing.pem")
    assert _read_as_me(tmp_path / "dangling.pem") == install.ON_THE_WAY


def test_a_fifo_put_in_the_files_place_is_cut_off_and_not_taken_for_a_file(tmp_path, monkeypatch):
    """A FIFO swapped in after `_ca_file` looked: `head` waits on a writer that never
    comes, is killed at the deadline, and the check is not made."""
    fifo = tmp_path / "ca.pem"
    os.mkfifo(fifo)
    monkeypatch.setattr(install, "CA_CHECK_TIMEOUT_S", 0.5)
    assert _read_as_me(fifo) == install.NOT_CHECKED


def test_programs_that_cannot_be_run_are_no_answer(tmp_path, monkeypatch):
    """A PATH that holds neither program, and a start the kernel refuses (here, as
    setgroups refuses one without root), give NOT_CHECKED, never a readable file."""
    ca = tmp_path / "ca.pem"
    ca.write_bytes(b"x")
    monkeypatch.setattr(install, "COMMAND_ENV", {"PATH": str(tmp_path / "empty"), "LC_ALL": "C"})
    assert _read_as_me(ca) == install.NOT_CHECKED

    def refused(argv, **keywords):
        raise PermissionError(1, "Operation not permitted")

    with mock.patch.object(install.subprocess, "run", refused):
        assert install.read_as(str(ca), 1, 1, [1]) == install.NOT_CHECKED


@pytest.mark.parametrize("head, reach, answer", [
    (0, None, None), (1, 0, install.AT_THE_FILE), (1, 1, install.ON_THE_WAY),
    (1, 2, install.NOT_CHECKED),  # `test` itself failed
    (-9, 0, install.AT_THE_FILE),  # head killed by a signal, the file there
])
def test_exit_statuses_are_read_as_listed(head, reach, answer):
    codes = iter([head, reach])

    def run(argv, **keywords):
        return subprocess.CompletedProcess(argv, next(codes))

    with mock.patch.object(install.subprocess, "run", run):
        assert install.read_as("/ca.pem", 1, 1, [1]) == answer


def test_the_real_host_checks_with_the_accounts_groups_and_prints_no_path(monkeypatch, capsys):
    """`Host.service_reads` gives `read_as` the groups initgroups(3) would give the
    unit's process, and says what it runs without the path (#140)."""
    seen = []
    monkeypatch.setattr(install.os, "getgrouplist", lambda name, gid: seen.append((name, gid)) or [gid, 27, 100])
    monkeypatch.setattr(install, "read_as", lambda *a: seen.append(a) or None)
    assert install.Host().service_reads("/etc/ssl/private-ca.pem", 999, 998) is None
    assert seen == [("tlg-ipam-agent", 998), ("/etc/ssl/private-ca.pem", 999, 998, [998, 27, 100])]
    out = capsys.readouterr().out
    assert out == "+ head -c 1 -- <--ca-file>, as tlg-ipam-agent\n"


# --- as root, on Linux: what only the kernel decides (#136) ---

as_root = pytest.mark.skipif(
    not sys.platform.startswith("linux") or os.geteuid() != 0, reason="takes on another user's ids: root on Linux"
)
NOBODY = 65534


@pytest.fixture
def shared(tmp_path):
    """A directory every user can enter, with a CA bundle in it, root's."""
    for directory in (tmp_path, *tmp_path.parents):
        assert os.stat(directory).st_mode & stat.S_IXOTH, "the test's directory must be reachable by others"
    ca = tmp_path / "ca.pem"
    ca.write_bytes(b"-----BEGIN CERTIFICATE-----\n")
    os.chown(ca, 0, 0)
    return ca


@as_root
def test_as_root_the_mode_bits_the_service_user_gets_are_the_kernels(shared):
    shared.chmod(0o644)
    assert install.read_as(str(shared), NOBODY, NOBODY, [NOBODY]) is None
    shared.chmod(0o600)
    assert install.read_as(str(shared), NOBODY, NOBODY, [NOBODY]) == install.AT_THE_FILE
    shared.parent.chmod(0o700)
    try:
        assert install.read_as(str(shared), NOBODY, NOBODY, [NOBODY]) == install.ON_THE_WAY
    finally:
        shared.parent.chmod(0o755)


@as_root
def test_as_root_a_supplementary_group_that_grants_the_file_is_honoured(shared):
    """The reviewer's second reproduction: a bundle of mode 0640 whose group the
    account holds as a supplementary group, not as its primary one."""
    group = 4242
    os.chown(shared, 0, group)
    shared.chmod(0o640)
    assert install.read_as(str(shared), NOBODY, NOBODY, [NOBODY, group]) is None
    assert install.read_as(str(shared), NOBODY, NOBODY, [NOBODY]) == install.AT_THE_FILE


def deny_by_acl(path, uid):
    """What `setfacl -m u:<uid>:--- <path>` does to a file of mode 0644: it writes the
    POSIX ACL as the `system.posix_acl_access` attribute (version 2, then each entry
    as tag, permission and id, sorted by tag; the mask is the group's bits, so the mode
    stays 0644). Written here so that no image needs the acl package."""
    unset = 0xFFFFFFFF
    entries = [(0x01, 6, unset), (0x02, 0, uid), (0x04, 4, unset), (0x10, 4, unset), (0x20, 4, unset)]
    blob = struct.pack("<I", 2) + b"".join(struct.pack("<HHI", *entry) for entry in entries)
    try:
        os.setxattr(path, "system.posix_acl_access", blob)
    except OSError as exc:
        pytest.skip(f"the filesystem takes no POSIX ACL ({exc.strerror})")


@as_root
def test_as_root_an_acl_that_denies_the_account_is_honoured(shared):
    """The reviewer's first reproduction: mode 0644, and an ACL entry for the account
    with no permission."""
    shared.chmod(0o644)
    deny_by_acl(shared, NOBODY)
    assert stat.S_IMODE(os.stat(shared).st_mode) == 0o644
    assert install.read_as(str(shared), NOBODY, NOBODY, [NOBODY]) == install.AT_THE_FILE
    assert install.read_as(str(shared), NOBODY - 1, NOBODY, [NOBODY]) is None  # another user: the bits


@as_root
def test_as_root_the_programs_hold_none_of_roots_groups(shared):
    """A file only root's group may read: the check's programs keep none of the
    installer's groups, whatever groups root holds."""
    os.chown(shared, 0, 0)
    shared.chmod(0o640)
    assert install.read_as(str(shared), NOBODY, NOBODY, [NOBODY]) == install.AT_THE_FILE


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


def test_an_account_with_the_recorded_uid_and_another_group_is_left(root):
    """Codex on #130: the record is the uid and the primary gid together."""
    host = FakeHost()
    run_install(root, host)
    record = root / "etc/tlg-ipam-agent/created-user"
    record.write_text(f"{os.getuid()}:{os.getgid() + 1}\n")
    host.commands.clear()
    said = []
    assert install.uninstall(host=host, paths=paths_in(root), out=said.append) == 0
    assert not any(c[0] in ("userdel", "groupdel") for c in host.commands) and host.user is True
    assert any("left the tlg-ipam-agent account in place" in line for line in said)


def test_an_uninstall_whose_userdel_failed_can_be_run_again(root):
    """Round 2 of the review of #130: the record went before `userdel`, which failed
    (the account busy), and the next uninstall left the account as not the install's.
    The record now stays until the account and group are gone."""
    host = FakeHost()
    run_install(root, host)
    record = root / "etc/tlg-ipam-agent/created-user"
    host.fail = {"userdel": 8}
    said = []
    assert install.uninstall(host=host, paths=paths_in(root), out=said.append) == 1
    assert host.user is True and record.exists()
    assert any("exited with 8" in line for line in said)
    assert tree(root) == {
        "etc", "etc/systemd", "etc/systemd/system", "var", "var/lib",
        "etc/tlg-ipam-agent", "etc/tlg-ipam-agent/created-user",
    }
    host.fail = {}
    host.commands.clear()
    assert install.uninstall(host=host, paths=paths_in(root), out=lambda s: None) == 0
    assert host.commands == [("userdel", "tlg-ipam-agent")]
    assert host.user is False and host.group is None
    assert tree(root) == {"etc", "etc/systemd", "etc/systemd/system", "var", "var/lib"}


def test_a_retry_removes_the_recorded_group_the_user_left(root):
    host = FakeHost(group_stays=True)
    run_install(root, host)
    host.fail = {"groupdel": 6}
    assert install.uninstall(host=host, paths=paths_in(root), out=lambda s: None) == 1
    assert host.user is False and host.group == os.getgid()
    assert (root / "etc/tlg-ipam-agent/created-user").exists()
    host.fail = {}
    host.commands.clear()
    assert install.uninstall(host=host, paths=paths_in(root), out=lambda s: None) == 0
    assert host.commands == [("groupdel", "tlg-ipam-agent")] and host.group is None
    assert tree(root) == {"etc", "etc/systemd", "etc/systemd/system", "var", "var/lib"}


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


# --- live TLS: the exchange and the unit trust the same CA (round 2 of #130) ---

from test_tls import pki  # noqa: E402,F401  (the private CA and a certificate for 127.0.0.1)


class TlsServer:
    """An HTTPS server on 127.0.0.1, in a thread of its own, serving the enrol and
    check-in routes as the server answers them. `seen` is every request it read."""

    def __init__(self, cert_and_key):
        import json
        import ssl
        import threading

        self.seen: list[tuple[str, str]] = []
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(*cert_and_key)
        self.loop = asyncio.new_event_loop()
        ready = threading.Event()

        async def handle(reader, writer):
            try:
                head = await reader.readuntil(b"\r\n\r\n")
                lines = head.decode().split("\r\n")
                length = next((int(l.split(":")[1]) for l in lines if l.lower().startswith("content-length")), 0)
                await reader.readexactly(length)
                path = lines[0].split()[1]
                self.seen.append((path, next((l for l in lines if l.lower().startswith("authorization")), "")))
                data = (
                    {"agent_id": "0b6f3c1e-1111-4222-8333-444455556666", "agent_token": TOKEN}
                    if path.endswith("/enrol")
                    else {"checkin_interval_s": 60, "jobs": []}
                )
                body = json.dumps({"data": data}).encode()
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n"
                    + f"Content-Length: {len(body)}\r\n\r\n".encode() + body
                )
                await writer.drain()
            except (ssl.SSLError, asyncio.IncompleteReadError, ConnectionError):
                pass  # a client that did not trust the certificate
            finally:
                writer.close()

        async def start():
            self.server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=ctx)
            self.port = self.server.sockets[0].getsockname()[1]
            ready.set()

        self.thread = threading.Thread(target=lambda: (self.loop.run_until_complete(start()), self.loop.run_forever()), daemon=True)
        self.thread.start()
        ready.wait(5)

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)


@pytest.fixture
def tls_server(pki):
    server = TlsServer(pki["loopback"])
    yield server
    server.close()


def _as_the_service(root: Path, monkeypatch, manager=None) -> Kind:
    """A check-in made the way the unit's agent makes it: with the environment the
    unit gives it (`unit_environment`: nothing of root's, the system manager's
    `manager` with the unit's own settings applied), and the token the install stored."""
    from tlg_ipam_agent.client import AgentClient, make_http

    unit = (root / "etc/systemd/system/tlg-ipam-agent.service").read_text()
    env = unit_environment(unit, manager or {})
    for name in install.TRUST_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    for name in install.TRUST_VARIABLES:
        if name in env:
            monkeypatch.setenv(name, env[name])
    token = (root / "var/lib/tlg-ipam-agent/token").read_text()

    async def checkin():
        async with make_http(env["IPAM_URL"], token) as http:
            return await AgentClient(http).checkin({})

    return asyncio.run(checkin()).kind


def _live(root, tls_server, monkeypatch, *, ca=None, inherited=None):
    for name in install.TRUST_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    for name, value in (inherited or {}).items():
        monkeypatch.setenv(name, value)
    said = []
    code = install.install(
        Options(url=f"https://127.0.0.1:{tls_server.port}", enrol_token="-", ca_file=ca),
        host=FakeHost(),
        paths=paths_in(root),
        read_stdin=lambda: ENROL_TOKEN + "\n",
        interpreter=PYTHON,
        out=said.append,
        environ=os.environ,
    )
    return code, said


def test_live_tls_an_install_with_ca_file_gives_the_unit_the_trust_it_enrolled_with(root, tls_server, pki, monkeypatch):
    code, said = _live(root, tls_server, monkeypatch, ca=str(pki["ca"]))
    assert code == 0, said
    assert [path for path, _ in tls_server.seen] == ["/api/v1/agent/enrol"]
    assert tls_server.seen[0][1].lower() == f"authorization: bearer {ENROL_TOKEN}".lower()
    assert _as_the_service(root, monkeypatch) is Kind.OK
    assert [path for path, _ in tls_server.seen][-1] == "/api/v1/agent/checkin"


@pytest.mark.parametrize("manager", MANAGER_TRUST[1:], ids=["file", "dir", "both"])
def test_live_tls_with_ca_file_the_unit_trusts_it_whatever_the_manager_sets(root, tls_server, pki, monkeypatch, manager):
    """#138, acceptance 2: the system manager's environment names a bundle and a
    directory that do not exist; the unit's agent trusts the `--ca-file` and checks in."""
    code, said = _live(root, tls_server, monkeypatch, ca=str(pki["ca"]))
    assert code == 0, said
    assert _as_the_service(root, monkeypatch, manager) is Kind.OK
    assert [path for path, _ in tls_server.seen] == ["/api/v1/agent/enrol", "/api/v1/agent/checkin"]


@pytest.mark.parametrize("name", ["SSL_CERT_FILE", "SSL_CERT_DIR"])
def test_live_tls_trust_only_root_inherited_spends_nothing(root, tls_server, pki, monkeypatch, name):
    """The reviewer's reproduction: the CA named only in root's environment. Before the
    fix the exchange trusted it, enrolled, and the unit could not verify the server.
    Now nothing reaches the server and nothing is written."""
    value = str(pki["ca"]) if name == "SSL_CERT_FILE" else str(pki["ca"].parent)
    before = tree(root)
    code, said = _live(root, tls_server, monkeypatch, inherited={name: value})
    assert code == 1 and f"{name} is set in this environment" in said[-1], said
    assert tls_server.seen == [] and tree(root) == before


def test_live_tls_with_no_trust_for_the_server_the_exchange_fails_before_any_request(root, tls_server, monkeypatch):
    """Neither the exchange nor the unit trusts the private CA: the TLS handshake fails,
    no request is sent, the token is not spent and the install is taken back."""
    before = tree(root)
    code, said = _live(root, tls_server, monkeypatch)
    assert code == 1 and "no answer" in said[-1], said
    assert tls_server.seen == [] and tree(root) == before


@pytest.mark.parametrize("token", [ENROL_TOKEN, TOKEN])
def test_a_ca_file_that_is_not_a_regular_file_is_refused_without_its_path(root, token):
    """A directory named by the token, so the path is there but is no file (#140)."""
    there = root / token
    there.mkdir()
    code, said = run_install(
        root, FakeHost(), Options(url=URL, enrol_token="-", ca_file=str(there)), exchange=lambda *a: pytest.fail("no exchange")
    )
    assert code == 1 and "--ca-file: the path given is not a regular file" in said[-1], said
    assert token[5:] not in said[-1]
