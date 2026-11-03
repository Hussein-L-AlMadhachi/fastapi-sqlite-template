import asyncio
import json

import aiosqlite
import pytest

import db


def _write(directory, name, sql=""):
    (directory / name).write_text(sql)


def _table_names():
    async def query():
        async with db.db_context() as conn:
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
            rows = await cursor.fetchall()
            await cursor.close()
            return [row["name"] for row in rows]

    return asyncio.run(query())


def test_db_context_commits(tmp_path):
    path = str(tmp_path / "test.db")

    async def scenario():
        async with db.db_context(path) as conn:
            await conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
            await conn.execute("INSERT INTO items (name) VALUES (?)", ("a",))

        async with db.db_context(path) as conn:
            cursor = await conn.execute("SELECT name FROM items")
            rows = await cursor.fetchall()
            await cursor.close()
            return rows

    rows = asyncio.run(scenario())
    assert [row["name"] for row in rows] == ["a"]


def test_db_context_rolls_back(tmp_path):
    path = str(tmp_path / "test.db")

    async def scenario():
        async with db.db_context(path) as conn:
            await conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")

        try:
            async with db.db_context(path) as conn:
                await conn.execute("INSERT INTO items (name) VALUES (?)", ("a",))
                raise RuntimeError("boom")
        except RuntimeError:
            pass

        async with db.db_context(path) as conn:
            cursor = await conn.execute("SELECT COUNT(*) AS total FROM items")
            row = await cursor.fetchone()
            await cursor.close()
            return row["total"]

    assert asyncio.run(scenario()) == 0


def test_run_migrations_applies_pending_in_order(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write(migrations, "0002-second.up.sql", "CREATE TABLE b (id INTEGER PRIMARY KEY);")
    _write(migrations, "0001-first.up.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    _write(migrations, "0001-first.down.sql", "DROP TABLE a;")
    state = tmp_path / "state.json"

    applied = asyncio.run(db.run_migrations(migrations, state))

    assert applied == ["0001-first.up.sql", "0002-second.up.sql"]
    assert json.loads(state.read_text()) == {"applied": applied}
    assert _table_names() == ["a", "b"]
    assert asyncio.run(db.run_migrations(migrations, state)) == []


def test_run_migrations_ignores_down_files(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write(migrations, "0001-first.down.sql", "DROP TABLE a;")
    state = tmp_path / "state.json"

    assert asyncio.run(db.run_migrations(migrations, state)) == []
    assert not state.exists()


def test_underscore_separated_filename_is_accepted(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write(migrations, "0001_init_db.up.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    state = tmp_path / "state.json"

    assert asyncio.run(db.run_migrations(migrations, state)) == ["0001_init_db.up.sql"]
    assert _table_names() == ["a"]


def test_failed_migration_is_not_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write(
        migrations,
        "0001-broken.up.sql",
        "CREATE TABLE a (id INTEGER PRIMARY KEY);\nINSERT INTO missing VALUES (1);",
    )
    state = tmp_path / "state.json"

    with pytest.raises(aiosqlite.OperationalError):
        asyncio.run(db.run_migrations(migrations, state))

    assert not state.exists()
    # no implicit transaction is added: statements before the failure stay applied
    assert _table_names() == ["a"]


def test_migration_can_manage_its_own_transaction(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write(
        migrations,
        "0001-broken.up.sql",
        "BEGIN;\n"
        "CREATE TABLE a (id INTEGER PRIMARY KEY);\n"
        "INSERT INTO missing VALUES (1);\n"
        "COMMIT;",
    )
    state = tmp_path / "state.json"

    with pytest.raises(aiosqlite.OperationalError):
        asyncio.run(db.run_migrations(migrations, state))

    assert not state.exists()
    assert _table_names() == []


def test_partial_run_records_only_applied_migrations(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write(migrations, "0001-good.up.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    _write(migrations, "0002-broken.up.sql", "INSERT INTO missing VALUES (1);")
    state = tmp_path / "state.json"

    with pytest.raises(aiosqlite.OperationalError):
        asyncio.run(db.run_migrations(migrations, state))

    assert json.loads(state.read_text()) == {"applied": ["0001-good.up.sql"]}
    assert _table_names() == ["a"]

    _write(migrations, "0002-broken.up.sql", "CREATE TABLE b (id INTEGER PRIMARY KEY);")
    assert asyncio.run(db.run_migrations(migrations, state)) == ["0002-broken.up.sql"]
    assert _table_names() == ["a", "b"]


def test_invalid_migration_filename_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write(migrations, "first.up.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")

    with pytest.raises(ValueError, match="invalid migration filename"):
        asyncio.run(db.run_migrations(migrations, tmp_path / "state.json"))


def test_duplicate_migration_version_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write(migrations, "0001-a.up.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    _write(migrations, "0001-b.up.sql", "CREATE TABLE b (id INTEGER PRIMARY KEY);")

    with pytest.raises(ValueError, match="duplicate migration version"):
        asyncio.run(db.run_migrations(migrations, tmp_path / "state.json"))


def test_malformed_state_file_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write(migrations, "0001-a.up.sql", "CREATE TABLE a (id INTEGER PRIMARY KEY);")
    state = tmp_path / "state.json"
    state.write_text("not json")

    with pytest.raises(RuntimeError, match="cannot parse migration state"):
        asyncio.run(db.run_migrations(migrations, state))
