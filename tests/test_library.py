from __future__ import annotations

import re
from datetime import date

from app import colour_rules, db, outfits, tags, users
from app.outfits import OutfitFilter, OutfitInput
from tests.test_outfits import lauren_headers


def test_filter_outfits(settings):
    conn = db.connect(settings.db_path)
    db.migrate(conn)
    lauren = users.upsert_by_email(conn, "lauren@example.com")
    t = {x.name: x for x in tags.list_tags(conn, lauren.id)}
    a = outfits.create_outfit(
        conn,
        lauren.id,
        OutfitInput(
            name="Navy floor",
            tag_ids=[t["active day"].id],
            colours=["navy"],
            temp_min=15,
            temp_max=25,
            favourite=True,
        ),
    )
    b = outfits.create_outfit(
        conn,
        lauren.id,
        OutfitInput(
            name="Mustard desk",
            tag_ids=[t["desk day"].id],
            colours=["mustard", "black"],
            garment_names=["wool coat"],
            temp_min=5,
            temp_max=14,
        ),
    )
    c = outfits.create_outfit(conn, lauren.id, OutfitInput(name="Anything", notes="fallback"))
    lib = outfits.list_outfits(conn, lauren.id)

    ids = lambda items: sorted(o.id for o in items)  # noqa: E731
    assert ids(outfits.filter_outfits(lib, OutfitFilter())) == ids([a, b, c])
    assert ids(outfits.filter_outfits(lib, OutfitFilter(tag_ids=[t["active day"].id]))) == [a.id]
    assert ids(outfits.filter_outfits(lib, OutfitFilter(colours=["black"]))) == [b.id]
    assert ids(outfits.filter_outfits(lib, OutfitFilter(temperature=20))) == ids(
        [a, c]
    )  # unknown range counts
    assert ids(outfits.filter_outfits(lib, OutfitFilter(temperature=10))) == ids([b, c])
    assert ids(outfits.filter_outfits(lib, OutfitFilter(favourite=True))) == [a.id]
    assert ids(outfits.filter_outfits(lib, OutfitFilter(query="coat"))) == [b.id]
    assert ids(outfits.filter_outfits(lib, OutfitFilter(query="fallback"))) == [c.id]
    assert ids(outfits.filter_outfits(lib, OutfitFilter(query="Mustard"))) == [b.id]


def test_colour_rules_crud_and_pairs(settings):
    conn = db.connect(settings.db_path)
    db.migrate(conn)
    lauren = users.upsert_by_email(conn, "lauren@example.com")
    colour_rules.set_rule(conn, lauren.id, "mustard", "navy", "good")
    colour_rules.set_rule(
        conn, lauren.id, "navy", "mustard", "avoid"
    )  # same pair, any order: overwrite
    colour_rules.set_rule(conn, lauren.id, "black", "navy", "avoid")
    rules = colour_rules.list_rules(conn, lauren.id)
    assert len(rules) == 2
    m = colour_rules.rules_map(conn, lauren.id)
    assert m[frozenset({"navy", "mustard"})] == "avoid"
    hits = colour_rules.pairs_in(["black", "navy", "mustard", "white"], m)
    assert len(hits) == 2
    colour_rules.delete_rule(conn, lauren.id, rules[0].id)
    assert len(colour_rules.list_rules(conn, lauren.id)) == 1
    import pytest

    with pytest.raises(ValueError):
        colour_rules.set_rule(conn, lauren.id, "navy", "navy", "good")
    with pytest.raises(ValueError):
        colour_rules.set_rule(conn, lauren.id, "navy", "mustard", "meh")


def test_library_filters_history_and_hint_via_ui(client, rsa_key):
    h = lauren_headers(rsa_key)
    tag_ids = re.findall(
        r'name="tag_ids" value="(\d+)"', client.get("/outfits/new?details=1", headers=h).text
    )
    client.post(
        "/outfits",
        data={
            "name": "Navy floor",
            "has_tags": "1",
            "tag_ids": [tag_ids[0]],
            "has_colours": "1",
            "colours": ["navy"],
        },
        headers=h,
    )
    client.post(
        "/outfits",
        data={
            "name": "Mustard desk",
            "has_tags": "1",
            "tag_ids": [tag_ids[1]],
            "has_colours": "1",
            "colours": ["mustard"],
        },
        headers=h,
    )

    r = client.get(f"/outfits?tag={tag_ids[0]}", headers=h)
    assert "Navy floor" in r.text and "Mustard desk" not in r.text
    r = client.get("/outfits?colour=mustard", headers=h)
    assert "Mustard desk" in r.text and "Navy floor" not in r.text
    r = client.get("/outfits?q=floor", headers=h)
    assert "Navy floor" in r.text and "Mustard desk" not in r.text

    # History: log a wear on a past date from the detail page, then see it in the calendar.
    navy_id = re.search(
        r'href="/outfits/(\d+)"', client.get("/outfits?q=floor", headers=h).text
    ).group(1)
    r = client.post(
        f"/wear/{navy_id}", data={"worn_on": "2026-09-15"}, headers=h, follow_redirects=False
    )
    assert r.status_code == 303
    r = client.get("/history?month=2026-09", headers=h)
    assert r.status_code == 200
    assert "September 2026" in r.text
    assert "Tue 15 Sep" in r.text
    assert "Navy floor" in r.text
    assert "Most worn" in r.text
    assert client.get("/history?month=not-a-month", headers=h).status_code == 200

    # Colour rules and hint.
    client.post(
        "/settings/colour-rules",
        data={"colour_a": "navy", "colour_b": "mustard", "verdict": "good"},
        headers=h,
    )
    client.post(
        "/settings/colour-rules",
        data={"colour_a": "black", "colour_b": "navy", "verdict": "avoid"},
        headers=h,
    )
    r = client.get("/settings", headers=h)
    assert "Mustard + Navy" in r.text and "Black + Navy" in r.text
    r = client.get("/colour-rules/hint?colours=navy,mustard,black", headers=h)
    assert r.status_code == 200
    assert "You like: Mustard + Navy" in r.text
    assert "You usually avoid: Black + Navy" in r.text
    assert client.get("/colour-rules/hint?colours=navy", headers=h).text == ""

    # Rules nudge the Today ranking.
    r = client.get("/", headers=h)
    assert "pairing you like" not in r.text  # single-colour outfits trip no rule
    client.post(
        "/outfits",
        data={"name": "Navy mustard combo", "has_colours": "1", "colours": ["navy", "mustard"]},
        headers=h,
    )
    r = client.get("/", headers=h)
    assert "Mustard + Navy is a pairing you like" in r.text


def test_history_month_grid_pads_mondays():
    from app.routers.history import month_grid

    weeks = month_grid(date(2026, 10, 1))  # 1 Oct 2026 is a Thursday
    assert weeks[0][:3] == [None, None, None]
    assert weeks[0][3] == date(2026, 10, 1)
    assert weeks[-1][-1] in (None, date(2026, 10, 31))
