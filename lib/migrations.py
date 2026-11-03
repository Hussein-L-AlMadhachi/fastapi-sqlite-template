"""Parsing, ordering and execution of the SQL files in ./migrations.

Migrations are `*.up.sql` files named `<version>-<title>.up.sql` (or
`<version>_<title>.up.sql`), for example `0001-init_db.up.sql`. They are
executed exactly as written: put BEGIN/COMMIT in the file yourself if you want
it to run as a single transaction.

Applied filenames are recorded in a JSON state file next to the database
(`app.db` -> `app.migrations.json`), unless `MIGRATIONS_STATE_PATH` is set.
`.down.sql` files are ignored when migrating forward.
"""

import asyncio
import fcntl
import json
import os
import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

import db
from logs import logger

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"

_MIGRATION_RE = re.compile(r"^(?P<version>\d+)[-_](?P<name>.+)\.up\.sql$")


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path


def _discover(directory: Path) -> list[Migration]:
    if not directory.is_dir():
        raise RuntimeError(f"migrations directory not found: {directory}")

    migrations: list[Migration] = []
    seen: dict[int, Migration] = {}

    for path in sorted(directory.iterdir()):
        if not path.is_file() or not path.name.endswith(".up.sql"):
            continue

        match = _MIGRATION_RE.match(path.name)
        if match is None:
            raise ValueError(
                f"invalid migration filename: {path.name} "
                "(expected <version>-<title>.up.sql or <version>_<title>.up.sql)"
            )

        migration = Migration(int(match["version"]), path.name, path)
        if migration.version in seen:
            raise ValueError(
                f"duplicate migration version {match['version']}: "
                f"{seen[migration.version].name} and {migration.name}"
            )

        seen[migration.version] = migration
        migrations.append(migration)

    return sorted(migrations, key=lambda item: (item.version, item.name))


def _resolve_state_path(state_path: str | Path | None) -> Path:
    if state_path is not None:
        return Path(state_path)

    override = os.getenv("MIGRATIONS_STATE_PATH")
    if override:
        return Path(override)

    database = Path(db.DB_PATH)
    return database.with_name(f"{database.stem}.migrations.json")


def _read_applied(state_path: Path) -> list[str]:
    if not state_path.is_file():
        return []

    try:
        data = json.loads(state_path.read_text())
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"cannot parse migration state file {state_path}: {exc}"
        ) from exc

    applied = data.get("applied") if isinstance(data, dict) else None
    if not isinstance(applied, list) or not all(isinstance(x, str) for x in applied):
        raise RuntimeError(
            f"invalid migration state file {state_path}: "
            'expected {"applied": ["<migration>.up.sql", ...]}'
        )

    return applied


def _write_applied(state_path: Path, applied: list[str]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temp = state_path.with_name(state_path.name + ".tmp")
    temp.write_text(json.dumps({"applied": applied}, indent=2) + "\n")
    temp.replace(state_path)


@asynccontextmanager
async def _state_lock(state_path: Path) -> AsyncGenerator[None]:
    """Serialize migration runs across processes holding the same state file."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(f"{state_path}.lock", "a+")
    try:
        await asyncio.to_thread(fcntl.flock, handle, fcntl.LOCK_EX)
        yield
    finally:
        await asyncio.to_thread(fcntl.flock, handle, fcntl.LOCK_UN)
        handle.close()


async def _apply(conn: aiosqlite.Connection, migration: Migration) -> None:
    await conn.executescript(migration.path.read_text())


async def run_migrations(
    migrations_dir: str | Path | None = None,
    state_path: str | Path | None = None,
) -> list[str]:
    """Apply pending migrations in version order and return their filenames."""
    migrations = _discover(Path(migrations_dir or MIGRATIONS_DIR))
    state = _resolve_state_path(state_path)

    async with _state_lock(state):
        applied = _read_applied(state)
        known = set(applied)

        on_disk = {migration.name for migration in migrations}
        for name in applied:
            if name not in on_disk:
                logger.warning("applied migration file is missing", migration=name)

        pending = [migration for migration in migrations if migration.name not in known]
        if not pending:
            return []

        async with db.db_context() as conn:
            for migration in pending:
                await _apply(conn, migration)
                applied.append(migration.name)
                _write_applied(state, applied)
                logger.info("migration applied", migration=migration.name)

        return [migration.name for migration in pending]
