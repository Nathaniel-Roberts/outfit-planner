"""Housekeeping that runs inside the app: nightly backup and trash purge.

A single asyncio task started from the lifespan. Every hour it checks whether a
backup is due (none newer than 24 hours in DATA_DIR/backups) and, if so, writes
one and trims the folder to the newest BACKUP_KEEP files. The trash is purged
on the same tick. Everything is best-effort and logged; a failure never takes
the app down.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app import backup, db, outfits
from app.config import Settings

log = logging.getLogger(__name__)

BACKUP_DIR_NAME = "backups"
BACKUP_INTERVAL = timedelta(hours=24)
CHECK_EVERY_SECONDS = 3600


def backups_dir(settings: Settings) -> Path:
    return settings.data_dir / BACKUP_DIR_NAME


def latest_backup(settings: Settings) -> Path | None:
    folder = backups_dir(settings)
    if not folder.exists():
        return None
    files = sorted(folder.glob("outfit-planner-*.zip"))
    return files[-1] if files else None


def backup_due(settings: Settings, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    latest = latest_backup(settings)
    if latest is None:
        return True
    modified = datetime.fromtimestamp(latest.stat().st_mtime, tz=UTC)
    return now - modified >= BACKUP_INTERVAL


def run_backup(settings: Settings, version: str = "dev", keep: int | None = None) -> Path:
    keep = settings.backup_keep if keep is None else keep
    folder = backups_dir(settings)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out = backup.export_zip(settings.data_dir, folder / f"outfit-planner-{stamp}.zip", version)
    for old in sorted(folder.glob("outfit-planner-*.zip"))[:-keep] if keep > 0 else []:
        try:
            old.unlink()
        except OSError as exc:
            log.warning("Could not remove old backup %s: %s", old, exc)
    return out


def purge_trash(settings: Settings) -> int:
    conn = db.connect(settings.db_path)
    try:
        return outfits.purge_trash(conn, settings.photos_dir)
    finally:
        conn.close()


def tick(settings: Settings, version: str = "dev") -> dict:
    """One housekeeping pass. Safe to call any time."""
    result: dict = {"backup": None, "purged": 0}
    try:
        result["purged"] = purge_trash(settings)
    except Exception as exc:  # noqa: BLE001
        log.warning("Trash purge failed: %s", exc)
    if settings.auto_backup and backup_due(settings):
        try:
            result["backup"] = str(run_backup(settings, version))
            log.info("Wrote backup %s", result["backup"])
        except Exception as exc:  # noqa: BLE001
            log.warning("Automatic backup failed: %s", exc)
    return result


async def run_forever(settings: Settings, version: str = "dev") -> None:
    await asyncio.sleep(20)  # let the app settle first
    while True:
        await asyncio.to_thread(tick, settings, version)
        await asyncio.sleep(CHECK_EVERY_SECONDS)
