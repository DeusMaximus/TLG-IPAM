"""The command line (#22): with no command the agent runs; `install` and `uninstall`
are the systemd install's."""

import pytest

from conftest import ENROL_TOKEN
from tlg_ipam_agent import cli, install


def test_install_takes_its_options_and_passes_them_on(monkeypatch):
    seen = {}
    monkeypatch.setattr(install, "install", lambda options: seen.setdefault("options", options) and 0)
    cli.main([
        "install", "--url", "https://ipam.home.arpa", "--enrol-token", ENROL_TOKEN,
        "--allowed-cidrs", "10.0.0.0/8", "--max-pps", "50", "--ca-file", "/etc/ca.pem",
    ])
    assert seen["options"] == install.Options(
        url="https://ipam.home.arpa",
        enrol_token=ENROL_TOKEN,
        allowed_cidrs="10.0.0.0/8",
        max_pps="50",
        ca_file="/etc/ca.pem",
    )


def test_install_needs_the_url(capsys):
    with pytest.raises(SystemExit) as exited:
        cli.main(["install", "--enrol-token", ENROL_TOKEN])
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
