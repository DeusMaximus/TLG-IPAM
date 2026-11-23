"""Spec §7.2 invariant 1, held in the agent's source: it never executes, downloads or
updates from anything the server sends.

What this shows, and no more: the agent's own modules import only the modules listed
here, and contain none of the names listed here (as a call, an attribute, a bare name,
or a string that could be handed to `getattr` or an importer), but for the few listed
names a module is allowed (`NAMES_WHERE`). The names are ways to start a process, load
or evaluate code, reach an object's attributes by computed name, or write a file.

Two modules are allowed some of them, since #22: `store.py` opens, writes, renames and
removes files (the stored agent token, and what `install` puts on the host), and
`install.py` starts the fixed programs that create the service user and drive systemd.
Every other module is held to the lists whole, and only `enrol.py` and `install.py`
may use `store` (`test_only_the_enrolment_and_the_installer_write_files`). It proves the absence of those names, not the invariant: code that
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
# it: `stat.S_ISREG` on what `os.stat` says refuses a path that is not a regular file
# before anything opens it, and a daemon thread keeps a blocked read off the event
# loop. `io` is not here: its `FileIO` and `open_code` open files, and the agent needs
# none of it. #22 added `subprocess`, for `install.py` alone (`IMPORTED_ONLY_IN`): it
# runs useradd, userdel, groupdel and systemctl with fixed arguments. `pwd` and `grp`
# came with it, to look the service user and its group up (they read the user
# database and change nothing), `functools`, for `partial`, and `termios`, for
# `install.py` alone, to turn echo off while the enrolment token is typed.
ALLOWED_IMPORTS = {
    "argparse", "asyncio", "collections.abc", "dataclasses", "datetime", "enum",
    "functools", "grp", "ipaddress", "json", "logging", "os", "pathlib", "platform",
    "pwd", "random", "re", "signal", "socket", "ssl", "stat", "struct", "subprocess",
    "sys", "termios", "threading", "time", "typing", "urllib.parse",
    "httpx", "icmplib",
    "dns.asyncquery", "dns.exception", "dns.flags", "dns.message", "dns.rcode", "dns.rdatatype",
    "dns.resolver", "dns.reversename",
}
# names that start a process, load or evaluate code, reach attributes by a computed
# name, or write files; matched as identifiers and as string constants. `open` is one
# in every spelling (`open`, `os.open`, `io.open`, a method named `open`): no agent
# module opens a file itself (#104; #99 had let a read-only `os.open` through in
# probe.py, which an alias of `os` turned into a writer). The second block (#99) names
# spellings of the allowed modules that open a file to write, create one, or set an
# attribute by name, which the agent uses none of; `FileIO` and `open_code` stay listed
# though `io` is not importable. The third block (#22) names the ways of making,
# renaming, removing and changing the owner or mode of a file or directory that
# `os` and `pathlib` name, which `store.py` alone may use (`NAMES_WHERE`). Two such
# names are not listed, since harmless code shares them: `replace` (`os.replace`, a
# rename; every `str.replace`) and `link` (`os.link`; the ARP link in scan.py and
# probe.py). Review holds those two.
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
    "mkdir", "makedirs", "rename", "renames", "unlink", "remove", "rmdir", "removedirs",
    "chown", "fchown", "lchown", "fchmod", "lchmod", "ftruncate", "mkdtemp", "mkstemp",
}
# and any identifier with these beginnings: os.exec*, os.spawn*, os.fork*,
# os.posix_spawn*, asyncio.create_subprocess_*, loop.subprocess_*
FORBIDDEN_PREFIXES = ("exec", "spawn", "fork", "posix_spawn", "create_subprocess", "subprocess_")
# a forbidden name that is harmless on this one module: `re.compile` builds a pattern,
# and `sys.executable` is the interpreter's path, which the unit's `ExecStart` names
# (#22; `executable` starts with `exec`)
ALLOWED_QUALIFIED = {"re.compile", "sys.executable"}

# The listed names one module may use, and no other (#22). `store.py` opens files
# with `os.open` (reading the token file without following a link or waiting on a
# FIFO, and creating a file exclusively), sets the new file's owner and mode on its
# descriptor before writing it, renames it into place, and makes and removes the
# install's directories and files. `install.py` names `subprocess`, to run its fixed
# programs. What each does with them is in its docstring; review holds the rest.
NAMES_WHERE = {
    "store.py": {"open", "fchown", "fchmod", "rename", "unlink", "rmdir", "mkdir"},
    "install.py": {"subprocess"},
}
# an allowed import that one module alone may make
IMPORTED_ONLY_IN = {"subprocess": "install.py", "termios": "install.py"}

# The spellings of having a library open a file to read that the checks enumerate
# (the agent's own ways of opening one are forbidden names above): pathlib's read_text
# and read_bytes, ssl's load_*, platform's file readers, dnspython's
# `read_resolv_conf` and a `Resolver` not made with `configure=False`, and a keyword
# that names a file (`cafile`, `filename`, ...), written out in a call or as a string
# (`**{"filename": path}`, which `logging.basicConfig` opens and writes).
# `test_files_are_opened_only_where_listed` holds where each is used.
#
# It sees those spellings in the agent's own source and nothing else: not a keyword
# made at run time (`"file" + "name"`), not an allowed module's own way of opening a
# file under another name, and not what a library opens inside a call (ssl's CA
# bundle, say). It matches names, not types, so it also flags harmless code that
# shares one: a call with an unrelated `filename=` keyword, the string "filename" in a
# list of log fields, a class of the agent's own named `Resolver`, a variable named
# `read_bytes` (`test_what_the_file_checks_flag_that_opens_nothing` keeps these). Name
# such a thing otherwise. (A harmless parameter named `os` was flagged by #99's
# rebinding check, which went with its `os.open` exception in #104.)
READ_OPENERS = {
    "read_text", "read_bytes", "load_verify_locations", "load_cert_chain",
    "load_dh_params", "freedesktop_os_release", "libc_ver", "read_resolv_conf",
}
FILE_KEYWORDS = {"cafile", "capath", "certfile", "keyfile", "filename"}
OPENED_WHERE = {
    # the host's resolv.conf: dnspython opens it, once `os.stat` says it is a
    # regular file of at most RESOLV_CONF_MAX bytes
    "probe.py": {"read_resolv_conf"},
    "interfaces.py": {"read_text"},  # an interface's MAC, from /sys/class/net
}


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
    return name.startswith(FORBIDDEN_PREFIXES)


def _file_opens(tree) -> set[str]:
    """The enumerated spellings of having a file opened (READ_OPENERS, an unconfigured
    `Resolver`, FILE_KEYWORDS as a keyword or a string) the module uses, called or
    not; not every way a file can be opened (the comment on READ_OPENERS)."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in READ_OPENERS:
            found.add(node.attr)
        elif isinstance(node, ast.Name) and node.id in READ_OPENERS:
            found.add(node.id)
        elif isinstance(node, ast.Constant) and node.value in FILE_KEYWORDS:
            found.add(f"{node.value}=")  # a keyword passed as `**{"filename": ...}`
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


def _refused_imports(tree, module: str) -> set[str]:
    imported = set(_imports(tree))
    confined = {m for m in imported if IMPORTED_ONLY_IN.get(m, module) != module}
    return (imported - ALLOWED_IMPORTS) | confined


def _refused_names(tree, module: str) -> set[str]:
    allowed = NAMES_WHERE.get(module, set())
    return {n for n in _identifiers(tree) if _forbidden(n) and n not in allowed}


def _violations(tree, module: str) -> list[str]:
    """Everything the checks in this file refuse in one module's source, each with the
    check that refuses it: `import ...` (the allowlist, and IMPORTED_ONLY_IN), `name
    ...` (a listed name, but those NAMES_WHERE allows the module), `opens a file with
    ...` (an enumerated opener outside OPENED_WHERE)."""
    found = [f"import {m}" for m in sorted(_refused_imports(tree, module))]
    found += sorted({f"name {n}" for n in _refused_names(tree, module)})
    found += [f"opens a file with {o}" for o in sorted(_file_opens(tree) - OPENED_WHERE.get(module, set()))]
    return found


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_only_listed_modules_are_imported(path):
    refused = _refused_imports(ast.parse(path.read_text()), path.name)
    assert refused == set(), f"{path.name}: {sorted(refused)}"


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_nothing_that_could_run_load_or_fetch_code_is_named(path):
    found = sorted(_refused_names(ast.parse(path.read_text()), path.name))
    assert found == [], f"{path.name}: {found}"


def test_each_name_a_module_is_allowed_is_one_it_uses():
    """NAMES_WHERE allows no more than the module uses, so a name dropped from the code
    cannot stay allowed unnoticed; and every name in it is a listed one."""
    for module, names in NAMES_WHERE.items():
        used = {n for n in _identifiers(ast.parse((PACKAGE / module).read_text())) if _forbidden(n)}
        assert used == names, module
        assert all(_forbidden(n) for n in names)


@pytest.mark.parametrize(
    "source, module, reasons",
    [
        # what store.py may do, refused anywhere else
        ("import os\nfd = os.open('x', os.O_WRONLY | os.O_CREAT)\n", "loop.py", ["name open"]),
        ("import os\nos.rename('x', 'y')\n", "install.py", ["name rename"]),
        ("from pathlib import Path\nPath('x').unlink()\n", "enrol.py", ["name unlink"]),
        ("from pathlib import Path\nPath('x').mkdir()\n", "cli.py", ["name mkdir"]),
        ("import os\nos.fchown(3, 0, 0)\n", "probe.py", ["name fchown"]),
        # what install.py may do, refused anywhere else, in every spelling of the import
        ("import subprocess\nsubprocess.run(['x'])\n", "store.py", ["import subprocess", "name subprocess"]),
        ("from subprocess import run\nrun(['x'])\n", "enrol.py", ["import subprocess"]),
        ("import subprocess as s\ns.run(['x'])\n", "loop.py", ["import subprocess", "name subprocess"]),
        # and neither is given the other's
        ("import os\nos.unlink('x')\n", "install.py", ["name unlink"]),
        # what neither is given
        ("import os\nos.chown('x', 0, 0)\n", "store.py", ["name chown"]),
        ("import os\nos.execv('x', ['x'])\n", "install.py", ["name execv"]),
        ("import os\nos.makedirs('x')\n", "store.py", ["name makedirs"]),
    ],
)
def test_a_name_allowed_in_one_module_is_refused_in_any_other(source, module, reasons):
    """NAMES_WHERE and IMPORTED_ONLY_IN place each allowance in one module: appended to
    another real module, the same source is refused for exactly its reasons."""
    base = (PACKAGE / module).read_text() + "\n"
    assert _violations(ast.parse(base + source), module) == reasons


def _relative_imports(path) -> set[str]:
    """The agent's own modules a module imports (`from . import store`, `from .store
    import ...`)."""
    found = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.level == 1:
            if node.module:
                found.add(node.module.split(".")[0])
            else:
                found.update(alias.name for alias in node.names)
    return found


def test_only_the_enrolment_and_the_installer_write_files():
    """`store` is how a file is written, and `install` how a process is started: who
    imports them is who can (#22). The container's first start stores its token
    through `enrol`; `install` is reached only from the command line."""
    users = {p.name: _relative_imports(p) for p in MODULES}
    assert {name for name, used in users.items() if "store" in used} == {"enrol.py", "install.py"}
    assert {name for name, used in users.items() if "install" in used} == {"cli.py"}


def test_files_are_opened_only_where_listed():
    """The enumerated spellings of having a file opened (`_file_opens`), in every
    module: the reads OPENED_WHERE names are the only ones, so an inert `_file_opens`
    fails here too. A tripwire on spellings, not a proof that no module opens a file
    (the comment on READ_OPENERS says what it does not see)."""
    used = {p.name: _file_opens(ast.parse(p.read_text())) for p in MODULES}
    assert {name: ways for name, ways in used.items() if ways} == OPENED_WHERE


def test_the_real_modules_pass_every_check():
    assert {p.name: _violations(ast.parse(p.read_text()), p.name) for p in MODULES} == {p.name: [] for p in MODULES}


# Sources each check must refuse, with exactly the reasons `_violations` gives: a case
# names every check that catches it, so disabling any one of them turns its case red
# on that check's own reason, and one check cannot stand in for another.
REFUSED = [
    # the review of PR #70: an uncalled `create_subprocess_exec` passed a call-only guard
    ("import asyncio\nasync def f(c):\n    await asyncio.create_subprocess_exec(c)\n",
     ["name create_subprocess_exec"]),  # a prefix, on an attribute
    ("import os\ndef f(c):\n    os.execv(c, [c])\n", ["name execv"]),
    ("def f(m):\n    return getattr(__builtins__, 'ev' + 'al')(m)\n",
     ["name __builtins__", "name getattr"]),  # bare names
    ("import builtins\nf = builtins.__dict__['exec']\n",
     ["import builtins", "name __dict__", "name exec"]),
    ("x = 'subprocess'\n", ["name subprocess"]),  # a string
    ("from os import system as run\n", ["name system"]),  # an imported name
    ("def open(path):\n    pass\n", ["name open"]),  # a definition
    ("def f(m):\n    return m.compile('x')\n", ["name compile"]),  # only `re.compile` passes
    ("from pathlib import Path\nPath('x').write_text('y')\n", ["name write_text"]),
    ("with open('x', 'w') as f:\n    pass\n", ["name open"]),
    ("import urllib.request\n", ["import urllib.request"]),
    # round 1 of #103: `io` allowed whole, where FileIO writes and open_code opens
    ("import io\ndef bypass(path):\n    io.FileIO(path, 'w').write(b'y')\n", ["import io", "name FileIO"]),
    ("import io\ndef bypass(path):\n    return io.open_code(path)\n", ["import io", "name open_code"]),
    ("from io import FileIO as F\nF('x', 'w')\n", ["import io", "name FileIO"]),
    # `os.open` in every spelling, the read-only call #99 allowed among them
    ("import os\nfd = os.open('x', os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY)\n", ["name open"]),
    ("import os\nfd = os.open('x', os.O_WRONLY | os.O_CREAT | os.O_TRUNC)\nos.write(fd, b'y')\n", ["name open"]),
    ("import os\nwrite = os.open\n", ["name open"]),
    ("from os import open as o\n", ["name open"]),
    ("import os as o\nfd = o.open('x', o.O_RDONLY)\n", ["name open"]),
    # round 1 of #103: the flags rebound before a read-only `os.open`
    ("import os\ndef bypass(path):\n    saved = os.O_RDONLY\n    os.O_RDONLY = os.O_WRONLY | os.O_CREAT | os.O_TRUNC\n"
     "    fd = os.open(path, os.O_RDONLY)\n    os.write(fd, b'y')\n    os.O_RDONLY = saved\n", ["name open"]),
    ("import os\nos.__setattr__('O_RDONLY', 577)\nfd = os.open('x', os.O_RDONLY)\n",
     ["name __setattr__", "name open"]),
    # round 2 of #103 (#104): an alias of `os`, and a default argument bound to it
    ("import os\ndef bypass(path):\n    alias = os\n    saved = alias.O_RDONLY\n"
     "    alias.O_RDONLY = alias.O_WRONLY | alias.O_CREAT | alias.O_TRUNC\n    try:\n"
     "        fd = os.open(path, os.O_RDONLY)\n        os.write(fd, b'y')\n        os.close(fd)\n"
     "    finally:\n        alias.O_RDONLY = saved\n", ["name open"]),
    ("import os\ndef bypass(path, other=os):\n    other.O_RDONLY = other.O_WRONLY | other.O_CREAT\n"
     "    fd = os.open(path, os.O_RDONLY)\n    os.write(fd, b'y')\n", ["name open"]),
    # other named openers of the allowed modules (#99)
    ("import os\nos.fdopen(0, 'w')\n", ["name fdopen"]),
    ("from pathlib import Path\nPath('x').touch()\n", ["name touch"]),
    ("import os\nos.truncate('x', 0)\n", ["name truncate"]),
    ("import logging\nlogging.FileHandler('x')\n", ["name FileHandler"]),
    ("from pathlib import Path\nkey = Path('x').read_bytes()\n", ["opens a file with read_bytes"]),
    ("text = read_text('x')\n", ["opens a file with read_text"]),  # a bare name
    ("import dns.resolver\nr = dns.resolver.Resolver()\n", ["opens a file with Resolver"]),
    ("import ssl\nssl.create_default_context(cafile='x')\n", ["opens a file with cafile="]),
    ("import logging\nlogging.basicConfig(filename='x')\n", ["opens a file with filename="]),
    # round 2 of #103: the same keywords passed through `**{...}` (#104)
    ("import logging\nlogging.basicConfig(**{'filename': 'x', 'force': True})\n", ["opens a file with filename="]),
    ("import ssl\nssl.create_default_context(**{'cafile': 'x'})\n", ["opens a file with cafile="]),
]


@pytest.mark.parametrize("module", ["snippet.py", "arp.py", "probe.py"])
@pytest.mark.parametrize("source, reasons", REFUSED)
def test_each_check_refuses_what_it_is_there_for(source, reasons, module):
    """Each source, alone and appended to the real arp.py and probe.py, is refused for
    exactly its reasons. probe.py is where the agent's one file read is allowed, so a
    case appended there is refused by the check named, not by being in the wrong
    module (round 2 of #103: an inert flag check passed every case written as a
    snippet, where any `os.open` was refused for its placement)."""
    base = "" if module == "snippet.py" else (PACKAGE / module).read_text() + "\n"
    assert _violations(ast.parse(base + source), module) == reasons


# One refused source for each entry of FORBIDDEN, FORBIDDEN_PREFIXES and FILE_KEYWORDS
# (#107: before this, 44 of the 60 names, three of the five keywords and four of the six
# prefixes could be dropped with every case above green). The names below are written
# out and are not read from the lists they guard, so deleting an entry from a list
# leaves its case here to fail: `test_every_entry_of_each_list_has_a_case` is the
# other direction, a new entry needing a case. Each is refused for exactly its own
# reason, alone and appended to the real arp.py and probe.py, as above.
FORBIDDEN_NAME_CASES = (
    "exec", "eval", "compile", "__import__", "breakpoint", "getattr", "setattr",
    "delattr", "globals", "locals", "vars", "__builtins__", "__dict__", "__globals__",
    "__code__", "__subclasses__", "__loader__", "system", "popen", "startfile",
    "run_path", "run_module", "import_module", "load_module", "exec_module",
    "find_spec", "reload", "write_text", "write_bytes", "open", "chmod", "symlink",
    "subprocess", "pickle", "marshal", "ctypes", "importlib", "runpy", "pip",
    "ensurepip", "venv", "shutil", "pty", "zipimport", "urlopen", "urlretrieve",
    "FileIO", "open_code", "fdopen", "openpty", "mkfifo", "mknod", "touch", "truncate",
    "symlink_to", "hardlink_to", "link_to", "FileHandler", "__setattr__", "__delattr__",
    "mkdir", "makedirs", "rename", "renames", "unlink", "remove", "rmdir", "removedirs",
    "chown", "fchown", "lchown", "fchmod", "lchmod", "ftruncate", "mkdtemp", "mkstemp",
)
# a name that is on no list but its prefix, on an attribute of `os`
FORBIDDEN_PREFIX_CASES = {
    "exec": "execv",
    "spawn": "spawnl",
    "fork": "forkpty",
    "posix_spawn": "posix_spawnp",
    "create_subprocess": "create_subprocess_shell",
    "subprocess_": "subprocess_exec",
}
# each keyword as a call's keyword and as a key of `**{...}`
FILE_KEYWORD_CASES = [
    (keyword, form, source)
    for keyword, (call, unpacked) in {
        "cafile": ("wrap(cafile='x')", "wrap(**{'cafile': 'x'})"),
        "capath": ("wrap(capath='x')", "wrap(**{'capath': 'x'})"),
        "certfile": ("wrap(certfile='x')", "wrap(**{'certfile': 'x'})"),
        "keyfile": ("wrap(keyfile='x')", "wrap(**{'keyfile': 'x'})"),
        "filename": ("wrap(filename='x')", "wrap(**{'filename': 'x'})"),
    }.items()
    for form, source in (("keyword", call), ("unpacked", unpacked))
]


def _refused_in(source: str, module: str) -> list[str]:
    base = "" if module == "snippet.py" else (PACKAGE / module).read_text() + "\n"
    return _violations(ast.parse(base + source), module)


@pytest.mark.parametrize("module", ["snippet.py", "arp.py", "probe.py"])
@pytest.mark.parametrize("name", FORBIDDEN_NAME_CASES)
def test_each_forbidden_name_is_refused_for_its_own_reason(name, module):
    assert _refused_in(f"value = {name}\n", module) == [f"name {name}"]


@pytest.mark.parametrize("module", ["snippet.py", "arp.py", "probe.py"])
@pytest.mark.parametrize("prefix", FORBIDDEN_PREFIX_CASES)
def test_each_forbidden_prefix_is_refused_for_its_own_reason(prefix, module):
    name = FORBIDDEN_PREFIX_CASES[prefix]
    assert not name.startswith(tuple(p for p in FORBIDDEN_PREFIX_CASES if p != prefix))
    assert _refused_in(f"import os\nvalue = os.{name}\n", module) == [f"name {name}"]


@pytest.mark.parametrize("module", ["snippet.py", "arp.py", "probe.py"])
@pytest.mark.parametrize(
    "keyword, form, source", FILE_KEYWORD_CASES, ids=[f"{k}-{f}" for k, f, _ in FILE_KEYWORD_CASES]
)
def test_each_file_keyword_is_refused_for_its_own_reason(keyword, form, source, module):
    assert _refused_in(source + "\n", module) == [f"opens a file with {keyword}="]


def test_every_entry_of_each_list_has_a_case():
    """The other direction from the cases above, which stand when an entry is deleted:
    an entry added to a list needs a case written out beside it."""
    assert set(FORBIDDEN_NAME_CASES) == FORBIDDEN
    assert len(FORBIDDEN_NAME_CASES) == len(set(FORBIDDEN_NAME_CASES))
    assert set(FORBIDDEN_PREFIX_CASES) == set(FORBIDDEN_PREFIXES)
    assert {keyword for keyword, _, _ in FILE_KEYWORD_CASES} == FILE_KEYWORDS


@pytest.mark.parametrize("name", ["exception", "exc_info", "exc_text", "exctype", "excess", "specs", "forward"])
def test_names_near_a_forbidden_prefix_are_not_refused(name):
    """What the removed PREFIX_EXCEPTIONS list was for (#107): `exception` and its
    neighbours do not start with `exec`, so the prefix check never needed them
    exempted. The prefixes are exact beginnings, and these are not among them."""
    assert _violations(ast.parse(f"value = {name}\n"), "snippet.py") == []


def test_a_read_passes_only_in_the_module_listed_for_it():
    """The placement check alone: dnspython's `read_resolv_conf`, the read probe.py is
    allowed, is refused appended to arp.py."""
    source = "\nresolver.read_resolv_conf(path)\n"
    probe, arp = ((PACKAGE / m).read_text() + source for m in ("probe.py", "arp.py"))
    assert _violations(ast.parse(probe), "probe.py") == []
    assert _violations(ast.parse(arp), "arp.py") == ["opens a file with read_resolv_conf"]


@pytest.mark.parametrize(
    "source, reasons",
    [
        ("def label(**kw):\n    pass\nlabel(filename='report')\n", ["opens a file with filename="]),
        ("fields = ['filename', 'lineno']\n", ["opens a file with filename="]),
        ("class Resolver:\n    pass\nr = Resolver()\n", ["opens a file with Resolver"]),
        ("read_bytes = 4096\n", ["opens a file with read_bytes"]),
    ],
)
def test_what_the_file_checks_flag_that_opens_nothing(source, reasons):
    """The false alarms round 2 of #103 found, kept so that a future author who meets
    one finds why: the checks match names, not types. None of these opens a file; the
    fix is another name, not a narrower check."""
    assert _violations(ast.parse(source), "snippet.py") == reasons


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
