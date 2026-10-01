"""Logging and un-logging wears from the UI."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app import wear
from app.auth import require_user
from app.routers.outfits import conn_of, load_owned
from app.templating import today_in
from app.users import User

router = APIRouter()


def _parse_day(value: str | None, tz: str) -> date:
    if not value:
        return today_in(tz)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Bad date") from exc


@router.post("/wear/{outfit_id}")
def log_wear(
    request: Request,
    outfit_id: int,
    user: User = Depends(require_user),
    worn_on: str = Form(""),
    next: str = Form(""),
):
    outfit, viewer = load_owned(request, outfit_id, user)
    day = _parse_day(worn_on, request.app.state.settings.tz)
    wear.log_wear(conn_of(request), outfit.id, outfit.user_id, day)
    if request.headers.get("hx-request") == "true":
        return RedirectResponse("/", status_code=303, headers={"HX-Redirect": "/"})
    target = next if next.startswith("/") else f"/outfits/{outfit.id}"
    return RedirectResponse(target, status_code=303)


@router.post("/wear/entry/{entry_id}/delete")
def unlog_wear(
    request: Request, entry_id: int, user: User = Depends(require_user), next: str = Form("/")
):
    conn = conn_of(request)
    from app.scope import viewing_user

    viewer = viewing_user(request, conn, user)
    if not wear.unlog_wear(conn, entry_id, viewer.id):
        raise HTTPException(status_code=404)
    return RedirectResponse(next if next.startswith("/") else "/", status_code=303)
