"""Outfit CRUD, photo upload and photo serving."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from app import colours as palette
from app import images, outfits, tags
from app.auth import require_user
from app.forms import new_tag_names, outfit_input_from_form, read_upload
from app.outfits import Outfit
from app.scope import viewing_user
from app.templating import render
from app.users import User

router = APIRouter()


def conn_of(request: Request) -> sqlite3.Connection:
    from app.main import get_conn

    return get_conn(request)


def load_owned(request: Request, outfit_id: int, user: User) -> tuple[Outfit, User]:
    conn = conn_of(request)
    viewer = viewing_user(request, conn, user)
    outfit = outfits.get_outfit(conn, outfit_id)
    if outfit is None or (outfit.user_id != viewer.id and not user.is_admin):
        raise HTTPException(status_code=404, detail="Outfit not found")
    return outfit, viewer


def _back(request: Request, default: str) -> RedirectResponse:
    target = request.headers.get("hx-current-url") or request.headers.get("referer") or default
    if not target.startswith(("/", str(request.base_url).rstrip("/"))):
        target = default
    return RedirectResponse(target, status_code=303)


# --- Library -------------------------------------------------------------------------


def filter_from_query(request: Request) -> outfits.OutfitFilter:
    qp = request.query_params
    temp = qp.get("temp")
    try:
        temperature = float(temp) if temp not in (None, "") else None
    except ValueError:
        temperature = None
    return outfits.OutfitFilter(
        tag_ids=[int(t) for t in qp.getlist("tag") if t.isdigit()],
        colours=qp.getlist("colour"),
        temperature=temperature,
        favourite=qp.get("favourite") == "1",
        archived=qp.get("archived") == "1",
        query=qp.get("q") or None,
    )


@router.get("/outfits", response_class=HTMLResponse)
def library(request: Request, user: User = Depends(require_user)):
    conn = conn_of(request)
    viewer = viewing_user(request, conn, user)
    f = filter_from_query(request)
    items = outfits.filter_outfits(
        outfits.list_outfits(conn, viewer.id, only_archived=f.archived), f
    )
    return render(
        request.app.state.templates,
        request,
        "outfits_list.html",
        outfits=items,
        filter=f,
        all_tags=tags.list_tags(conn, viewer.id),
        palette_list=palette.PALETTE,
        archived=f.archived,
        viewer=viewer,
        nav="library",
        palette=palette.BY_KEY,
    )


# --- Create --------------------------------------------------------------------------


@router.get("/outfits/new", response_class=HTMLResponse)
def new_outfit(request: Request, user: User = Depends(require_user), details: int = 0):
    conn = conn_of(request)
    viewer = viewing_user(request, conn, user)
    if not details:
        return render(request.app.state.templates, request, "outfit_new.html", nav="new")
    return render(
        request.app.state.templates,
        request,
        "outfit_form.html",
        outfit=None,
        all_tags=tags.list_tags(conn, viewer.id),
        palette=palette.PALETTE,
        suggested=[],
        nav="new",
        action="/outfits",
    )


@router.post("/outfits")
async def create_outfit(request: Request, user: User = Depends(require_user)):
    conn = conn_of(request)
    viewer = viewing_user(request, conn, user)
    form = await request.form()
    data = outfit_input_from_form(form)
    for tag in tags.resolve_names(conn, viewer.id, new_tag_names(form)):
        if tag.id not in data.tag_ids:
            data.tag_ids.append(tag.id)
    photo_bytes = await read_upload(form.get("photo"))
    outfit = outfits.create_outfit(conn, viewer.id, data)
    suggested: list[str] = []
    if photo_bytes:
        try:
            outfits.add_photo(conn, request.app.state.settings.photos_dir, outfit, photo_bytes)
        except images.BadImage as exc:
            outfits.delete_outfit(conn, request.app.state.settings.photos_dir, outfit)
            request.state.flash = {"kind": "error", "message": str(exc)}
            response = render(request.app.state.templates, request, "outfit_new.html", nav="new")
            response.status_code = 400
            return response
        if not data.colours:
            img = images.open_image(photo_bytes)
            suggested = palette.extract_colours(img)
            if suggested:
                data.colours = suggested
                outfit = outfits.update_outfit(conn, outfit, data)
    if form.get("then") == "edit" or photo_bytes and not data.tag_ids:
        query = "?new=1" + ("&suggested=1" if suggested else "")
        return RedirectResponse(f"/outfits/{outfit.id}/edit{query}", status_code=303)
    return RedirectResponse(f"/outfits/{outfit.id}", status_code=303)


# --- Read ----------------------------------------------------------------------------


@router.get("/outfits/{outfit_id}", response_class=HTMLResponse)
def outfit_detail(request: Request, outfit_id: int, user: User = Depends(require_user)):
    outfit, viewer = load_owned(request, outfit_id, user)
    conn = conn_of(request)
    history = conn.execute(
        "SELECT worn_on FROM wear_log WHERE outfit_id = ? ORDER BY worn_on DESC LIMIT 10",
        (outfit.id,),
    ).fetchall()
    return render(
        request.app.state.templates,
        request,
        "outfit_detail.html",
        outfit=outfit,
        palette=palette.BY_KEY,
        history=[r["worn_on"] for r in history],
        nav="library",
    )


# --- Update --------------------------------------------------------------------------


@router.get("/outfits/{outfit_id}/edit", response_class=HTMLResponse)
def edit_outfit(
    request: Request,
    outfit_id: int,
    user: User = Depends(require_user),
    new: int = 0,
    suggested: int = 0,
):
    outfit, viewer = load_owned(request, outfit_id, user)
    conn = conn_of(request)
    return render(
        request.app.state.templates,
        request,
        "outfit_form.html",
        outfit=outfit,
        all_tags=tags.list_tags(conn, viewer.id),
        palette=palette.PALETTE,
        suggested=outfit.colours if suggested else [],
        is_new=bool(new),
        nav="new" if new else "library",
        action=f"/outfits/{outfit.id}",
    )


@router.post("/outfits/{outfit_id}")
async def update_outfit(request: Request, outfit_id: int, user: User = Depends(require_user)):
    outfit, viewer = load_owned(request, outfit_id, user)
    conn = conn_of(request)
    form = await request.form()
    data = outfit_input_from_form(form, outfit)
    for tag in tags.resolve_names(conn, outfit.user_id, new_tag_names(form)):
        if tag.id not in data.tag_ids:
            data.tag_ids.append(tag.id)
    outfits.update_outfit(conn, outfit, data)
    photo_bytes = await read_upload(form.get("photo"))
    if photo_bytes:
        try:
            outfits.add_photo(conn, request.app.state.settings.photos_dir, outfit, photo_bytes)
        except images.BadImage as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(f"/outfits/{outfit.id}", status_code=303)


@router.post("/outfits/{outfit_id}/photos")
async def add_photo(request: Request, outfit_id: int, user: User = Depends(require_user)):
    outfit, _ = load_owned(request, outfit_id, user)
    conn = conn_of(request)
    form = await request.form()
    photo_bytes = await read_upload(form.get("photo"))
    if not photo_bytes:
        raise HTTPException(status_code=400, detail="No photo received")
    try:
        outfits.add_photo(conn, request.app.state.settings.photos_dir, outfit, photo_bytes)
    except images.BadImage as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(f"/outfits/{outfit.id}/edit", status_code=303)


@router.post("/outfits/{outfit_id}/photos/{photo_id}/delete")
def delete_photo(
    request: Request, outfit_id: int, photo_id: int, user: User = Depends(require_user)
):
    outfit, _ = load_owned(request, outfit_id, user)
    outfits.remove_photo(conn_of(request), request.app.state.settings.photos_dir, outfit, photo_id)
    return RedirectResponse(f"/outfits/{outfit.id}/edit", status_code=303)


@router.post("/outfits/{outfit_id}/photos/{photo_id}/cover")
def cover_photo(
    request: Request, outfit_id: int, photo_id: int, user: User = Depends(require_user)
):
    outfit, _ = load_owned(request, outfit_id, user)
    outfits.make_cover(conn_of(request), outfit, photo_id)
    return RedirectResponse(f"/outfits/{outfit.id}/edit", status_code=303)


@router.post("/outfits/{outfit_id}/favourite")
def toggle_favourite(request: Request, outfit_id: int, user: User = Depends(require_user)):
    outfit, _ = load_owned(request, outfit_id, user)
    conn = conn_of(request)
    outfits.set_flag(conn, outfit.id, "favourite", not outfit.favourite)
    if request.headers.get("hx-request") == "true":
        refreshed = outfits.get_outfit(conn, outfit.id)
        return render(
            request.app.state.templates, request, "partials/favourite_button.html", outfit=refreshed
        )
    return _back(request, f"/outfits/{outfit.id}")


@router.post("/outfits/{outfit_id}/archive")
def archive(request: Request, outfit_id: int, user: User = Depends(require_user)):
    outfit, _ = load_owned(request, outfit_id, user)
    outfits.set_flag(conn_of(request), outfit.id, "archived", not outfit.archived)
    return RedirectResponse(f"/outfits/{outfit.id}", status_code=303)


@router.post("/outfits/{outfit_id}/delete")
def delete_outfit(request: Request, outfit_id: int, user: User = Depends(require_user)):
    outfit, _ = load_owned(request, outfit_id, user)
    outfits.delete_outfit(conn_of(request), request.app.state.settings.photos_dir, outfit)
    return RedirectResponse("/outfits", status_code=303)


# --- Photo files ---------------------------------------------------------------------


@router.get("/photos/{outfit_id}/{filename}")
def photo_file(request: Request, outfit_id: int, filename: str, user: User = Depends(require_user)):
    if not images.safe_filename(filename):
        raise HTTPException(status_code=404)
    conn = conn_of(request)
    row = conn.execute("SELECT user_id FROM outfits WHERE id = ?", (outfit_id,)).fetchone()
    if row is None or (row["user_id"] != user.id and not user.is_admin):
        raise HTTPException(status_code=404)
    path = outfits.photos_dir_for(request.app.state.settings.photos_dir, outfit_id) / filename
    if not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@router.get("/outfits/{outfit_id}/card", response_class=HTMLResponse)
def outfit_card(request: Request, outfit_id: int, user: User = Depends(require_user)) -> Response:
    outfit, _ = load_owned(request, outfit_id, user)
    return render(
        request.app.state.templates,
        request,
        "partials/outfit_card.html",
        outfit=outfit,
        palette=palette.BY_KEY,
    )
