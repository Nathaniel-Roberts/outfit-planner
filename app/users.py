"""User records. Users arrive via Cloudflare Access (by email) or the local admin login."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app.db import transaction

# Default activity tags for a new user. She will rename and add her own.
SEED_TAGS: list[tuple[str, str]] = [
    ("active day", "On the floor with kids, lots of movement"),
    ("desk day", "Mostly seated, admin and reports"),
    ("aged care", "Seated, card games with older clients"),
    ("home visits", "Driving between homes"),
    ("clinic", "In the clinic rooms"),
    ("meetings", "Meetings and case conferences"),
    ("smart", "Dressier than usual"),
]


@dataclass(frozen=True)
class User:
    id: int
    email: str | None
    username: str | None
    display_name: str
    is_admin: bool

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> User:
        return cls(
            id=row["id"],
            email=row["email"],
            username=row["username"],
            display_name=row["display_name"],
            is_admin=bool(row["is_admin"]),
        )


def _display_name_from_email(email: str) -> str:
    local = email.split("@", 1)[0]
    first = local.replace(".", " ").replace("_", " ").split(" ")[0]
    return first.capitalize() if first else email


def seed_tags(conn: sqlite3.Connection, user_id: int) -> None:
    for order, (name, description) in enumerate(SEED_TAGS):
        conn.execute(
            """
            INSERT OR IGNORE INTO tags (user_id, name, description, sort_order)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, name, description, order),
        )


def get_by_id(conn: sqlite3.Connection, user_id: int) -> User | None:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return User.from_row(row) if row else None


def get_by_email(conn: sqlite3.Connection, email: str) -> User | None:
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email.lower(),)).fetchone()
    return User.from_row(row) if row else None


def get_by_username(conn: sqlite3.Connection, username: str) -> User | None:
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    return User.from_row(row) if row else None


def upsert_by_email(conn: sqlite3.Connection, email: str, *, is_admin: bool = False) -> User:
    """Find or create the user for an Access-authenticated email."""
    email = email.strip().lower()
    existing = get_by_email(conn, email)
    if existing:
        conn.execute("UPDATE users SET last_seen_at = datetime('now') WHERE id = ?", (existing.id,))
        return existing
    with transaction(conn):
        cur = conn.execute(
            """
            INSERT INTO users (email, display_name, is_admin, last_seen_at)
            VALUES (?, ?, ?, datetime('now'))
            """,
            (email, _display_name_from_email(email), int(is_admin)),
        )
        user_id = cur.lastrowid
        seed_tags(conn, user_id)
    user = get_by_id(conn, user_id)
    assert user is not None
    return user


def ensure_admin(conn: sqlite3.Connection, username: str) -> User:
    """Create or update the single local admin account."""
    existing = get_by_username(conn, username)
    if existing:
        if not existing.is_admin:
            conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (existing.id,))
        return get_by_id(conn, existing.id) or existing
    with transaction(conn):
        cur = conn.execute(
            "INSERT INTO users (username, display_name, is_admin) VALUES (?, ?, 1)",
            (username, username.capitalize()),
        )
        user_id = cur.lastrowid
        seed_tags(conn, user_id)
    user = get_by_id(conn, user_id)
    assert user is not None
    return user


def list_users(conn: sqlite3.Connection) -> list[User]:
    rows = conn.execute("SELECT * FROM users ORDER BY is_admin DESC, display_name").fetchall()
    return [User.from_row(r) for r in rows]


def non_admin_users(conn: sqlite3.Connection) -> list[User]:
    rows = conn.execute(
        "SELECT * FROM users WHERE is_admin = 0 AND email IS NOT NULL ORDER BY id"
    ).fetchall()
    return [User.from_row(r) for r in rows]
