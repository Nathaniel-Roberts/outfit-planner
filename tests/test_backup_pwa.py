from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import pytest

from app import backup, db, outfits, users
from app.outfits import OutfitInput
from tests.helpers import make_jpeg
from tests.test_outfits import lauren_headers


def seed(settings):
    conn = db.connect(settings.db_path)
    db.migrate(conn)
    lauren = users.upsert_by_email(conn, "lauren@example.com")
    outfit = outfits.create_outfit(conn, lauren.id, OutfitInput(name="Keep me", colours=["navy"]))
    outfits.add_photo(conn, settings.photos_dir, outfit, make_jpeg())
    conn.close()
    return outfit.id


def test_export_import_round_trip(settings, tmp_path):
    outfit_id = seed(settings)
    out = backup.export_zip(settings.data_dir, tmp_path / "b.zip", version="test")
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert "manifest.json" in names and "outfits.db" in names
    assert sum(n.startswith("photos/") for n in names) == 3

    # Wipe and restore.
    conn = db.connect(settings.db_path)
    conn.execute("DELETE FROM outfits")
    conn.close()
    for p in settings.photos_dir.rglob("*"):
        if p.is_file():
            p.unlink()
    info = backup.import_zip(out, settings.data_dir)
    assert info["photos"] == 3
    assert Path(info["moved_aside_to"]).exists()

    conn = db.connect(settings.db_path)
    restored = outfits.get_outfit(conn, outfit_id)
    conn.close()
    assert restored is not None and restored.name == "Keep me"
    photo_dir = outfits.photos_dir_for(settings.photos_dir, outfit_id)
    assert len(list(photo_dir.iterdir())) == 3


@pytest.mark.parametrize(
    "members, message",
    [
        ({"manifest.json": b'{"app": "outfit-planner"}'}, "missing"),
        ({"manifest.json": b'{"app": "other"}', "outfits.db": b""}, "not made by"),
        (
            {"manifest.json": b'{"app": "outfit-planner"}', "outfits.db": b"", "../evil": b"x"},
            "Unsafe",
        ),
        (
            {"manifest.json": b'{"app": "outfit-planner"}', "outfits.db": b"", "etc/passwd": b"x"},
            "Unexpected",
        ),
    ],
)
def test_inspect_rejects_bad_archives(tmp_path, members, message):
    path = tmp_path / "bad.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    with pytest.raises(backup.BadBackup, match=message):
        backup.inspect_zip(path)
    with pytest.raises(backup.BadBackup):
        backup.inspect_zip(Path(__file__))  # a .py file is not a zip


def test_backup_routes_admin_only(client, rsa_key):
    h = lauren_headers(rsa_key)
    assert client.get("/backup/export", headers=h).status_code == 403
    client.post("/login", data={"username": "nathaniel", "password": "correct horse", "next": "/"})
    r = client.get("/backup/export")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert "outfits.db" in zf.namelist()

    # Restore needs the confirmation word.
    r = client.post(
        "/backup/import",
        data={"confirm": "nope"},
        files={"archive": ("b.zip", r.content, "application/zip")},
        follow_redirects=False,
    )
    assert r.status_code == 303 and "unconfirmed" in r.headers["location"]
    export = client.get("/backup/export").content
    r = client.post(
        "/backup/import",
        data={"confirm": "REPLACE"},
        files={"archive": ("b.zip", export, "application/zip")},
        follow_redirects=False,
    )
    assert r.status_code == 303 and "restored=" in r.headers["location"]
    assert client.get("/healthz").status_code == 200


def test_cli_export(settings, tmp_path, monkeypatch, capsys):
    seed(settings)
    monkeypatch.setenv("DATA_DIR", str(settings.data_dir))
    assert backup.main(["export", str(tmp_path / "cli.zip")]) == 0
    assert (tmp_path / "cli.zip").exists()
    assert backup.main(["import", str(tmp_path / "cli.zip"), "--yes"]) == 0
    assert "Restored" in capsys.readouterr().out


def test_pwa_assets(client, rsa_key):
    r = client.get("/sw.js")
    assert r.status_code == 200
    assert "application/javascript" in r.headers["content-type"]
    assert "__VERSION__" not in r.text
    assert re.search(r"const VERSION = '[0-9a-f]{12}'", r.text)
    assert r.headers["cache-control"] == "no-cache"
    r = client.get("/static/manifest.webmanifest")
    assert r.status_code == 200
    assert r.json()["display"] == "standalone"
    for icon in re.findall(r'"src": "([^"]+)"', client.get("/static/manifest.webmanifest").text):
        assert client.get(icon).status_code == 200, icon
    assert client.get("/offline", headers={"accept": "text/html"}).status_code == 200
    page = client.get("/login", headers={"accept": "text/html"}).text
    assert "serviceWorker.register('/sw.js')" in page
