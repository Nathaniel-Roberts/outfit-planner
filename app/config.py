"""Settings loaded from environment variables.

Every deployable value is an environment variable so the same image runs
anywhere. See .env.example for the full list.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_float(value: str | None, default: float) -> float:
    try:
        return float(value) if value not in (None, "") else default
    except ValueError:
        return default


def _load_or_create_secret(path: Path) -> str:
    if path.exists():
        secret = path.read_text().strip()
        if secret:
            return secret
    secret = secrets.token_urlsafe(48)
    path.write_text(secret)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return secret


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    tz: str
    dev_mode: bool
    dev_user_email: str
    cf_access_team_domain: str | None
    cf_access_aud: str | None
    weather_lat: float
    weather_lon: float
    admin_username: str | None
    admin_password: str | None
    admin_password_hash: str | None
    session_secret: str
    mcp_user_email: str | None
    mcp_bearer_token: str | None
    public_base_url: str | None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "outfits.db"

    @property
    def photos_dir(self) -> Path:
        return self.data_dir / "photos"

    @property
    def access_enabled(self) -> bool:
        return bool(self.cf_access_team_domain and self.cf_access_aud)

    @property
    def access_issuer(self) -> str | None:
        if not self.cf_access_team_domain:
            return None
        return f"https://{self.cf_access_team_domain}"

    @property
    def access_certs_url(self) -> str | None:
        if not self.cf_access_team_domain:
            return None
        return f"https://{self.cf_access_team_domain}/cdn-cgi/access/certs"

    @property
    def admin_login_enabled(self) -> bool:
        return bool(self.admin_username and (self.admin_password or self.admin_password_hash))


def _normalise_team_domain(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip().removeprefix("https://").removeprefix("http://").rstrip("/")
    if not value:
        return None
    if "." not in value:
        value = f"{value}.cloudflareaccess.com"
    return value


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    data_dir = Path(env.get("DATA_DIR") or "/data").expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "photos").mkdir(exist_ok=True)

    session_secret = env.get("SESSION_SECRET") or _load_or_create_secret(
        data_dir / "session_secret"
    )
    public_base_url = (env.get("PUBLIC_BASE_URL") or "").rstrip("/") or None

    return Settings(
        data_dir=data_dir,
        tz=env.get("TZ") or "Australia/Sydney",
        dev_mode=_as_bool(env.get("DEV_MODE")),
        dev_user_email=env.get("DEV_USER_EMAIL") or "dev@localhost",
        cf_access_team_domain=_normalise_team_domain(env.get("CF_ACCESS_TEAM_DOMAIN")),
        cf_access_aud=(env.get("CF_ACCESS_AUD") or "").strip() or None,
        weather_lat=_as_float(env.get("WEATHER_LAT"), -33.43),
        weather_lon=_as_float(env.get("WEATHER_LON"), 151.34),
        admin_username=(env.get("ADMIN_USERNAME") or "").strip() or None,
        admin_password=env.get("ADMIN_PASSWORD") or None,
        admin_password_hash=(env.get("ADMIN_PASSWORD_HASH") or "").strip() or None,
        session_secret=session_secret,
        mcp_user_email=(env.get("MCP_USER_EMAIL") or "").strip().lower() or None,
        mcp_bearer_token=(env.get("MCP_BEARER_TOKEN") or "").strip() or None,
        public_base_url=public_base_url,
    )
