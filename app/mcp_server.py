"""MCP server so Claude can work with the wardrobe.

Served from the same process at ``/mcp`` over Streamable HTTP (stateless, JSON
responses). Every tool resolves the acting user from the request headers:

1. ``Authorization: Bearer <MCP_BEARER_TOKEN>`` (fallback for clients that cannot
   send Cloudflare service token headers) acts as the configured MCP user.
2. A Cloudflare Access JWT. A user token (email claim) acts as that user. A
   service token (common_name claim) acts as the configured MCP user.
3. ``DEV_MODE``: the dev user.

The "configured MCP user" is ``MCP_USER_EMAIL``, or the only Access user if there
is exactly one.
"""

from __future__ import annotations

import hmac
import logging
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import Context, Image, MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp
from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app import colour_rules, db, outfits, tags, users, wear
from app import colours as palette
from app.auth import ACCESS_COOKIE, ACCESS_HEADER, AccessError, AccessVerifier
from app.config import Settings
from app.outfits import Outfit, OutfitInput
from app.scoring import rank_outfits
from app.templating import today_in
from app.users import User
from app.weather import DayForecast, WeatherService, manual_forecast

log = logging.getLogger(__name__)

PHOTO_URI = "outfit://{outfit_id}/photo/{index}"


class MCPAuthError(Exception):
    pass


def _bearer_ok(headers: Mapping[str, str], settings: Settings) -> bool:
    if not settings.mcp_bearer_token:
        return False
    auth = headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        return False
    return hmac.compare_digest(auth[7:].strip(), settings.mcp_bearer_token)


def mcp_actor(conn: sqlite3.Connection, settings: Settings) -> User:
    """Whose wardrobe a service token or bearer token acts on."""
    if settings.mcp_user_email:
        user = users.get_by_email(conn, settings.mcp_user_email)
        if user is None:
            raise MCPAuthError(
                f"MCP_USER_EMAIL {settings.mcp_user_email} has not signed in to the web app yet"
            )
        return user
    candidates = users.non_admin_users(conn)
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise MCPAuthError("No Access user has signed in yet, so there is no wardrobe to act on")
    raise MCPAuthError(
        "More than one user exists; set MCP_USER_EMAIL to choose whose wardrobe to use"
    )


def resolve_mcp_user(
    headers: Mapping[str, str],
    settings: Settings,
    verifier: AccessVerifier,
    conn: sqlite3.Connection,
) -> User:
    if _bearer_ok(headers, settings):
        return mcp_actor(conn, settings)
    token = headers.get(ACCESS_HEADER.lower()) or headers.get(ACCESS_HEADER)
    if not token:
        cookie = headers.get("cookie") or ""
        for part in cookie.split(";"):
            name, _, value = part.strip().partition("=")
            if name == ACCESS_COOKIE and value:
                token = value
    if token and settings.access_enabled:
        try:
            identity = verifier.verify(token)
        except AccessError as exc:
            raise MCPAuthError(str(exc)) from exc
        if identity.email:
            return users.upsert_by_email(conn, identity.email)
        return mcp_actor(conn, settings)
    if settings.dev_mode:
        return users.upsert_by_email(conn, settings.dev_user_email, is_admin=True)
    raise MCPAuthError("Not authenticated: send a Cloudflare Access token or the MCP bearer token")


class MCPAuthMiddleware:
    """Rejects unauthenticated requests before they reach the MCP transport."""

    def __init__(self, app: ASGIApp, settings: Settings, verifier: AccessVerifier) -> None:
        self.app = app
        self.settings = settings
        self.verifier = verifier

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        conn = db.connect(self.settings.db_path)
        try:
            resolve_mcp_user(headers, self.settings, self.verifier, conn)
        except MCPAuthError as exc:
            response = JSONResponse({"error": "unauthorized", "detail": str(exc)}, status_code=401)
            await response(scope, receive, send)
            return
        finally:
            conn.close()
        await self.app(scope, receive, send)


# --- Tool implementation ---------------------------------------------------------------


def _photo_resources(outfit: Outfit) -> list[str]:
    return [PHOTO_URI.format(outfit_id=outfit.id, index=i + 1) for i in range(len(outfit.photos))]


def _outfit_dict(outfit: Outfit, base_url: str) -> dict[str, Any]:
    data = outfit.to_dict(base_url)
    data["photo_resources"] = _photo_resources(outfit)
    return data


def _parse_date(value: str | None, tz: str) -> date:
    if not value:
        return today_in(tz)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ToolError(f"Date must be YYYY-MM-DD, got {value!r}") from exc


def _check_colours(keys: list[str] | None) -> list[str]:
    if not keys:
        return []
    bad = [k for k in keys if k.strip().lower() not in palette.BY_KEY]
    if bad:
        raise ToolError(f"Unknown colours {bad}. Palette keys: {', '.join(palette.KEYS)}")
    return palette.clean_keys(keys)


def build_mcp_server(
    settings: Settings, verifier: AccessVerifier, weather: WeatherService
) -> MCPServer:
    base_url = settings.public_base_url or ""

    server = MCPServer(
        name="Outfit Planner",
        instructions=(
            "Lauren's work outfit planner. Outfits are photos of a complete look with activity "
            "tags, a comfortable temperature range, weather flags and colours. Use suggest_outfits "
            "for 'what should I wear today', log_wear when she has picked one, and the "
            "outfit://<id>/photo/<n> resources (or get_outfit_photo) to look at photos. "
            "Temperatures are Celsius, dates are YYYY-MM-DD, timezone is " + settings.tz + "."
        ),
    )

    @contextmanager
    def session(ctx: Context) -> Iterator[tuple[sqlite3.Connection, User]]:
        conn = db.connect(settings.db_path)
        try:
            headers = ctx.headers or {}
            try:
                user = resolve_mcp_user(headers, settings, verifier, conn)
            except MCPAuthError as exc:
                raise ToolError(f"Not authorised: {exc}") from exc
            yield conn, user
        finally:
            conn.close()

    def owned(conn: sqlite3.Connection, user: User, outfit_id: int) -> Outfit:
        outfit = outfits.get_outfit(conn, outfit_id)
        if outfit is None or (outfit.user_id != user.id and not user.is_admin):
            raise ToolError(f"No outfit with id {outfit_id}")
        return outfit

    def conditions(conn: sqlite3.Connection, user: User, day: date) -> DayForecast | None:
        from app.routers.today import weather_override

        override = weather_override(conn, user.id, day)
        if override is not None:
            return override
        return weather.for_day(conn, day)

    # -- Tools --------------------------------------------------------------------------

    @server.tool()
    async def list_outfits(
        ctx: Context,
        activity_tags: list[str] | None = None,
        temperature: float | None = None,
        colours: list[str] | None = None,
        worn_since: str | None = None,
        favourite: bool | None = None,
        archived: bool = False,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        """List outfits, optionally filtered.

        activity_tags match any of the given tag names. temperature keeps outfits whose
        range covers that Celsius value (outfits with no range are kept). colours are
        palette keys (see list_colour_rules for the palette). worn_since is YYYY-MM-DD.
        archived=True lists archived outfits instead of active ones.
        """
        with session(ctx) as (conn, user):
            f = outfits.OutfitFilter(
                tag_ids=[
                    t.id
                    for t in tags.resolve_names(conn, user.id, activity_tags or [], create=False)
                ],
                colours=_check_colours(colours),
                temperature=temperature,
                favourite=bool(favourite),
                archived=archived,
                query=query,
                worn_since=_parse_date(worn_since, settings.tz) if worn_since else None,
            )
            if activity_tags and not f.tag_ids:
                return []
            library = outfits.list_outfits(conn, user.id, only_archived=archived)
            return [_outfit_dict(o, base_url) for o in outfits.filter_outfits(library, f)]

    @server.tool()
    async def get_outfit(ctx: Context, outfit_id: int) -> dict[str, Any]:
        """Full record for one outfit, including photo URLs and resource URIs."""
        with session(ctx) as (conn, user):
            outfit = owned(conn, user, outfit_id)
            data = _outfit_dict(outfit, base_url)
            data["wear_history"] = [
                r["worn_on"]
                for r in conn.execute(
                    "SELECT worn_on FROM wear_log WHERE outfit_id = ? "
                    "ORDER BY worn_on DESC LIMIT 30",
                    (outfit.id,),
                )
            ]
            return data

    @server.tool()
    async def suggest_outfits(
        ctx: Context,
        activity_tags: list[str] | None = None,
        temp_min: float | None = None,
        temp_max: float | None = None,
        rainy: bool | None = None,
        windy: bool | None = None,
        humid: bool | None = None,
        day: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Rank outfits for a day, best first, with a short why line for each.

        Uses the same scoring as the Today screen. Pass temp_min/temp_max (and optionally
        rainy/windy/humid) to override the forecast; otherwise the cached Open-Meteo
        forecast (or her manual override for that day) is used. day is YYYY-MM-DD,
        default today.
        """
        with session(ctx) as (conn, user):
            target = _parse_date(day, settings.tz)
            if temp_min is not None or temp_max is not None:
                lo = temp_min if temp_min is not None else (temp_max or 0) - 8
                hi = temp_max if temp_max is not None else (temp_min or 0) + 8
                forecast: DayForecast | None = manual_forecast(
                    target, lo, hi, bool(rainy), bool(windy), bool(humid)
                )
            else:
                forecast = conditions(conn, user, target)
            selected = tags.resolve_names(conn, user.id, activity_tags or [], create=False)
            unknown = [
                n
                for n in (activity_tags or [])
                if n.lower() not in {t.name.lower() for t in selected}
            ]
            ranked = rank_outfits(
                outfits.list_outfits(conn, user.id),
                {t.id for t in selected},
                forecast,
                target,
                colour_rules.rules_map(conn, user.id),
            )
            return {
                "day": target.isoformat(),
                "forecast": forecast.to_dict() if forecast else None,
                "activity_tags": [t.name for t in selected],
                "unknown_tags": unknown,
                "outfits": [
                    {**_outfit_dict(s.outfit, base_url), "score": round(s.score, 1), "why": s.why}
                    for s in ranked[: max(1, min(limit, 50))]
                ],
            }

    @server.tool()
    async def create_outfit(
        ctx: Context,
        name: str | None = None,
        notes: str | None = None,
        activity_tags: list[str] | None = None,
        temp_min: float | None = None,
        temp_max: float | None = None,
        rain_ok: bool = False,
        windy_ok: bool = False,
        humid_ok: bool = False,
        layers_removable: bool = False,
        colours: list[str] | None = None,
        garments: list[str] | None = None,
        favourite: bool = False,
    ) -> dict[str, Any]:
        """Create an outfit record. Photos are added in the web app. Unknown tags are created."""
        with session(ctx) as (conn, user):
            data = OutfitInput(
                name=name,
                notes=notes,
                temp_min=temp_min,
                temp_max=temp_max,
                rain_ok=rain_ok,
                windy_ok=windy_ok,
                humid_ok=humid_ok,
                layers_removable=layers_removable,
                favourite=favourite,
                tag_ids=[
                    t.id
                    for t in tags.resolve_names(conn, user.id, activity_tags or [], create=True)
                ],
                colours=_check_colours(colours),
                garment_names=list(garments or []),
            )
            return _outfit_dict(outfits.create_outfit(conn, user.id, data), base_url)

    @server.tool()
    async def update_outfit(
        ctx: Context,
        outfit_id: int,
        name: str | None = None,
        notes: str | None = None,
        activity_tags: list[str] | None = None,
        temp_min: float | None = None,
        temp_max: float | None = None,
        clear_temperature: bool = False,
        rain_ok: bool | None = None,
        windy_ok: bool | None = None,
        humid_ok: bool | None = None,
        layers_removable: bool | None = None,
        colours: list[str] | None = None,
        garments: list[str] | None = None,
        favourite: bool | None = None,
        archived: bool | None = None,
    ) -> dict[str, Any]:
        """Update fields on an outfit. Anything left out is unchanged.

        activity_tags, colours and garments replace the whole list when given. Pass
        clear_temperature=True to remove the temperature range.
        """
        with session(ctx) as (conn, user):
            outfit = owned(conn, user, outfit_id)
            data = outfits.input_from(outfit)
            if name is not None:
                data.name = name
            if notes is not None:
                data.notes = notes
            if clear_temperature:
                data.temp_min = data.temp_max = None
            if temp_min is not None:
                data.temp_min = temp_min
            if temp_max is not None:
                data.temp_max = temp_max
            for field_name, value in (
                ("rain_ok", rain_ok),
                ("windy_ok", windy_ok),
                ("humid_ok", humid_ok),
                ("layers_removable", layers_removable),
                ("favourite", favourite),
                ("archived", archived),
            ):
                if value is not None:
                    setattr(data, field_name, value)
            if activity_tags is not None:
                data.tag_ids = [
                    t.id
                    for t in tags.resolve_names(conn, outfit.user_id, activity_tags, create=True)
                ]
            if colours is not None:
                data.colours = _check_colours(colours)
            if garments is not None:
                data.garment_names = list(garments)
            return _outfit_dict(outfits.update_outfit(conn, outfit, data), base_url)

    @server.tool()
    async def log_wear(ctx: Context, outfit_id: int, day: str | None = None) -> dict[str, Any]:
        """Record that an outfit was worn on a day (YYYY-MM-DD, default today)."""
        with session(ctx) as (conn, user):
            outfit = owned(conn, user, outfit_id)
            target = _parse_date(day, settings.tz)
            entry = wear.log_wear(conn, outfit.id, outfit.user_id, target)
            return {
                "entry_id": entry.id,
                "outfit_id": outfit.id,
                "outfit": outfit.display_name,
                "worn_on": target.isoformat(),
            }

    @server.tool()
    async def get_forecast(ctx: Context) -> dict[str, Any]:
        """The cached Open-Meteo forecast for the configured location (up to three days)."""
        with session(ctx) as (conn, user):
            days = weather.forecast(conn)
            today = today_in(settings.tz)
            from app.routers.today import weather_override

            override = weather_override(conn, user.id, today)
            return {
                "location": {"latitude": settings.weather_lat, "longitude": settings.weather_lon},
                "timezone": settings.tz,
                "today": today.isoformat(),
                "manual_override_today": override.to_dict() if override else None,
                "days": [d.to_dict() for d in days],
                "available": bool(days),
            }

    @server.tool()
    async def list_tags(ctx: Context) -> list[dict[str, Any]]:
        """Her activity tags (name and description)."""
        with session(ctx) as (conn, user):
            return [
                {"id": t.id, "name": t.name, "description": t.description}
                for t in tags.list_tags(conn, user.id)
            ]

    @server.tool()
    async def list_colour_rules(ctx: Context) -> dict[str, Any]:
        """Her colour pairing rules (good / avoid) and the fixed colour palette."""
        with session(ctx) as (conn, user):
            return {
                "palette": [
                    {"key": s.key, "label": s.label, "hex": s.hex} for s in palette.PALETTE
                ],
                "rules": [
                    {
                        "id": r.id,
                        "colour_a": r.colour_a,
                        "colour_b": r.colour_b,
                        "verdict": r.verdict,
                    }
                    for r in colour_rules.list_rules(conn, user.id)
                ],
            }

    def photo_path(conn: sqlite3.Connection, user: User, outfit_id: int, index: int) -> Path:
        outfit = owned(conn, user, outfit_id)
        if index < 1 or index > len(outfit.photos):
            raise ToolError(
                f"Outfit {outfit_id} has {len(outfit.photos)} photo(s); index is 1-based"
            )
        photo = outfit.photos[index - 1]
        from app import images

        return outfits.photos_dir_for(settings.photos_dir, outfit.id) / images.web_name(
            photo.filename
        )

    @server.tool()
    async def get_outfit_photo(ctx: Context, outfit_id: int, index: int = 1) -> Image:
        """Return a photo of the outfit (web size) so you can look at it. index is 1-based."""
        with session(ctx) as (conn, user):
            return Image(path=photo_path(conn, user, outfit_id, index))

    # -- Resources ----------------------------------------------------------------------

    @server.resource(
        PHOTO_URI, mime_type="image/jpeg", description="A photo of an outfit, 1-based index"
    )
    async def outfit_photo(outfit_id: int, index: int, ctx: Context) -> bytes:
        try:
            with session(ctx) as (conn, user):
                return photo_path(conn, user, outfit_id, index).read_bytes()
        except ToolError as exc:
            raise ResourceError(str(exc)) from exc

    return server


def mcp_asgi_app(server: MCPServer, settings: Settings, verifier: AccessVerifier) -> ASGIApp:
    """The ASGI endpoint to serve at /mcp, wrapped in our auth check.

    Building the SDK's Starlette app creates the session manager; we then route to the
    transport directly so /mcp works without a trailing-slash redirect. DNS rebinding
    protection is off because the app only ever sits behind Cloudflare Access, which
    already pins the hostname.
    """
    server.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    inner = StreamableHTTPASGIApp(server.session_manager)
    return MCPAuthMiddleware(inner, settings, verifier)
