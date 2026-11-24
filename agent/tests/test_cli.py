"""The command line (#22): with no command the agent runs; `install` and `uninstall`
are the systemd install's."""

import pytest

from tlg_ipam_agent import cli, install, trust


@pytest.fixture(autouse=True)
def trusted(monkeypatch):
    """This checkout is not root's: the check itself is test_trust.py's."""
    monkeypatch.setattr(trust, "check", lambda *a, **kw: None)


def test_install_takes_its_options_and_passes_them_on(monkeypatch):
    seen = {}
    monkeypatch.setattr(install, "install", lambda options, **kw: seen.update(options=options, **kw) or 0)
    cli.main([
        "install", "--url", "https://ipam.home.arpa", "--enrol-token", "-",
        "--allowed-cidrs", "10.0.0.0/8", "--max-pps", "50", "--ca-file", "/etc/ca.pem",
    ])
    assert seen["options"] == install.Options(
        url="https://ipam.home.arpa",
        enrol_token="-",
        allowed_cidrs="10.0.0.0/8",
        max_pps="50",
        ca_file="/etc/ca.pem",
    )
    # each token the install learns is handed to the log redaction
    assert seen["secret"] is cli.configure_logging


def test_install_needs_the_url(capsys):
    with pytest.raises(SystemExit) as exited:
        cli.main(["install", "--enrol-token", "-"])
    assert exited.value.code == 2 and "--url" in capsys.readouterr().err


def test_uninstall_takes_no_option(monkeypatch):
    monkeypatch.setattr(install, "uninstall", lambda: 7)
    assert cli.main(["uninstall"]) == 7
    with pytest.raises(SystemExit):
        cli.main(["uninstall", "--url", "x"])


def test_with_no_command_it_runs_from_the_environment(monkeypatch, caplog):
    monkeypatch.setattr(cli.sys, "platform", "linux")
    monkeypatch.delenv("IPAM_URL", raising=False)
    assert cli.main([]) == 2
    assert "IPAM_URL is not set" in caplog.text


def test_the_parser_names_the_installs_own_paths():
    """`cli` writes the settings path out rather than import `install` to build its
    parser; it must stay the install's."""
    assert cli.ETC_ENV == f"{install.ETC_DIR}/{install.ENV_NAME}"


def test_a_refused_check_stops_the_command(monkeypatch, capsys):
    def refuse(*a, **kw):
        raise trust.Untrusted("/opt/x/lib")

    monkeypatch.setattr(trust, "check", refuse)
    monkeypatch.setattr(install, "uninstall", lambda: pytest.fail("must not run"))
    assert cli.main(["uninstall"]) == 1
    assert "uninstall refused: /opt/x/lib can be changed by a user other than root" in capsys.readouterr().err
