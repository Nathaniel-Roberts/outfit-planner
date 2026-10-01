"""Who is making this request?

Order of resolution:

1. A signed session cookie from the local admin login.
2. A Cloudflare Access JWT in ``Cf-Access-Jwt-Assertion`` (or the ``CF_Authorization``
   cookie), verified against the team's JWKS and the configured audience.
3. ``DEV_MODE``: a fixed dev admin user, for local development only.
4. Otherwise anonymous: the web UI redirects to the admin login page.

The app must never be exposed without Access (or an equivalent) in front of it.
Without Access, the only thing standing between the internet and the wardrobe is
the admin password.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer
from jwt import PyJWK

from app import users
from app.config import Settings
from app.users import User

log = logging.getLogger(__name__)

SESSION_COOKIE = "op_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 days
ACCESS_HEADER = "Cf-Access-Jwt-Assertion"
ACCESS_EMAIL_HEADER = "Cf-Access-Authenticated-User-Email"
ACCESS_COOKIE = "CF_Authorization"


class AccessError(Exception):
    """The Access JWT was missing, malformed, or failed verification."""


@dataclass(frozen=True)
class AccessIdentity:
    """What a verified Access JWT tells us."""

    email: str | None
    common_name: str | None  # set for service tokens
    claims: dict[str, Any]

    @property
    def is_service_token(self) -> bool:
        return self.email is None and self.common_name is not None


JWKSFetcher = Callable[[str], dict[str, Any]]


def _default_fetcher(url: str) -> dict[str, Any]:
    response = httpx.get(url, timeout=10.0)
    response.raise_for_status()
    return response.json()


class AccessVerifier:
    """Verifies Cloudflare Access JWTs.

    Keys are fetched from ``<team>/cdn-cgi/access/certs``, cached in memory for an hour
    and mirrored to a file in ``DATA_DIR`` so a restart while Cloudflare is unreachable
    still has a key set to work with.
    """

    def __init__(
        self,
        settings: Settings,
        fetcher: JWKSFetcher | None = None,
        cache_ttl: float = 3600.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self.fetcher = fetcher or _default_fetcher
        self.cache_ttl = cache_ttl
        self.clock = clock
        self._keys: dict[str, PyJWK] = {}
        self._fetched_at: float = 0.0
        self._lock = threading.Lock()
        self._cache_file: Path | None = settings.data_dir / "access_certs.json"

    def _load_keys(self, payload: dict[str, Any]) -> dict[str, PyJWK]:
        keys: dict[str, PyJWK] = {}
        for jwk in payload.get("keys", []):
            try:
                key = PyJWK.from_dict(jwk)
            except Exception as exc:  # noqa: BLE001 - skip malformed keys, keep going
                log.warning("Skipping unusable JWK: %s", exc)
                continue
            if key.key_id:
                keys[key.key_id] = key
        return keys

    def refresh(self, force: bool = False) -> None:
        with self._lock:
            fresh = self.clock() - self._fetched_at < self.cache_ttl
            if self._keys and fresh and not force:
                return
            url = self.settings.access_certs_url
            if not url:
                raise AccessError("CF_ACCESS_TEAM_DOMAIN is not configured")
            try:
                payload = self.fetcher(url)
            except Exception as exc:  # noqa: BLE001 - fall back to cache below
                log.warning("Could not fetch Access certs from %s: %s", url, exc)
                if not self._keys and self._cache_file and self._cache_file.exists():
                    payload = json.loads(self._cache_file.read_text())
                elif self._keys:
                    return  # keep serving with stale keys
                else:
                    raise AccessError("Access certs unavailable") from exc
            else:
                if self._cache_file:
                    try:
                        self._cache_file.write_text(json.dumps(payload))
                    except OSError as exc:
                        log.warning("Could not cache Access certs: %s", exc)
            keys = self._load_keys(payload)
            if keys:
                self._keys = keys
                self._fetched_at = self.clock()

    def _key_for(self, kid: str | None) -> PyJWK | None:
        if kid is None:
            return None
        self.refresh()
        key = self._keys.get(kid)
        if key is None:
            # Unknown kid: Cloudflare may have rotated. Refresh once and retry.
            self.refresh(force=True)
            key = self._keys.get(kid)
        return key

    def verify(self, token: str) -> AccessIdentity:
        if not self.settings.access_enabled:
            raise AccessError("Cloudflare Access is not configured")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AccessError(f"Malformed Access token: {exc}") from exc
        key = self._key_for(header.get("kid"))
        if key is None:
            raise AccessError("Access token signed with an unknown key")
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                audience=self.settings.cf_access_aud,
                issuer=self.settings.access_issuer,
                options={"require": ["exp", "iat", "aud", "iss"]},
                leeway=30,
            )
        except jwt.PyJWTError as exc:
            raise AccessError(f"Access token rejected: {exc}") from exc
        email = claims.get("email")
        common_name = claims.get("common_name")
        if not email and not common_name:
            raise AccessError("Access token has neither email nor common_name")
        return AccessIdentity(
            email=email.lower() if isinstance(email, str) else None,
            common_name=common_name if isinstance(common_name, str) else None,
            claims=claims,
        )


# --- Local admin login -------------------------------------------------------------

_hasher = PasswordHasher()


class AdminCredentials:
    def __init__(self, settings: Settings) -> None:
        self.username = settings.admin_username
        self._hash: str | None = settings.admin_password_hash
        if self._hash is None and settings.admin_password:
            self._hash = _hasher.hash(settings.admin_password)

    @property
    def enabled(self) -> bool:
        return bool(self.username and self._hash)

    def check(self, username: str, password: str) -> bool:
        if not self.enabled or username != self.username or not self._hash:
            return False
        try:
            return _hasher.verify(self._hash, password)
        except VerifyMismatchError:
            return False
        except Exception as exc:  # noqa: BLE001 - a bad hash string should not 500
            log.warning("Admin password hash could not be verified: %s", exc)
            return False


def hash_password(password: str) -> str:
    return _hasher.hash(password)


class SessionCodec:
    def __init__(self, secret: str) -> None:
        self._serializer = URLSafeTimedSerializer(secret, salt="outfit-planner-session")

    def encode(self, user_id: int) -> str:
        return self._serializer.dumps({"uid": user_id})

    def decode(self, value: str) -> int | None:
        try:
            data = self._serializer.loads(value, max_age=SESSION_MAX_AGE)
        except BadSignature:
            return None
        uid = data.get("uid") if isinstance(data, dict) else None
        return uid if isinstance(uid, int) else None


# --- Request resolution --------------------------------------------------------------


def _access_token_from(request: Request) -> str | None:
    token = request.headers.get(ACCESS_HEADER)
    if token:
        return token
    return request.cookies.get(ACCESS_COOKIE)


def resolve_user(request: Request, conn: sqlite3.Connection) -> User | None:
    """Work out who this request is from. Returns None for anonymous."""
    state = request.app.state
    settings: Settings = state.settings

    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        uid = state.sessions.decode(cookie)
        if uid is not None:
            user = users.get_by_id(conn, uid)
            if user is not None:
                return user

    token = _access_token_from(request)
    if token and settings.access_enabled:
        try:
            identity = state.access_verifier.verify(token)
        except AccessError as exc:
            log.info("Access token rejected: %s", exc)
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        header_email = request.headers.get(ACCESS_EMAIL_HEADER)
        if identity.email and header_email and header_email.lower() != identity.email:
            raise HTTPException(status_code=401, detail="Access email header mismatch")
        if identity.email:
            return users.upsert_by_email(conn, identity.email)
        # Service tokens carry no email. They are only meaningful on the MCP path.
        return None

    if settings.dev_mode:
        return users.upsert_by_email(conn, settings.dev_user_email, is_admin=True)

    return None


def wants_html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "text/html" in accept or request.headers.get("hx-request") == "true"


def require_user(request: Request) -> User:
    """FastAPI dependency: the signed-in user, or a redirect/401."""
    user = getattr(request.state, "user", None)
    if user is not None:
        return user
    if wants_html(request) and request.method == "GET":
        raise RedirectToLogin(request.url.path)
    raise HTTPException(status_code=401, detail="Not signed in")


def require_admin(request: Request) -> User:
    user = require_user(request)
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    return user


class RedirectToLogin(Exception):
    def __init__(self, next_path: str) -> None:
        self.next_path = next_path


def redirect_to_login(next_path: str) -> RedirectResponse:
    target = "/login"
    if next_path and next_path != "/" and next_path.startswith("/"):
        target += f"?next={next_path}"
    return RedirectResponse(target, status_code=303)
