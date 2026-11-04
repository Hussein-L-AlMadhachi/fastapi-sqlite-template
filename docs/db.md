# The `DB` wrapper

`db.py` gives you one small async wrapper around a single `aiosqlite` connection.

What you get:

- one connection per request, opened and closed for you
- query helpers that hand back plain `dict` rows instead of tuples
- `insert`/`insertMany` for the insert boilerplate, with validated table and column names
- a transaction block for grouping writes

What you do not get: an ORM, models, lazy loading, generated migrations, or helpers for `UPDATE`/`DELETE`/count. Those are raw SQL, by design. Migrations live in `migrations/` and run from `lib/migrations.py`.

## Contents

1. [Opening a connection](#1-opening-a-connection)
2. [Quick start](#2-quick-start)
3. [Raw statements](#3-raw-statements)
4. [Reading rows](#4-reading-rows)
5. [Writing rows](#5-writing-rows)
6. [Transactions](#6-transactions)
7. [Value adaptation](#7-value-adaptation)
8. [Schema introspection](#8-schema-introspection)
9. [Safety rules](#9-safety-rules)
10. [Patterns used in this project](#10-patterns-used-in-this-project)
11. [Gotchas](#11-gotchas)
12. [API reference](#12-api-reference)

## 1. Opening a connection

There are two entry points. Both are in `db.py`.

### `db_context(path=None)`

An async context manager that opens a connection, commits on clean exit, rolls back on an exception, and always closes.

```python
import asyncio
import db

async def main():
    async with db.db_context("data/app.db") as conn:
        database = db.DB(conn)
        print(await database.scalar("SELECT COUNT(*) FROM items"))

asyncio.run(main())
```

Pass no path and it uses `db.DB_PATH`, which is `$DATABASE_PATH` or `data/app.db`. Parent directories are created for you. Every connection gets `journal_mode = WAL`, `foreign_keys = ON` and `busy_timeout = 5000`, plus `row_factory = aiosqlite.Row`, so `row["column_name"]` works.

### `get_db()`

The FastAPI dependency. It wraps `db_context()` and yields the connection for the duration of one request.

```python
from fastapi import Depends
import aiosqlite

from db import get_db

async def handler(conn: aiosqlite.Connection = Depends(get_db)):
    ...
```

From there you either use the connection directly or wrap it: `DB(conn)`.

Do not open a new `db_context()` per query inside a request. One connection per request is the rule; it keeps transactions, PRAGMAs and WAL bookkeeping sane.

## 2. Quick start

A complete, runnable script against a throwaway database:

```python
import asyncio
import db

async def main():
    async with db.db_context("data/scratch.db") as conn:
        database = db.DB(conn)

        await database.executescript("""
            CREATE TABLE IF NOT EXISTS melons (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                weight REAL
            );
        """)

        melon_id = await database.insert("melons", {"name": "sugar baby", "weight": 4.2})
        print(melon_id)                                            # 1

        print(await database.fetchOne("SELECT * FROM melons WHERE id = ?", melon_id))
        # {'id': 1, 'name': 'sugar baby', 'weight': 4.2}

        changed = await database.execute(
            "UPDATE melons SET weight = ? WHERE id = ?", 5.0, melon_id
        )
        print(changed.rowcount)                                     # 1
        await changed.close()

        print(await database.scalar("SELECT weight FROM melons WHERE id = ?", melon_id))
        # 5.0

        removed = await database.execute("DELETE FROM melons WHERE id = ?", melon_id)
        print(removed.rowcount)                                     # 1
        await removed.close()

        print(await database.scalar("SELECT COUNT(*) FROM melons"))  # 0

asyncio.run(main())
```

Every method on `DB` is `async`, so it must be `await`ed. Forgetting the `await` does not fail at the call site: the coroutine gets passed along and blows up later during response serialisation with `TypeError: 'coroutine' object is not iterable`. If you see that, look for a missing `await`.

Query parameters are positional and bound as `?` placeholders:

```python
await database.fetchAll(
    "SELECT * FROM items WHERE price > ? AND name LIKE ?",
    10,
    "water%",
)
```

## 3. Raw statements

Use these when the helpers do not fit, for example for CTEs, window functions, `INSERT ... ON CONFLICT`, `ATTACH` or multi-statement scripts.

### `execute(sql, *params)` -> cursor

```python
cursor = await database.execute(
    "INSERT INTO items (name, price) VALUES (?, ?) "
    "ON CONFLICT(name) DO UPDATE SET price = excluded.price",
    "melon",
    3.5,
)
print(cursor.lastrowid, cursor.rowcount)
await cursor.close()
```

`execute` commits immediately, unless you are inside `database.transaction()` (see section 6). It returns the cursor unclosed so you can read `lastrowid`, `rowcount` or `description`; close it yourself when you are done.

### `executemany(sql, rows)` -> cursor

`rows` is any iterable of sequences.

```python
cursor = await database.executemany(
    "INSERT INTO items (name, price) VALUES (?, ?)",
    [("melon", 3.5), ("pumpkin", 12.0)],
)
print(cursor.rowcount)   # 2
await cursor.close()
```

### `executescript(sql)`

Runs several statements separated by semicolons. No parameters, no return value. Handy for `CREATE TABLE` blocks in tests.

```python
await database.executescript("""
    CREATE TABLE a (id INTEGER PRIMARY KEY);
    CREATE TABLE b (id INTEGER PRIMARY KEY);
""")
```

`aiosqlite` commits before running the script, so an `executescript` call inside `transaction()` is not part of that transaction. Use `execute` per statement if the writes must be atomic.

## 4. Reading rows

Helpers here never mutate and never commit. Each one opens a cursor, reads, and closes it.

### `fetchOne(sql, *params)` -> `dict | None`

```python
item = await database.fetchOne("SELECT * FROM items WHERE id = ?", 1)

if item is None:
    ...  # nothing matched
else:
    print(item["name"])
```

Returns `None` when nothing matched. This is the helper to use for lookups; it does not raise on empty results.

### `fetchAll(sql, *params)` -> `list[dict]`

```python
items = await database.fetchAll("SELECT * FROM items ORDER BY id")
for item in items:
    print(item["id"], item["name"])
```

Empty result gives `[]`. This materialises the whole result set, so do not point it at a million rows.

### `fetchMany(sql, size, *params)` -> `list[dict]`

The size goes before the query parameters, not after:

```python
page = await database.fetchMany("SELECT * FROM items ORDER BY id", 20)
```

Use it for previews and "first N" queries. For real pagination, use `LIMIT`/`OFFSET` (or keyset pagination) in the SQL itself; `fetchMany` only caps how many rows are read.

### `iterate(sql, *params, size=256)` -> async generator

Streams rows in chunks so memory stays flat regardless of result size.

```python
async for item in database.iterate("SELECT * FROM items ORDER BY id", size=500):
    await publish(item)
```

The cursor is closed when the generator is exhausted or closed. If you leave the loop early, close it explicitly with `await stream.aclose()` where `stream` is the generator:

```python
stream = database.iterate("SELECT * FROM items ORDER BY id")
try:
    async for item in stream:
        if item["id"] > 100:
            break
        await publish(item)
finally:
    await stream.aclose()
```

### `scalar(sql, *params)` -> `Any`

First column of the first row, `None` if there are no rows.

```python
total = await database.scalar("SELECT COUNT(*) FROM items")
newest = await database.scalar("SELECT MAX(created_at) FROM items")
```

### `pluck(sql, *params)` -> `list[Any]`

First column of every row. Nice for id lists.

```python
ids = await database.pluck("SELECT id FROM items WHERE price > ?", 10)
```

### `exists(sql, *params)` -> `bool`

True when the query returns at least one row.

```python
taken = await database.exists("SELECT 1 FROM items WHERE name = ?", "melon")
```

Write the query as a plain `SELECT ...`; `exists` does not wrap it, it only checks for a row. With an aggregate like `COUNT(*)` the answer is always `True`, because aggregates always return a row.

## 5. Writing rows

Two helpers exist for inserts, because building an `INSERT` from a dict is pure boilerplate. Everything else (`UPDATE`, `DELETE`, `upsert`, `RETURNING`) is raw SQL through `execute`. This wrapper is not an ORM.

### `insert(table, values)` -> rowid

```python
item_id = await database.insert("items", {"name": "melon", "price": 3.5, "tax": None})
```

Returns `cursor.lastrowid`. On a table whose primary key is `INTEGER PRIMARY KEY` that value is the new `id`. On any other table it is still the internal rowid, so do not assume it equals a non-integer primary key.

### `insertMany(table, rows)` -> number of rows

```python
count = await database.insertMany("items", [
    {"name": "melon", "price": 3.5, "tax": None},
    {"name": "pumpkin", "price": 12.0, "tax": 1.2},
])
```

Every dict must have the same columns. The column list comes from the first row; a later row that is missing a key raises `ValueError("row is missing column ...")`. Extra keys in later rows are ignored.

An empty list returns `0` and touches nothing.

All rows go through one `executemany` call, so they succeed or fail together. Wrap in `transaction()` if they must be committed together with other writes.

### Changing rows

```python
cursor = await database.execute(
    "UPDATE items SET price = ? WHERE id = ?", 4.0, 1
)
print(cursor.rowcount)   # rows matched by the WHERE
await cursor.close()
```

`rowcount` is sqlite's count of matched rows, not "values that actually differed".

### Deleting rows

```python
cursor = await database.execute("DELETE FROM items WHERE id = ?", 3)
print(cursor.rowcount)
await cursor.close()
```

There is no `delete(table, where)` helper on purpose: writing the `WHERE` yourself means the destructive statement is visible in the code. A `DELETE` with no `WHERE` is one grep away from being obvious.

### Upserts, RETURNING and friends

```python
cursor = await database.execute(
    "INSERT INTO items (id, name, price) VALUES (?, ?, ?) "
    "ON CONFLICT(id) DO UPDATE SET price = excluded.price "
    "RETURNING id",
    1,
    "melon",
    4.0,
)

created = await database.fetchOne(
    "INSERT INTO items (name, price) VALUES (?, ?) RETURNING *", "melon", 4.0
)
```

Anything sqlite parses works: `INSERT OR REPLACE`, `REPLACE INTO`, CTEs in front of `UPDATE`, `DELETE ... RETURNING`, `ALTER TABLE`, `VACUUM`, `ATTACH`.

## 6. Transactions

By default every write commits as soon as it returns. To group several statements, use the transaction block:

```python
async with database.transaction() as tx:
    order_id = await tx.insert("orders", {"customer": "hussein"})
    await tx.insert("order_lines", {"order_id": order_id, "sku": "melon"})
    await tx.execute("UPDATE stock SET count = count - 1 WHERE sku = ?", "melon")
```

- clean exit: one `COMMIT`
- any exception: `ROLLBACK`, and the exception keeps propagating
- inside the block, `execute`, `executemany`, `insert` and `insertMany` do not commit, they wait for the block

Catch your own errors outside the block if you want to keep going after a failure:

```python
try:
    async with database.transaction() as tx:
        await tx.insert("items", {"name": "melon", "price": 1.0})
        raise RuntimeError("something went wrong")
except RuntimeError:
    pass

# nothing was written
```

Nesting is allowed; the inner block restores the previous mode when it exits. Note that a connection from `get_db()` already commits at the end of a clean request, so a transaction block is only needed when you want a rollback on a partial failure mid-request.

Check state with the property:

```python
database.in_transaction   # bool, straight from the aiosqlite connection
```

There are also plain `commit()`, `rollback()` and `close()` for when you are driving the connection yourself.

## 7. Value adaptation

Sqlite can only bind `None`, `int`, `float`, `str` and `bytes`. Every parameter you pass goes through `_adapt` first:

| Python value | Bound as |
| --- | --- |
| `True` / `False` | `1` / `0` |
| `datetime` / `date` | ISO 8601 string, e.g. `2026-11-04T12:30:00` |
| `dict`, `list`, `tuple` | JSON text |
| anything else | unchanged |

```python
await database.insert("events", {
    "name": "planting",
    "payload": {"rows": 3, "melons": ["a", "b"]},   # stored as json text
    "created_at": datetime(2026, 11, 4, 12, 30),    # stored as ISO text
})

payload = await database.scalar("SELECT payload FROM events")
json.loads(payload)   # {'rows': 3, 'melons': ['a', 'b']}
```

Adaptation is one way. Reads hand back what sqlite stored; parse it yourself with `json.loads` or `datetime.fromisoformat`. On a `REAL` or `NUMERIC` column sqlite may convert text back to numbers on its own.

Because dicts are silently JSON encoded, a mistake like passing a whole object where a scalar was meant will not raise, it will store JSON. Check column types when a query returns something surprising.

## 8. Schema introspection

```python
await database.tables()
# ['items', 'orders']

await database.columns("items")
# [{'cid': 0, 'name': 'id', 'type': 'INTEGER', 'notnull': 0, 'dflt_value': None, 'pk': 1}, ...]

await database.pragma("journal_mode")            # [{'journal_mode': 'wal'}]
await database.pragma("synchronous", "NORMAL")   # set it
```

`tables()` skips sqlite's internal `sqlite_%` tables. `pragma()` accepts an `int` or an identifier-like `str` as value; anything else raises `ValueError`.

## 9. Safety rules

One thing the wrapper enforces, one it cannot.

**Identifiers are validated.** Table and column names in `insert`, `insertMany`, `columns` and `pragma` must match `[A-Za-z_][A-Za-z0-9_]*`. Anything else raises:

```python
await database.columns("items; DROP TABLE items")
# ValueError: invalid sql identifier: 'items; DROP TABLE items'
```

Identifiers cannot be placeholders in SQL, because they are not values, so the wrapper checks their shape instead of quoting blindly.

**Values are always placeholders.** Never format user input into the SQL string yourself:

```python
# don't
await database.fetchAll(f"SELECT * FROM items WHERE name = '{name}'")

# do
await database.fetchAll("SELECT * FROM items WHERE name = ?", name)
```

That is the part the wrapper cannot save you from: any `execute`/`fetchAll`/`scalar` string you build by hand is your responsibility.

## 10. Patterns used in this project

### A service that owns the queries

`model/items/service.py` is the intended shape. The service takes the request connection and is the only thing that knows SQL:

```python
from db import DB
from model.items.dto import Item
import aiosqlite


class ItemsService:
    db: DB

    def __init__(self, conn: aiosqlite.Connection):
        self.db = DB(conn)

    async def create(self, item: Item) -> int:
        return await self.db.insert("items", item.model_dump())

    async def listAll(self) -> list[dict]:
        return await self.db.fetchAll("SELECT * FROM items")

    async def fetch(self, item_id: int) -> dict | None:
        return await self.db.fetchOne("SELECT * FROM items WHERE id = ?", item_id)
```

The handler stays thin:

```python
@router.get("/")
async def root(conn: aiosqlite.Connection = Depends(get_db)):
    items = await ItemsService(conn).listAll()
    return StandardResponse("ok", data=items).success()
```

### Streaming a large export

```python
@router.get("/export")
async def export(conn: aiosqlite.Connection = Depends(get_db)):
    database = DB(conn)

    async def rows():
        async for item in database.iterate("SELECT * FROM items ORDER BY id"):
            yield json.dumps(item) + "\n"

    return StreamingResponse(rows(), media_type="application/x-ndjson")
```

The generator pulls from the cursor chunk by chunk, so the row set never sits in memory. The connection stays open for the whole response, which is fine because the dependency closes it afterwards.

### Bulk import in one transaction

```python
async def import_items(conn, rows: list[dict]) -> int:
    database = DB(conn)

    async with database.transaction():
        await database.execute("DELETE FROM items")
        inserted = await database.insertMany("items", rows)

    return inserted
```

## 11. Gotchas

**Missing `await`.** The most common mistake by far. An async helper returning a coroutine gives you `TypeError: 'coroutine' object is not iterable` at response serialisation, with a traceback pointing at FastAPI's encoder instead of your handler.

**Column names are whatever sqlite says.** `fetchOne("SELECT * FROM items")` returns keys exactly as the columns are declared or aliased. Use `SELECT price AS total_price` if you want a different key.

**`fetchMany` argument order.** `fetchMany(sql, size, *params)`. Written the other way round it raises a sqlite binding error.

**`insert` returns a rowid, not necessarily your `id`.** Only tables with `INTEGER PRIMARY KEY` make those the same thing. The `items` table was fixed in migration `0002` for exactly this reason.

**No `update`, `delete` or `count` helpers.** Deliberate. `UPDATE`, `DELETE` and counting are raw SQL through `execute` or `scalar`, so the `WHERE` clause is written out in the open instead of hidden behind a dict. `await database.scalar("SELECT COUNT(*) FROM items WHERE tax IS NULL")` is the count.

**Every helper commits.** Two `insert` calls without a transaction are two transactions. If the second fails the first stays. Use `transaction()` when the pair must be all or nothing.

**Do not reuse a connection across requests.** `get_db()` gives you one per request; sqlite connections are not meant to be shared between concurrent tasks.

**WAL files.** `data/app.db-wal` and `data/app.db-shm` appear next to the database. That is normal WAL behaviour; commit and close happen through `db_context`.

**Locking.** `busy_timeout = 5000` makes concurrent writers wait up to five seconds instead of failing instantly. Long transactions still block other writers, so keep them short.

## 12. API reference

| Method | Returns | Notes |
| --- | --- | --- |
| `DB(connection)` | `DB` | Wraps an open `aiosqlite.Connection` |
| `execute(sql, *params)` | cursor | Commits unless inside a transaction |
| `executemany(sql, rows)` | cursor | One statement, many parameter tuples |
| `executescript(sql)` | `None` | Multiple statements, no parameters |
| `fetchOne(sql, *params)` | `dict \| None` | `None` when nothing matches |
| `fetchAll(sql, *params)` | `list[dict]` | Materialises the full result |
| `fetchMany(sql, size, *params)` | `list[dict]` | Size comes before parameters |
| `iterate(sql, *params, size=256)` | async generator of `dict` | Streams in chunks |
| `scalar(sql, *params)` | `Any` | First column of first row |
| `pluck(sql, *params)` | `list[Any]` | First column of every row |
| `exists(sql, *params)` | `bool` | True when at least one row comes back |
| `insert(table, values)` | `int` | Rowid of the new row |
| `insertMany(table, rows)` | `int` | Number of rows inserted |
| `tables()` | `list[str]` | Skips `sqlite_%` internal tables |
| `columns(table)` | `list[dict]` | Output of `PRAGMA table_info` |
| `pragma(name, value=None)` | `list[dict]` | Read, or set when a value is given |
| `transaction()` | async context manager | Commit on success, rollback on error |
| `in_transaction` | `bool` | Current connection state |
| `commit()` / `rollback()` / `close()` | `None` | Direct connection control |

Not in the wrapper, on purpose: `update`, `delete`, `count`, `upsert`, joins, relations, lazy loading. Use `execute`, `fetchOne`, `fetchAll` or `scalar` with your own SQL.
