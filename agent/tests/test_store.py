"""The one module that opens and writes files (#22): the stored token, and what the
install writes. Every path is inside the test's own temporary directory."""

import os
import stat
import threading

import pytest

from tlg_ipam_agent import store
from tlg_ipam_agent.store import PendingFile, StoreError


def _mode(path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


# --- reading ---


def test_nothing_at_the_path_is_none(tmp_path):
    assert store.read_small(str(tmp_path / "token")) is None


def test_a_small_regular_file_is_read_whole(tmp_path):
    path = tmp_path / "token"
    path.write_bytes(b"x" * store.READ_MAX)
    assert store.read_small(str(path)) == b"x" * store.READ_MAX


def test_a_larger_file_is_refused(tmp_path):
    path = tmp_path / "token"
    path.write_bytes(b"x" * (store.READ_MAX + 1))
    with pytest.raises(StoreError, match="larger than"):
        store.read_small(str(path))


def test_a_link_at_the_path_is_not_followed(tmp_path):
    target = tmp_path / "elsewhere"
    target.write_bytes(b"tlga_secret")
    (tmp_path / "token").symlink_to(target)
    with pytest.raises(StoreError, match="cannot be read") as err:
        store.read_small(str(tmp_path / "token"))
    assert "tlga_secret" not in str(err.value)


def test_a_directory_is_refused(tmp_path):
    (tmp_path / "token").mkdir()
    with pytest.raises(StoreError):
        store.read_small(str(tmp_path / "token"))


def test_a_fifo_is_refused_without_waiting_for_a_writer(tmp_path):
    path = tmp_path / "token"
    os.mkfifo(path)
    done = threading.Event()
    failure = []

    def read():
        try:
            store.read_small(str(path))
        except StoreError as exc:
            failure.append(exc)
        done.set()

    threading.Thread(target=read, daemon=True).start()
    assert done.wait(5), "the read waited on the FIFO"
    assert "not a regular file" in str(failure[0])


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_a_file_the_agent_may_not_read_is_refused(tmp_path):
    path = tmp_path / "token"
    path.write_bytes(b"tlga_x")
    path.chmod(0)
    with pytest.raises(StoreError, match="cannot be read"):
        store.read_small(str(path))


# --- writing ---


@pytest.mark.parametrize("umask", [0o000, 0o022, 0o077])
@pytest.mark.parametrize("mode", [0o600, 0o644])
def test_a_written_file_has_exactly_its_mode_whatever_the_umask(tmp_path, umask, mode):
    old = os.umask(umask)
    try:
        store.write_file(str(tmp_path / "f"), b"data", mode)
    finally:
        os.umask(old)
    assert _mode(tmp_path / "f") == mode
    assert (tmp_path / "f").read_bytes() == b"data"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f"]


def test_a_file_is_put_in_place_whole_only_at_commit(tmp_path):
    path = tmp_path / "token"
    path.write_bytes(b"old")
    with PendingFile(str(path), 0o600) as pending:
        # the pending file exists, empty, beside the old one, which is untouched
        assert (tmp_path / "token.new").read_bytes() == b""
        assert _mode(tmp_path / "token.new") == 0o600
        assert path.read_bytes() == b"old"
        pending.commit(b"new")
    assert path.read_bytes() == b"new"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["token"]


def test_leaving_without_a_commit_leaves_nothing(tmp_path):
    with pytest.raises(RuntimeError):
        with PendingFile(str(tmp_path / "token"), 0o600):
            raise RuntimeError("the exchange failed")
    assert list(tmp_path.iterdir()) == []


def test_a_pending_file_a_stopped_run_left_is_replaced(tmp_path):
    (tmp_path / "token.new").write_bytes(b"half")
    store.write_file(str(tmp_path / "token"), b"whole", 0o600)
    assert (tmp_path / "token").read_bytes() == b"whole"
    assert not (tmp_path / "token.new").exists()


def test_a_link_at_the_pending_path_is_removed_not_followed(tmp_path):
    target = tmp_path / "target"
    target.write_bytes(b"keep")
    (tmp_path / "token.new").symlink_to(target)
    store.write_file(str(tmp_path / "token"), b"t", 0o600)
    assert target.read_bytes() == b"keep"


def test_the_owner_is_set_before_anything_is_written(tmp_path):
    owner = (os.getuid(), os.getgid())
    store.write_file(str(tmp_path / "token"), b"t", 0o600, owner=owner)
    info = os.stat(tmp_path / "token")
    assert (info.st_uid, info.st_gid) == owner


def test_a_directory_that_cannot_take_the_file_fails_before_any_content(tmp_path):
    with pytest.raises(StoreError, match="cannot be created"):
        PendingFile(str(tmp_path / "missing" / "token"), 0o600)


# --- directories ---


def test_a_directory_is_made_with_exactly_its_mode(tmp_path):
    old = os.umask(0o077)
    try:
        assert store.make_dir(str(tmp_path / "d"), 0o755) is True
    finally:
        os.umask(old)
    assert _mode(tmp_path / "d") == 0o755
    # already there and acceptable: kept as it is
    assert store.make_dir(str(tmp_path / "d"), 0o700) is False
    assert _mode(tmp_path / "d") == 0o755


@pytest.mark.parametrize("mode", [0o775, 0o757, 0o777])
def test_a_directory_others_can_write_is_refused(tmp_path, mode):
    (tmp_path / "d").mkdir()
    (tmp_path / "d").chmod(mode)
    with pytest.raises(StoreError, match="writable by users other than its owner"):
        store.check_dir(str(tmp_path / "d"), os.geteuid())
    with pytest.raises(StoreError):
        store.make_dir(str(tmp_path / "d"), 0o755)


def test_a_link_to_a_directory_is_refused(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "d").symlink_to(tmp_path / "real")
    with pytest.raises(StoreError, match="not a directory"):
        store.check_dir(str(tmp_path / "d"), os.geteuid())


def test_a_directory_someone_else_owns_is_refused(tmp_path):
    with pytest.raises(StoreError, match="not owned by"):
        store.check_dir(str(tmp_path), os.geteuid() + 1)


def test_a_missing_directory_is_refused(tmp_path):
    with pytest.raises(StoreError, match="does not exist"):
        store.check_dir(str(tmp_path / "none"), os.geteuid())


def test_removing_says_whether_there_was_anything(tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "f").write_bytes(b"")
    assert store.remove_file(str(tmp_path / "d" / "f")) is True
    assert store.remove_file(str(tmp_path / "d" / "f")) is False
    assert store.remove_dir(str(tmp_path / "d")) is True
    assert store.remove_dir(str(tmp_path / "d")) is False


def test_a_directory_that_holds_anything_is_not_removed(tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "mine").write_bytes(b"the operator's")
    with pytest.raises(StoreError, match="cannot be removed"):
        store.remove_dir(str(tmp_path / "d"))
    assert (tmp_path / "d" / "mine").read_bytes() == b"the operator's"


# --- the directory lock ---


def test_the_directory_lock_is_held_by_one_holder_at_a_time(tmp_path):
    """Greptile on #130: two processes enrolling into one directory. Each `DirLock`
    opens its own descriptor, so two in one process exclude each other as two
    processes would."""
    first, second = store.DirLock(str(tmp_path)), store.DirLock(str(tmp_path))
    assert first.acquire() is True
    assert second.acquire() is False
    first.release()
    assert second.acquire() is True
    second.release()


def test_a_directory_that_cannot_be_locked_is_refused(tmp_path):
    with pytest.raises(StoreError, match="cannot be locked"):
        store.DirLock(str(tmp_path / "none")).acquire()
