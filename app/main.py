"""Application factory and the few routes that don't belong to a feature router."""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.routing import Route

from app import db, users
from app.auth import (
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    AccessVerifier,
    AdminCredentials,
    RedirectToLogin,
    SessionCodec,
    redirect_to_login,
    resolve_user,
)
from app.config import Settings, load_settings
from app.mcp_server import build_mcp_server, mcp_asgi_app
from app.routers import backup as backup_router
from app.routers import history as history_router
from app.routers import outfits as outfits_router
from app.routers import settings as settings_router
from app.routers import today as today_router
from app.routers import wear as wear_router
from app.templating import build_templates, render
from app.weather import WeatherService

log = logging.getLogger("outfit_planner")
STATIC_DIR = Path(__file__).resolve().parent / "static"


def app_version() -> str:
    """A short hash of the app shell, so the service worker cache rolls over on upgrade.

    The package is not installed as a distribution inside the image, so a version
    number from metadata is not reliable. Hashing the files the worker precaches is.
    """
    digest = hashlib.sha1()
    for name in ("app.css", "app.js", "sw.js", "vendor/htmx.min.js", "manifest.webmanifest"):
        path = STATIC_DIR / name
        if path.exists():
            digest.update(path.read_bytes())
    for path in sorted(STATIC_DIR.parent.glob("templates/**/*.html")):
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def get_conn(request: Request) -> sqlite3.Connection:
    conn = getattr(request.state, "conn", None)
    if conn is None:
        conn = db.connect(request.app.state.settings.db_path)
        request.state.conn = conn
    return conn


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    logging.basicConfig(level=logging.DEBUG if settings.dev_mode else logging.INFO)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        conn = db.connect(settings.db_path)
        try:
            applied = db.migrate(conn)
            if applied:
                log.info("Applied migrations: %s", ", ".join(applied))
            if settings.admin_username:
                users.ensure_admin(conn, settings.admin_username)
        finally:
            conn.close()
        if settings.dev_mode:
            log.warning(
                "DEV_MODE is on: Access checks are bypassed. Never run like this in production."
            )
        elif not settings.access_enabled:
            log.warning(
                "Cloudflare Access is not configured (CF_ACCESS_TEAM_DOMAIN / CF_ACCESS_AUD). "
                "Only the admin login protects this app. Do not expose it."
            )
        async with app.state.mcp_server.session_manager.run():
            yield

    app = FastAPI(title="Outfit Planner", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.templates = build_templates(settings.tz)
    app.state.sessions = SessionCodec(settings.session_secret)
    app.state.access_verifier = AccessVerifier(settings)
    app.state.admin_credentials = AdminCredentials(settings)
    app.state.weather = WeatherService(settings.weather_lat, settings.weather_lon, settings.tz)
    app.state.mcp_server = build_mcp_server(settings, app.state.access_verifier, app.state.weather)
    mcp_endpoint = mcp_asgi_app(app.state.mcp_server, settings, app.state.access_verifier)
    for mcp_path in ("/mcp", "/mcp/"):
        app.router.routes.append(
            Route(mcp_path, endpoint=mcp_endpoint, methods=["GET", "POST", "DELETE"], name="mcp")
        )

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.middleware("http")
    async def identify_user(request: Request, call_next):
        request.state.user = None
        request.state.conn = None
        path = request.url.path
        if path.startswith(("/static", "/mcp")) or path in ("/healthz", "/sw.js"):
            return await call_next(request)
        try:
            conn = get_conn(request)
            try:
                request.state.user = resolve_user(request, conn)
            except HTTPException as exc:
                # Raised inside middleware, so FastAPI's handlers don't see it.
                return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
            response = await call_next(request)
        finally:
            conn = getattr(request.state, "conn", None)
            if conn is not None:
                conn.close()
        return response

    @app.exception_handler(RedirectToLogin)
    async def _redirect_to_login(request: Request, exc: RedirectToLogin):
        return redirect_to_login(exc.next_path)

    shell_version = app_version()

    @app.get("/sw.js", include_in_schema=False)
    def service_worker() -> Response:
        source = (STATIC_DIR / "sw.js").read_text().replace("__VERSION__", shell_version)
        return Response(
            source,
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
        )

    @app.get("/offline", include_in_schema=False)
    def offline(request: Request):
        return render(request.app.state.templates, request, "offline.html")

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        try:
            conn = db.connect(settings.db_path)
            try:
                conn.execute("SELECT 1 FROM users LIMIT 1").fetchall()
                pending = db.pending_migrations(conn)
            finally:
                conn.close()
        except sqlite3.Error as exc:
            return JSONResponse({"status": "error", "detail": str(exc)}, status_code=503)
        if pending:
            return JSONResponse(
                {"status": "error", "detail": "pending migrations"}, status_code=503
            )
        return JSONResponse({"status": "ok"})

    @app.get("/login")
    def login_form(request: Request, next: str = "/"):
        # Behind Access everyone arrives already identified, so the form stays
        # reachable for switching to the admin account. Only an admin is bounced.
        current = request.state.user
        if current is not None and current.is_admin:
            return RedirectResponse(next if next.startswith("/") else "/", status_code=303)
        return render(
            request.app.state.templates,
            request,
            "login.html",
            admin_enabled=app.state.admin_credentials.enabled,
            next_path=next if next.startswith("/") else "/",
        )

    @app.post("/login")
    def login_submit(
        request: Request,
        username: str = Form(""),
        password: str = Form(""),
        next: str = Form("/"),
    ):
        creds: AdminCredentials = app.state.admin_credentials
        conn = get_conn(request)
        if creds.enabled and creds.check(username.strip(), password):
            admin = users.get_by_username(conn, username.strip())
            if admin is None and creds.username:
                admin = users.ensure_admin(conn, creds.username)
            assert admin is not None
            target = next if next.startswith("/") else "/"
            response = RedirectResponse(target, status_code=303)
            response.set_cookie(
                SESSION_COOKIE,
                app.state.sessions.encode(admin.id),
                max_age=SESSION_MAX_AGE,
                httponly=True,
                samesite="lax",
                secure=not settings.dev_mode,
            )
            return response
        request.state.flash = {"kind": "error", "message": "Wrong username or password."}
        response = render(
            request.app.state.templates,
            request,
            "login.html",
            admin_enabled=creds.enabled,
            next_path=next if next.startswith("/") else "/",
        )
        response.status_code = 401
        return response

    @app.post("/logout")
    def logout() -> Response:
        # Clearing the admin cookie drops back to the Access identity, if any.
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(SESSION_COOKIE)
        return response

    app.include_router(today_router.router)
    app.include_router(outfits_router.router)
    app.include_router(history_router.router)
    app.include_router(backup_router.router)
    app.include_router(settings_router.router)
    app.include_router(wear_router.router)
    return app
