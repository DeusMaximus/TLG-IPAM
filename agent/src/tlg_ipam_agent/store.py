"""The one agent module that opens, writes and removes files itself (spec §5.6, #22).

What it is for, and nothing else: the stored agent token (`/var/lib/tlg-ipam-agent/token`,
read on every start, written by the enrolment exchange), and the files and directories
`tlg-ipam-agent install` puts on the host and `uninstall` takes away. Every module but
this one is held to opening no file at all (`tests/test_invariants.py`, `WRITES_WHERE`).

Nothing here takes a path or content from the server: the paths are the agent's own
constants (or a test's directory), and the one thing the server supplies, the agent
token, is checked for the token's shape (`config.parse_token`) before it is written.

A file is read without following a link at its path (`O_NOFOLLOW`) and without waiting
on a FIFO or device (`O_NONBLOCK`, then `fstat`), and only up to READ_MAX bytes. A file
is written beside its final path, created exclusively with its mode and owner set
before any byte goes in, flushed to disk, and renamed into place, so a reader sees the
old file or the whole new one; the directory is flushed after the rename.
"""

import fcntl
import os
import stat

# a token file holds a few dozen bytes; anything larger is not one
READ_MAX = 4096
# the suffix of a file being written, beside its final path
PENDING_SUFFIX = ".new"

_NO_LINK = os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NOCTTY


class StoreError(Exception):
    """A file or directory the agent cannot use. The message names the path and the
    operating system's reason, never the file's content."""


def _reason(exc: OSError) -> str:
    return exc.strerror or type(exc).__name__


def read_small(path: str) -> bytes | None:
    """The bytes of the regular file at `path`, at most READ_MAX of them, or None when
    there is nothing at `path`. A link, a directory, a FIFO, a device or a larger file
    is refused (StoreError), and so is a file the agent may not read."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | _NO_LINK)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise StoreError(f"{path} cannot be read: {_reason(exc)}") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise StoreError(f"{path} is not a regular file")
        if info.st_size > READ_MAX:
            raise StoreError(f"{path} is larger than {READ_MAX} bytes")
        data = bytearray()
        while len(data) <= READ_MAX:
            chunk = os.read(fd, READ_MAX + 1 - len(data))
            if not chunk:
                break
            data += chunk
        if len(data) > READ_MAX:
            raise StoreError(f"{path} is larger than {READ_MAX} bytes")
        return bytes(data)
    except OSError as exc:
        raise StoreError(f"{path} cannot be read: {_reason(exc)}") from None
    finally:
        os.close(fd)


def check_dir(path: str, owner: int) -> None:
    """Refuses (StoreError) a directory the agent should not keep a credential in: not
    there, a link, not a directory, not owned by `owner`, or writable by its group or
    by others, any of whom could swap the file under it."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        raise StoreError(f"{path} does not exist") from None
    except OSError as exc:
        raise StoreError(f"{path} cannot be used: {_reason(exc)}") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise StoreError(f"{path} is not a directory")
    if info.st_uid != owner:
        raise StoreError(f"{path} is not owned by uid {owner}")
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise StoreError(f"{path} is writable by users other than its owner")


class DirLock:
    """An exclusive `flock(2)` on a directory, so that two agent processes sharing one
    state directory (two containers on one volume) never exchange and write at once:
    each would remove the other's pending file. It is held from before the stored token
    is looked for to after the new one is in place, and released when the process ends
    however it ends. `acquire` does not wait: it says whether it got the lock."""

    def __init__(self, path: str):
        self.path = path
        self._fd: int | None = None

    def acquire(self) -> bool:
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        except OSError as exc:
            raise StoreError(f"{self.path} cannot be locked: {_reason(exc)}") from None
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        except OSError as exc:
            os.close(fd)
            raise StoreError(f"{self.path} cannot be locked: {_reason(exc)}") from None
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)  # closing the descriptor releases the lock
            self._fd = None


def _sync_dir(path: str) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class PendingFile:
    """A file being written to `path`. It is created at `path` + PENDING_SUFFIX, with
    `O_EXCL` (after removing one a stopped run left there), its mode set to `mode`
    exactly and its owner to `owner` (uid, gid) before anything is written. `commit`
    writes the content, flushes it and renames it into place; `discard`, or leaving a
    `with` block without committing, removes it.

    It exists before the content does so that a directory that cannot take the file
    fails before the enrolment exchange, not after it has spent the token."""

    def __init__(self, path: str, mode: int, owner: tuple[int, int] | None = None):
        self.path = path
        self.pending = path + PENDING_SUFFIX
        remove_file(self.pending)
        try:
            self._fd: int | None = os.open(
                self.pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NO_LINK, mode
            )
        except OSError as exc:
            raise StoreError(f"{self.pending} cannot be created: {_reason(exc)}") from None
        try:
            if owner is not None:
                os.fchown(self._fd, *owner)
            # the mode exactly, whatever the process's umask took from it
            os.fchmod(self._fd, mode)
        except OSError as exc:
            self.discard()
            raise StoreError(f"{self.pending} cannot be set up: {_reason(exc)}") from None

    def commit(self, data: bytes) -> None:
        if self._fd is None:
            raise StoreError(f"{self.pending} is no longer open")
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(self._fd, view):]
            os.fsync(self._fd)
            os.close(self._fd)
            self._fd = None
            os.rename(self.pending, self.path)
            _sync_dir(os.path.dirname(self.path) or ".")
        except OSError as exc:
            self.discard()
            raise StoreError(f"{self.path} cannot be written: {_reason(exc)}") from None

    def discard(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        remove_file(self.pending)

    def __enter__(self) -> "PendingFile":
        return self

    def __exit__(self, *exc) -> None:
        # after a commit the pending path is gone and the fd closed: nothing to do
        if self._fd is not None:
            self.discard()


def write_file(path: str, data: bytes, mode: int, owner: tuple[int, int] | None = None) -> None:
    with PendingFile(path, mode, owner) as pending:
        pending.commit(data)


def make_dir(path: str, mode: int) -> bool:
    """Creates the directory with exactly `mode`, owned by the process, and says whether
    it did. One already there is kept if `check_dir` accepts it for the process's user,
    and its mode is not changed."""
    try:
        os.mkdir(path, mode)
    except FileExistsError:
        check_dir(path, os.geteuid())
        return False
    except OSError as exc:
        raise StoreError(f"{path} cannot be created: {_reason(exc)}") from None
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | _NO_LINK)
    try:
        os.fchmod(fd, mode)
    finally:
        os.close(fd)
    return True


def remove_file(path: str) -> bool:
    """Removes the file (or link) at `path`, and says whether there was one."""
    try:
        os.unlink(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise StoreError(f"{path} cannot be removed: {_reason(exc)}") from None
    return True


def remove_dir(path: str) -> bool:
    """Removes the empty directory at `path`, and says whether there was one. One that
    holds anything is refused (StoreError), and what it holds is left alone."""
    try:
        os.rmdir(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise StoreError(f"{path} cannot be removed: {_reason(exc)}") from None
    return True
