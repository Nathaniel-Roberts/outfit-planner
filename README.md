# Outfit Planner

A small self-hosted web app for deciding work outfits once, photographing them, and
then on any morning filtering down to the outfits that suit that day's activities and
weather.

- Mobile-first, installable as a PWA. Photograph the outfit in the mirror, tag it, save.
- "Today" screen: current forecast, pick today's activity tags, outfits ranked best to worst.
- Library browsing by tag, colour and temperature. Wear history and calendar.
- Colour palette with simple extraction from the photo, plus your own pairing rules.
- Single Docker image, SQLite and photos in one `/data` volume, no cloud storage.
- Sits behind Cloudflare Access. Exposes an MCP server at `/mcp` so Claude can help.

See `docs/deploy.md` for deployment and `CLAUDE.md` for development notes.

## Quick start (local development)

```sh
uv sync
cp .env.example .env     # optional, DEV_MODE below is enough for local use
DATA_DIR=./data DEV_MODE=true uv run uvicorn app.asgi:app --reload
```

Open http://127.0.0.1:8000. `DEV_MODE=true` signs you in as a dev admin and bypasses
Cloudflare Access. Never set it in production.

Run the tests with `uv run pytest`.

## Security note

The app has no login of its own for everyday users. It trusts Cloudflare Access to
identify people and verifies the Access JWT on every request. The only local account is
the admin's username and password. Do not expose the app to the internet without
Cloudflare Access, or an equivalent authenticating proxy, in front of it.
