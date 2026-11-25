"""`python -I -m tlg_ipam_agent.reach`: whether the user it runs as can use the agent's
code (#169, spec §5.6).

`install` runs it as the service user, with the interpreter the unit names, once the
account exists and before anything is written or the enrolment token spent
(`install.runs_as`). What it shows comes in two parts, both answered by the kernel:

- That it starts at all. The kernel let that user execute the interpreter by the path
  the unit's `ExecStart=` names (every directory and link on the way, the interpreter
  a link resolves to, a `noexec` mount), and Python's start-up opened what it reads
  first: the standard library it imports, the environment's `pyvenv.cfg` and
  `site-packages`, the `.pth` files there, and the package's `__init__` and this
  module. These are real opens, so a security module that checks an open is asked
  too. A start that fails exits non-zero (1, from Python) or is refused at exec.
- What it then finds. It walks the virtual environment (`sys.prefix`) and the agent's
  package directory, wherever it was loaded from, and exits UNUSABLE (3) at the first
  directory that user cannot list and enter or regular file it cannot read, naming it
  on stderr (`install` sends that to /dev/null; run by hand as the service user, it
  says which). Each directory is listed by a real open (`os.scandir`); each file is
  asked of `os.access`, which the kernel answers with POSIX ACLs and every group the
  process holds, but which a security module that checks only an open does not see.
  A `__pycache__` directory is not walked: Python reads a module's cached bytecode
  when it can and compiles the source when it cannot, so an unreadable cache costs
  the agent time, not its start. The installer, run as root, writes that cache itself
  as it imports the dependencies, as root's group and with the source's mode, so in an
  environment the service user reads through a group the cache is root's alone
  (measured in the PR of #169: the walk refused such an environment on it).

A source file it cannot read is refused even where Python could import the module
without it, from cached bytecode it can read (measured in the PR of #169: the root-run
installer had cached a module an ACL denied the user, and the unit ran on the cache).
That cache is the root-run installer's, rewritten or dropped as the source changes, so
it is not counted on.

It does not show that a module the agent imports later can be loaded (only that it is
readable), that the code works, or anything outside those two trees but what the
start reads; and it answers for the process it runs in, so for the unit only as far as
the unit gets the same user, groups and view of the filesystem (spec §5.6 says what a
drop-in can change).

It takes no argument, reads no environment, imports nothing of the agent's and nothing
but `os`, `stat` and `sys`, and writes nothing (`-I` keeps the working directory, the
user's site directory and `PYTHON*` variables out of it; it does not stop the
environment's `.pth` files or a `sitecustomize`, which run before it). Its exit status
is its whole answer, and a walk that finished with nothing to report exits USABLE (7),
never 0: a start-up hook that ends the interpreter early, with status 0, before this
module has run would otherwise be read as an answer (the briefed review of PR #178).
That tells an ordinary early end apart from the walk's answer; it does not attest
against start-up code written to lie, which only root can put in the environment
(`trust.check`) and which runs as this user with no more than its rights.
"""

import os
import stat
import sys

# the exit statuses of the walk's two answers: "every directory and file of the two
# trees is usable" and "one is not". Python itself exits 0 when the interpreter ends
# normally (a start-up hook's `os._exit(0)`, say), 1 when it cannot start the module
# and 2 on a command-line error, so neither answer is one of those
USABLE = 7
UNUSABLE = 3

PACKAGE = os.path.dirname(os.path.realpath(__file__))
# what Python names the directory of a module's cached bytecode (PEP 3147), which the
# walk passes over
BYTECODE_CACHE = "__pycache__"


def first_unusable(trees) -> str | None:
    """The first directory under `trees` (each one included) the running process
    cannot list and enter, or regular file it cannot read, as the kernel answers it;
    None when there is none. A `__pycache__` directory below them is passed over. A link is not followed: what it names outside the trees
    is the start's to show (the interpreter `bin/python` names), and a linked
    directory inside them (`lib64`) is walked under its own name."""
    pending = [str(tree) for tree in reversed(tuple(trees))]
    while pending:
        directory = pending.pop()
        if not os.access(directory, os.R_OK | os.X_OK):
            return directory
        try:
            with os.scandir(directory) as listing:
                entries = sorted(listing, key=lambda entry: entry.name)
        except OSError:
            return directory
        below = []
        for entry in entries:
            try:
                mode = entry.stat(follow_symlinks=False).st_mode
            except OSError:
                return entry.path
            if stat.S_ISDIR(mode):
                if entry.name != BYTECODE_CACHE:
                    below.append(entry.path)
            elif stat.S_ISREG(mode) and not os.access(entry.path, os.R_OK):
                return entry.path
        pending.extend(reversed(below))
    return None


def main() -> int:
    stopped = first_unusable((sys.prefix, PACKAGE))
    if stopped is None:
        return USABLE
    print(f"not usable by this user: {stopped!r}", file=sys.stderr)
    return UNUSABLE


if __name__ == "__main__":
    sys.exit(main())
