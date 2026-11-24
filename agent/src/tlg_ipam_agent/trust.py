"""Whether the code `install` and `uninstall` would run as root is root's alone (#22).

Both commands run as root, from the virtual environment the agent was installed into,
and the unit `install` writes runs the same code with CAP_NET_RAW. Whoever can change
a file Python could load, or a directory or link on the way to one, could run their own
code as root during the install and with the capability ever after. `check` refuses
that. `cli` calls it before it imports any other module of the agent's, and so before
any of the agent's dependencies (httpx, icmplib, dnspython) is imported.

What `check` looks at:

- the interpreter the unit will name (`sys.executable`), resolved one path component
  at a time and one link at a time, so every directory on the way from `/` and every
  directory a link passes through is checked, as well as the file it ends at;
- every entry of `sys.path` (the virtual environment's site-packages and `bin`, the
  standard library, and anything `PYTHONPATH` or a `.pth` file added), each resolved
  the same way and then walked whole: every directory and file under it, a link in it
  followed in the same way, a linked directory walked too;
- the agent's own package directory, walked the same way, wherever it was loaded from.

`cli` takes the current directory off `sys.path` first (`python -m` puts it there),
so no later import comes from it and the walk never covers a working directory such
as `/`; the package itself, if it was loaded from there, is walked as the last item
says.

Each must be owned by root (`owner`) and writable by neither its group nor others. A
missing `sys.path` entry is skipped, since nothing can be imported from it, though
root could later create it.

What it cannot see:

- what has already run when it runs: the interpreter's own start-up (`site` and the
  `.pth` files it processes, `sitecustomize`), the agent's `__init__.py` and
  `__main__.py` or the console script, `cli.py`, this module, and the standard-library
  modules those import (`argparse`, `os`, `stat`, `sys`). Each of those files lies in a
  tree it walks, so a changed one is refused, but only after it has run;
- what is loaded from outside Python's import path: the interpreter's shared
  libraries and the system libraries an extension module links against (libc,
  libssl), which are the system's, and any file a library opens itself;
- a change made after it ran. Every directory it passed is root's, so after it only
  root can make one.
"""

import os
import stat
import sys

# how many links a path may pass through, as the kernel's own limit for one lookup
HOPS_MAX = 40


class Untrusted(Exception):
    """The path `check` refused, as its message."""


def _changeable(info: os.stat_result, owner: int) -> bool:
    return info.st_uid != owner or bool(info.st_mode & (stat.S_IWGRP | stat.S_IWOTH))


def _within(path: str, top: str) -> bool:
    return top == "/" or path == top or path.startswith(top + "/")


def resolve(path: str, refuse, top: str = "/") -> str:
    """Follows `path` one component and one link at a time, from `/`, and returns the
    real path it names. `refuse(path, info, last)` is asked about every directory on
    the way (`last` False) and about what the path ends at (`last` True); a link is not
    asked about, since the directory that holds it was. A true answer raises
    Untrusted(path). Paths outside `top` are resolved but not asked about (a test's
    own directory; `/` in use). FileNotFoundError when something on the way is
    missing; Untrusted past HOPS_MAX links."""
    pending = [part for part in path.split("/") if part]
    current = "/"
    if _within("/", top) and refuse("/", os.stat("/"), not pending):
        raise Untrusted("/")
    hops = 0
    while pending:
        name = pending.pop(0)
        if name == ".":
            continue
        if name == "..":
            # `current` holds no link, so its parent is the real one
            current = os.path.dirname(current)
            continue
        candidate = os.path.join(current, name)
        info = os.lstat(candidate)
        if stat.S_ISLNK(info.st_mode):
            hops += 1
            if hops > HOPS_MAX:
                raise Untrusted(f"{candidate} (more than {HOPS_MAX} links)")
            target = os.readlink(candidate)
            if target.startswith("/"):
                current = "/"
            pending = [part for part in target.split("/") if part] + pending
            continue
        if _within(candidate, top) and refuse(candidate, info, not pending):
            raise Untrusted(candidate)
        current = candidate
    return current


def _walk(root: str, owner: int, top: str, seen: set[str]) -> None:
    refuse = lambda path, info, last: _changeable(info, owner)  # noqa: E731
    real = resolve(root, refuse, top)
    if real in seen or not os.path.isdir(real):
        return
    seen.add(real)
    for directory, dirs, files in os.walk(real):
        for name in (*dirs, *files):
            path = os.path.join(directory, name)
            info = os.lstat(path)
            if stat.S_ISLNK(info.st_mode):
                try:
                    _walk(path, owner, top, seen)
                except FileNotFoundError:
                    continue  # a dangling link loads nothing
            elif _within(path, top) and _changeable(info, owner):
                raise Untrusted(path)


PACKAGE = os.path.dirname(os.path.abspath(__file__))


def without_working_directory(search: list[str]) -> list[str]:
    """`search` without the entries that name the current directory (`""`, `.`, or
    its path), which `python -m` and `python -c` put first."""
    here = os.getcwd()
    return [entry for entry in search if entry not in ("", ".") and os.path.abspath(entry) != here]


def check(
    interpreter: str = sys.executable,
    search: list[str] | None = None,
    *,
    package: str = PACKAGE,
    owner: int = 0,
    top: str = "/",
) -> None:
    """Raises Untrusted naming the first path, of those the module docstring lists,
    that a user other than `owner` owns or could change. `search` is `sys.path` unless
    given; `owner` and `top` are for a test."""
    resolve(interpreter, lambda path, info, last: _changeable(info, owner), top)
    seen: set[str] = set()
    for entry in [package, *(sys.path if search is None else search)]:
        try:
            _walk(os.path.abspath(entry or "."), owner, top, seen)
        except FileNotFoundError:
            continue
