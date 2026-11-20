"""Spec §7.2 invariant 1, held in the agent's source: it never executes, downloads or
updates from anything the server sends.

What this shows, and no more: the agent's own modules import only the modules listed
here, and contain none of the names listed here (as a call, an attribute, a bare name,
or a string that could be handed to `getattr` or an importer). The names are ways to
start a process, load or evaluate code, reach an object's attributes by computed name,
or write a file. It proves the absence of those names, not the invariant: code that
reaches the same effect without one of them passes (an uncalled
`os.__getattribute__("sy" + "stem")(command)` does), and so would anything inside the
three dependencies. It is a tripwire for review; review holds the invariant.
"""

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "tlg_ipam_agent"
MODULES = sorted(PACKAGE.glob("*.py"))

# every module the agent may import; anything else fails, so a new import is a choice
# made in review. `urllib.parse` only: `urllib.request` downloads. `io`, `stat` and
# `threading` came with #99, for `probe.read_resolv_conf` and the one thread that runs
# it: `io.StringIO` hands dnspython the bytes read, `stat.S_ISREG` refuses what is not
# a regular file, and a daemon thread keeps a blocked read off the event loop.
ALLOWED_IMPORTS = {
    "argparse", "asyncio", "collections.abc", "dataclasses", "datetime", "enum", "io",
    "ipaddress", "json", "logging", "os", "pathlib", "platform", "random", "re",
    "signal", "socket", "ssl", "stat", "struct", "sys", "threading", "time", "typing",
    "urllib.parse",
    "httpx", "icmplib",
    "dns.asyncquery", "dns.exception", "dns.flags", "dns.message", "dns.rcode", "dns.rdatatype",
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
# a forbidden name that is harmless on this one module: `re.compile` builds a pattern.
# `os.open` can write, so it is allowed only as a call whose flags name nothing but
# READ_ONLY_FLAGS (`test_os_open_only_reads`): `probe.read_resolv_conf` opens the
# host's resolv.conf without blocking, which no other way of opening a file offers
ALLOWED_QUALIFIED = {"re.compile", "os.open"}
READ_ONLY_FLAGS = {"O_RDONLY", "O_NONBLOCK", "O_NOCTTY", "O_CLOEXEC"}
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


def _flag_names(node) -> set[str]:
    """The `os.O_*` names an `os.open` flags argument is made of, joined by `|`; any
    other expression (a number, a variable, a call) is "?", which no list allows."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _flag_names(node.left) | _flag_names(node.right)
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "os":
        return {node.attr}
    return {"?"}


def _os_opens(tree) -> list[set[str]]:
    """For each `os.open` in the module, the flags its call names: "?" for flags that
    are not plain `os.O_*` names, and for an `os.open` that is not called right there
    (`f = os.open`), whose later flags this check cannot see."""
    called = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            flags = node.args[1] if len(node.args) > 1 else next(
                (k.value for k in node.keywords if k.arg == "flags"), None)
            called[id(node.func)] = _flag_names(flags) if flags is not None else {"?"}
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and node.attr == "open"
                and isinstance(node.value, ast.Name) and node.value.id == "os"):
            found.append(called.get(id(node), {"?"}))
    return found


def _opens_only_to_read(tree) -> bool:
    return all(flags <= READ_ONLY_FLAGS for flags in _os_opens(tree))


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


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_os_open_only_reads(path):
    tree = ast.parse(path.read_text())
    assert _opens_only_to_read(tree), f"{path.name}: {_os_opens(tree)}"


def test_only_the_probe_module_opens_a_file():
    users = {p.name for p in MODULES if _os_opens(ast.parse(p.read_text()))}
    assert users == {"probe.py"}


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
        # `os.open` (#99) only to read
        "import os\nfd = os.open('x', os.O_WRONLY | os.O_CREAT)\n",
        "import os\nfd = os.open('x', os.O_RDONLY | os.O_APPEND)\n",
        "import os\nfd = os.open('x', 1)\n",
        "import os\nfd = os.open('x', flags=os.O_RDWR)\n",
        "import os\nfd = os.open('x', MODE)\n",
        "import os\nwrite = os.open\n",
        "from os import open as o\n",
        "import os as o\nfd = o.open('x', o.O_RDONLY)\n",
    ],
)
def test_the_guard_catches_what_the_review_found_it_missed(source):
    """An uncalled `asyncio.create_subprocess_exec` passed the earlier, call-only
    guard (briefed review of PR #70); these must all fail the checks above."""
    tree = ast.parse(source)
    bad_import = not set(_imports(tree)) <= ALLOWED_IMPORTS
    bad_name = any(_forbidden(n) for n in _identifiers(tree))
    bad_open = not _opens_only_to_read(tree)
    assert bad_import or bad_name or bad_open


def test_a_read_only_os_open_passes_the_guard():
    tree = ast.parse("import os\nfd = os.open('x', os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY)\n")
    assert _opens_only_to_read(tree)
    assert not any(_forbidden(n) for n in _identifiers(tree))


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


def _socket_use(tree) -> tuple[bool, set[str]]:
    """Whether a module imports `socket` in any spelling (`import socket`, `import
    socket as x`, `from socket import ...`), and the names it reaches on it: attributes
    of any name the module is bound to, and the names it imports from it."""
    aliases: set[str] = set()
    names: set[str] = set()
    imported = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "socket":
                    imported = True
                    aliases.add(alias.asname or "socket")
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == "socket":
            imported = True
            names.update(alias.name for alias in node.names)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in aliases:
            names.add(node.attr)
    return imported, names


def _socket_names(path):
    return _socket_use(ast.parse(path.read_text()))


def test_only_the_probe_module_opens_a_packet_socket():
    """A tripwire, like the checks above: it sees which modules import `socket`, in
    any spelling, and which of its names they use, not what they do with them.

    The ARP sweep's raw `AF_PACKET` socket is made in `probe.py`. Two other modules
    may import `socket`: `loop.py` for the host's name alone, and `interfaces.py`,
    which names no address family but netlink, inet and unspec: its socket talks to
    the local kernel."""
    users = {p.name: _socket_names(p) for p in MODULES}
    assert {name for name, (imported, _) in users.items() if imported} == {"probe.py", "interfaces.py", "loop.py"}
    assert users["loop.py"][1] == {"gethostname"}
    assert {name for name, (_, used) in users.items() if "AF_PACKET" in used} == {"probe.py"}
    families = {n for n in users["interfaces.py"][1] if n.startswith("AF_")}
    assert families <= {"AF_NETLINK", "AF_INET", "AF_INET6", "AF_UNSPEC"}


@pytest.mark.parametrize(
    "source",
    [
        "import socket as wire\ns = wire.socket(17, wire.SOCK_RAW, 0)\n",
        "from socket import socket as make\n",
        "from socket import AF_PACKET\n",
        "import socket\n",
    ],
)
def test_the_socket_tripwire_sees_every_spelling(source):
    """Briefed review of PR #84: `import socket as wire` passed the first version."""
    imported, _ = _socket_use(ast.parse(source))
    assert imported
