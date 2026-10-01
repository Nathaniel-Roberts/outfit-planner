from __future__ import annotations

from datetime import date

from app import db, outfits, tags, users, wear
from app.outfits import OutfitInput
from tests.conftest import make_token
from tests.helpers import make_jpeg, two_tone_jpeg


def fresh_conn(settings):
    conn = db.connect(settings.db_path)
    db.migrate(conn)
    return conn


def test_create_update_delete_outfit(settings):
    conn = fresh_conn(settings)
    lauren = users.upsert_by_email(conn, "lauren@example.com")
    all_tags = tags.list_tags(conn, lauren.id)
    assert [t.name for t in all_tags][:2] == ["active day", "desk day"]

    data = OutfitInput(
        temp_min=12,
        temp_max=20,
        rain_ok=True,
        tag_ids=[all_tags[0].id, 99999],  # unknown tag ids are ignored
        colours=["navy", "mustard", "bogus"],
        garment_names=["Navy chinos", "navy chinos", " white shirt "],
    )
    outfit = outfits.create_outfit(conn, lauren.id, data)
    assert outfit.tag_names == ["active day"]
    assert outfit.colours == ["navy", "mustard"]
    assert sorted(g.name for g in outfit.garments) == ["Navy chinos", "white shirt"]
    assert outfit.display_name == "Navy and Mustard, active day"
    assert outfit.temp_label == "12 to 20°"

    data.name = "  Clinic  staple "
    data.colours = ["mustard"]
    data.garment_names = []
    updated = outfits.update_outfit(conn, outfit, data)
    assert updated.name == "Clinic staple"
    assert updated.colours == ["mustard"]
    assert updated.garments == []
    # The garment row itself is kept for reuse.
    assert conn.execute("SELECT COUNT(*) FROM garments").fetchone()[0] == 2

    photo = outfits.add_photo(conn, settings.photos_dir, updated, make_jpeg())
    photo_dir = outfits.photos_dir_for(settings.photos_dir, updated.id)
    assert len(list(photo_dir.iterdir())) == 3
    reloaded = outfits.get_outfit(conn, updated.id)
    assert reloaded.cover.id == photo.id

    outfits.delete_outfit(conn, settings.photos_dir, reloaded)
    assert outfits.get_outfit(conn, updated.id) is None
    assert not photo_dir.exists()


def test_list_outfits_is_per_user_and_hides_archived(settings):
    conn = fresh_conn(settings)
    lauren = users.upsert_by_email(conn, "lauren@example.com")
    other = users.upsert_by_email(conn, "other@example.com")
    a = outfits.create_outfit(conn, lauren.id, OutfitInput(name="A"))
    outfits.create_outfit(conn, lauren.id, OutfitInput(name="B", archived=True))
    outfits.create_outfit(conn, other.id, OutfitInput(name="C"))

    assert [o.name for o in outfits.list_outfits(conn, lauren.id)] == ["A"]
    assert [o.name for o in outfits.list_outfits(conn, lauren.id, include_archived=True)] == [
        "B",
        "A",
    ]
    assert [o.name for o in outfits.list_outfits(conn, lauren.id, only_archived=True)] == ["B"]
    assert [o.name for o in outfits.list_outfits(conn, other.id)] == ["C"]
    outfits.set_flag(conn, a.id, "favourite", True)
    assert outfits.get_outfit(conn, a.id).favourite is True


def test_wear_log_updates_last_worn(settings):
    conn = fresh_conn(settings)
    lauren = users.upsert_by_email(conn, "lauren@example.com")
    outfit = outfits.create_outfit(conn, lauren.id, OutfitInput(name="A"))
    wear.log_wear(conn, outfit.id, lauren.id, date(2026, 9, 28))
    entry = wear.log_wear(conn, outfit.id, lauren.id, date(2026, 9, 30))
    # Same day twice is one entry.
    wear.log_wear(conn, outfit.id, lauren.id, date(2026, 9, 30))
    assert wear.wear_counts(conn, lauren.id) == {outfit.id: 2}
    assert outfits.get_outfit(conn, outfit.id).last_worn_on == date(2026, 9, 30)
    assert wear.unlog_wear(conn, entry.id, lauren.id)
    assert outfits.get_outfit(conn, outfit.id).last_worn_on == date(2026, 9, 28)


def test_auto_name_variants():
    assert outfits.auto_name([], []) == ""
    assert outfits.auto_name(["desk day"], []) == "Desk day"
    assert outfits.auto_name([], ["navy"]) == "Navy"
    assert (
        outfits.auto_name(["desk day", "smart", "x"], ["navy", "white", "red"])
        == "Navy and White, desk day, smart"
    )


# --- Through the web UI ----------------------------------------------------------------


def lauren_headers(rsa_key):
    from app.auth import ACCESS_EMAIL_HEADER, ACCESS_HEADER

    token = make_token(rsa_key, email="lauren@example.com")
    return {ACCESS_HEADER: token, ACCESS_EMAIL_HEADER: "lauren@example.com", "accept": "text/html"}


def test_photo_first_flow(client, rsa_key):
    h = lauren_headers(rsa_key)
    r = client.post(
        "/outfits",
        data={"then": "edit"},
        files={"photo": ("mirror.jpg", two_tone_jpeg(), "image/jpeg")},
        headers=h,
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].endswith("/edit?new=1&suggested=1")
    outfit_id = int(r.headers["location"].split("/")[2])

    r = client.get(r.headers["location"], headers=h)
    assert r.status_code == 200
    assert "Suggested from the photo" in r.text
    assert 'value="navy" checked' in r.text
    assert 'value="mustard" checked' in r.text

    # Save tags and a temperature range.
    tag_id = client.app.state  # noqa: F841 - just to show app is reachable
    r = client.get("/settings", headers=h)
    assert "active day" in r.text
    import re

    tag_ids = re.findall(
        r'name="tag_ids" value="(\d+)"', client.get(f"/outfits/{outfit_id}/edit", headers=h).text
    )
    r = client.post(
        f"/outfits/{outfit_id}",
        data={
            "has_flags": "1",
            "has_tags": "1",
            "has_colours": "1",
            "tag_ids": [tag_ids[0]],
            "new_tags": "school visit",
            "temp_min": "12",
            "temp_max": "20",
            "rain_ok": "1",
            "colours": ["navy", "mustard"],
            "garments": "navy chinos, mustard knit",
        },
        headers=h,
        follow_redirects=False,
    )
    assert r.status_code == 303
    r = client.get(f"/outfits/{outfit_id}", headers=h)
    assert r.status_code == 200
    assert "active day, school visit" in r.text
    assert "12 to 20°" in r.text
    assert "navy chinos" in r.text

    # Photos are served to the owner and not to a stranger.
    thumb = re.search(r'src="(/photos/\d+/[0-9a-f]+_web\.jpg)"', r.text).group(1)
    assert client.get(thumb, headers=h).status_code == 200
    stranger = dict(h)
    stranger["Cf-Access-Jwt-Assertion"] = make_token(rsa_key, email="stranger@example.com")
    stranger["Cf-Access-Authenticated-User-Email"] = "stranger@example.com"
    assert client.get(thumb, headers=stranger).status_code == 404
    assert client.get(f"/outfits/{outfit_id}", headers=stranger).status_code == 404

    # Library shows it.
    r = client.get("/outfits", headers=h)
    assert f"/outfits/{outfit_id}" in r.text

    # Wear it today, then delete.
    r = client.post(f"/wear/{outfit_id}", headers=h, follow_redirects=False)
    assert r.status_code == 303
    r = client.get(f"/outfits/{outfit_id}", headers=h)
    assert "Last worn" in r.text
    r = client.post(f"/outfits/{outfit_id}/delete", headers=h, follow_redirects=False)
    assert r.status_code == 303
    assert client.get(f"/outfits/{outfit_id}", headers=h).status_code == 404


def test_bad_photo_upload_is_400(client, rsa_key):
    h = lauren_headers(rsa_key)
    r = client.post(
        "/outfits",
        data={"then": "edit"},
        files={"photo": ("x.jpg", b"not an image", "image/jpeg")},
        headers=h,
    )
    assert r.status_code == 400
    assert "not a photo" in r.text
    assert client.get("/outfits", headers=h).text.count("outfit-card") == 0


def test_admin_can_view_as_other_user(client, rsa_key):
    h = lauren_headers(rsa_key)
    client.post("/outfits", data={"name": "Lauren's outfit"}, headers=h)
    client.post("/login", data={"username": "nathaniel", "password": "correct horse", "next": "/"})
    r = client.get("/outfits", headers={"accept": "text/html"})
    assert "Lauren" not in r.text
    r = client.get("/settings", headers={"accept": "text/html"})
    import re

    lauren_id = re.search(r'value="(\d+)"[^>]*>Lauren', r.text).group(1)
    client.post("/settings/view-as", data={"user_id": lauren_id})
    r = client.get("/outfits", headers={"accept": "text/html"})
    assert "Lauren&#39;s outfit" in r.text or "Lauren's outfit" in r.text
