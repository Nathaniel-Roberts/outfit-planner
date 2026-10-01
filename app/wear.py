"""Wear log: which outfit was worn on which day. More than one per day is fine."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from app.db import transaction


@dataclass(frozen=True)
class WearEntry:
    id: int
    outfit_id: int
    user_id: int
    worn_on: date
    feedback: str | None = None


def _entry(r: sqlite3.Row) -> WearEntry:
    return WearEntry(
        r["id"], r["outfit_id"], r["user_id"], date.fromisoformat(r["worn_on"]), r["feedback"]
    )


def _refresh_last_worn(conn: sqlite3.Connection, outfit_id: int) -> None:
    conn.execute(
        """
        UPDATE outfits SET last_worn_on = (SELECT MAX(worn_on) FROM wear_log WHERE outfit_id = ?)
        WHERE id = ?
        """,
        (outfit_id, outfit_id),
    )


def log_wear(conn: sqlite3.Connection, outfit_id: int, user_id: int, worn_on: date) -> WearEntry:
    with transaction(conn):
        existing = conn.execute(
            "SELECT id FROM wear_log WHERE outfit_id = ? AND worn_on = ?",
            (outfit_id, worn_on.isoformat()),
        ).fetchone()
        if existing:
            entry_id = existing["id"]
        else:
            entry_id = conn.execute(
                "INSERT INTO wear_log (outfit_id, user_id, worn_on) VALUES (?, ?, ?)",
                (outfit_id, user_id, worn_on.isoformat()),
            ).lastrowid
        _refresh_last_worn(conn, outfit_id)
    return WearEntry(entry_id, outfit_id, user_id, worn_on)


def unlog_wear(conn: sqlite3.Connection, entry_id: int, user_id: int) -> bool:
    row = conn.execute(
        "SELECT outfit_id FROM wear_log WHERE id = ? AND user_id = ?", (entry_id, user_id)
    ).fetchone()
    if row is None:
        return False
    with transaction(conn):
        conn.execute("DELETE FROM wear_log WHERE id = ?", (entry_id,))
        _refresh_last_worn(conn, row["outfit_id"])
    return True


def worn_on(conn: sqlite3.Connection, user_id: int, day: date) -> list[WearEntry]:
    rows = conn.execute(
        "SELECT * FROM wear_log WHERE user_id = ? AND worn_on = ? ORDER BY id",
        (user_id, day.isoformat()),
    ).fetchall()
    return [
        WearEntry(r["id"], r["outfit_id"], r["user_id"], date.fromisoformat(r["worn_on"]))
        for r in rows
    ]


def entries_between(
    conn: sqlite3.Connection, user_id: int, start: date, end: date
) -> list[WearEntry]:
    rows = conn.execute(
        """
        SELECT * FROM wear_log WHERE user_id = ? AND worn_on BETWEEN ? AND ?
        ORDER BY worn_on, id
        """,
        (user_id, start.isoformat(), end.isoformat()),
    ).fetchall()
    return [
        WearEntry(r["id"], r["outfit_id"], r["user_id"], date.fromisoformat(r["worn_on"]))
        for r in rows
    ]


def wear_counts(conn: sqlite3.Connection, user_id: int) -> dict[int, int]:
    rows = conn.execute(
        "SELECT outfit_id, COUNT(*) AS n FROM wear_log WHERE user_id = ? GROUP BY outfit_id",
        (user_id,),
    ).fetchall()
    return {r["outfit_id"]: r["n"] for r in rows}


FEEDBACK_NUDGE = 1.0  # degrees per 'too hot' / 'too cold'


def awaiting_feedback(conn: sqlite3.Connection, user_id: int, before: date) -> list[WearEntry]:
    """Recent wears (last 3 days, strictly before `before`) with no feedback yet."""
    rows = conn.execute(
        """
        SELECT w.* FROM wear_log w
        JOIN outfits o ON o.id = w.outfit_id
        WHERE w.user_id = ? AND w.feedback IS NULL AND o.deleted_at IS NULL
          AND w.worn_on < ? AND w.worn_on >= date(?, '-3 days')
        ORDER BY w.worn_on DESC, w.id DESC
        """,
        (user_id, before.isoformat(), before.isoformat()),
    ).fetchall()
    return [_entry(r) for r in rows]


def set_feedback(
    conn: sqlite3.Connection, entry_id: int, user_id: int, feedback: str
) -> WearEntry | None:
    """Record how the outfit felt and nudge its temperature range accordingly.

    'hot' lowers the top of the range by a degree, 'cold' raises the bottom. Only
    outfits with a range are adjusted, and the range never collapses below 4 degrees.
    """
    if feedback not in ("hot", "cold", "ok", "skip"):
        raise ValueError("feedback must be hot, cold, ok or skip")
    row = conn.execute(
        "SELECT * FROM wear_log WHERE id = ? AND user_id = ?", (entry_id, user_id)
    ).fetchone()
    if row is None:
        return None
    with transaction(conn):
        conn.execute("UPDATE wear_log SET feedback = ? WHERE id = ?", (feedback, entry_id))
        o = conn.execute(
            "SELECT temp_min, temp_max FROM outfits WHERE id = ?", (row["outfit_id"],)
        ).fetchone()
        if o and o["temp_min"] is not None and o["temp_max"] is not None:
            lo, hi = float(o["temp_min"]), float(o["temp_max"])
            if feedback == "hot" and hi - FEEDBACK_NUDGE - lo >= 4:
                hi -= FEEDBACK_NUDGE
            elif feedback == "cold" and hi - (lo + FEEDBACK_NUDGE) >= 4:
                lo += FEEDBACK_NUDGE
            conn.execute(
                "UPDATE outfits SET temp_min = ?, temp_max = ?, updated_at = datetime('now') "
                "WHERE id = ?",
                (lo, hi, row["outfit_id"]),
            )
    return _entry(conn.execute("SELECT * FROM wear_log WHERE id = ?", (entry_id,)).fetchone())
