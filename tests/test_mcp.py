"""MCP tool contracts, exercised through the real Streamable HTTP endpoint."""

from __future__ import annotations

import base64
import json
import re

import anyio
import httpx2
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.auth import ACCESS_HEADER
from tests.conftest import make_token, settings_for
from tests.helpers import two_tone_jpeg
from tests.test_outfits import lauren_headers
from tests.test_weather import PAYLOAD

BEARER = "x" * 48


@pytest.fixture
def mcp_app(tmp_path, rsa_key):
    from app.main import create_app
    from tests.conftest import jwk_from

    application = create_app(
        settings_for(tmp_path, MCP_BEARER_TOKEN=BEARER, PUBLIC_BASE_URL="https://outfits.example")
    )
    application.state.access_verifier.fetcher = lambda url: {"keys": [jwk_from(rsa_key)]}
    application.state.weather.fetcher = lambda lat, lon, tz: PAYLOAD
    return application


@pytest.fixture
def web(mcp_app):
    from fastapi.testclient import TestClient

    with TestClient(mcp_app, base_url="https://testserver") as c:
        yield c


def run_mcp(app, headers: dict[str, str], body, setup=None):
    """Run the app lifespan once, optionally ``await setup(http)`` against the web UI,
    then ``await body(session)`` against the mounted /mcp endpoint. All in-process and
    on one event loop, which the MCP session manager requires."""

    async def main():
        async with app.router.lifespan_context(app):
            transport = httpx2.ASGITransport(app=app)
            if setup is not None:
                async with httpx2.AsyncClient(
                    transport=transport, base_url="https://testserver"
                ) as http:
                    await setup(http)
            async with httpx2.AsyncClient(
                transport=transport, base_url="https://testserver", headers=headers
            ) as http:
                async with streamable_http_client("https://testserver/mcp", http_client=http) as (
                    read,
                    write,
                    *_,
                ):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        return await body(session)

    return anyio.run(main)


def result_json(result):
    if result.structured_content is not None:
        sc = result.structured_content
        return sc["result"] if isinstance(sc, dict) and set(sc) == {"result"} else sc
    return json.loads(result.content[0].text)


def test_mcp_requires_auth(web):
    r = web.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"accept": "application/json, text/event-stream"},
    )
    assert r.status_code == 401
    assert r.json()["error"] == "unauthorized"


def test_bearer_without_user_is_a_clear_error(web):
    r = web.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={
            "accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {BEARER}",
        },
    )
    assert r.status_code == 401
    assert "signed in" in r.json()["detail"]


def test_tool_contracts_via_bearer_token(mcp_app, rsa_key):
    # Lauren uses the web app first, so she is the single Access user the bearer token acts as.
    h = lauren_headers(rsa_key)
    ids = {}

    async def setup(http):
        r = await http.post(
            "/outfits",
            data={"then": "edit"},
            files={"photo": ("m.jpg", two_tone_jpeg(), "image/jpeg")},
            headers=h,
        )
        assert r.status_code == 303, r.text
        ids["photo"] = int(r.headers["location"].split("/")[2])

    headers = {"Authorization": f"Bearer {BEARER}"}

    async def body(session: ClientSession):
        out = {}
        tools = await session.list_tools()
        out["tools"] = sorted(t.name for t in tools.tools)
        templates = await session.list_resource_templates()
        out["templates"] = [t.uri_template for t in templates.resource_templates]

        out["tags"] = result_json(await session.call_tool("list_tags", {}))
        out["rules"] = result_json(await session.call_tool("list_colour_rules", {}))
        out["forecast"] = result_json(await session.call_tool("get_forecast", {}))

        created = result_json(
            await session.call_tool(
                "create_outfit",
                {
                    "name": "Navy floor",
                    "activity_tags": ["active day", "school visit"],
                    "temp_min": 15,
                    "temp_max": 25,
                    "rain_ok": True,
                    "colours": ["navy", "mustard"],
                    "garments": ["navy chinos"],
                },
            )
        )
        out["created"] = created
        updated = result_json(
            await session.call_tool(
                "update_outfit",
                {
                    "outfit_id": created["id"],
                    "favourite": True,
                    "colours": ["navy"],
                    "notes": "comfy",
                },
            )
        )
        out["updated"] = updated
        out["bad_colour"] = await session.call_tool(
            "create_outfit", {"name": "x", "colours": ["neon"]}
        )
        out["suggest"] = result_json(
            await session.call_tool(
                "suggest_outfits",
                {"activity_tags": ["active day", "nope"], "temp_min": 16, "temp_max": 24},
            )
        )
        out["suggest_forecast"] = result_json(await session.call_tool("suggest_outfits", {}))
        out["wear"] = result_json(
            await session.call_tool("log_wear", {"outfit_id": created["id"], "day": "2026-09-30"})
        )
        out["get"] = result_json(
            await session.call_tool("get_outfit", {"outfit_id": created["id"]})
        )
        out["listed"] = result_json(
            await session.call_tool("list_outfits", {"activity_tags": ["active day"]})
        )
        out["listed_colour"] = result_json(
            await session.call_tool("list_outfits", {"colours": ["mustard"]})
        )
        out["listed_none"] = result_json(
            await session.call_tool("list_outfits", {"activity_tags": ["does not exist"]})
        )
        out["missing"] = await session.call_tool("get_outfit", {"outfit_id": 99999})
        out["photo_tool"] = await session.call_tool(
            "get_outfit_photo", {"outfit_id": ids["photo"], "index": 1}
        )
        out["photo_resource"] = await session.read_resource(f"outfit://{ids['photo']}/photo/1")
        return out

    out = run_mcp(mcp_app, headers, body, setup=setup)
    photo_outfit_id = ids["photo"]

    assert out["tools"] == sorted(
        [
            "list_outfits",
            "get_outfit",
            "suggest_outfits",
            "create_outfit",
            "update_outfit",
            "log_wear",
            "get_forecast",
            "list_tags",
            "list_colour_rules",
            "get_outfit_photo",
            "mark_in_wash",
        ]
    )
    assert out["templates"] == ["outfit://{outfit_id}/photo/{index}"]
    assert [t["name"] for t in out["tags"]][:2] == ["active day", "desk day"]
    assert len(out["rules"]["palette"]) == 20 and out["rules"]["rules"] == []
    assert out["forecast"]["available"] and out["forecast"]["days"][0]["temp_max"] == 29.2

    created = out["created"]
    assert created["activity_tags"] == ["active day", "school visit"]
    assert created["colours"] == ["navy", "mustard"] and created["garments"] == ["navy chinos"]
    assert created["rain_ok"] is True and created["photos"] == []
    assert (
        out["updated"]["favourite"] is True
        and out["updated"]["colours"] == ["navy"]
        and out["updated"]["notes"] == "comfy"
    )
    assert out["updated"]["activity_tags"] == ["active day", "school visit"]  # untouched
    assert out["bad_colour"].is_error and "Palette keys" in out["bad_colour"].content[0].text

    s = out["suggest"]
    assert s["activity_tags"] == ["active day"] and s["unknown_tags"] == ["nope"]
    assert s["forecast"]["manual"] is True and s["forecast"]["temp_max"] == 24
    assert s["outfits"][0]["id"] == created["id"]
    assert (
        "matches active day" in s["outfits"][0]["why"]
        and "suits 15 to 25°" in s["outfits"][0]["why"]
    )
    assert out["suggest_forecast"]["forecast"]["manual"] is False

    assert out["wear"]["worn_on"] == "2026-09-30"
    assert (
        out["get"]["wear_history"] == ["2026-09-30"] and out["get"]["last_worn_on"] == "2026-09-30"
    )
    assert [o["id"] for o in out["listed"]] == [created["id"]]
    assert [o["id"] for o in out["listed_colour"]] == [photo_outfit_id]
    assert out["listed_none"] == []
    assert out["missing"].is_error and "No outfit with id 99999" in out["missing"].content[0].text

    photo = out["photo_tool"].content[0]
    assert photo.type == "image" and photo.mime_type == "image/jpeg"
    assert base64.b64decode(photo.data)[:2] == b"\xff\xd8"
    blob = out["photo_resource"].contents[0]
    assert blob.mime_type == "image/jpeg" and base64.b64decode(blob.blob)[:2] == b"\xff\xd8"
    # Photo URLs use the public base URL.
    listed = {o["id"]: o for o in out["listed_colour"]}
    assert listed[photo_outfit_id]["photos"][0]["url"].startswith("https://outfits.example/photos/")
    assert listed[photo_outfit_id]["photo_resources"] == [f"outfit://{photo_outfit_id}/photo/1"]


def test_access_user_token_acts_as_that_user(tmp_path, rsa_key):
    from app.main import create_app
    from tests.conftest import jwk_from

    h = lauren_headers(rsa_key)

    async def setup(http):
        await http.post("/outfits", data={"name": "Lauren only"}, headers=h)

    async def body(session: ClientSession):
        return result_json(await session.call_tool("list_outfits", {}))

    def fresh():
        app = create_app(settings_for(tmp_path))
        app.state.access_verifier.fetcher = lambda url: {"keys": [jwk_from(rsa_key)]}
        return app

    stranger = {ACCESS_HEADER: make_token(rsa_key, email="stranger@example.com")}
    assert run_mcp(fresh(), stranger, body, setup=setup) == []
    mine = {ACCESS_HEADER: make_token(rsa_key, email="lauren@example.com")}
    assert [o["name"] for o in run_mcp(fresh(), mine, body)] == ["Lauren only"]


def test_service_token_acts_as_mcp_user(tmp_path, rsa_key):
    from app.main import create_app
    from tests.conftest import jwk_from

    app = create_app(settings_for(tmp_path, MCP_USER_EMAIL="lauren@example.com"))
    app.state.access_verifier.fetcher = lambda url: {"keys": [jwk_from(rsa_key)]}
    svc = {ACCESS_HEADER: make_token(rsa_key, email=None, extra={"common_name": "abc.access"})}

    async def body(session: ClientSession):
        return result_json(await session.call_tool("list_tags", {}))

    async def setup(http):
        # Before Lauren has signed in, the service token has nobody to act as.
        r = await http.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"accept": "application/json, text/event-stream", **svc},
        )
        assert r.status_code == 401 and "has not signed in" in r.json()["detail"]
        await http.get("/", headers=lauren_headers(rsa_key))

    names = [t["name"] for t in run_mcp(app, svc, body, setup=setup)]
    assert "active day" in names


def test_bad_access_token_on_mcp_is_401(web, rsa_key):
    r = web.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={
            "accept": "application/json, text/event-stream",
            ACCESS_HEADER: make_token(rsa_key, aud="b" * 64),
        },
    )
    assert r.status_code == 401
    assert re.search(r"Audience", r.json()["detail"])
