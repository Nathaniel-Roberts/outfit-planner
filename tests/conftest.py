from __future__ import annotations

import time
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app

TEAM = "example.cloudflareaccess.com"
AUD = "a" * 64


@pytest.fixture
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwk_from(private_key, kid: str = "kid-1") -> dict:
    public = private_key.public_key()
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(public, as_dict=True)
    jwk.update({"kid": kid, "alg": "RS256", "use": "sig"})
    return jwk


def make_token(
    private_key,
    *,
    kid="kid-1",
    email="lauren@example.com",
    aud=AUD,
    iss=f"https://{TEAM}",
    exp_delta=600,
    extra=None,
) -> str:
    now = int(time.time())
    claims = {
        "aud": [aud],
        "iss": iss,
        "iat": now,
        "exp": now + exp_delta,
        "sub": "user-1",
        "type": "app",
    }
    if email is not None:
        claims["email"] = email
    if extra:
        claims.update(extra)
    pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return jwt.encode(claims, pem, algorithm="RS256", headers={"kid": kid})


def settings_for(tmp_path: Path, **overrides):
    env = {
        "DATA_DIR": str(tmp_path / "data"),
        "TZ": "Australia/Sydney",
        "CF_ACCESS_TEAM_DOMAIN": TEAM,
        "CF_ACCESS_AUD": AUD,
        "ADMIN_USERNAME": "nathaniel",
        "ADMIN_PASSWORD": "correct horse",
        "SESSION_SECRET": "test-secret",
        "DEV_MODE": "false",
    }
    env.update({k: str(v) for k, v in overrides.items()})
    return load_settings(env)


@pytest.fixture
def settings(tmp_path):
    return settings_for(tmp_path)


@pytest.fixture
def app(settings, rsa_key):
    application = create_app(settings)
    jwks = {"keys": [jwk_from(rsa_key)]}
    application.state.access_verifier.fetcher = lambda url: jwks
    return application


@pytest.fixture
def client(app):
    with TestClient(app, base_url="https://testserver") as c:
        yield c


@pytest.fixture
def dev_client(tmp_path, rsa_key):
    application = create_app(settings_for(tmp_path, DEV_MODE="true"))
    with TestClient(application, base_url="https://testserver") as c:
        yield c
