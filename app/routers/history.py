"""Wear history: a month calendar and most/least worn lists."""

from __future__ import annotations

import calendar
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app import colours as palette
from app import outfits, wear
from app.auth import require_user
from app.routers.outfits import conn_of
from app.scope import viewing_user
from app.templating import render, today_in
from app.users import User

router = APIRouter()


def _parse_month(value: str | None, today: date) -> date:
    if value:
        try:
            y, m = value.split("-")[:2]
            return date(int(y), int(m), 1)
        except ValueError:
            pass
    return today.replace(day=1)


def month_grid(first: date) -> list[list[date | None]]:
    """Weeks starting Monday, None for padding days."""
    cal = calendar.Calendar(firstweekday=0)
    weeks: list[list[date | None]] = []
    for week in cal.monthdatescalendar(first.year, first.month):
        weeks.append([d if d.month == first.month else None for d in week])
    return weeks


@router.get("/history", response_class=HTMLResponse)
def history(request: Request, user: User = Depends(require_user), month: str | None = None):
    conn = conn_of(request)
    viewer = viewing_user(request, conn, user)
    today = today_in(request.app.state.settings.tz)
    first = _parse_month(month, today)
    last = date(first.year, first.month, calendar.monthrange(first.year, first.month)[1])

    entries = wear.entries_between(conn, viewer.id, first, last)
    library = {o.id: o for o in outfits.list_outfits(conn, viewer.id, include_archived=True)}
    by_day: dict[date, list] = {}
    for e in entries:
        outfit = library.get(e.outfit_id)
        if outfit is not None:
            by_day.setdefault(e.worn_on, []).append((e, outfit))

    counts = wear.wear_counts(conn, viewer.id)
    active = [o for o in library.values() if not o.archived]
    most = sorted(active, key=lambda o: (-counts.get(o.id, 0), o.last_worn_on or date.min))[:8]
    least = sorted(active, key=lambda o: (counts.get(o.id, 0), o.last_worn_on or date.min))[:8]

    prev_month = (first - timedelta(days=1)).replace(day=1)
    next_month = (last + timedelta(days=1)).replace(day=1)

    return render(
        request.app.state.templates,
        request,
        "history.html",
        first=first,
        weeks=month_grid(first),
        by_day=by_day,
        today=today,
        prev_month=prev_month.strftime("%Y-%m"),
        next_month=next_month.strftime("%Y-%m") if next_month <= today.replace(day=1) else None,
        most=[(o, counts.get(o.id, 0)) for o in most if counts.get(o.id, 0)],
        least=[(o, counts.get(o.id, 0)) for o in least],
        total=sum(counts.values()),
        palette=palette.BY_KEY,
        viewer=viewer,
        nav="history",
    )
