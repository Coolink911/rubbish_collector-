"""Database access for two engines with one dialect.

The rule: no state on the local filesystem of the *deployed* host, ever - a
free Space wipes its disk on restart. So the deployed instance talks to a
hosted Postgres (Neon) via DATABASE_URL. But an assessor doing a clean clone
should not need anyone's database account, so with DATABASE_URL unset the same
code runs against a local SQLite file.

That buys a zero-config clone at the cost of every query having to be legal in
both engines. The queries stick to a shared subset:

  - placeholders are written as %s (psycopg style) and translated to ? for
    sqlite3. None of our SQL contains a literal '%s', so plain replacement is
    safe. New queries must keep it that way.
  - INSERT ... RETURNING id works on both (SQLite gained RETURNING in 3.35).
  - ON CONFLICT ... DO UPDATE works on both.
  - timestamps are TEXT, written by the app in UTC, so neither engine's
    now() is involved.
  - the only per-engine DDL is the primary-key column, handled in _pk().

Concurrency, which is what this project is really about:

Both engines make a single conditional UPDATE atomic. In SQLite the writer
holds the database write lock; WAL mode plus a busy timeout means a second
writer waits its turn instead of raising `database is locked`, then finds the
row no longer matches. In Postgres the second writer blocks on the row lock,
and when the first commits it re-checks the WHERE against the committed row
(READ COMMITTED semantics) and matches nothing. Same statement, same outcome,
different machinery - which is exactly why the claim logic lives in single
statements and never in read-then-write pairs.

Connections are opened per unit of work in autocommit mode. Each statement is
its own transaction; the claim's UPDATE needs nothing wrapped around it.
"""

from __future__ import annotations

import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from . import config

BUSY_TIMEOUT_MS = 5000


def is_postgres() -> bool:
    return config.database_url().startswith(("postgres://", "postgresql://"))


def sqlite_path() -> Path:
    url = config.database_url()
    for prefix in ("sqlite:///", "sqlite://"):
        if url.startswith(prefix):
            return Path(url[len(prefix):]).expanduser()
    raise ValueError(f"not a sqlite url: {url}")


class Cursor:
    """Thin wrapper that hides the two engines' differences from callers."""

    def __init__(self, cur: Any, postgres: bool):
        self._cur = cur
        self._postgres = postgres

    def execute(self, sql: str, params: tuple = ()) -> "Cursor":
        if not self._postgres:
            sql = sql.replace("%s", "?")
        self._cur.execute(sql, params)
        return self

    def fetchone(self) -> dict | None:
        row = self._cur.fetchone()
        return dict(row) if row is not None else None

    def fetchall(self) -> list[dict]:
        return [dict(r) for r in self._cur.fetchall()]

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount


def _connect_raw() -> tuple[Any, bool]:
    if is_postgres():
        import psycopg
        from psycopg.rows import dict_row

        conn = psycopg.connect(
            config.database_url(), row_factory=dict_row, autocommit=True
        )
        return conn, True

    path = sqlite_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn, False


@contextmanager
def cursor() -> Iterator[Cursor]:
    """A connection scoped to one unit of work. Never shared across threads."""
    conn, postgres = _connect_raw()
    try:
        yield Cursor(conn.cursor(), postgres)
    finally:
        conn.close()


# --- schema -----------------------------------------------------------------
#
# Append-only list of migrations; index + 1 is the schema version, tracked in
# the schema_version table (PRAGMA user_version is sqlite-only). Never edit a
# migration that has shipped; add another one.


def _pk() -> str:
    return (
        "INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY"
        if is_postgres()
        else "INTEGER PRIMARY KEY AUTOINCREMENT"
    )


def _migrations() -> list[list[str]]:
    pk = _pk()
    return [
        # 1 - initial schema
        [
            f"""
            CREATE TABLE users (
                id          {pk},
                name        TEXT NOT NULL,
                role        TEXT NOT NULL CHECK (role IN ('resident', 'collector')),
                lat         REAL,
                lng         REAL,
                created_at  TEXT NOT NULL
            )
            """,
            f"""
            CREATE TABLE pickups (
                id            {pk},
                resident_id   INTEGER NOT NULL REFERENCES users(id),
                collector_id  INTEGER REFERENCES users(id),

                description   TEXT NOT NULL,
                bag_count     INTEGER,
                size          TEXT,
                address       TEXT,
                when_text     TEXT,
                notes         TEXT,
                raw_text      TEXT,
                parsed        INTEGER NOT NULL DEFAULT 0,

                lat           REAL,
                lng           REAL,
                geocoded      INTEGER NOT NULL DEFAULT 0,
                geocode_note  TEXT,

                status        TEXT NOT NULL DEFAULT 'open'
                              CHECK (status IN ('open', 'claimed', 'done', 'cancelled')),

                created_at    TEXT NOT NULL,
                claimed_at    TEXT,
                completed_at  TEXT,

                -- A claimed or done pickup must have a collector; an open or
                -- cancelled one must not. Belt-and-braces: if any future code
                -- writes a bad pair, the write fails instead of quietly
                -- producing a job with no owner.
                CHECK (
                    (status IN ('claimed', 'done') AND collector_id IS NOT NULL)
                    OR (status IN ('open', 'cancelled') AND collector_id IS NULL)
                )
            )
            """,
            "CREATE INDEX idx_pickups_status ON pickups(status)",
            "CREATE INDEX idx_pickups_resident ON pickups(resident_id)",
            "CREATE INDEX idx_pickups_collector ON pickups(collector_id)",
            """
            CREATE TABLE geocode_cache (
                query       TEXT PRIMARY KEY,
                lat         REAL,
                lng         REAL,
                label       TEXT
            )
            """,
        ],
        # 2 - structured collection windows, parsed from "tomorrow morning".
        # Stored as naive local wall-clock strings (YYYY-MM-DD HH:MM) on
        # purpose: residents and collectors share one city and one clock,
        # and pretending we have timezone handling we don't would be worse.
        [
            "ALTER TABLE pickups ADD COLUMN window_start TEXT",
            "ALTER TABLE pickups ADD COLUMN window_end TEXT",
        ],
    ]


def schema_version(cur: Cursor) -> int:
    cur.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
    )
    row = cur.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        cur.execute("INSERT INTO schema_version (version) VALUES (0)")
        return 0
    return row["version"]


def migrate(verbose: bool = False) -> int:
    """Bring the database up to the latest schema. Idempotent - runs on every
    boot, does nothing when there is nothing to do."""
    with cursor() as cur:
        current = schema_version(cur)
        for version, statements in enumerate(_migrations(), start=1):
            if version <= current:
                continue
            if verbose:
                print(f"applying migration {version}")
            for statement in statements:
                cur.execute(statement)
            cur.execute("UPDATE schema_version SET version = %s", (version,))
            current = version
    return current


def drop_all() -> None:
    """Throw the schema away. Tests use this; nothing else should."""
    with cursor() as cur:
        for table in ("geocode_cache", "pickups", "users", "schema_version"):
            cur.execute(f"DROP TABLE IF EXISTS {table}")


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "init"
    engine = "postgres" if is_postgres() else f"sqlite ({sqlite_path()})"
    if command in ("init", "migrate"):
        version = migrate(verbose=True)
        print(f"{engine} is at schema version {version}")
        return 0
    if command == "reset":
        drop_all()
        print(f"dropped all tables on {engine}")
        return 0
    print("usage: python -m app.db [init|migrate|reset]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
