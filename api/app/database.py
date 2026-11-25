import os
import shlex
import unicodedata

from sqlalchemy import event, inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from .config import SQLITE_URL_PREFIX, settings
from .models import Base

# The files SQLite keeps beside the database in WAL mode
SIDE_FILES = ("-wal", "-shm")


def on_filesystem(db_path: str) -> bool:
    """Whether `db_path` names a database file, whose directory and files can be checked.

    Exactly `:memory:` and the empty name are not: through this engine both open an
    in-memory database, with no directory this process must write. Every other value is
    a path. `config.db_path_problem` has already refused a `file:` name and anything the
    engine's URL would read otherwise than as the path, so the path checked is the file
    SQLite opens.
    """
    return db_path not in ("", ":memory:")


def _control(char: str) -> bool:
    # control, format, unassigned and private-use characters, and the line and
    # paragraph separators, which a terminal may also act on
    category = unicodedata.category(char)
    return category[0] == "C" or category in ("Zl", "Zp")


def shown(text: str) -> str:
    """`text` with every control character written as an escape (`\\x1b`, `\\n`), so a
    path from `DB_PATH` cannot move the cursor or start a new line in the message."""
    return "".join(
        char.encode("unicode_escape").decode("ascii") if _control(char) else char
        for char in text
    )


def storage_problem(db_path: str) -> str | None:
    """Why this process cannot write the database at `db_path`, or None when it can.

    SQLite opens a database it cannot write read-only, without an error, so a server
    started on a volume another user owns would answer every read and fail every write.
    The image runs as uid 10001 (#173), and a volume made by an image before it belongs
    to root. The directory must be writable (SQLite creates the database and its `-wal`
    and `-shm` files there), and each of those files that exists readable and writable,
    by this process's ids as the kernel judges them (`os.access`, so ACLs and
    supplementary groups count). A target with no file (`on_filesystem`) is never
    checked. Each answer is a fixed sentence and the path, its control characters shown
    as escapes.
    """
    if not on_filesystem(db_path):
        return None
    path = os.path.abspath(db_path)
    directory = os.path.dirname(path)
    if not os.path.isdir(directory):
        return f"the database's directory {shown(directory)} does not exist"
    if not os.access(directory, os.W_OK | os.X_OK):
        return f"the database's directory {shown(directory)} is not writable by this process"
    for name in (path, *(path + suffix for suffix in SIDE_FILES)):
        if os.path.lexists(name) and not os.access(name, os.R_OK | os.W_OK):
            return f"{shown(name)} is not readable and writable by this process"
    return None


def volume_commands(directory: str, uid: int, gid: int) -> list[str] | None:
    """The one-time commands that give `directory` to `uid`:`gid` and make it 0700, as a
    new volume is (README, "Upgrading from an image that ran as root"), the directory one
    shell word (`shlex.quote`). None when the directory holds a control character: its
    escaped spelling names another path, and the raw one would act on the terminal, so
    no command is printed that does not name the real directory."""
    if shown(directory) != directory:
        return None
    target = shlex.quote(directory)
    run = "docker compose run --rm --no-deps --user root --entrypoint"
    return [f"{run} chown ipam -R {uid}:{gid} {target}", f"{run} chmod ipam 700 {target}"]


def refuse_unwritable_storage(db_path: str) -> None:
    """Stop the start when `storage_problem` finds one, with the one-time commands that
    give a volume to the ids this process runs as (`volume_commands`)."""
    problem = storage_problem(db_path)
    if problem is None:
        return
    uid, gid = os.getuid(), os.getgid()
    directory = os.path.dirname(os.path.abspath(db_path))
    if not os.path.isdir(directory):
        raise SystemExit(f"TLG-IPAM will not start: {problem} (DB_PATH).")
    commands = volume_commands(directory, uid, gid)
    if commands is None:
        how = (
            f"with `chown -R {uid}:{gid}` and `chmod 700` on it, run as root through "
            f"`docker compose run`; its path holds a control character, shown escaped "
            f"here, so the commands must name the real directory"
        )
    else:
        how = f"with `{commands[0]}` and `{commands[1]}`"
    raise SystemExit(
        f"TLG-IPAM will not start: {problem} (uid {uid}, gid {gid}). A volume made by an "
        f"image that ran as root is still root's: give it to the server's user once, from "
        f"the directory that holds docker-compose.yml, {how}, then start the server again "
        f"(README, \"Upgrading from an image that ran as root\")."
    )


# Checked when this module is imported, as the owner key is when `app.config` is, so the
# server exits with the message alone, before it serves anything or opens the database.
refuse_unwritable_storage(settings.db_path)
engine = create_async_engine(SQLITE_URL_PREFIX + settings.db_path)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


@event.listens_for(engine.sync_engine, "connect")
def _configure_connection(dbapi_connection, _record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    # WAL + busy_timeout so concurrent UI/API/MCP writes queue instead of
    # failing with "database is locked".
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


async def get_session():
    async with SessionLocal() as session:
        yield session


def _add_missing_columns(conn):
    """Lightweight forward-only migration: new nullable columns on existing tables
    are added in place, so upgrades never need a manual migration step."""
    inspector = inspect(conn)
    for table in Base.metadata.sorted_tables:
        existing = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name not in existing:
                ddl = (
                    f"ALTER TABLE {table.name} "
                    f"ADD COLUMN {column.name} {column.type.compile(conn.dialect)}"
                )
                conn.execute(text(ddl))


def _add_missing_indexes(conn):
    """New indexes on existing tables: `create_all` makes a table's indexes only with
    the table, so one declared later is created here, once (`checkfirst`)."""
    for table in Base.metadata.sorted_tables:
        for index in table.indexes:
            index.create(conn, checkfirst=True)


async def init_models(target: AsyncEngine = engine):
    async with target.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)
        await conn.run_sync(_add_missing_indexes)
