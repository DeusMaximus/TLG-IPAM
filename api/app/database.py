from sqlalchemy import event, inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from .config import settings
from .models import Base

engine = create_async_engine(f"sqlite+aiosqlite:///{settings.db_path}")
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


@event.listens_for(engine.sync_engine, "connect")
def _enable_foreign_keys(dbapi_connection, _record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
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


async def init_models():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)
