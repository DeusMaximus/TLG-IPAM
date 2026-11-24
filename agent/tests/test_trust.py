"""`trust.check`: the code `install` and `uninstall` would run as root is root's alone
(#22; finding 1 of the review of #130).

Each layout is built in the test's own directory, whose owner stands in for root
(`owner`), and `top` keeps the directories above it out of the judgement."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tlg_ipam_agent import trust

SRC = Path(__file__).resolve().parents[1] / "src"


def _venv(root: Path) -> dict:
    """A virtual environment's layout: `bin/python3` a link to an interpreter outside
    it, the agent's package, a dependency, a `.pth` file, and the standard library."""
    os.chmod(root, 0o755)
    (root / "usr/bin").mkdir(parents=True)
    (root / "usr/bin/python3.13").write_bytes(b"")
    stdlib = root / "usr/lib/python3.13"
    stdlib.mkdir(parents=True)
    (stdlib / "os.py").write_bytes(b"")
    venv = root / "opt/tlg-ipam-agent"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin/python3").symlink_to(root / "usr/bin/python3.13")
    site = venv / "lib/python3.13/site-packages"
    (site / "tlg_ipam_agent").mkdir(parents=True)
    (site / "tlg_ipam_agent/cli.py").write_bytes(b"")
    (site / "icmplib").mkdir()
    (site / "icmplib/__init__.py").write_bytes(b"")
    (site / "distutils-precedence.pth").write_bytes(b"import os\n")
    for path in (root, *root.rglob("*")):
        if not path.is_symlink():
            path.chmod(0o755 if path.is_dir() else 0o644)
    return {
        "interpreter": str(venv / "bin/python3"),
        "search": [str(venv / "bin"), str(stdlib), str(root / "usr/lib/python313.zip"), str(site)],
    }


def _package(root: Path) -> str:
    return str(root / "opt/tlg-ipam-agent/lib/python3.13/site-packages/tlg_ipam_agent")


def _check(root: Path, layout: dict, **kw) -> None:
    trust.check(layout["interpreter"], layout["search"], package=_package(root), owner=kw.get("owner", os.getuid()), top=str(root))


def _refused(root: Path, layout: dict) -> str:
    with pytest.raises(trust.Untrusted) as refused:
        _check(root, layout)
    return str(refused.value)


def test_a_venv_only_its_owner_can_change_passes(tmp_path):
    _check(tmp_path, _venv(tmp_path))


@pytest.mark.parametrize(
    "path",
    [
        # the reviewer's first reproduction: one dependency file
        "opt/tlg-ipam-agent/lib/python3.13/site-packages/icmplib/__init__.py",
        "opt/tlg-ipam-agent/lib/python3.13/site-packages/icmplib",
        "opt/tlg-ipam-agent/lib/python3.13/site-packages/distutils-precedence.pth",
        "opt/tlg-ipam-agent/lib/python3.13/site-packages/tlg_ipam_agent/cli.py",
        "opt/tlg-ipam-agent/lib/python3.13/site-packages",
        "opt/tlg-ipam-agent/bin",  # the directory the unit names, behind a link
        "opt/tlg-ipam-agent",
        "opt",
        "usr/bin",  # where the link leads
        "usr/bin/python3.13",
        "usr/lib/python3.13/os.py",  # the standard library is on the import path too
    ],
)
def test_anything_on_the_import_path_or_the_interpreters_way_others_can_change_is_refused(tmp_path, path):
    layout = _venv(tmp_path)
    (tmp_path / path).chmod(0o777 if (tmp_path / path).is_dir() else 0o666)
    assert _refused(tmp_path, layout) == str(tmp_path / path)


def test_a_link_through_a_directory_others_can_write_is_refused(tmp_path):
    """The reviewer's second reproduction: `bin/python-hop -> /opt/bridge/python ->
    the interpreter`, both ends root's, the directory of the middle hop `0777`."""
    layout = _venv(tmp_path)
    bridge = tmp_path / "opt/bridge"
    bridge.mkdir()
    (bridge / "python").symlink_to(tmp_path / "usr/bin/python3.13")
    hop = tmp_path / "opt/tlg-ipam-agent/bin/python-hop"
    hop.symlink_to(bridge / "python")
    bridge.chmod(0o755)
    _check(tmp_path, {**layout, "interpreter": str(hop)})
    bridge.chmod(0o777)
    assert _refused(tmp_path, {**layout, "interpreter": str(hop)}) == str(bridge)


def test_a_relative_link_with_dotdot_is_followed_from_its_own_directory(tmp_path):
    layout = _venv(tmp_path)
    hop = tmp_path / "opt/tlg-ipam-agent/bin/python-rel"
    hop.symlink_to("../../../usr/bin/python3.13")
    _check(tmp_path, {**layout, "interpreter": str(hop)})
    (tmp_path / "usr/bin/python3.13").chmod(0o666)
    assert _refused(tmp_path, {**layout, "interpreter": str(hop)}) == str(tmp_path / "usr/bin/python3.13")


def test_a_linked_directory_on_the_import_path_is_walked_where_it_leads(tmp_path):
    layout = _venv(tmp_path)
    elsewhere = tmp_path / "srv/shared"
    elsewhere.mkdir(parents=True)
    (elsewhere / "evil.py").write_bytes(b"")
    for path in (tmp_path / "srv", elsewhere):
        path.chmod(0o755)
    (elsewhere / "evil.py").chmod(0o644)
    site = tmp_path / "opt/tlg-ipam-agent/lib/python3.13/site-packages"
    (site / "shared").symlink_to(elsewhere)
    _check(tmp_path, layout)
    (elsewhere / "evil.py").chmod(0o666)
    assert _refused(tmp_path, layout) == str(elsewhere / "evil.py")


def test_a_link_loop_is_refused(tmp_path):
    layout = _venv(tmp_path)
    a, b = tmp_path / "opt/a", tmp_path / "opt/b"
    a.symlink_to(b)
    b.symlink_to(a)
    assert "more than" in _refused(tmp_path, {**layout, "interpreter": str(a)})


def test_a_missing_entry_and_a_dangling_link_load_nothing_and_pass(tmp_path):
    layout = _venv(tmp_path)
    site = tmp_path / "opt/tlg-ipam-agent/lib/python3.13/site-packages"
    (site / "gone").symlink_to(tmp_path / "nowhere")
    _check(tmp_path, {**layout, "search": [*layout["search"], str(tmp_path / "no-such-dir")]})


def test_another_owner_is_refused(tmp_path):
    """A file root does not own is refused even when nobody else may write it: the
    owner can change its mode."""
    layout = _venv(tmp_path)
    with pytest.raises(trust.Untrusted):
        trust.check(layout["interpreter"], layout["search"], package=_package(tmp_path), owner=os.getuid() + 1, top=str(tmp_path))


def test_the_current_directory_on_the_import_path_is_checked(tmp_path, monkeypatch):
    """`python -m` puts the current directory on `sys.path`; an empty entry is it."""
    layout = _venv(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    work.chmod(0o777)
    monkeypatch.chdir(work)
    with pytest.raises(trust.Untrusted) as refused:
        trust.check(layout["interpreter"], ["", *layout["search"]], package=_package(tmp_path), owner=os.getuid(), top=str(tmp_path))
    assert str(refused.value) == str(work)


# --- the order: the check comes before the agent's other modules are imported ---

ORDER = r"""
import sys
sys.path.insert(0, {src!r})
from tlg_ipam_agent import cli, trust

seen = {{}}

def check(*a, **kw):
    seen["modules"] = sorted(m for m in sys.modules if m.split(".")[0] in ("tlg_ipam_agent", "httpx", "icmplib", "dns"))
    raise trust.Untrusted("/opt/tlg-ipam-agent")

trust.check = check
code = cli.main([{command}])
print(code, seen["modules"], "tlg_ipam_agent.install" in sys.modules)
"""


@pytest.mark.parametrize(
    "command",
    ['"install", "--url", "https://ipam.home.arpa", "--enrol-token", "-"', '"uninstall"'],
    ids=["install", "uninstall"],
)
def test_install_and_uninstall_check_before_importing_the_rest_of_the_agent(command):
    """In a fresh interpreter: when `trust.check` runs, the agent's modules loaded are
    the package, `cli` and `trust`, and no dependency; refused, `install` is never
    imported. What ran before it is the boundary the check cannot cross (trust.py's
    docstring)."""
    out = subprocess.run(
        [sys.executable, "-c", ORDER.format(src=str(SRC), command=command)],
        capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == "1 ['tlg_ipam_agent', 'tlg_ipam_agent.cli', 'tlg_ipam_agent.trust'] False"
    assert "refused: /opt/tlg-ipam-agent can be changed by a user other than root" in out.stderr


def test_the_cli_imports_nothing_of_the_agent_at_its_top():
    out = subprocess.run(
        [sys.executable, "-c", f"import sys; sys.path.insert(0, {str(SRC)!r}); import tlg_ipam_agent.cli; "
         "print(sorted(m for m in sys.modules if m.split('.')[0] in ('tlg_ipam_agent', 'httpx', 'icmplib', 'dns')))"],
        capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == "['tlg_ipam_agent', 'tlg_ipam_agent.cli']"


def test_the_working_directory_is_taken_off_the_import_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    here = os.getcwd()
    assert trust.without_working_directory(["", ".", here, here + "/", "/usr/lib/python3.13"]) == ["/usr/lib/python3.13"]


def test_the_package_is_walked_wherever_it_was_loaded_from(tmp_path):
    """Loaded from a directory not on the import path (the working directory, taken
    off it), the package is still walked."""
    layout = _venv(tmp_path)
    elsewhere = tmp_path / "work/tlg_ipam_agent"
    elsewhere.mkdir(parents=True)
    (tmp_path / "work").chmod(0o755)
    elsewhere.chmod(0o755)
    (elsewhere / "cli.py").write_bytes(b"")
    (elsewhere / "cli.py").chmod(0o666)
    with pytest.raises(trust.Untrusted) as refused:
        trust.check(layout["interpreter"], layout["search"], package=str(elsewhere), owner=os.getuid(), top=str(tmp_path))
    assert str(refused.value) == str(elsewhere / "cli.py")


def test_the_cli_takes_the_working_directory_off_before_it_checks(monkeypatch, tmp_path):
    from tlg_ipam_agent import cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", ["", str(tmp_path), *sys.path])
    seen = []
    monkeypatch.setattr(trust, "check", lambda: seen.append(list(sys.path)))
    from tlg_ipam_agent import install

    monkeypatch.setattr(install, "uninstall", lambda: 0)
    assert cli.main(["uninstall"]) == 0
    assert "" not in seen[0] and str(tmp_path) not in seen[0]


def test_a_running_agent_never_imports_the_installer_or_the_check():
    """What holds of the scanning process (round 2 of #130): it imports every module the
    run path does (`cli._run`'s), and neither `install` nor `trust` is among them, so
    the module that names `subprocess` and the fixed programs it runs are not loaded
    there. That is not "`subprocess` is not loaded": the standard library's asyncio
    imports it in every agent process. What keeps a launch out of the agent is that no
    agent module but `install.py` names one (test_invariants.py) and that nothing the
    server sends reaches `install`'s fixed argument lists."""
    out = subprocess.run(
        [sys.executable, "-c", f"import sys; sys.path.insert(0, {str(SRC)!r}); import tlg_ipam_agent.cli; "
         "from tlg_ipam_agent import config, enrol, interfaces, client, loop, probe, scan; "
         "import json; print(json.dumps(sorted(m for m in sys.modules if m.startswith('tlg_ipam_agent.'))))"],
        capture_output=True, text=True, check=True,
    )
    loaded = json.loads(out.stdout)
    assert "tlg_ipam_agent.install" not in loaded and "tlg_ipam_agent.trust" not in loaded
    assert {"tlg_ipam_agent.loop", "tlg_ipam_agent.probe", "tlg_ipam_agent.enrol"} <= set(loaded)
