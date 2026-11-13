from sqlalchemy import event, inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from .config import settings
from .models import Base

engine = create_async_engine(f"sqlite+aiosqlite:///{settings.db_path}")
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


async def init_models(target: AsyncEngine = engine):
    async with target.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)
