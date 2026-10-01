"""Forecast from Open-Meteo, cached in SQLite, with graceful degradation.

Open-Meteo is free and needs no key. We ask for three days of hourly and daily
values and cache the raw payload for an hour. Temperatures used for scoring are
the working-hours range (7am to 6pm), because a 4am overnight low says nothing
about what the day will feel like at the clinic. The overnight minimum is kept
for display. If the fetch fails we serve the stale cache (flagged as stale); if
there is no cache at all, the Today screen falls back to manual weather.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta

import httpx

log = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
DAILY_FIELDS = [
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_probability_max",
    "precipitation_sum",
    "wind_speed_10m_max",
    "relative_humidity_2m_mean",
    "weather_code",
]
HOURLY_FIELDS = [
    "temperature_2m",
    "precipitation_probability",
    "wind_speed_10m",
    "relative_humidity_2m",
]
WORK_HOURS = range(7, 19)  # 7am to 6pm inclusive
CACHE_TTL = timedelta(hours=1)

RAIN_CHANCE_THRESHOLD = 40  # percent
WIND_THRESHOLD = 35  # km/h, "noticeably windy"
HUMID_TEMP_THRESHOLD = 26  # degrees
HUMID_RH_THRESHOLD = 65  # percent

WMO_LABELS = {
    0: "Clear",
    1: "Mostly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    66: "Freezing rain",
    67: "Freezing rain",
    71: "Snow",
    73: "Snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Showers",
    81: "Showers",
    82: "Heavy showers",
    85: "Snow showers",
    86: "Snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Thunderstorm with hail",
}


@dataclass(frozen=True)
class DayForecast:
    day: date
    temp_min: float
    temp_max: float
    rain_chance: int  # percent
    rain_mm: float
    wind_max: float  # km/h
    humidity: int | None  # percent
    weather_code: int | None
    stale: bool = False
    manual: bool = False
    night_min: float | None = None  # overnight low, for display only
    day_max: float | None = None  # full-day high, for display only

    @property
    def label(self) -> str:
        if self.manual:
            return "Set by you"
        return WMO_LABELS.get(self.weather_code or -1, "")

    @property
    def rainy(self) -> bool:
        return self.rain_chance >= RAIN_CHANCE_THRESHOLD

    @property
    def windy(self) -> bool:
        return self.wind_max >= WIND_THRESHOLD

    @property
    def humid(self) -> bool:
        if self.humidity is None:
            return False
        return self.temp_max >= HUMID_TEMP_THRESHOLD and self.humidity >= HUMID_RH_THRESHOLD

    @property
    def work_hours(self) -> bool:
        return self.night_min is not None and not self.manual

    @property
    def summary(self) -> str:
        parts = [f"{round(self.temp_min)} to {round(self.temp_max)}°"]
        if self.work_hours and self.night_min is not None and self.night_min < self.temp_min - 1:
            parts[0] += f" during the day ({round(self.night_min)}° overnight)"
        parts.append(f"{self.rain_chance}% rain")
        parts.append(f"wind {round(self.wind_max)} km/h")
        if self.humid:
            parts.append("humid")
        return ", ".join(parts)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["day"] = self.day.isoformat()
        data.update(
            {
                "label": self.label,
                "rainy": self.rainy,
                "windy": self.windy,
                "humid": self.humid,
                "summary": self.summary,
            }
        )
        return data


def _hourly_by_day(payload: dict) -> dict[str, dict[str, list[float]]]:
    """Working-hours hourly values grouped by ISO date."""
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    out: dict[str, dict[str, list[float]]] = {}
    for i, stamp in enumerate(times):
        try:
            day_str, hour_str = stamp.split("T")
            hour = int(hour_str[:2])
        except (ValueError, AttributeError):
            continue
        if hour not in WORK_HOURS:
            continue
        bucket = out.setdefault(day_str, {k: [] for k in HOURLY_FIELDS})
        for key in HOURLY_FIELDS:
            values = hourly.get(key) or []
            if i < len(values) and values[i] is not None:
                bucket[key].append(float(values[i]))
    return out


def _parse(payload: dict, stale: bool) -> list[DayForecast]:
    daily = payload.get("daily") or {}
    days = daily.get("time") or []
    by_day = _hourly_by_day(payload)
    out: list[DayForecast] = []

    def pick(key: str, i: int, default=None):
        values = daily.get(key) or []
        return values[i] if i < len(values) and values[i] is not None else default

    for i, day_str in enumerate(days):
        try:
            day_min = float(pick("temperature_2m_min", i, 0.0))
            day_max = float(pick("temperature_2m_max", i, 0.0))
            rain_chance = int(pick("precipitation_probability_max", i, 0) or 0)
            wind_max = float(pick("wind_speed_10m_max", i, 0.0) or 0.0)
            humidity_raw = pick("relative_humidity_2m_mean", i)
            humidity = int(humidity_raw) if humidity_raw is not None else None
            temp_min, temp_max = day_min, day_max
            night_min: float | None = None

            hours = by_day.get(day_str)
            if hours and hours["temperature_2m"]:
                temps = hours["temperature_2m"]
                temp_min, temp_max = min(temps), max(temps)
                night_min = day_min
                if hours["precipitation_probability"]:
                    rain_chance = int(max(hours["precipitation_probability"]))
                if hours["wind_speed_10m"]:
                    wind_max = max(hours["wind_speed_10m"])
                if hours["relative_humidity_2m"]:
                    rh = hours["relative_humidity_2m"]
                    humidity = int(round(sum(rh) / len(rh)))

            code = pick("weather_code", i)
            out.append(
                DayForecast(
                    day=date.fromisoformat(day_str),
                    temp_min=temp_min,
                    temp_max=temp_max,
                    rain_chance=rain_chance,
                    rain_mm=float(pick("precipitation_sum", i, 0.0) or 0.0),
                    wind_max=wind_max,
                    humidity=humidity,
                    weather_code=int(code) if code is not None else None,
                    stale=stale,
                    night_min=night_min,
                    day_max=day_max,
                )
            )
        except (TypeError, ValueError) as exc:
            log.warning("Skipping unparseable forecast day %s: %s", day_str, exc)
    return out


def fetch_payload(lat: float, lon: float, tz: str, days: int = 3) -> dict:
    params = {
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "daily": ",".join(DAILY_FIELDS),
        "hourly": ",".join(HOURLY_FIELDS),
        "timezone": tz,
        "forecast_days": str(days),
    }
    response = httpx.get(OPEN_METEO_URL, params=params, timeout=8.0)
    response.raise_for_status()
    return response.json()


class WeatherService:
    def __init__(self, lat: float, lon: float, tz: str, fetcher=fetch_payload) -> None:
        self.lat = lat
        self.lon = lon
        self.tz = tz
        self.fetcher = fetcher

    @property
    def cache_key(self) -> str:
        return f"open-meteo:{self.lat:.4f},{self.lon:.4f}:{self.tz}"

    def _read_cache(self, conn: sqlite3.Connection) -> tuple[dict, datetime] | None:
        row = conn.execute(
            "SELECT fetched_at, payload FROM weather_cache WHERE key = ?", (self.cache_key,)
        ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["payload"]), datetime.fromisoformat(row["fetched_at"])
        except (ValueError, json.JSONDecodeError):
            return None

    def _write_cache(self, conn: sqlite3.Connection, payload: dict, now: datetime) -> None:
        conn.execute(
            """
            INSERT INTO weather_cache (key, fetched_at, payload) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET fetched_at = excluded.fetched_at,
                                           payload = excluded.payload
            """,
            (self.cache_key, now.isoformat(), json.dumps(payload)),
        )

    def forecast(self, conn: sqlite3.Connection, now: datetime | None = None) -> list[DayForecast]:
        """Up to three days of forecast. Empty list if nothing is available at all."""
        now = now or datetime.now(UTC)
        cached = self._read_cache(conn)
        if cached and now - cached[1] < CACHE_TTL:
            return _parse(cached[0], stale=False)
        try:
            payload = self.fetcher(self.lat, self.lon, self.tz)
        except Exception as exc:  # noqa: BLE001 - any failure degrades to cache
            log.warning("Forecast fetch failed: %s", exc)
            if cached:
                return _parse(cached[0], stale=True)
            return []
        self._write_cache(conn, payload, now)
        return _parse(payload, stale=False)

    def for_day(
        self, conn: sqlite3.Connection, day: date, now: datetime | None = None
    ) -> DayForecast | None:
        for f in self.forecast(conn, now):
            if f.day == day:
                return f
        return None


def manual_forecast(
    day: date,
    temp_min: float,
    temp_max: float,
    rainy: bool = False,
    windy: bool = False,
    humid: bool = False,
) -> DayForecast:
    """A forecast the user typed in. Flags are mapped onto the thresholds."""
    if temp_min > temp_max:
        temp_min, temp_max = temp_max, temp_min
    return DayForecast(
        day=day,
        temp_min=temp_min,
        temp_max=temp_max,
        rain_chance=70 if rainy else 10,
        rain_mm=0.0,
        wind_max=45.0 if windy else 15.0,
        humidity=80 if humid else 50,
        weather_code=None,
        manual=True,
    )
