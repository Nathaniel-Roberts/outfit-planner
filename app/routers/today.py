"""The Today screen: forecast, the day's activity tags, ranked outfits, one-tap wear.

The same screen serves Tomorrow (and any date via ?day=YYYY-MM-DD). After 6pm it
opens on Tomorrow, since that is when she is planning. Per-day state (chosen tags,
manual weather) is stored per user in user_settings as small JSON maps keyed by date.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import colour_rules, outfits, tags, wear
from app import colours as palette
from app.auth import require_user
from app.routers.outfits import conn_of
from app.scope import viewing_user
from app.scoring import rank_outfits
from app.templating import render, today_in
from app.users import User
from app.weather import DayForecast, manual_forecast

router = APIRouter()

DAY_TAGS_KEY = "day_tags"  # {"2026-10-01": [tag ids], ...}
WEATHER_OVERRIDE_KEY = "weather_overrides"  # {"2026-10-01": {...}, ...}
ROUTINE_KEY = "routine"  # {"0": [tag ids], ... "6": [...]}  Monday is 0
EVENING_HOUR = 18
KEEP_DAYS = 14


def _get_setting(conn: sqlite3.Connection, user_id: int, key: str) -> dict:
    row = conn.execute(
        "SELECT value FROM user_settings WHERE user_id = ? AND key = ?", (user_id, key)
    ).fetchone()
    if row is None:
        return {}
    try:
        value = json.loads(row["value"])
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _set_setting(conn: sqlite3.Connection, user_id: int, key: str, value: dict | None) -> None:
    if not value:
        conn.execute("DELETE FROM user_settings WHERE user_id = ? AND key = ?", (user_id, key))
        return
    conn.execute(
        """
        INSERT INTO user_settings (user_id, key, value) VALUES (?, ?, ?)
        ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value
        """,
        (user_id, key, json.dumps(value)),
    )


def _prune(mapping: dict, today: date) -> dict:
    cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
    return {k: v for k, v in mapping.items() if k >= cutoff}


def _ids(values) -> list[int]:
    return [int(t) for t in values if str(t).isdigit()]


# --- Routine -------------------------------------------------------------------------------


def routine(conn: sqlite3.Connection, user_id: int) -> dict[int, list[int]]:
    raw = _get_setting(conn, user_id, ROUTINE_KEY)
    return {int(k): _ids(v) for k, v in raw.items() if str(k).isdigit() and 0 <= int(k) <= 6}


def set_routine(conn: sqlite3.Connection, user_id: int, by_weekday: dict[int, list[int]]) -> None:
    _set_setting(conn, user_id, ROUTINE_KEY, {str(k): v for k, v in by_weekday.items() if v})


# --- Per-day state ---------------------------------------------------------------------------


def chosen_tags(conn: sqlite3.Connection, user_id: int, day: date) -> tuple[list[int], bool]:
    """Tags for the day and whether they came from the routine (True) or her choice."""
    saved = _get_setting(conn, user_id, DAY_TAGS_KEY)
    if day.isoformat() in saved:
        return _ids(saved[day.isoformat()]), False
    return routine(conn, user_id).get(day.weekday(), []), True


def remember_tags(
    conn: sqlite3.Connection, user_id: int, day: date, tag_ids: list[int], today: date
) -> None:
    saved = _prune(_get_setting(conn, user_id, DAY_TAGS_KEY), today)
    saved[day.isoformat()] = tag_ids
    _set_setting(conn, user_id, DAY_TAGS_KEY, saved)


def weather_override(conn: sqlite3.Connection, user_id: int, day: date) -> DayForecast | None:
    saved = _get_setting(conn, user_id, WEATHER_OVERRIDE_KEY).get(day.isoformat())
    if not saved:
        return None
    try:
        return manual_forecast(
            day,
            float(saved["temp_min"]),
            float(saved["temp_max"]),
            bool(saved.get("rainy")),
            bool(saved.get("windy")),
            bool(saved.get("humid")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def set_weather_override(
    conn: sqlite3.Connection, user_id: int, day: date, values: dict | None, today: date
) -> None:
    saved = _prune(_get_setting(conn, user_id, WEATHER_OVERRIDE_KEY), today)
    if values is None:
        saved.pop(day.isoformat(), None)
    else:
        saved[day.isoformat()] = values
    _set_setting(conn, user_id, WEATHER_OVERRIDE_KEY, saved)


def conditions_for(
    request: Request, conn: sqlite3.Connection, user_id: int, day: date
) -> DayForecast | None:
    override = weather_override(conn, user_id, day)
    if override is not None:
        return override
    return request.app.state.weather.for_day(conn, day)


def build_results(
    request: Request, conn: sqlite3.Connection, viewer: User, selected: list[int], day: date
):
    forecast = conditions_for(request, conn, viewer.id, day)
    library = [o for o in outfits.list_outfits(conn, viewer.id) if not o.in_wash_on(day)]
    in_wash = [o for o in outfits.list_outfits(conn, viewer.id) if o.in_wash_on(day)]
    rules = colour_rules.rules_map(conn, viewer.id)
    ranked = rank_outfits(library, set(selected), forecast, day, rules)
    return forecast, ranked, in_wash


# --- Day selection ---------------------------------------------------------------------------


def resolve_day(request: Request, tz: str) -> tuple[date, date]:
    """(the day being planned, today). Evening visits default to tomorrow."""
    today = today_in(tz)
    raw = request.query_params.get("day")
    if raw == "today":
        return today, today
    if raw == "tomorrow":
        return today + timedelta(days=1), today
    if raw:
        try:
            chosen = date.fromisoformat(raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Bad day") from exc
        return chosen, today
    if datetime.now(ZoneInfo(tz)).hour >= EVENING_HOUR:
        return today + timedelta(days=1), today
    return today, today


def _selected_from_query(request: Request) -> list[int] | None:
    if "tags" not in request.query_params and "tags_set" not in request.query_params:
        return None
    return _ids(request.query_params.getlist("tags"))


def _day_context(
    request: Request,
    conn: sqlite3.Connection,
    viewer: User,
    day: date,
    today: date,
    selected: list[int],
):
    forecast, ranked, in_wash = build_results(request, conn, viewer, selected, day)
    worn = wear.worn_on(conn, viewer.id, day)
    by_id = {o.id: o for o in outfits.list_outfits(conn, viewer.id, include_archived=True)}
    return {
        "day": day,
        "today": today,
        "is_today": day == today,
        "is_tomorrow": day == today + timedelta(days=1),
        "forecast": forecast,
        "has_override": weather_override(conn, viewer.id, day) is not None,
        "ranked": ranked,
        "in_wash": in_wash,
        "worn_today": [(w, by_id.get(w.outfit_id)) for w in worn],
        "worn_ids": {w.outfit_id for w in worn},
        "palette": palette.BY_KEY,
    }


@router.get("/", response_class=HTMLResponse)
def today_page(request: Request, user: User = Depends(require_user)):
    conn = conn_of(request)
    viewer = viewing_user(request, conn, user)
    day, today = resolve_day(request, request.app.state.settings.tz)
    selected = _selected_from_query(request)
    from_routine = False
    if selected is None:
        selected, from_routine = chosen_tags(conn, viewer.id, day)
    else:
        remember_tags(conn, viewer.id, day, selected, today)
    ctx = _day_context(request, conn, viewer, day, today, selected)
    feedback_due = []
    if day == today:
        by_id = {o.id: o for o in outfits.list_outfits(conn, viewer.id, include_archived=True)}
        feedback_due = [
            (w, by_id.get(w.outfit_id)) for w in wear.awaiting_feedback(conn, viewer.id, today)
        ]
        feedback_due = [(w, o) for w, o in feedback_due if o is not None][:2]
    return render(
        request.app.state.templates,
        request,
        "today.html",
        all_tags=tags.list_tags(conn, viewer.id),
        selected=set(selected),
        from_routine=from_routine and bool(selected),
        feedback_due=feedback_due,
        viewer=viewer,
        nav="today",
        **ctx,
    )


@router.get("/today/results", response_class=HTMLResponse)
def today_results(request: Request, user: User = Depends(require_user)):
    """HTMX partial: re-rank when the tag chips change."""
    conn = conn_of(request)
    viewer = viewing_user(request, conn, user)
    day, today = resolve_day(request, request.app.state.settings.tz)
    selected = _selected_from_query(request) or []
    remember_tags(conn, viewer.id, day, selected, today)
    ctx = _day_context(request, conn, viewer, day, today, selected)
    return render(request.app.state.templates, request, "partials/today_results.html", **ctx)


@router.post("/today/weather")
def set_weather(
    request: Request,
    user: User = Depends(require_user),
    day: str = Form(""),
    temp_min: str = Form(""),
    temp_max: str = Form(""),
    rainy: str = Form(""),
    windy: str = Form(""),
    humid: str = Form(""),
):
    conn = conn_of(request)
    viewer = viewing_user(request, conn, user)
    today = today_in(request.app.state.settings.tz)
    target = _parse_day(day) or today
    try:
        lo, hi = float(temp_min), float(temp_max)
    except ValueError:
        return RedirectResponse(f"/?day={target.isoformat()}", status_code=303)
    set_weather_override(
        conn,
        viewer.id,
        target,
        {
            "temp_min": min(lo, hi),
            "temp_max": max(lo, hi),
            "rainy": bool(rainy),
            "windy": bool(windy),
            "humid": bool(humid),
        },
        today,
    )
    return RedirectResponse(f"/?day={target.isoformat()}", status_code=303)


@router.post("/today/weather/clear")
def clear_weather(request: Request, user: User = Depends(require_user), day: str = Form("")):
    conn = conn_of(request)
    viewer = viewing_user(request, conn, user)
    today = today_in(request.app.state.settings.tz)
    target = _parse_day(day) or today
    set_weather_override(conn, viewer.id, target, None, today)
    return RedirectResponse(f"/?day={target.isoformat()}", status_code=303)


@router.post("/today/feedback/{entry_id}")
def feedback(
    request: Request, entry_id: int, user: User = Depends(require_user), feedback: str = Form("")
):
    conn = conn_of(request)
    viewer = viewing_user(request, conn, user)
    try:
        wear.set_feedback(conn, entry_id, viewer.id, feedback)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse("/?day=today", status_code=303)


def _parse_day(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
