"""Backup and restore: one zip with the SQLite database and all photos.

Layout inside the zip:

    manifest.json        {"app": "outfit-planner", "version": ..., "exported_at": ...}
    outfits.db           consistent snapshot made with the SQLite backup API
    photos/<id>/<file>   every photo variant

Restore replaces the database and photos wholesale. Callers must confirm.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from app import db

MANIFEST = "manifest.json"
DB_NAME = "outfits.db"
PHOTOS_PREFIX = "photos/"
MAX_MEMBERS = 200_000
MAX_UNCOMPRESSED = 20 * 1024**3  # 20 GB


class BadBackup(ValueError):
    pass


def export_zip(data_dir: Path, out_path: Path, version: str = "dev") -> Path:
    db_path = data_dir / DB_NAME
    photos_dir = data_dir / "photos"
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / DB_NAME
        if db_path.exists():
            src = db.connect(db_path)
            try:
                dest = sqlite3.connect(str(snapshot))
                with dest:
                    src.backup(dest)
                dest.close()
            finally:
                src.close()
        with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                MANIFEST,
                json.dumps(
                    {
                        "app": "outfit-planner",
                        "version": version,
                        "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    },
                    indent=2,
                ),
            )
            if snapshot.exists():
                zf.write(snapshot, DB_NAME, compress_type=zipfile.ZIP_DEFLATED)
            if photos_dir.exists():
                for path in sorted(photos_dir.rglob("*")):
                    if path.is_file():
                        arcname = PHOTOS_PREFIX + path.relative_to(photos_dir).as_posix()
                        # JPEGs don't compress; store them as-is for speed.
                        zf.write(path, arcname, compress_type=zipfile.ZIP_STORED)
    return out_path


def _safe_member(name: str) -> bool:
    if name.startswith("/") or name.startswith("\\") or ".." in Path(name).parts:
        return False
    return True


def inspect_zip(zip_path: Path) -> dict:
    """Validate the archive and return its manifest plus counts. Raises BadBackup."""
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise BadBackup("That file is not a zip archive.") from exc
    with zf:
        names = zf.namelist()
        if len(names) > MAX_MEMBERS:
            raise BadBackup("Archive has too many files.")
        if MANIFEST not in names or DB_NAME not in names:
            raise BadBackup("Archive is missing manifest.json or outfits.db.")
        total = sum(info.file_size for info in zf.infolist())
        if total > MAX_UNCOMPRESSED:
            raise BadBackup("Archive is implausibly large.")
        for name in names:
            if not _safe_member(name):
                raise BadBackup(f"Unsafe path in archive: {name}")
            if name not in (MANIFEST, DB_NAME) and not name.startswith(PHOTOS_PREFIX):
                raise BadBackup(f"Unexpected file in archive: {name}")
        try:
            manifest = json.loads(zf.read(MANIFEST))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise BadBackup("manifest.json is unreadable.") from exc
        if manifest.get("app") != "outfit-planner":
            raise BadBackup("This zip was not made by Outfit Planner.")
        photo_count = sum(1 for n in names if n.startswith(PHOTOS_PREFIX) and not n.endswith("/"))
        return {"manifest": manifest, "photos": photo_count}


def import_zip(zip_path: Path, data_dir: Path) -> dict:
    """Replace the database and photos with the archive's contents.

    The old data is moved aside to ``data_dir/restore-backup-<timestamp>/`` rather
    than deleted, so a bad restore can be undone by hand.
    """
    info = inspect_zip(zip_path)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    aside = data_dir / f"restore-backup-{stamp}"
    aside.mkdir(parents=True, exist_ok=False)

    db_path = data_dir / DB_NAME
    photos_dir = data_dir / "photos"
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            shutil.move(str(p), aside / p.name)
    if photos_dir.exists():
        shutil.move(str(photos_dir), aside / "photos")
    photos_dir.mkdir()

    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(DB_NAME) as src, open(db_path, "wb") as dst:
            shutil.copyfileobj(src, dst)
        for name in zf.namelist():
            if name.startswith(PHOTOS_PREFIX) and not name.endswith("/"):
                target = photos_dir / name[len(PHOTOS_PREFIX) :]
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(name) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)

    # Make sure the restored database opens and is current.
    conn = db.connect(db_path)
    try:
        conn.execute("PRAGMA integrity_check").fetchone()
        db.migrate(conn)
    finally:
        conn.close()
    info["moved_aside_to"] = str(aside)
    return info


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m app.backup export out.zip`` or ``import in.zip --yes``."""
    import argparse
    import os

    parser = argparse.ArgumentParser(prog="python -m app.backup")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("export", help="write a backup zip")
    ex.add_argument("path")
    im = sub.add_parser("import", help="restore from a backup zip (replaces all data)")
    im.add_argument("path")
    im.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args(argv)

    data_dir = Path(os.environ.get("DATA_DIR") or "/data")
    if args.cmd == "export":
        out = export_zip(data_dir, Path(args.path))
        print(f"Wrote {out} ({out.stat().st_size / 1024 / 1024:.1f} MB)")
        return 0
    info = inspect_zip(Path(args.path))
    print(f"Backup from {info['manifest'].get('exported_at')} with {info['photos']} photo files.")
    if not args.yes:
        answer = input(f"Replace everything in {data_dir}? Type 'yes' to continue: ")
        if answer.strip().lower() != "yes":
            print("Cancelled.")
            return 1
    result = import_zip(Path(args.path), data_dir)
    print(f"Restored. Previous data moved to {result['moved_aside_to']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
