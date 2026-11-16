"""Spec §7.2 invariant 1, held in the agent's source: it never executes, downloads or
updates from anything the server sends. A job is data; these tests fail if code that
could run, fetch or install something appears in the package."""

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "tlg_ipam_agent"
MODULES = sorted(PACKAGE.glob("*.py"))

# modules through which a value could be run, loaded or installed
FORBIDDEN_IMPORTS = {
    "subprocess", "os.system", "pty", "pickle", "marshal", "shelve", "importlib",
    "runpy", "code", "codeop", "zipimport", "ensurepip", "pip", "venv", "ctypes",
    "multiprocessing", "urllib.request", "shutil",
}
# builtins, called by name
FORBIDDEN_BUILTINS = {"exec", "eval", "compile", "__import__", "breakpoint"}
# `os` functions, called as attributes
FORBIDDEN_OS = {"system", "popen", "execv", "execve", "execvp", "execl", "spawnv", "spawnl",
                "posix_spawn", "startfile", "fork", "forkpty"}


def _imports(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            yield node.module or ""
            yield from (f"{node.module}.{a.name}" for a in node.names)


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_nothing_that_could_run_or_fetch_code(path):
    tree = ast.parse(path.read_text())
    imported = set(_imports(tree))
    assert not imported & FORBIDDEN_IMPORTS, path.name
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                assert node.func.id not in FORBIDDEN_BUILTINS, f"{path.name}: {node.func.id}()"
            elif isinstance(node.func, ast.Attribute):
                assert node.func.attr not in FORBIDDEN_OS, f"{path.name}: {node.func.attr}()"


def _third_party(path):
    roots = {name.split(".")[0] for name in _imports(ast.parse(path.read_text()))}
    return roots & {"httpx", "icmplib", "dns"}


def test_only_the_probe_module_sends_packets_and_only_the_client_speaks_http():
    users = {p.name: _third_party(p) for p in MODULES if _third_party(p)}
    assert users == {"probe.py": {"icmplib", "dns"}, "client.py": {"httpx"}}


def test_the_dependencies_are_the_three_the_spec_names():
    lines = (PACKAGE.parents[1] / "requirements.in").read_text().splitlines()
    names = sorted(l.split(">")[0] for l in lines if l.strip() and not l.startswith("#"))
    assert names == ["dnspython", "httpx", "icmplib"]
