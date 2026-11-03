import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from typing import Any, Callable, Literal, Optional, Union
import aiosqlite

DB_PATH = os.getenv("DATABASE_PATH", "data/app.db")

_PRAGMAS = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA foreign_keys = ON",
    "PRAGMA busy_timeout = 5000",
)


async def _open(path: str) -> aiosqlite.Connection:
    parent = Path(path).parent
    if str(parent) not in ("", "."):
        parent.mkdir(parents=True, exist_ok=True)

    conn = await aiosqlite.connect(path)
    conn.row_factory = aiosqlite.Row
    for pragma in _PRAGMAS:
        await conn.execute(pragma)
    return conn


@asynccontextmanager
async def db_context(path: str | None = None) -> AsyncGenerator[aiosqlite.Connection]:
    conn = await _open(path or DB_PATH)
    try:
        yield conn
        await conn.commit()
    except BaseException:
        await conn.rollback()
        raise
    finally:
        await conn.close()


async def get_db() -> AsyncGenerator[aiosqlite.Connection]:
    """FastAPI dependency that yields a connection for a single request."""
    async with db_context() as conn:
        yield conn


async def run_migrations(
    migrations_dir: str | Path | None = None,
    state_path: str | Path | None = None,
) -> list[str]:
    from lib import migrations

    return await migrations.run_migrations(migrations_dir, state_path)


class DB:
    connection: aiosqlite.Connection
    def __init__(self, connection: aiosqlite.Connection):
        self.connection = connection

    async def fetchMany(self, sql: str, *parameters: Any):
        cursor = await self.connection.execute(sql, *parameters)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def fetchOne(self, sql: str, *parameters: Any):
        cursor = await self.connection.execute(sql, *parameters)
        row = await cursor.fetchone()
        return dict(row)

    async def excecute(self, sql: str, *parameters: Any):
        await self.connection.execute(sql, *parameters)
        await self.connection.commit()
