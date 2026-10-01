from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app import db
from app.weather import WeatherService

PAYLOAD = {
    "daily": {
        "time": ["2026-10-01", "2026-10-02"],
        "temperature_2m_max": [29.2, 28.7],
        "temperature_2m_min": [13.7, 15.6],
        "precipitation_probability_max": [0, 45],
        "precipitation_sum": [0.0, 2.8],
        "wind_speed_10m_max": [16.2, 40.0],
        "relative_humidity_2m_mean": [47, 70],
        "weather_code": [3, 53],
    }
}


def conn_for(settings):
    conn = db.connect(settings.db_path)
    db.migrate(conn)
    return conn


def test_forecast_is_cached_for_an_hour(settings):
    conn = conn_for(settings)
    calls = []

    def fetcher(lat, lon, tz):
        calls.append((lat, lon, tz))
        return PAYLOAD

    svc = WeatherService(-33.43, 151.34, "Australia/Sydney", fetcher=fetcher)
    t0 = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)
    first = svc.for_day(conn, date(2026, 10, 1), now=t0)
    assert first.temp_max == 29.2 and first.label == "Overcast" and not first.rainy
    assert svc.for_day(conn, date(2026, 10, 2), now=t0 + timedelta(minutes=30)).rainy
    assert len(calls) == 1
    svc.forecast(conn, now=t0 + timedelta(hours=2))
    assert len(calls) == 2
    second_day = svc.for_day(conn, date(2026, 10, 2), now=t0 + timedelta(hours=2))
    assert second_day.windy and second_day.humid


def test_fetch_failure_serves_stale_cache(settings):
    conn = conn_for(settings)
    state = {"fail": False}

    def fetcher(lat, lon, tz):
        if state["fail"]:
            raise RuntimeError("offline")
        return PAYLOAD

    svc = WeatherService(-33.43, 151.34, "Australia/Sydney", fetcher=fetcher)
    t0 = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)
    svc.forecast(conn, now=t0)
    state["fail"] = True
    stale = svc.for_day(conn, date(2026, 10, 1), now=t0 + timedelta(days=1))
    assert stale is not None and stale.stale


def test_fetch_failure_with_no_cache_is_empty(settings):
    conn = conn_for(settings)

    def fetcher(lat, lon, tz):
        raise RuntimeError("offline")

    svc = WeatherService(-33.43, 151.34, "Australia/Sydney", fetcher=fetcher)
    assert svc.forecast(conn) == []
    assert svc.for_day(conn, date(2026, 10, 1)) is None
