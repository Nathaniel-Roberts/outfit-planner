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
- `app/routers/` feature routers (outfits, today, library, history, settings, backup).
- `app/templates/`, `app/static/` server-rendered UI.
- `tests/` focused on scoring, Access JWT verification and MCP tool contracts.

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
