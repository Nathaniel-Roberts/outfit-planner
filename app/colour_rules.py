"""Her own colour pairing preferences. Used only for small scoring nudges and hints."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app import colours as palette


@dataclass(frozen=True)
class ColourRule:
    id: int
    colour_a: str
    colour_b: str
    verdict: str  # 'good' or 'avoid'

    @property
    def pair(self) -> frozenset[str]:
        return frozenset((self.colour_a, self.colour_b))

    @property
    def label(self) -> str:
        return f"{palette.label(self.colour_a)} + {palette.label(self.colour_b)}"


Rules = dict[frozenset[str], str]


def list_rules(conn: sqlite3.Connection, user_id: int) -> list[ColourRule]:
    rows = conn.execute(
        "SELECT * FROM colour_rules WHERE user_id = ? ORDER BY verdict, colour_a, colour_b",
        (user_id,),
    ).fetchall()
    return [ColourRule(r["id"], r["colour_a"], r["colour_b"], r["verdict"]) for r in rows]


def rules_map(conn: sqlite3.Connection, user_id: int) -> Rules:
    return {r.pair: r.verdict for r in list_rules(conn, user_id)}


def set_rule(
    conn: sqlite3.Connection, user_id: int, colour_a: str, colour_b: str, verdict: str
) -> None:
    if verdict not in ("good", "avoid"):
        raise ValueError("verdict must be good or avoid")
    a, b = (
        sorted(palette.clean_keys([colour_a, colour_b]))[:2]
        if colour_a != colour_b
        else (None, None)
    )
    if not a or not b:
        raise ValueError("two different palette colours are required")
    conn.execute(
        """
        INSERT INTO colour_rules (user_id, colour_a, colour_b, verdict) VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, colour_a, colour_b) DO UPDATE SET verdict = excluded.verdict
        """,
        (user_id, a, b, verdict),
    )


def delete_rule(conn: sqlite3.Connection, user_id: int, rule_id: int) -> None:
    conn.execute("DELETE FROM colour_rules WHERE id = ? AND user_id = ?", (rule_id, user_id))


def pairs_in(colour_keys: list[str], rules: Rules) -> list[tuple[frozenset[str], str]]:
    """Which rules apply to this set of colours."""
    keys = list(dict.fromkeys(colour_keys))
    hits: list[tuple[frozenset[str], str]] = []
    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            pair = frozenset((a, b))
            verdict = rules.get(pair)
            if verdict:
                hits.append((pair, verdict))
    return hits


def pair_label(pair: frozenset[str]) -> str:
    a, b = sorted(pair)
    return f"{palette.label(a)} + {palette.label(b)}"
