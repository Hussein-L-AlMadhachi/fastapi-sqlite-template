import json
import os
import re
from collections.abc import AsyncGenerator, Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, TypeAlias

import aiosqlite

DB_PATH = os.getenv("DATABASE_PATH", "data/app.db")

_PRAGMAS = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA foreign_keys = ON",
    "PRAGMA busy_timeout = 5000",
)

_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")

Row: TypeAlias = dict[str, Any]


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


def _adapt(value: Any) -> Any:
    """Convert python values sqlite cannot bind into something it can."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=str)
    return value


def _quote_identifier(name: str) -> str:
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(f"invalid sql identifier: {name!r}")
    return f'"{name}"'


def _build_insert(table: str, columns: Sequence[str]) -> str:
    quoted = ", ".join(_quote_identifier(column) for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    return f"INSERT INTO {_quote_identifier(table)} ({quoted}) VALUES ({placeholders})"


class DB:
    """Async wrapper around one aiosqlite connection.

    Every statement is committed right away unless it runs inside
    ``DB.transaction()``. Query helpers return plain dicts.
    """

    connection: aiosqlite.Connection

    def __init__(self, connection: aiosqlite.Connection):
        self.connection = connection
        self._autocommit = True

    # -- raw statements ----------------------------------------------------

    async def execute(self, sql: str, *parameters: Any) -> aiosqlite.Cursor:
        cursor = await self.connection.execute(sql, self._params(parameters))
        await self._maybe_commit()
        return cursor

    async def executemany(
        self, sql: str, parameters: Iterable[Sequence[Any]]
    ) -> aiosqlite.Cursor:
        rows = [tuple(_adapt(value) for value in row) for row in parameters]
        cursor = await self.connection.executemany(sql, rows)
        await self._maybe_commit()
        return cursor


    async def executescript(self, sql: str) -> None:
        await self.connection.executescript(sql)
        await self._maybe_commit()

    # -- reads -------------------------------------------------------------

    async def fetchOne(self, sql: str, *parameters: Any) -> Row | None:
        cursor = await self.connection.execute(sql, self._params(parameters))
        try:
            row = await cursor.fetchone()
        finally:
            await cursor.close()
        return None if row is None else dict(row)

    async def fetchAll(self, sql: str, *parameters: Any) -> list[Row]:
        cursor = await self.connection.execute(sql, self._params(parameters))
        try:
            rows = await cursor.fetchall()
        finally:
            await cursor.close()
        return [dict(row) for row in rows]

    async def fetchMany(self, sql: str, size: int, *parameters: Any) -> list[Row]:
        if size <= 0:
            raise ValueError("size must be a positive integer")

        cursor = await self.connection.execute(sql, self._params(parameters))
        try:
            rows = await cursor.fetchmany(size)
        finally:
            await cursor.close()
        return [dict(row) for row in rows]

    async def iterate(self, sql: str, *parameters: Any, size: int = 256) -> AsyncGenerator[Row]:
        """Stream rows in chunks instead of materialising the whole result."""
        if size <= 0:
            raise ValueError("size must be a positive integer")

        cursor = await self.connection.execute(sql, self._params(parameters))
        try:
            while True:
                rows = await cursor.fetchmany(size)
                if not rows:
                    break
                for row in rows:
                    yield dict(row)
        finally:
            await cursor.close()

    async def scalar(self, sql: str, *parameters: Any) -> Any:
        """First column of the first row, or None when nothing matches."""
        cursor = await self.connection.execute(sql, self._params(parameters))
        try:
            row = await cursor.fetchone()
        finally:
            await cursor.close()
        return None if row is None else row[0]

    async def pluck(self, sql: str, *parameters: Any) -> list[Any]:
        """First column of every row."""
        cursor = await self.connection.execute(sql, self._params(parameters))
        try:
            rows = await cursor.fetchall()
        finally:
            await cursor.close()
        return [row[0] for row in rows]

    async def exists(self, sql: str, *parameters: Any) -> bool:
        return await self.scalar(sql, *parameters) is not None

    # -- writes ------------------------------------------------------------

    async def insert(self, table: str, values: Mapping[str, Any]) -> int:
        """Insert one row, return its rowid (the ``id`` column for INTEGER PRIMARY KEY tables)."""
        if not values:
            raise ValueError("nothing to insert")

        sql = _build_insert(table, list(values))
        cursor = await self.execute(sql, *values.values())
        try:
            return cursor.lastrowid
        finally:
            await cursor.close()


    async def insertMany(self, table: str, rows: Sequence[Mapping[str, Any]]) -> int:
        """Insert several rows with the same columns, return the number of rows."""
        if not rows:
            return 0

        columns = list(rows[0])
        try:
            parameters = [tuple(row[column] for column in columns) for row in rows]
        except KeyError as missing:
            raise ValueError(f"row is missing column {missing.args[0]!r}") from None

        cursor = await self.executemany(_build_insert(table, columns), parameters)
        try:
            return cursor.rowcount
        finally:
            await cursor.close()

    # -- sqlite introspection ---------------------------------------------

    async def tables(self) -> list[str]:
        return await self.pluck(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )

    async def columns(self, table: str) -> list[Row]:
        cursor = await self.connection.execute(f"PRAGMA table_info({_quote_identifier(table)})")
        try:
            rows = await cursor.fetchall()
        finally:
            await cursor.close()
        return [dict(row) for row in rows]

    async def pragma(self, name: str, value: str | int | None = None) -> list[Row]:
        """Read a pragma, or set it when a value is given."""
        if not _IDENTIFIER_RE.match(name):
            raise ValueError(f"invalid pragma name: {name!r}")

        statement = f"PRAGMA {name}"
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, str)):
                raise ValueError(f"unsupported pragma value: {value!r}")
            if isinstance(value, str) and not _IDENTIFIER_RE.match(value):
                raise ValueError(f"invalid pragma value: {value!r}")
            statement = f"{statement} = {value}"

        cursor = await self.connection.execute(statement)
        try:
            rows = await cursor.fetchall()
        finally:
            await cursor.close()
        return [dict(row) for row in rows]

    # -- transactions ------------------------------------------------------

    @property
    def in_transaction(self) -> bool:
        return self.connection.in_transaction

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator["DB"]:
        """Group writes into one transaction: commit on success, rollback on error."""
        previous = self._autocommit
        self._autocommit = False
        try:
            yield self
            await self.connection.commit()
        except BaseException:
            await self.connection.rollback()
            raise
        finally:
            self._autocommit = previous

    async def commit(self) -> None:
        await self.connection.commit()

    async def rollback(self) -> None:
        await self.connection.rollback()

    async def close(self) -> None:
        await self.connection.close()

    # -- internals ---------------------------------------------------------

    async def _maybe_commit(self) -> None:
        if self._autocommit:
            await self.connection.commit()

    @staticmethod
    def _params(parameters: Sequence[Any]) -> tuple[Any, ...]:
        return tuple(_adapt(value) for value in parameters)
