# Deploying Outfit Planner

This walks through running the app on a home server with Docker, exposing it through a
Cloudflare Tunnel, putting Cloudflare Access in front of it, connecting Claude to the
MCP endpoint, and backing up.

The app has no login of its own for everyday users. It trusts Cloudflare Access to
identify people and verifies the Access JWT on every request. **Never expose the app to
the internet without Cloudflare Access, or an equivalent authenticating proxy, in front
of it.** With the compose file below the container publishes no ports, so the tunnel is
the only way in.

## 1. Run it with docker compose

Works with plain Docker, Docker Compose, Portainer and Dockge. The compose file is
standard. There are two ways to get the image:

- **Pull the prebuilt image** (the default in `docker-compose.yml`, and the right choice for
  Dockge on Home Assistant, where there is no shell to clone into). Every push to `main`
  and every `v*` tag builds `ghcr.io/nathaniel-roberts/outfit-planner` for amd64 and arm64
  via `.github/workflows/docker.yml`; releases are listed at
  https://github.com/Nathaniel-Roberts/outfit-planner/releases. Dockge only needs the
  compose text and the `.env` values, nothing cloned. The package is public, so no
  `docker login` is needed.
- **Build from source** on the server: replace the `image:` line with `build: .` and clone
  the repo as below.

1. Create a folder for the stack, for example `/opt/stacks/outfit-planner`. With the
   prebuilt image you only need `docker-compose.yml` and a `.env` there (in Dockge, paste
   the compose text and the env values into the stack). If building from source, clone
   the whole repo there instead:

   ```sh
   git clone https://github.com/Nathaniel-Roberts/outfit-planner.git /opt/stacks/outfit-planner
   cd /opt/stacks/outfit-planner
   cp .env.example .env
   ```

2. Edit `.env`. For a first local run you only need the admin account:

   ```
   ADMIN_USERNAME=nathaniel
   ADMIN_PASSWORD=choose-a-long-passphrase
   WEATHER_LAT=-33.43
   WEATHER_LON=151.34
   TZ=Australia/Sydney
   ```

   Leave `CF_ACCESS_TEAM_DOMAIN` and `CF_ACCESS_AUD` empty until step 3. The app logs a
   warning at startup while Access is not configured.

3. Create the data folder. Everything persistent (SQLite database and photos) lives in
   one bind-mounted folder, `./data` on the host, `/data` in the container:

   ```sh
   mkdir -p data
   ```

4. Build and start. In Dockge, paste the compose file into a new stack named
   `outfit-planner`, add the `.env` contents in the environment panel, and press Deploy.
   From a shell:

   ```sh
   docker compose up -d --build
   docker compose logs -f app
   ```

   The first build takes a few minutes. The image is `python:3.13-slim` plus a virtualenv,
   around 150 MB, and builds on amd64 and arm64.

5. Check health. From the host:

   ```sh
   docker compose exec app python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/healthz').read())"
   ```

   You should see `{"status": "ok"}`.

If you want to reach it on the LAN before the tunnel is up, uncomment the `ports` block in
`docker-compose.yml` (it binds to `127.0.0.1` only; change that deliberately if you need
another machine on the LAN to reach it, and remove it again once the tunnel works). Sign
in at `/login` with the admin account.

## 2. Cloudflare Tunnel

The compose file includes `cloudflared` as a sidecar. It needs a tunnel token.

1. In the Cloudflare dashboard go to **Zero Trust > Networks > Tunnels > Create a tunnel**.
   Choose **Cloudflared**, name it `outfit-planner`, and save.
2. On the install page, copy the token from the `cloudflared service install <TOKEN>`
   command. Add it to `.env`:

   ```
   TUNNEL_TOKEN=eyJ...
   ```

3. Under **Public Hostname**, add one:
   - Subdomain: `outfits` (or whatever you like), Domain: your zone.
   - Service type: `HTTP`, URL: `app:8000`

   The hostname `app` resolves inside the compose network because both containers share it.

4. Restart the stack (`docker compose up -d`). The tunnel shows as Healthy in the
   dashboard. Opening `https://outfits.yourdomain.com` should show the admin login page,
   because Access is not in front yet. Do step 3 straight away.

## 3. Cloudflare Access

Two policies: one for people using the web UI (Google sign-in), one for the `/mcp` path
that lets a service token through.

### 3a. The web UI application

1. **Zero Trust > Access > Applications > Add an application > Self-hosted.**
2. Name: `Outfit Planner`. Session duration: 1 month is fine for a household app
   (the app installs as a PWA and a short session would mean frequent re-logins).
3. Application domain: `outfits.yourdomain.com`, path left empty.
4. Identity providers: tick **Google** (already configured on your team).
5. Policy `Household`, action **Allow**, include **Emails** and list the addresses
   (yours and Lauren's). Save.

### 3b. The MCP path

Still in the same application, add a second policy so service tokens can reach `/mcp`:

1. **Zero Trust > Access > Service Auth > Service Tokens > Create Service Token.**
   Name it `claude-mcp`. Copy the **Client ID** and **Client Secret** now; the secret is
   shown once.
2. Back in the Outfit Planner application, add a policy `MCP service token`, action
   **Service Auth**, include **Service Token** and pick `claude-mcp`.

   Service Auth policies apply to the whole application, which is fine here: the app
   itself refuses a service-token JWT on anything except `/mcp` (service tokens have no
   email, so the web UI treats them as anonymous), and on `/mcp` it maps them to the
   wardrobe named by `MCP_USER_EMAIL`.

   If you prefer to scope the policy to the path, create a second Access application with
   domain `outfits.yourdomain.com` and path `mcp`, give it only the Service Auth policy,
   and give it the same AUD handling as below (each application has its own AUD tag; set
   `CF_ACCESS_AUD` to a comma separated list is **not** supported, so keep one application
   unless you also want to run a second container).

## 4. Tell the app about Access

1. Open the application in **Access > Applications**, click **Configure**, and copy the
   **Application Audience (AUD) Tag** from the Overview tab.
2. Your team domain is at **Zero Trust > Settings > Custom Pages**, shown as
   `<team>.cloudflareaccess.com`.
3. Put both in `.env`:

   ```
   CF_ACCESS_TEAM_DOMAIN=<team>.cloudflareaccess.com
   CF_ACCESS_AUD=<64 hex characters>
   MCP_USER_EMAIL=lauren@example.com
   ```

   `MCP_USER_EMAIL` is whose wardrobe the service token acts on. Lauren must have signed in
   to the web app once before the MCP endpoint will accept a service token.

4. `docker compose up -d`. From now on every request must carry a valid Access JWT; the
   startup warning disappears. Opening the hostname sends you to Google first.

The app fetches Cloudflare's signing keys from
`https://<team>.cloudflareaccess.com/cdn-cgi/access/certs`, caches them for an hour and
keeps a copy in `/data/access_certs.json` so a restart during a Cloudflare outage still
has keys.

### Admin sign-in

The admin account is separate from Access. After Access is on, you sign in with Google
like everyone else and then visit `/login` to switch to the admin account when you want
to back up, restore, or look at Lauren's wardrobe (Settings > Viewing). The admin session
is a cookie that lasts 30 days.

### Installing on the phone

Open the hostname in Safari (iPhone) or Chrome (Android), sign in with Google, then
Share > **Add to Home Screen**. The app opens full screen, and the library keeps working
offline from what has already been viewed. Uploading needs a connection.

## 5. Connect Claude to the MCP endpoint

The MCP server is at `https://outfits.yourdomain.com/mcp` (Streamable HTTP). Tools:
`list_outfits`, `get_outfit`, `suggest_outfits`, `create_outfit`, `update_outfit`,
`log_wear`, `get_forecast`, `list_tags`, `list_colour_rules`, `get_outfit_photo`.
Photos are also exposed as resources at `outfit://<id>/photo/<n>`.

### Claude Code (sends custom headers)

```sh
claude mcp add --transport http outfits https://outfits.yourdomain.com/mcp \
  --header "CF-Access-Client-Id: <client id>.access" \
  --header "CF-Access-Client-Secret: <client secret>"
```

Or in `.mcp.json` / `~/.claude.json`:

```json
{
  "mcpServers": {
    "outfits": {
      "type": "http",
      "url": "https://outfits.yourdomain.com/mcp",
      "headers": {
        "CF-Access-Client-Id": "<client id>.access",
        "CF-Access-Client-Secret": "<client secret>"
      }
    }
  }
}
```

Cloudflare checks the two headers at the edge, swaps them for an Access JWT, and the app
verifies that JWT the same way it does for the web UI.

### Claude desktop and mobile apps (custom connectors)

The Claude apps add remote MCP servers under **Settings > Connectors > Add custom
connector** with a URL and, optionally, OAuth. They do not let you type arbitrary headers.
Use the bearer token fallback:

1. Generate a token and add it to `.env`:

   ```sh
   openssl rand -hex 32
   ```

   ```
   MCP_BEARER_TOKEN=<the 64 hex characters>
   ```

2. The connector still has to get through Access. Add a **Bypass** policy to the Access
   application for the `/mcp` path only. The cleanest way is a second Access application:
   domain `outfits.yourdomain.com`, path `mcp`, one policy with action **Bypass** and
   include **Everyone**. Access evaluates the more specific path first, so the web UI
   stays behind Google while `/mcp` is passed through to the app, which then insists on
   the bearer token (or a valid Access JWT) on every request and returns 401 otherwise.

3. In the Claude app, add the connector with URL `https://outfits.yourdomain.com/mcp` and,
   where the client offers an "Authorization" or "Bearer token" field, paste the token.
   If a client offers no such field at all, it cannot use this endpoint; use Claude Code
   or the Access service token route instead.

Rotate the token by changing `.env` and restarting; the old one stops working immediately.

### Local development

With `DEV_MODE=true` the endpoint accepts unauthenticated requests as the dev user:

```sh
DATA_DIR=./data DEV_MODE=true uv run uvicorn app.asgi:app --reload
claude mcp add --transport http outfits-dev http://127.0.0.1:8000/mcp
```

## 6. Backup and restore

A backup is one zip holding a consistent snapshot of the SQLite database and every
photo (`manifest.json`, `outfits.db`, `photos/...`).

**From the web UI (admin only).** Settings > Backup > **Download backup**. Restore is on
the same card: choose the zip, type `REPLACE`, and submit. The current data is moved to
`/data/restore-backup-<timestamp>/` rather than deleted, so a mistaken restore can be
undone by moving it back.

**From the command line on the server.**

```sh
# export
docker compose exec app python -m app.backup export /data/backup.zip
docker compose cp app:/data/backup.zip ./outfit-planner-backup-$(date +%F).zip

# restore (replaces everything in /data, after a yes/no prompt; --yes skips it)
docker compose cp ./outfit-planner-backup-2026-10-01.zip app:/data/restore.zip
docker compose exec app python -m app.backup import /data/restore.zip
docker compose restart app
```

**Scheduled.** A nightly cron on the host is enough:

```
15 2 * * * cd /opt/stacks/outfit-planner && docker compose exec -T app python -m app.backup export /data/nightly.zip && cp data/nightly.zip /mnt/backups/outfit-planner-$(date +\%F).zip
```

Because everything is in `./data`, backing up that folder with whatever already backs up
the server (restic, rsync, a NAS job) also works. Stop the container first, or use the
export command, to avoid copying a half-written database.

## Upgrading

Built from source:

```sh
cd /opt/stacks/outfit-planner
git pull
docker compose up -d --build
```

Prebuilt image: `docker compose pull && docker compose up -d` (in Dockge, the Update
button does the same).

Database migrations run automatically at startup. Take a backup first.

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATA_DIR` | `/data` | SQLite database and photos |
| `TZ` | `Australia/Sydney` | Timezone for "today" and the forecast |
| `WEATHER_LAT`, `WEATHER_LON` | `-33.43`, `151.34` | Forecast location (Gosford) |
| `CF_ACCESS_TEAM_DOMAIN` | | `<team>.cloudflareaccess.com` |
| `CF_ACCESS_AUD` | | Access application AUD tag |
| `ADMIN_USERNAME` | | Local admin account name |
| `ADMIN_PASSWORD` or `ADMIN_PASSWORD_HASH` | | Admin password (plain, or an argon2 hash) |
| `SESSION_SECRET` | generated | Signs the admin session cookie; stored in `DATA_DIR` if unset |
| `MCP_USER_EMAIL` | | Whose wardrobe service tokens and the bearer token act on |
| `MCP_BEARER_TOKEN` | | Fallback auth for MCP clients that cannot send Access headers |
| `PUBLIC_BASE_URL` | | Used to build absolute photo URLs in MCP responses |
| `DEV_MODE` | `false` | Bypass Access and sign in as a dev admin. Local use only |

To produce an argon2 hash instead of keeping the plain password in `.env`:

```sh
docker compose exec app python -c "from app.auth import hash_password; print(hash_password('your passphrase'))"
```
