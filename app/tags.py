"""Activity tags. Per user, editable."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class Tag:
    id: int
    user_id: int
    name: str
    description: str | None
    sort_order: int

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Tag:
        return cls(row["id"], row["user_id"], row["name"], row["description"], row["sort_order"])


def list_tags(conn: sqlite3.Connection, user_id: int) -> list[Tag]:
    rows = conn.execute(
        "SELECT * FROM tags WHERE user_id = ? ORDER BY sort_order, name", (user_id,)
    ).fetchall()
    return [Tag.from_row(r) for r in rows]


def get_tag(conn: sqlite3.Connection, user_id: int, tag_id: int) -> Tag | None:
    row = conn.execute(
        "SELECT * FROM tags WHERE id = ? AND user_id = ?", (tag_id, user_id)
    ).fetchone()
    return Tag.from_row(row) if row else None


def find_by_name(conn: sqlite3.Connection, user_id: int, name: str) -> Tag | None:
    row = conn.execute(
        "SELECT * FROM tags WHERE user_id = ? AND lower(name) = lower(?)", (user_id, name.strip())
    ).fetchone()
    return Tag.from_row(row) if row else None


def create_tag(
    conn: sqlite3.Connection, user_id: int, name: str, description: str | None = None
) -> Tag:
    name = " ".join(name.split()).strip()
    if not name:
        raise ValueError("Tag name is required")
    existing = find_by_name(conn, user_id, name)
    if existing:
        return existing
    order = conn.execute(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM tags WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    cur = conn.execute(
        "INSERT INTO tags (user_id, name, description, sort_order) VALUES (?, ?, ?, ?)",
        (user_id, name, (description or "").strip() or None, order),
    )
    tag = get_tag(conn, user_id, cur.lastrowid)
    assert tag is not None
    return tag


def rename_tag(
    conn: sqlite3.Connection, user_id: int, tag_id: int, name: str, description: str | None
) -> None:
    name = " ".join(name.split()).strip()
    if not name:
        raise ValueError("Tag name is required")
    conn.execute(
        "UPDATE tags SET name = ?, description = ? WHERE id = ? AND user_id = ?",
        (name, (description or "").strip() or None, tag_id, user_id),
    )


def delete_tag(conn: sqlite3.Connection, user_id: int, tag_id: int) -> None:
    conn.execute("DELETE FROM tags WHERE id = ? AND user_id = ?", (tag_id, user_id))


def resolve_names(
    conn: sqlite3.Connection, user_id: int, names: list[str], create: bool = True
) -> list[Tag]:
    """Map tag names to Tag rows, optionally creating missing ones."""
    out: list[Tag] = []
    for name in names:
        tag = find_by_name(conn, user_id, name)
        if tag is None and create and name.strip():
            tag = create_tag(conn, user_id, name)
        if tag and tag not in out:
            out.append(tag)
    return out
