# Outfit Planner (project notes for Claude Code)

Self-hosted outfit planner for a small household. One primary user (Lauren) plus a local
admin (Nathaniel). The unit of data is the **outfit** (photos of the whole look plus tags),
not the garment.

## Stack

- Python 3.13, FastAPI, Jinja2 templates with HTMX (vendored in `app/static/vendor/`).
- SQLite via the standard library `sqlite3`. Migrations are numbered SQL files in
  `migrations/`, applied at startup by `app/db.py`.
- Pillow for thumbnails, EXIF stripping and colour extraction. httpx for Open-Meteo and the
  Access JWKS. PyJWT for Access JWT verification. Official `mcp` SDK (v2) for `/mcp`.
- uv for dependencies. pytest for tests. ruff for lint and format.

## Run locally

```sh
uv sync
DATA_DIR=./data DEV_MODE=true uv run uvicorn app.asgi:app --reload
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Docker is not required for development. The image is built from `Dockerfile` on the server.

## Layout

- `app/main.py` app factory (`create_app(settings)`), auth middleware, login routes.
- `app/asgi.py` the `app` object uvicorn serves.
- `app/config.py` settings from environment variables (see `.env.example`).
- `app/auth.py` Cloudflare Access JWT verification, admin login, session cookie, DEV_MODE.
- `app/users.py` user records and seed tags.
- `app/db.py` connection and migration runner.
- `app/templating.py` Jinja2 setup and Australian date filters.
- `app/outfits.py`, `app/tags.py`, `app/wear.py`, `app/colour_rules.py` data access.
- `app/scoring.py` the ranking used by both the Today screen and the MCP `suggest_outfits` tool.
- `app/weather.py` Open-Meteo fetch with a one-hour SQLite cache and stale fallback.
- `app/images.py` upload processing (EXIF stripped, original/web/thumb). `app/colours.py` palette and extraction.
- `app/mcp_server.py` the MCP server (tools, photo resources, auth middleware) served at `/mcp`.
- `app/backup.py` zip export/import, also a CLI: `python -m app.backup export|import`.
- `app/maintenance.py` in-process housekeeping: daily backup into `DATA_DIR/backups`, trash purge.
- `app/routers/` feature routers: today, outfits, history, settings, wear, backup.
- `app/templates/`, `app/static/` server-rendered UI. `static/sw.js` is the service worker.
- `migrations/` numbered SQL files. Add a new file for schema changes; never edit an applied one.
- `tests/` focused on scoring, Access JWT verification, images, backup and MCP tool contracts.
- `docs/deploy.md` deployment: compose, Cloudflare Tunnel, Access, Claude connection, backups.

## Feature notes

- Today serves any date (`?day=today|tomorrow|YYYY-MM-DD`); after 6pm it opens on Tomorrow.
  Per-day tag choices and manual weather live in `user_settings` as JSON maps keyed by date.
- Weekly routine (`user_settings.routine`) pre-selects tags per weekday when nothing was chosen.
- Forecast temperatures are the 7am to 6pm range from Open-Meteo hourly data; the overnight
  minimum is display-only (`DayForecast.night_min`).
- Wear feedback (`wear_log.feedback`: hot/cold/ok/skip) nudges the outfit's range by 1 degree.
- `outfits.unavailable_until` is "in the wash"; `outfits.deleted_at` is the bin (30 days).
- Scoring exposes `fit` (great/good/poor) alongside the score; the UI groups by it.

## Conventions

- Australian English in the UI, Celsius, DD/MM/YYYY. Dates are stored as ISO strings.
- Per-user data: every outfit, tag, rule and wear log row has a `user_id`. Admin can see all.
- Keep it small and boring. No new dependencies without a reason.
- Commit after each phase. Do not bump the version in `pyproject.toml` mid-phase.

## Auth model

1. Session cookie from the admin login (`/login`).
2. `Cf-Access-Jwt-Assertion` header verified against `CF_ACCESS_TEAM_DOMAIN` and
   `CF_ACCESS_AUD`. The email claim identifies the user; a user row is created on first visit.
3. `DEV_MODE=true` signs in as a dev admin. Local development only.

Never expose the app without Cloudflare Access (or equivalent) in front of it.

## MCP server

Streamable HTTP at `/mcp` (stateless, JSON responses), built with the official `mcp` 2.x
SDK (`MCPServer`, formerly FastMCP). Auth order per request: `Authorization: Bearer
$MCP_BEARER_TOKEN`, then a Cloudflare Access JWT (user token acts as that user, service
token acts as `MCP_USER_EMAIL`), then `DEV_MODE`.

Tools: `list_outfits`, `get_outfit`, `suggest_outfits` (same scoring as the UI, with
optional weather override), `create_outfit`, `update_outfit`, `log_wear`, `get_forecast`,
`list_tags`, `list_colour_rules`, `get_outfit_photo` (returns the image). Resource
template `outfit://{outfit_id}/photo/{index}` (1-based) serves web-size JPEGs.

Local use: run with `DEV_MODE=true`, then
`claude mcp add --transport http outfits-dev http://127.0.0.1:8000/mcp`.

## Testing notes

- `tests/test_mcp.py` drives the real `/mcp` endpoint in-process with the SDK client over an
  `httpx2.ASGITransport`. The session manager can only be started once per app instance, so
  each MCP test builds its own app and runs everything inside one `anyio.run`.
- UI checks were done with Playwright against nix-provided Chromium at phone width. The
  service worker was verified offline by stopping the server, not with `setOffline`
  (Playwright's offline mode does not apply to service worker fetches).
- ruff from pip does not run on NixOS; use `nix run nixpkgs#ruff -- check .`.
