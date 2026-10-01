"""SQLite connection handling and the migration runner.

Migrations are plain SQL files in ``migrations/`` named ``NNN_description.sql``.
They run once each, in order, tracked in ``schema_migrations``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Explicit transaction. The connection runs in autocommit otherwise."""
    conn.execute("BEGIN")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def pending_migrations(conn: sqlite3.Connection) -> list[Path]:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    files = sorted(MIGRATIONS_DIR.glob("[0-9]*.sql"))
    return [f for f in files if _version_of(f) not in applied]


def _version_of(path: Path) -> int:
    return int(path.name.split("_", 1)[0])


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Apply all pending migrations. Returns the names applied."""
    applied: list[str] = []
    for path in pending_migrations(conn):
        sql = path.read_text()
        conn.executescript("BEGIN;\n" + sql + "\nCOMMIT;")
        conn.execute(
            "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
            (_version_of(path), path.name),
        )
        applied.append(path.name)
    return applied
