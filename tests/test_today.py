from __future__ import annotations

import re

from tests.test_outfits import lauren_headers
from tests.test_weather import PAYLOAD


def test_today_screen_ranks_and_logs_wear(client, rsa_key):
    client.app.state.weather.fetcher = lambda lat, lon, tz: PAYLOAD
    h = lauren_headers(rsa_key)
    # Two outfits: one tagged active day, one desk day.
    tag_ids = re.findall(
        r'name="tag_ids" value="(\d+)"', client.get("/outfits/new?details=1", headers=h).text
    )
    active, desk = tag_ids[0], tag_ids[1]
    client.post(
        "/outfits",
        data={
            "name": "Floor day",
            "has_tags": "1",
            "tag_ids": [active],
            "temp_min": "15",
            "temp_max": "30",
        },
        headers=h,
    )
    client.post(
        "/outfits", data={"name": "Desk look", "has_tags": "1", "tag_ids": [desk]}, headers=h
    )

    r = client.get("/", headers=h)
    assert r.status_code == 200
    assert "chance of rain" in r.text or "No forecast" in r.text

    r = client.get(f"/?tags={active}", headers=h)
    assert r.text.index("Floor day") < r.text.index("Desk look")
    assert "matches active day" in r.text

    # Selection is remembered for today.
    r = client.get("/", headers=h)
    assert f'value="{active}" checked' in r.text

    # HTMX partial.
    r = client.get(f"/today/results?tags_set=1&tags={desk}", headers={**h, "HX-Request": "true"})
    assert r.text.index("Desk look") < r.text.index("Floor day")

    # Wear it.
    outfit_id = re.search(r"/wear/(\d+)", r.text).group(1)
    r = client.post(
        f"/wear/{outfit_id}",
        data={"next": "/"},
        headers={**h, "HX-Request": "true"},
        follow_redirects=False,
    )
    assert r.headers.get("HX-Redirect") == "/"
    r = client.get("/", headers=h)
    assert "Wearing today" in r.text
    assert "worn today" in r.text

    # Undo.
    entry = re.search(r"/wear/entry/(\d+)/delete", r.text).group(1)
    client.post(f"/wear/entry/{entry}/delete", data={"next": "/"}, headers=h)
    assert "Wearing today" not in client.get("/", headers=h).text


def test_weather_override_and_clear(client, rsa_key):
    client.app.state.weather.fetcher = lambda lat, lon, tz: PAYLOAD
    h = lauren_headers(rsa_key)
    r = client.post(
        "/today/weather",
        data={"temp_min": "8", "temp_max": "14", "rainy": "1"},
        headers=h,
        follow_redirects=False,
    )
    assert r.status_code == 303
    r = client.get("/", headers=h)
    assert "Set by you" in r.text and "70% chance of rain" in r.text
    client.post("/today/weather/clear", headers=h)
    r = client.get("/", headers=h)
    assert "Set by you" not in r.text


def test_today_without_forecast_offers_manual(client, rsa_key):
    def failing(lat, lon, tz):
        raise RuntimeError("offline")

    client.app.state.weather.fetcher = failing
    h = lauren_headers(rsa_key)
    r = client.get("/", headers=h)
    assert "No forecast available" in r.text
