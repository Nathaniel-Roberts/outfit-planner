"""Settings: activity tags, and the admin's 'view as' switch."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import tags, users
from app.auth import require_user
from app.scope import VIEW_COOKIE, viewing_user
from app.templating import render
from app.users import User

router = APIRouter()


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
