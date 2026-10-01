"""The Today screen: forecast, today's activity tags, ranked outfits, one-tap wear."""

from __future__ import annotations

import json
import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import colour_rules, outfits, tags, wear
from app.auth import require_user
from app.routers.outfits import conn_of
from app.scope import viewing_user
from app.scoring import rank_outfits
from app.templating import render, today_in
from app.users import User
from app.weather import DayForecast, manual_forecast

router = APIRouter()

TODAY_TAGS_KEY = "today_tags"
WEATHER_OVERRIDE_KEY = "weather_override"


def _get_setting(conn: sqlite3.Connection, user_id: int, key: str) -> dict | None:
    row = conn.execute(
        "SELECT value FROM user_settings WHERE user_id = ? AND key = ?", (user_id, key)
    ).fetchone()
    if row is None:
        return None
    try:
        value = json.loads(row["value"])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _set_setting(conn: sqlite3.Connection, user_id: int, key: str, value: dict | None) -> None:
    if value is None:
        conn.execute("DELETE FROM user_settings WHERE user_id = ? AND key = ?", (user_id, key))
        return
    conn.execute(
        """
        INSERT INTO user_settings (user_id, key, value) VALUES (?, ?, ?)
        ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value
        """,
        (user_id, key, json.dumps(value)),
    )


def remembered_tags(conn: sqlite3.Connection, user_id: int, today: date) -> list[int]:
    saved = _get_setting(conn, user_id, TODAY_TAGS_KEY)
    if saved and saved.get("day") == today.isoformat():
        return [int(t) for t in saved.get("tags", []) if str(t).isdigit()]
    return []


def remember_tags(conn: sqlite3.Connection, user_id: int, today: date, tag_ids: list[int]) -> None:
    _set_setting(conn, user_id, TODAY_TAGS_KEY, {"day": today.isoformat(), "tags": tag_ids})


def weather_override(conn: sqlite3.Connection, user_id: int, today: date) -> DayForecast | None:
    saved = _get_setting(conn, user_id, WEATHER_OVERRIDE_KEY)
    if not saved or saved.get("day") != today.isoformat():
        return None
    try:
        return manual_forecast(
            today,
            float(saved["temp_min"]),
            float(saved["temp_max"]),
            bool(saved.get("rainy")),
            bool(saved.get("windy")),
            bool(saved.get("humid")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def conditions_for(
    request: Request, conn: sqlite3.Connection, user_id: int, today: date
) -> DayForecast | None:
    override = weather_override(conn, user_id, today)
    if override is not None:
        return override
    return request.app.state.weather.for_day(conn, today)


def build_results(
    request: Request, conn: sqlite3.Connection, viewer: User, selected: list[int], today: date
):
    forecast = conditions_for(request, conn, viewer.id, today)
    library = outfits.list_outfits(conn, viewer.id)
    rules = colour_rules.rules_map(conn, viewer.id)
    ranked = rank_outfits(library, set(selected), forecast, today, rules)
    return forecast, ranked


def _selected_from_query(request: Request) -> list[int] | None:
    if "tags" not in request.query_params and "tags_set" not in request.query_params:
        return None
    return [int(t) for t in request.query_params.getlist("tags") if t.isdigit()]


@router.get("/", response_class=HTMLResponse)
def today_page(request: Request, user: User = Depends(require_user)):
    conn = conn_of(request)
    viewer = viewing_user(request, conn, user)
    today = today_in(request.app.state.settings.tz)
    selected = _selected_from_query(request)
    if selected is None:
        selected = remembered_tags(conn, viewer.id, today)
    else:
        remember_tags(conn, viewer.id, today, selected)
    forecast, ranked = build_results(request, conn, viewer, selected, today)
    worn_today = wear.worn_on(conn, viewer.id, today)
    worn_ids = {w.outfit_id for w in worn_today}
    from app import colours as palette

    return render(
        request.app.state.templates,
        request,
        "today.html",
        today=today,
        forecast=forecast,
        has_override=weather_override(conn, viewer.id, today) is not None,
        all_tags=tags.list_tags(conn, viewer.id),
        selected=set(selected),
        ranked=ranked,
        worn_today=[
            (w, next((s.outfit for s in ranked if s.outfit.id == w.outfit_id), None))
            for w in worn_today
        ],
        worn_ids=worn_ids,
        palette=palette.BY_KEY,
        viewer=viewer,
        nav="today",
    )


@router.get("/today/results", response_class=HTMLResponse)
def today_results(request: Request, user: User = Depends(require_user)):
    """HTMX partial: re-rank when the tag chips change."""
    conn = conn_of(request)
    viewer = viewing_user(request, conn, user)
    today = today_in(request.app.state.settings.tz)
    selected = _selected_from_query(request) or []
    remember_tags(conn, viewer.id, today, selected)
    forecast, ranked = build_results(request, conn, viewer, selected, today)
    worn_ids = {w.outfit_id for w in wear.worn_on(conn, viewer.id, today)}
    from app import colours as palette

    return render(
        request.app.state.templates,
        request,
        "partials/today_results.html",
        ranked=ranked,
        worn_ids=worn_ids,
        forecast=forecast,
        palette=palette.BY_KEY,
    )


@router.post("/today/weather")
def set_weather(
    request: Request,
    user: User = Depends(require_user),
    temp_min: str = Form(""),
    temp_max: str = Form(""),
    rainy: str = Form(""),
    windy: str = Form(""),
    humid: str = Form(""),
):
    conn = conn_of(request)
    viewer = viewing_user(request, conn, user)
    today = today_in(request.app.state.settings.tz)
    try:
        lo, hi = float(temp_min), float(temp_max)
    except ValueError:
        return RedirectResponse("/", status_code=303)
    _set_setting(
        conn,
        viewer.id,
        WEATHER_OVERRIDE_KEY,
        {
            "day": today.isoformat(),
            "temp_min": min(lo, hi),
            "temp_max": max(lo, hi),
            "rainy": bool(rainy),
            "windy": bool(windy),
            "humid": bool(humid),
        },
    )
    return RedirectResponse("/", status_code=303)


@router.post("/today/weather/clear")
def clear_weather(request: Request, user: User = Depends(require_user)):
    conn = conn_of(request)
    viewer = viewing_user(request, conn, user)
    _set_setting(conn, viewer.id, WEATHER_OVERRIDE_KEY, None)
    return RedirectResponse("/", status_code=303)
