"""Outfit records: the primary object. Photos, tags, colours and garments hang off it."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from app import colours as palette
from app import images
from app.db import transaction
from app.tags import Tag


@dataclass(frozen=True)
class Photo:
    id: int
    outfit_id: int
    filename: str
    width: int | None
    height: int | None
    sort_order: int

    @property
    def url(self) -> str:
        return f"/photos/{self.outfit_id}/{self.filename}"

    @property
    def web_url(self) -> str:
        return f"/photos/{self.outfit_id}/{images.web_name(self.filename)}"

    @property
    def thumb_url(self) -> str:
        return f"/photos/{self.outfit_id}/{images.thumb_name(self.filename)}"


@dataclass(frozen=True)
class Garment:
    id: int
    user_id: int
    name: str
    type: str | None
    colour: str | None


@dataclass
class Outfit:
    id: int
    user_id: int
    name: str | None
    notes: str | None
    temp_min: float | None
    temp_max: float | None
    rain_ok: bool
    windy_ok: bool
    humid_ok: bool
    layers_removable: bool
    favourite: bool
    archived: bool
    last_worn_on: date | None
    created_at: str
    updated_at: str
    unavailable_until: date | None = None
    deleted_at: str | None = None
    photos: list[Photo] = field(default_factory=list)
    tags: list[Tag] = field(default_factory=list)
    colours: list[str] = field(default_factory=list)
    garments: list[Garment] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        return (
            self.name or auto_name([t.name for t in self.tags], self.colours) or f"Outfit {self.id}"
        )

    @property
    def cover(self) -> Photo | None:
        return self.photos[0] if self.photos else None

    def in_wash_on(self, day: date) -> bool:
        return self.unavailable_until is not None and self.unavailable_until >= day

    @property
    def deleted(self) -> bool:
        return self.deleted_at is not None

    @property
    def tag_names(self) -> list[str]:
        return [t.name for t in self.tags]

    @property
    def tag_ids(self) -> set[int]:
        return {t.id for t in self.tags}

    @property
    def temp_label(self) -> str:
        if self.temp_min is None and self.temp_max is None:
            return "any temperature"
        if self.temp_min is None:
            return f"up to {round(self.temp_max)}°"
        if self.temp_max is None:
            return f"from {round(self.temp_min)}°"
        return f"{round(self.temp_min)} to {round(self.temp_max)}°"

    @property
    def weather_flags(self) -> list[str]:
        out = []
        if self.rain_ok:
            out.append("rain ok")
        if self.windy_ok:
            out.append("windy ok")
        if self.humid_ok:
            out.append("hot and humid ok")
        if self.layers_removable:
            out.append("layers removable")
        return out

    def to_dict(self, base_url: str = "") -> dict:
        return {
            "id": self.id,
            "name": self.display_name,
            "custom_name": self.name,
            "notes": self.notes,
            "activity_tags": self.tag_names,
            "temp_min": self.temp_min,
            "temp_max": self.temp_max,
            "rain_ok": self.rain_ok,
            "windy_ok": self.windy_ok,
            "humid_ok": self.humid_ok,
            "layers_removable": self.layers_removable,
            "colours": self.colours,
            "garments": [g.name for g in self.garments],
            "favourite": self.favourite,
            "archived": self.archived,
            "unavailable_until": (
                self.unavailable_until.isoformat() if self.unavailable_until else None
            ),
            "last_worn_on": self.last_worn_on.isoformat() if self.last_worn_on else None,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "photos": [
                {
                    "id": p.id,
                    "url": base_url + p.web_url,
                    "thumb_url": base_url + p.thumb_url,
                    "original_url": base_url + p.url,
                    "width": p.width,
                    "height": p.height,
                }
                for p in self.photos
            ],
        }


@dataclass
class OutfitInput:
    name: str | None = None
    notes: str | None = None
    temp_min: float | None = None
    temp_max: float | None = None
    rain_ok: bool = False
    windy_ok: bool = False
    humid_ok: bool = False
    layers_removable: bool = False
    favourite: bool = False
    archived: bool = False
    tag_ids: list[int] = field(default_factory=list)
    colours: list[str] = field(default_factory=list)
    garment_names: list[str] = field(default_factory=list)


def auto_name(tag_names: list[str], colour_keys: list[str]) -> str:
    colour_part = ""
    labels = [palette.label(k) for k in colour_keys[:2]]
    if labels:
        colour_part = " and ".join(labels)
    tag_part = ", ".join(tag_names[:2])
    if colour_part and tag_part:
        return f"{colour_part}, {tag_part}"
    return colour_part or tag_part.capitalize()


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _row_to_outfit(row: sqlite3.Row) -> Outfit:
    return Outfit(
        id=row["id"],
        user_id=row["user_id"],
        name=row["name"],
        notes=row["notes"],
        temp_min=row["temp_min"],
        temp_max=row["temp_max"],
        rain_ok=bool(row["rain_ok"]),
        windy_ok=bool(row["windy_ok"]),
        humid_ok=bool(row["humid_ok"]),
        layers_removable=bool(row["layers_removable"]),
        favourite=bool(row["favourite"]),
        archived=bool(row["archived"]),
        last_worn_on=_parse_date(row["last_worn_on"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        unavailable_until=_parse_date(row["unavailable_until"]),
        deleted_at=row["deleted_at"],
    )


def _hydrate(conn: sqlite3.Connection, outfits: list[Outfit]) -> list[Outfit]:
    if not outfits:
        return outfits
    by_id = {o.id: o for o in outfits}
    ids = list(by_id)
    marks = ",".join("?" * len(ids))

    for row in conn.execute(
        f"SELECT * FROM outfit_photos WHERE outfit_id IN ({marks}) ORDER BY sort_order, id", ids
    ):
        by_id[row["outfit_id"]].photos.append(
            Photo(
                row["id"],
                row["outfit_id"],
                row["filename"],
                row["width"],
                row["height"],
                row["sort_order"],
            )
        )
    for row in conn.execute(
        f"""
        SELECT ot.outfit_id, t.* FROM outfit_tags ot
        JOIN tags t ON t.id = ot.tag_id
        WHERE ot.outfit_id IN ({marks}) ORDER BY t.sort_order, t.name
        """,
        ids,
    ):
        by_id[row["outfit_id"]].tags.append(Tag.from_row(row))
    for row in conn.execute(
        f"SELECT outfit_id, colour FROM outfit_colours WHERE outfit_id IN ({marks}) "
        "ORDER BY sort_order",
        ids,
    ):
        by_id[row["outfit_id"]].colours.append(row["colour"])
    for row in conn.execute(
        f"""
        SELECT og.outfit_id, g.* FROM outfit_garments og
        JOIN garments g ON g.id = og.garment_id
        WHERE og.outfit_id IN ({marks}) ORDER BY g.name
        """,
        ids,
    ):
        by_id[row["outfit_id"]].garments.append(
            Garment(row["id"], row["user_id"], row["name"], row["type"], row["colour"])
        )
    return outfits


def list_outfits(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    include_archived: bool = False,
    only_archived: bool = False,
) -> list[Outfit]:
    sql = "SELECT * FROM outfits WHERE user_id = ? AND deleted_at IS NULL"
    if only_archived:
        sql += " AND archived = 1"
    elif not include_archived:
        sql += " AND archived = 0"
    sql += " ORDER BY favourite DESC, created_at DESC, id DESC"
    rows = conn.execute(sql, (user_id,)).fetchall()
    return _hydrate(conn, [_row_to_outfit(r) for r in rows])


def list_deleted(conn: sqlite3.Connection, user_id: int) -> list[Outfit]:
    rows = conn.execute(
        "SELECT * FROM outfits WHERE user_id = ? AND deleted_at IS NOT NULL "
        "ORDER BY deleted_at DESC",
        (user_id,),
    ).fetchall()
    return _hydrate(conn, [_row_to_outfit(r) for r in rows])


def get_outfit(conn: sqlite3.Connection, outfit_id: int) -> Outfit | None:
    row = conn.execute("SELECT * FROM outfits WHERE id = ?", (outfit_id,)).fetchone()
    if row is None:
        return None
    return _hydrate(conn, [_row_to_outfit(row)])[0]


def _write_relations(
    conn: sqlite3.Connection, user_id: int, outfit_id: int, data: OutfitInput
) -> None:
    conn.execute("DELETE FROM outfit_tags WHERE outfit_id = ?", (outfit_id,))
    for tag_id in dict.fromkeys(data.tag_ids):
        owned = conn.execute(
            "SELECT 1 FROM tags WHERE id = ? AND user_id = ?", (tag_id, user_id)
        ).fetchone()
        if owned:
            conn.execute(
                "INSERT INTO outfit_tags (outfit_id, tag_id) VALUES (?, ?)", (outfit_id, tag_id)
            )

    conn.execute("DELETE FROM outfit_colours WHERE outfit_id = ?", (outfit_id,))
    for order, key in enumerate(palette.clean_keys(data.colours)):
        conn.execute(
            "INSERT INTO outfit_colours (outfit_id, colour, sort_order) VALUES (?, ?, ?)",
            (outfit_id, key, order),
        )

    conn.execute("DELETE FROM outfit_garments WHERE outfit_id = ?", (outfit_id,))
    for raw in data.garment_names:
        name = " ".join(raw.split()).strip()
        if not name:
            continue
        row = conn.execute(
            "SELECT id FROM garments WHERE user_id = ? AND lower(name) = lower(?)", (user_id, name)
        ).fetchone()
        if row:
            garment_id = row["id"]
        else:
            garment_id = conn.execute(
                "INSERT INTO garments (user_id, name) VALUES (?, ?)", (user_id, name)
            ).lastrowid
        conn.execute(
            "INSERT OR IGNORE INTO outfit_garments (outfit_id, garment_id) VALUES (?, ?)",
            (outfit_id, garment_id),
        )


def _clean_name(name: str | None) -> str | None:
    name = " ".join((name or "").split()).strip()
    return name or None


def create_outfit(conn: sqlite3.Connection, user_id: int, data: OutfitInput) -> Outfit:
    with transaction(conn):
        cur = conn.execute(
            """
            INSERT INTO outfits (user_id, name, notes, temp_min, temp_max, rain_ok, windy_ok,
                                 humid_ok, layers_removable, favourite, archived)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                _clean_name(data.name),
                (data.notes or "").strip() or None,
                data.temp_min,
                data.temp_max,
                int(data.rain_ok),
                int(data.windy_ok),
                int(data.humid_ok),
                int(data.layers_removable),
                int(data.favourite),
                int(data.archived),
            ),
        )
        outfit_id = cur.lastrowid
        _write_relations(conn, user_id, outfit_id, data)
    outfit = get_outfit(conn, outfit_id)
    assert outfit is not None
    return outfit


def update_outfit(conn: sqlite3.Connection, outfit: Outfit, data: OutfitInput) -> Outfit:
    with transaction(conn):
        conn.execute(
            """
            UPDATE outfits SET name = ?, notes = ?, temp_min = ?, temp_max = ?, rain_ok = ?,
                   windy_ok = ?, humid_ok = ?, layers_removable = ?, favourite = ?, archived = ?,
                   updated_at = datetime('now')
            WHERE id = ?
            """,
            (
                _clean_name(data.name),
                (data.notes or "").strip() or None,
                data.temp_min,
                data.temp_max,
                int(data.rain_ok),
                int(data.windy_ok),
                int(data.humid_ok),
                int(data.layers_removable),
                int(data.favourite),
                int(data.archived),
                outfit.id,
            ),
        )
        _write_relations(conn, outfit.user_id, outfit.id, data)
    updated = get_outfit(conn, outfit.id)
    assert updated is not None
    return updated


def input_from(outfit: Outfit) -> OutfitInput:
    """An OutfitInput pre-filled from an existing record, for partial updates."""
    return OutfitInput(
        name=outfit.name,
        notes=outfit.notes,
        temp_min=outfit.temp_min,
        temp_max=outfit.temp_max,
        rain_ok=outfit.rain_ok,
        windy_ok=outfit.windy_ok,
        humid_ok=outfit.humid_ok,
        layers_removable=outfit.layers_removable,
        favourite=outfit.favourite,
        archived=outfit.archived,
        tag_ids=[t.id for t in outfit.tags],
        colours=list(outfit.colours),
        garment_names=[g.name for g in outfit.garments],
    )


def set_flag(conn: sqlite3.Connection, outfit_id: int, flag: str, value: bool) -> None:
    if flag not in {"favourite", "archived"}:
        raise ValueError(flag)
    conn.execute(
        f"UPDATE outfits SET {flag} = ?, updated_at = datetime('now') WHERE id = ?",
        (int(value), outfit_id),
    )


def photos_dir_for(photos_root: Path, outfit_id: int) -> Path:
    return photos_root / str(outfit_id)


def add_photo(conn: sqlite3.Connection, photos_root: Path, outfit: Outfit, data: bytes) -> Photo:
    dest = photos_dir_for(photos_root, outfit.id)
    processed = images.process_upload(data, dest)
    order = conn.execute(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM outfit_photos WHERE outfit_id = ?",
        (outfit.id,),
    ).fetchone()[0]
    cur = conn.execute(
        """
        INSERT INTO outfit_photos (outfit_id, filename, width, height, sort_order)
        VALUES (?, ?, ?, ?, ?)
        """,
        (outfit.id, processed.filename, processed.width, processed.height, order),
    )
    conn.execute("UPDATE outfits SET updated_at = datetime('now') WHERE id = ?", (outfit.id,))
    return Photo(
        cur.lastrowid, outfit.id, processed.filename, processed.width, processed.height, order
    )


def remove_photo(
    conn: sqlite3.Connection, photos_root: Path, outfit: Outfit, photo_id: int
) -> bool:
    photo = next((p for p in outfit.photos if p.id == photo_id), None)
    if photo is None:
        return False
    conn.execute("DELETE FROM outfit_photos WHERE id = ?", (photo_id,))
    images.delete_files(photos_dir_for(photos_root, outfit.id), photo.filename)
    return True


def make_cover(conn: sqlite3.Connection, outfit: Outfit, photo_id: int) -> None:
    ordered = sorted(outfit.photos, key=lambda p: (p.id != photo_id, p.sort_order, p.id))
    for order, photo in enumerate(ordered):
        conn.execute("UPDATE outfit_photos SET sort_order = ? WHERE id = ?", (order, photo.id))


TRASH_DAYS = 30


def trash_outfit(conn: sqlite3.Connection, outfit_id: int) -> None:
    """Move to the bin. Restorable for TRASH_DAYS, then purged."""
    conn.execute(
        "UPDATE outfits SET deleted_at = datetime('now'), updated_at = datetime('now') "
        "WHERE id = ?",
        (outfit_id,),
    )


def restore_outfit(conn: sqlite3.Connection, outfit_id: int) -> None:
    conn.execute(
        "UPDATE outfits SET deleted_at = NULL, updated_at = datetime('now') WHERE id = ?",
        (outfit_id,),
    )


def purge_trash(
    conn: sqlite3.Connection, photos_root: Path, older_than_days: int = TRASH_DAYS
) -> int:
    """Permanently delete outfits that have sat in the bin long enough. Returns the count."""
    rows = conn.execute(
        "SELECT id FROM outfits WHERE deleted_at IS NOT NULL AND deleted_at < datetime('now', ?)",
        (f"-{int(older_than_days)} days",),
    ).fetchall()
    for row in rows:
        outfit = get_outfit(conn, row["id"])
        if outfit is not None:
            delete_outfit(conn, photos_root, outfit)
    return len(rows)


def set_unavailable(conn: sqlite3.Connection, outfit_id: int, until: date | None) -> None:
    conn.execute(
        "UPDATE outfits SET unavailable_until = ?, updated_at = datetime('now') WHERE id = ?",
        (until.isoformat() if until else None, outfit_id),
    )


def delete_outfit(conn: sqlite3.Connection, photos_root: Path, outfit: Outfit) -> None:
    """Permanent delete: the row, its photos, and the folder."""
    conn.execute("DELETE FROM outfits WHERE id = ?", (outfit.id,))
    dest = photos_dir_for(photos_root, outfit.id)
    for photo in outfit.photos:
        images.delete_files(dest, photo.filename)
    try:
        dest.rmdir()
    except OSError:
        pass


@dataclass
class OutfitFilter:
    tag_ids: list[int] = field(default_factory=list)
    colours: list[str] = field(default_factory=list)
    temperature: float | None = None
    favourite: bool = False
    archived: bool = False
    query: str | None = None
    worn_since: date | None = None
    not_worn_days: int | None = None  # only outfits not worn in this many days (or ever)
    sort: str = "newest"

    @property
    def active(self) -> bool:
        return bool(
            self.tag_ids
            or self.colours
            or self.temperature is not None
            or self.favourite
            or self.query
            or self.worn_since
            or self.not_worn_days is not None
        )


SORTS = {
    "newest": "Newest first",
    "name": "Name",
    "last_worn": "Worn longest ago",
    "most_worn": "Most worn",
    "least_worn": "Least worn",
}


def sort_outfits(
    items: list[Outfit], sort: str, counts: dict[int, int] | None = None
) -> list[Outfit]:
    counts = counts or {}
    if sort == "name":
        return sorted(items, key=lambda o: o.display_name.lower())
    if sort == "last_worn":
        return sorted(items, key=lambda o: (o.last_worn_on or date.min, o.id))
    if sort == "most_worn":
        return sorted(items, key=lambda o: (-counts.get(o.id, 0), o.last_worn_on or date.min))
    if sort == "least_worn":
        return sorted(items, key=lambda o: (counts.get(o.id, 0), o.last_worn_on or date.min))
    return items  # list_outfits already orders newest (favourites first)


def filter_outfits(all_outfits: list[Outfit], f: OutfitFilter) -> list[Outfit]:
    """Apply a library filter in Python. Libraries are small; this keeps SQL simple."""
    out: list[Outfit] = []
    wanted_tags = set(f.tag_ids)
    wanted_colours = set(palette.clean_keys(f.colours))
    q = (f.query or "").strip().lower()
    for o in all_outfits:
        if wanted_tags and not (o.tag_ids & wanted_tags):
            continue
        if wanted_colours and not (set(o.colours) & wanted_colours):
            continue
        if f.temperature is not None:
            lo = o.temp_min if o.temp_min is not None else -99.0
            hi = o.temp_max if o.temp_max is not None else 99.0
            if not (lo <= f.temperature <= hi):
                continue
        if f.favourite and not o.favourite:
            continue
        if f.worn_since and (o.last_worn_on is None or o.last_worn_on < f.worn_since):
            continue
        if f.not_worn_days is not None and o.last_worn_on is not None:
            if (date.today() - o.last_worn_on).days < f.not_worn_days:
                continue
        if q:
            haystack = " ".join(
                [
                    o.display_name,
                    o.notes or "",
                    " ".join(o.tag_names),
                    " ".join(g.name for g in o.garments),
                    " ".join(palette.label(c) for c in o.colours),
                ]
            ).lower()
            if q not in haystack:
                continue
        out.append(o)
    return out
