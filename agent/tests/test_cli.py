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
        "--allowed-cidrs", "10.0.0.0/8", "--allowed-dns-servers", "10.0.0.53", "--max-pps", "50",
        "--ca-file", "/etc/ca.pem",
    ])
    assert seen["options"] == install.Options(
        url="https://ipam.home.arpa",
        enrol_token="-",
        allowed_cidrs="10.0.0.0/8",
        allowed_dns_servers="10.0.0.53",
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


# --- #140: no parser error repeats what was typed ---

ENROL = "tlge_" + "Ab3-_" * 8 + "xyz"
AGENT = "tlga_" + "Zq9_-" * 8 + "XYZ"
URL = "https://ipam.home.arpa"


def _refused(argv, capsys) -> str:
    """The whole output of a command line the parser refuses."""
    with pytest.raises(SystemExit) as exited:
        cli.main(argv)
    assert exited.value.code == 2
    out, err = capsys.readouterr()
    return out + err


def _plain(monkeypatch):
    """The same command line parsed by argparse's own parser, as before #140."""
    monkeypatch.setattr(cli, "_Parser", cli.argparse.ArgumentParser)


@pytest.mark.parametrize("token", [ENROL, AGENT])
@pytest.mark.parametrize(
    "argv",
    [
        ["{t}"],  # the command
        ["{t}", "install", "--url", URL],
        ["install", "--url", URL, "{t}"],  # an extra positional
        ["install", "{t}", "--url", URL],
        ["install", "--url", URL, "--enrol-tokn", "{t}"],  # an unknown option and its value
        ["install", "--url", URL, "--enrol-tokn={t}"],
        ["install", "--url", URL, "--enrol-token", "-", "{t}"],  # more after -
        ["install", "--url", URL, "--enrol-token", "-", "--", "{t}"],
        ["install", "--url", URL, "-x{t}"],
        ["install", "--url", URL, "--help={t}"],  # a value for an option that takes none
        ["install", "--url", URL, "--max-pps", "5", "{t}", "--ca-file", "/etc/ca.pem"],
        ["uninstall", "{t}"],
        ["uninstall", "--enrol-token", "{t}"],
        ["uninstall", "--enrol-token", "-", "{t}"],
        ["--version={t}"],
        ["-x{t}"],
        ["--url={t}"],
    ],
)
def test_no_parser_error_repeats_a_token_wherever_it_was_typed(argv, token, monkeypatch, capsys):
    """Each position the parser rejects, with the token in it: argparse's own parser
    shows it there (so the case is one it repeats), and this one shows neither the token
    nor its secret part, and says what a typed token means."""
    argv = [a.replace("{t}", token) for a in argv]
    shown = _refused(argv, capsys)
    assert token[5:] not in shown
    assert cli.TYPED_TOKEN in shown and "error: " in shown
    _plain(monkeypatch)
    assert token in _refused(argv, capsys)


def test_an_ambiguous_option_with_a_value_is_not_repeated(capsys):
    """This command's options share no prefix, so argparse's ambiguous-option message,
    which quotes the option and its `=value`, is shown on a parser that has two."""
    parser = cli._Parser(prog="x")
    parser.add_argument("--enrol-a")
    parser.add_argument("--enrol-b")
    with pytest.raises(SystemExit):
        parser.parse_args([f"--enrol={ENROL}"])
    err = capsys.readouterr().err
    assert ENROL[5:] not in err
    assert "x: error: an option could be more than one of this command's (not repeated here)" in err


@pytest.mark.parametrize(
    "message, said",
    [
        # argparse's forms that quote what was typed, as each Python from 3.11 writes them
        (f"unrecognized arguments: {ENROL}", "unrecognized arguments (not repeated here)"),
        (f"ambiguous option: --e={ENROL} could match --ea, --eb", "an option could be more than one"),
        (
            f"argument {{install,uninstall}}: invalid choice: '{ENROL}', maybe you meant 'install'?",
            "argument {install,uninstall}: invalid choice (not repeated here); choose from install, uninstall",
        ),
        (f"argument --version: ignored explicit argument '{ENROL}'", "argument --version: takes no value"),
        # anything else is not repeated at all, whatever argument it names
        (f"argument --url: invalid str value: '{ENROL}'", "argument --url: not understood"),
        (f"argument {ENROL}: expected one argument", "the command line was not understood"),
        (f"the following arguments are required: {ENROL}", "the command line was not understood"),
        (f"the following arguments are required: --url, {ENROL}", "the command line was not understood"),
        (f"{ENROL}", "the command line was not understood"),
        ("", "the command line was not understood"),
    ],
)
def test_a_message_built_from_what_was_typed_is_said_again_without_it(message, said):
    parser = cli._parser()
    [commands] = [a for a in parser._actions if isinstance(a, cli.argparse._SubParsersAction)]
    names = {**parser._names(), **commands.choices["install"]._names()}
    again = cli._without_typed_text(message, names)
    assert said in again and ENROL[5:] not in again


@pytest.mark.parametrize(
    "argv",
    [
        ["install"],
        ["install", "--url"],
        ["install", "--url", URL, "--enrol-token"],
        ["install", "--url", URL, "--max-pps"],
        ["install", "--url", URL, "--ca-file"],
    ],
)
def test_a_message_made_of_the_parsers_own_names_is_argparses(argv, monkeypatch, capsys):
    """"--url is required" and "--url: expected one argument" quote nothing typed: they
    are kept, word for word, with TYPED_TOKEN after them."""
    ours = _refused(argv, capsys)
    _plain(monkeypatch)
    assert ours == _refused(argv, capsys) + cli.TYPED_TOKEN + "\n"


@pytest.mark.parametrize("command", [[], ["install"], ["uninstall"]])
def test_the_help_text_is_argparses(command, monkeypatch, capsys):
    def help_text():
        with pytest.raises(SystemExit) as exited:
            cli.main([*command, "--help"])
        assert exited.value.code == 0
        return capsys.readouterr()

    ours = help_text()
    _plain(monkeypatch)
    assert ours == help_text() and "usage: tlg-ipam-agent" in ours.out


@pytest.mark.parametrize(
    "argv",
    [
        ["install", "--url", URL],
        ["install", "--url", URL, "--enrol-token", "-", "--allowed-cidrs", "10.0.0.0/8 192.168.0.0/16"],
        ["install", "--url=" + URL, "--enrol", "-", "--max-pps", "50", "--ca-file", "/etc/ca.pem"],
        ["install", "--url", URL, "--enrol-token", ENROL],  # install's own refusal reads it
        ["uninstall"],
        [],
    ],
)
def test_a_correct_command_parses_as_it_did(argv, monkeypatch):
    ours = cli._parser().parse_args(argv)
    _plain(monkeypatch)
    assert ours == cli._parser().parse_args(argv)


def test_the_version_is_shown_as_it_was(capsys):
    with pytest.raises(SystemExit) as exited:
        cli.main(["--version"])
    assert exited.value.code == 0 and capsys.readouterr().out == f"{cli.__version__}\n"
