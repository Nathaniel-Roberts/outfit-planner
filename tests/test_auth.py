from __future__ import annotations

import pytest

from app.auth import ACCESS_EMAIL_HEADER, ACCESS_HEADER, AccessError, AccessVerifier
from tests.conftest import AUD, jwk_from, make_token


def verifier_for(settings, rsa_key, kid="kid-1"):
    v = AccessVerifier(settings, fetcher=lambda url: {"keys": [jwk_from(rsa_key, kid)]})
    return v


def test_valid_token_yields_email(settings, rsa_key):
    v = verifier_for(settings, rsa_key)
    identity = v.verify(make_token(rsa_key, email="Lauren@Example.com"))
    assert identity.email == "lauren@example.com"
    assert not identity.is_service_token


def test_service_token_has_common_name(settings, rsa_key):
    v = verifier_for(settings, rsa_key)
    identity = v.verify(make_token(rsa_key, email=None, extra={"common_name": "abc.access"}))
    assert identity.is_service_token
    assert identity.common_name == "abc.access"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"aud": "b" * 64},
        {"iss": "https://other.cloudflareaccess.com"},
        {"exp_delta": -600},
    ],
)
def test_bad_claims_rejected(settings, rsa_key, kwargs):
    v = verifier_for(settings, rsa_key)
    with pytest.raises(AccessError):
        v.verify(make_token(rsa_key, **kwargs))


def test_unknown_kid_rejected_after_refresh(settings, rsa_key):
    calls = []

    def fetcher(url):
        calls.append(url)
        return {"keys": [jwk_from(rsa_key, "kid-1")]}

    v = AccessVerifier(settings, fetcher=fetcher)
    with pytest.raises(AccessError):
        v.verify(make_token(rsa_key, kid="kid-rotated"))
    assert len(calls) == 2  # initial fetch plus one forced refresh


def test_wrong_key_rejected(settings, rsa_key):
    from cryptography.hazmat.primitives.asymmetric import rsa

    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    v = verifier_for(settings, rsa_key)
    with pytest.raises(AccessError):
        v.verify(make_token(other))


def test_garbage_token_rejected(settings, rsa_key):
    v = verifier_for(settings, rsa_key)
    with pytest.raises(AccessError):
        v.verify("not.a.jwt")


def test_certs_fetch_failure_uses_file_cache(settings, rsa_key):
    good = AccessVerifier(settings, fetcher=lambda url: {"keys": [jwk_from(rsa_key)]})
    good.refresh()  # writes DATA_DIR/access_certs.json

    def failing(url):
        raise RuntimeError("cloudflare down")

    cold = AccessVerifier(settings, fetcher=failing)
    identity = cold.verify(make_token(rsa_key))
    assert identity.email == "lauren@example.com"


def test_access_disabled_rejects(tmp_path, rsa_key):
    from tests.conftest import settings_for

    s = settings_for(tmp_path, CF_ACCESS_TEAM_DOMAIN="", CF_ACCESS_AUD="")
    v = AccessVerifier(s, fetcher=lambda url: {"keys": []})
    with pytest.raises(AccessError):
        v.verify(make_token(rsa_key))


# --- Through the app -----------------------------------------------------------------


def test_anonymous_html_redirects_to_login(client):
    r = client.get("/", headers={"accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_access_header_signs_in(client, rsa_key):
    token = make_token(rsa_key, email="lauren@example.com")
    r = client.get(
        "/",
        headers={
            ACCESS_HEADER: token,
            ACCESS_EMAIL_HEADER: "lauren@example.com",
            "accept": "text/html",
        },
    )
    assert r.status_code == 200
    assert "Lauren" in r.text


def test_access_header_email_mismatch_is_401(client, rsa_key):
    token = make_token(rsa_key, email="lauren@example.com")
    r = client.get("/", headers={ACCESS_HEADER: token, ACCESS_EMAIL_HEADER: "someone@else.com"})
    assert r.status_code == 401


def test_bad_access_token_is_401(client):
    r = client.get("/", headers={ACCESS_HEADER: "garbage"})
    assert r.status_code == 401


def test_admin_login_and_logout(client):
    r = client.post(
        "/login",
        data={"username": "nathaniel", "password": "correct horse", "next": "/"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "op_session" in r.cookies
    r = client.get("/settings", headers={"accept": "text/html"})
    assert r.status_code == 200
    assert "admin" in r.text
    r = client.post("/logout", follow_redirects=False)
    assert r.status_code == 303
    r = client.get("/", headers={"accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303  # anonymous again: off to /login


def test_access_user_can_switch_to_admin(client, rsa_key):
    from tests.test_outfits import lauren_headers

    h = lauren_headers(rsa_key)
    # Behind Access the login form must stay reachable for a signed-in non-admin.
    r = client.get("/login", headers=h, follow_redirects=False)
    assert r.status_code == 200
    assert "switch to the admin account" in r.text
    r = client.post(
        "/login",
        data={"username": "nathaniel", "password": "correct horse", "next": "/settings"},
        headers=h,
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == "/settings"
    # The admin cookie wins over the Access identity on later requests.
    r = client.get("/settings", headers=h)
    assert "admin" in r.text and "Leave admin" in r.text
    # An admin visiting /login is bounced home.
    assert client.get("/login", headers=h, follow_redirects=False).status_code == 303
    # Leaving admin returns to the Access identity, not an anonymous state.
    r = client.post("/logout", headers=h, follow_redirects=False)
    assert r.headers["location"] == "/"
    r = client.get("/settings", headers=h)
    assert "Lauren" in r.text and "Switch to admin" in r.text


def test_admin_login_wrong_password(client):
    r = client.post("/login", data={"username": "nathaniel", "password": "nope"})
    assert r.status_code == 401
    assert "op_session" not in r.cookies


def test_dev_mode_signs_in_without_access(dev_client):
    r = dev_client.get("/settings", headers={"accept": "text/html"})
    assert r.status_code == 200
    assert "admin" in r.text


def test_aud_mismatch_through_app_is_401(client, rsa_key):
    token = make_token(rsa_key, aud="b" * 64)
    r = client.get("/", headers={ACCESS_HEADER: token})
    assert r.status_code == 401
    assert AUD not in r.text
