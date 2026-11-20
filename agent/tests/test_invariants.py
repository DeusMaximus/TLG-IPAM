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
# made in review. `urllib.parse` only: `urllib.request` downloads. `stat` and
# `threading` came with #99, for `probe.read_resolv_conf` and the one thread that runs
# it: `stat.S_ISREG` refuses what is not a regular file, and a daemon thread keeps a
# blocked read off the event loop. `io` is not here: its `FileIO` and `open_code` open
# files, and the agent needs none of it.
ALLOWED_IMPORTS = {
    "argparse", "asyncio", "collections.abc", "dataclasses", "datetime", "enum",
    "ipaddress", "json", "logging", "os", "pathlib", "platform", "random", "re",
    "signal", "socket", "ssl", "stat", "struct", "sys", "threading", "time", "typing",
    "urllib.parse",
    "httpx", "icmplib",
    "dns.asyncquery", "dns.exception", "dns.flags", "dns.message", "dns.rcode", "dns.rdatatype",
    "dns.resolver", "dns.reversename",
}
# names that start a process, load or evaluate code, reach attributes by a computed
# name, or write files; matched as identifiers and as string constants. The second
# block (#99) is every name the allowed modules offer that opens a file to write,
# creates one, or sets an attribute by name, which the agent uses none of; `FileIO`
# and `open_code` stay listed though `io` is not importable.
FORBIDDEN = {
    "exec", "eval", "compile", "__import__", "breakpoint", "getattr", "setattr",
    "delattr", "globals", "locals", "vars", "__builtins__", "__dict__", "__globals__",
    "__code__", "__subclasses__", "__loader__", "system", "popen", "startfile",
    "run_path", "run_module", "import_module", "load_module", "exec_module",
    "find_spec", "reload", "write_text", "write_bytes", "open", "chmod", "symlink",
    "subprocess", "pickle", "marshal", "ctypes", "importlib", "runpy", "pip",
    "ensurepip", "venv", "shutil", "pty", "zipimport", "urlopen", "urlretrieve",
    "FileIO", "open_code", "fdopen", "openpty", "mkfifo", "mknod", "touch", "truncate",
    "symlink_to", "hardlink_to", "link_to", "FileHandler", "__setattr__", "__delattr__",
}
# and any identifier with these beginnings: os.exec*, os.spawn*, os.fork*,
# os.posix_spawn*, asyncio.create_subprocess_*, loop.subprocess_*
FORBIDDEN_PREFIXES = ("exec", "spawn", "fork", "posix_spawn", "create_subprocess", "subprocess_")
# a forbidden name that is harmless on this one module: `re.compile` builds a pattern.
# `os.open` can write, so it passes only where `test_os_open_only_reads` holds: each
# use is a call whose flags argument is spelled as `os.O_*` names from READ_ONLY_FLAGS
# joined by `|`, in a module that never rebinds the name `os` (an assignment, a
# parameter, `import x as os`, a `def`, `global`) and never assigns to or deletes an
# attribute of it. That is a check of spelling, not a proof: it does not see what the
# flags are at run time if `os` is reached some way it does not look (a computed
# attribute, `sys.modules`, another module). `probe.read_resolv_conf` opens the host's
# resolv.conf with `O_NONBLOCK`, which no other way of opening a file offers.
ALLOWED_QUALIFIED = {"re.compile", "os.open"}
READ_ONLY_FLAGS = {"O_RDONLY", "O_NONBLOCK", "O_NOCTTY", "O_CLOEXEC"}
# every way the agent's own code opens a file to read, among what the allowed modules
# offer (the ways to write are forbidden above): `os.open`, pathlib's read_text and
# read_bytes, ssl's load_*, platform's file readers, dnspython's `read_resolv_conf` and
# a `Resolver` not made with `configure=False`, and any call naming a file by keyword.
# `test_files_are_opened_only_where_listed` holds where each is used. It sees the
# agent's own calls, not what a library opens inside one (ssl's CA bundle, say).
READ_OPENERS = {
    "read_text", "read_bytes", "load_verify_locations", "load_cert_chain",
    "load_dh_params", "freedesktop_os_release", "libc_ver", "read_resolv_conf",
}
FILE_KEYWORDS = {"cafile", "capath", "certfile", "keyfile", "filename"}
OPENED_WHERE = {
    "probe.py": {"os.open", "read_resolv_conf"},  # the host's resolv.conf
    "interfaces.py": {"read_text"},  # an interface's MAC, from /sys/class/net
}
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


def _os_rebound(tree) -> list[str]:
    """Each place the module could change what `os` or one of its attributes is: a
    binding of the name `os` other than `import os` (or `import os.path`), and an
    assignment to or deletion of an attribute of `os`."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [f"import {a.name} as os" for a in node.names if a.asname == "os" and a.name != "os"]
        elif isinstance(node, ast.ImportFrom):
            found += [f"from {node.module} import {a.name}" for a in node.names if (a.asname or a.name) == "os"]
        elif isinstance(node, ast.Name) and node.id == "os" and not isinstance(node.ctx, ast.Load):
            found.append("os assigned or deleted")
        elif isinstance(node, ast.arg) and node.arg == "os":
            found.append("a parameter named os")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == "os":
            found.append("a def or class named os")
        elif isinstance(node, (ast.Global, ast.Nonlocal)) and "os" in node.names:
            found.append("global or nonlocal os")
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name == "os":
            found.append("os bound by except or match")
        elif (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
              and node.value.id == "os" and not isinstance(node.ctx, ast.Load)):
            found.append(f"os.{node.attr} assigned or deleted")
    return found


def _file_opens(tree) -> set[str]:
    """The ways of opening a file (READ_OPENERS, `os.open`, FILE_KEYWORDS, an
    unconfigured `Resolver`) the module names, called or not."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if node.attr == "open" and isinstance(node.value, ast.Name) and node.value.id == "os":
                found.add("os.open")
            elif node.attr in READ_OPENERS:
                found.add(node.attr)
        elif isinstance(node, ast.Name) and node.id in READ_OPENERS:
            found.add(node.id)
        elif isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
            unconfigured = not any(
                k.arg == "configure" and isinstance(k.value, ast.Constant) and k.value.value is False
                for k in node.keywords
            )
            if name == "Resolver" and unconfigured:
                found.add("Resolver")
            found.update(f"{k.arg}=" for k in node.keywords if k.arg in FILE_KEYWORDS)
    return found


def _violations(tree, module: str) -> list[str]:
    """Everything the checks in this file refuse in one module's source."""
    found = [f"import {m}" for m in sorted(set(_imports(tree)) - ALLOWED_IMPORTS)]
    found += sorted({f"name {n}" for n in _identifiers(tree) if _forbidden(n)})
    found += [f"os.open flags {sorted(f)}" for f in _os_opens(tree) if not f <= READ_ONLY_FLAGS]
    found += _os_rebound(tree)
    found += [f"opens a file with {o}" for o in sorted(_file_opens(tree) - OPENED_WHERE.get(module, set()))]
    return found


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
    assert _os_rebound(tree) == [], f"{path.name}: {_os_rebound(tree)}"


def test_files_are_opened_only_where_listed():
    """Every way of opening a file the allow-list admits, in every module: only
    probe.py opens one with `os.open`, and the reads OPENED_WHERE names are the only
    ones. (Until round 1 of #103 this was `test_only_the_probe_module_opens_a_file`, and
    looked at `os.open` alone.)"""
    used = {p.name: _file_opens(ast.parse(p.read_text())) for p in MODULES}
    assert {name: ways for name, ways in used.items() if ways} == OPENED_WHERE


# round 1 of #103: ways to write a file that the widened checks let through
ROUND_1 = [
    # `io` allowed whole: FileIO writes, open_code opens
    "import io\ndef bypass(path):\n    io.FileIO(path, 'w').write(b'y')\n",
    "import io\ndef bypass(path):\n    return io.open_code(path)\n",
    "from io import FileIO as F\nF('x', 'w')\n",
    # the flags checked by spelling: rebind the constant, or the name `os`
    "import os\ndef bypass(path):\n    saved = os.O_RDONLY\n    os.O_RDONLY = os.O_WRONLY | os.O_CREAT | os.O_TRUNC\n"
    "    fd = os.open(path, os.O_RDONLY)\n    os.write(fd, b'y')\n    os.O_RDONLY = saved\n",
    "import os\nos.O_RDONLY |= os.O_WRONLY | os.O_CREAT\nfd = os.open('x', os.O_RDONLY)\n",
    "import os\nos.__setattr__('O_RDONLY', 577)\nfd = os.open('x', os.O_RDONLY)\n",
    "import os\nclass W:\n    O_RDONLY = 577\n    def open(self, *a):\n        pass\nos = W()\nfd = os.open('x', os.O_RDONLY)\n",
    "import os\ndef bypass(path, os=os):\n    return os.open(path, os.O_RDONLY)\n",
    "import os\nimport sys as os\nfd = os.open('x', os.O_RDONLY)\n",
    "import os.path as os\n",
    "from os import path as os\n",
    "import os\ndef bypass():\n    global os\n    os = None\n",
    "import os\ndel os.O_RDONLY\n",
    # the other openers the allowed modules offer
    "import os\nos.fdopen(os.open('x', os.O_RDONLY), 'w')\n",
    "from pathlib import Path\nPath('x').touch()\n",
    "import os\nos.truncate('x', 0)\n",
    "import logging\nlogging.FileHandler('x')\n",
    "import logging\nlogging.basicConfig(filename='x')\n",
    "from pathlib import Path\nkey = Path('x').read_bytes()\n",
    "import ssl\nssl.create_default_context(cafile='x')\n",
    "import dns.resolver\nr = dns.resolver.Resolver()\n",
]


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
    ] + ROUND_1,
)
def test_the_guard_catches_what_the_review_found_it_missed(source):
    """An uncalled `asyncio.create_subprocess_exec` passed the earlier, call-only
    guard (briefed review of PR #70); these must all fail the checks above."""
    assert _violations(ast.parse(source), "snippet.py")


@pytest.mark.parametrize("module", ["arp.py", "probe.py"])
@pytest.mark.parametrize("source", ROUND_1)
def test_each_way_round_1_found_fails_inside_a_real_module(source, module):
    """Round 1 of #103 appended these to arp.py and probe.py and the checks passed;
    appended to the module's real source now, each is refused."""
    tree = ast.parse((PACKAGE / module).read_text() + "\n" + source)
    assert _violations(tree, module)


def test_the_real_modules_pass_every_check():
    assert {p.name: _violations(ast.parse(p.read_text()), p.name) for p in MODULES} == {p.name: [] for p in MODULES}


def test_a_read_only_os_open_passes_the_guard():
    tree = ast.parse("import os\nfd = os.open('x', os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY)\n")
    assert _violations(tree, "probe.py") == []
    # `import os.path` binds `os` to os itself, and is not a rebinding
    assert _os_rebound(ast.parse("import os.path\n")) == []


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
