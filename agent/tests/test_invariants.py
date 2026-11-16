"""Spec §7.2 invariant 1, held in the agent's source: it never executes, downloads or
updates from anything the server sends.

What this proves: the agent's own modules import only the modules listed here, and
none of them names (as a call, an attribute, a bare name, or a string that could be
handed to `getattr` or an importer) a way to start a process, load or evaluate code,
reach an object's attributes by computed name, or write a file. What it does not
prove: how the three dependencies behave inside, that a future change cannot reach
the same effect through an allowed module in a way not listed, or anything about
logic errors. It is a tripwire for review, not a proof of the invariant.
"""

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "tlg_ipam_agent"
MODULES = sorted(PACKAGE.glob("*.py"))

# every module the agent may import; anything else fails, so a new import is a choice
# made in review. `urllib.parse` only: `urllib.request` downloads.
ALLOWED_IMPORTS = {
    "argparse", "asyncio", "collections.abc", "dataclasses", "datetime", "enum",
    "ipaddress", "json", "logging", "os", "pathlib", "platform", "random", "re",
    "signal", "socket", "ssl", "struct", "sys", "time", "typing", "urllib.parse",
    "httpx", "icmplib",
    "dns.asyncquery", "dns.exception", "dns.message", "dns.rcode", "dns.rdatatype",
    "dns.resolver", "dns.reversename",
}
# names that start a process, load or evaluate code, reach attributes by a computed
# name, or write files; matched as identifiers and as string constants
FORBIDDEN = {
    "exec", "eval", "compile", "__import__", "breakpoint", "getattr", "setattr",
    "delattr", "globals", "locals", "vars", "__builtins__", "__dict__", "__globals__",
    "__code__", "__subclasses__", "__loader__", "system", "popen", "startfile",
    "run_path", "run_module", "import_module", "load_module", "exec_module",
    "find_spec", "reload", "write_text", "write_bytes", "open", "chmod", "symlink",
    "subprocess", "pickle", "marshal", "ctypes", "importlib", "runpy", "pip",
    "ensurepip", "venv", "shutil", "pty", "zipimport", "urlopen", "urlretrieve",
}
# and any identifier with these beginnings: os.exec*, os.spawn*, os.fork*,
# os.posix_spawn*, asyncio.create_subprocess_*, loop.subprocess_*
FORBIDDEN_PREFIXES = ("exec", "spawn", "fork", "posix_spawn", "create_subprocess", "subprocess_")
# a forbidden name that is harmless on this one module: `re.compile` builds a pattern
ALLOWED_QUALIFIED = {"re.compile"}
# the identifiers these prefixes would wrongly catch in the agent's own code
PREFIX_EXCEPTIONS = {"exception", "exc_info", "exc_text", "exctype"}


def _imports(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            yield node.module or ""


def _identifiers(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            yield node.id
        elif isinstance(node, ast.Attribute):
            qualified = f"{node.value.id}.{node.attr}" if isinstance(node.value, ast.Name) else None
            if qualified not in ALLOWED_QUALIFIED:
                yield node.attr
        elif isinstance(node, ast.alias):
            yield node.name.split(".")[-1]
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield node.name
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value


def _forbidden(name: str) -> bool:
    if name in FORBIDDEN:
        return True
    return name.startswith(FORBIDDEN_PREFIXES) and name not in PREFIX_EXCEPTIONS


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_only_listed_modules_are_imported(path):
    imported = set(_imports(ast.parse(path.read_text())))
    assert imported <= ALLOWED_IMPORTS, f"{path.name}: {sorted(imported - ALLOWED_IMPORTS)}"


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_nothing_that_could_run_load_or_fetch_code_is_named(path):
    found = sorted({n for n in _identifiers(ast.parse(path.read_text())) if _forbidden(n)})
    assert found == [], f"{path.name}: {found}"


@pytest.mark.parametrize(
    "source",
    [
        "import asyncio\nasync def f(c):\n    await asyncio.create_subprocess_exec(c)\n",
        "import os\ndef f(c):\n    os.execv(c, [c])\n",
        "def f(m):\n    return getattr(__builtins__, 'ev' + 'al')(m)\n",
        "import builtins\nf = builtins.__dict__['exec']\n",
        "x = 'subprocess'\n",
        "from pathlib import Path\nPath('x').write_text('y')\n",
        "with open('x', 'w') as f:\n    pass\n",
        "import urllib.request\n",
    ],
)
def test_the_guard_catches_what_the_review_found_it_missed(source):
    """An uncalled `asyncio.create_subprocess_exec` passed the earlier, call-only
    guard (briefed review of PR #70); these must all fail the checks above."""
    tree = ast.parse(source)
    bad_import = not set(_imports(tree)) <= ALLOWED_IMPORTS
    bad_name = any(_forbidden(n) for n in _identifiers(tree))
    assert bad_import or bad_name


def _third_party(path):
    roots = {name.split(".")[0] for name in _imports(ast.parse(path.read_text()))}
    return roots & {"httpx", "icmplib", "dns"}


def test_only_the_probe_module_sends_packets_and_only_the_client_speaks_http():
    users = {p.name: _third_party(p) for p in MODULES if _third_party(p)}
    assert users == {"probe.py": {"icmplib", "dns"}, "client.py": {"httpx"}}


def test_the_dependencies_are_the_three_the_spec_names():
    lines = (PACKAGE.parents[1] / "requirements.in").read_text().splitlines()
    names = sorted(line.split(">")[0] for line in lines if line.strip() and not line.startswith("#"))
    assert names == ["dnspython", "httpx", "icmplib"]
