"""Settings: activity tags, and the admin's 'view as' switch."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from app import colour_rules, maintenance, outfits, tags, users
from app import colours as palette
from app.auth import require_user
from app.routers import today as today_router
from app.scope import VIEW_COOKIE, viewing_user
from app.templating import render
from app.users import User

router = APIRouter()

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def conn_of(request: Request):
    from app.main import get_conn

    return get_conn(request)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, user: User = Depends(require_user)):
    conn = conn_of(request)
    viewer = viewing_user(request, conn, user)
    return render(
        request.app.state.templates,
        request,
        "settings.html",
        viewer=viewer,
        all_tags=tags.list_tags(conn, viewer.id),
        all_users=users.list_users(conn) if user.is_admin else [],
        rules=colour_rules.list_rules(conn, viewer.id),
        palette_list=palette.PALETTE,
        routine=today_router.routine(conn, viewer.id),
        weekdays=WEEKDAYS,
        binned=outfits.list_deleted(conn, viewer.id),
        trash_days=outfits.TRASH_DAYS,
        latest_backup=maintenance.latest_backup(request.app.state.settings)
        if user.is_admin
        else None,
        settings=request.app.state.settings,
        nav="settings",
    )


@router.post("/settings/tags")
def add_tag(
    request: Request,
    user: User = Depends(require_user),
    name: str = Form(""),
    description: str = Form(""),
):
    conn = conn_of(request)
    viewer = viewing_user(request, conn, user)
    try:
        tags.create_tag(conn, viewer.id, name, description)
    except ValueError:
        pass
    return RedirectResponse("/settings#tags", status_code=303)


@router.post("/settings/tags/{tag_id}")
def rename_tag(
    request: Request,
    tag_id: int,
    user: User = Depends(require_user),
    name: str = Form(""),
    description: str = Form(""),
):
    conn = conn_of(request)
    viewer = viewing_user(request, conn, user)
    if tags.get_tag(conn, viewer.id, tag_id) is None:
        raise HTTPException(status_code=404)
    try:
        tags.rename_tag(conn, viewer.id, tag_id, name, description)
    except ValueError:
        pass
    return RedirectResponse("/settings#tags", status_code=303)


@router.post("/settings/tags/{tag_id}/delete")
def delete_tag(request: Request, tag_id: int, user: User = Depends(require_user)):
    conn = conn_of(request)
    viewer = viewing_user(request, conn, user)
    tags.delete_tag(conn, viewer.id, tag_id)
    return RedirectResponse("/settings#tags", status_code=303)


@router.post("/settings/view-as")
def view_as(request: Request, user: User = Depends(require_user), user_id: str = Form("")):
    if not user.is_admin:
        raise HTTPException(status_code=403)
    response = RedirectResponse("/settings", status_code=303)
    if user_id.isdigit() and int(user_id) != user.id:
        response.set_cookie(
            VIEW_COOKIE, user_id, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 365
        )
    else:
        response.delete_cookie(VIEW_COOKIE)
    return response


# --- Colour rules --------------------------------------------------------------------


@router.post("/settings/colour-rules")
def add_colour_rule(
    request: Request,
    user: User = Depends(require_user),
    colour_a: str = Form(""),
    colour_b: str = Form(""),
    verdict: str = Form("good"),
):
    conn = conn_of(request)
    viewer = viewing_user(request, conn, user)
    try:
        colour_rules.set_rule(conn, viewer.id, colour_a, colour_b, verdict)
    except ValueError:
        pass
    return RedirectResponse("/settings#colours", status_code=303)


@router.post("/settings/colour-rules/{rule_id}/delete")
def delete_colour_rule(request: Request, rule_id: int, user: User = Depends(require_user)):
    conn = conn_of(request)
    viewer = viewing_user(request, conn, user)
    colour_rules.delete_rule(conn, viewer.id, rule_id)
    return RedirectResponse("/settings#colours", status_code=303)


@router.get("/colour-rules/hint", response_class=PlainTextResponse)
def colour_hint(request: Request, user: User = Depends(require_user), colours: str = ""):
    """Plain-text hint for the tagging form: which of her rules the chosen colours trip."""
    conn = conn_of(request)
    viewer = viewing_user(request, conn, user)
    keys = [c.strip() for c in colours.split(",") if c.strip()]
    hits = colour_rules.pairs_in(keys, colour_rules.rules_map(conn, viewer.id))
    if not hits:
        return ""
    likes = [colour_rules.pair_label(p) for p, v in hits if v == "good"]
    avoids = [colour_rules.pair_label(p) for p, v in hits if v == "avoid"]
    parts = []
    if likes:
        parts.append("You like: " + ", ".join(likes))
    if avoids:
        parts.append("You usually avoid: " + ", ".join(avoids))
    return ". ".join(parts) + "."


# --- Weekly routine ----------------------------------------------------------------------


@router.post("/settings/routine")
async def save_routine(request: Request, user: User = Depends(require_user)):
    conn = conn_of(request)
    viewer = viewing_user(request, conn, user)
    form = await request.form()
    by_weekday: dict[int, list[int]] = {}
    for weekday in range(7):
        by_weekday[weekday] = [
            int(v) for v in form.getlist(f"routine_{weekday}") if str(v).isdigit()
        ]
    today_router.set_routine(conn, viewer.id, by_weekday)
    return RedirectResponse("/settings?saved=routine#routine", status_code=303)
