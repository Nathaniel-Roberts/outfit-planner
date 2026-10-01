"""Jinja2 setup with Australian date formatting."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def au_date(value: str | date | datetime | None, fmt: str = "%d/%m/%Y") -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value[:10])
        except ValueError:
            return value
    return value.strftime(fmt)


def au_day(value: str | date | None) -> str:
    """Like 'Wed 1 Oct'."""
    if value is None:
        return ""
    if isinstance(value, str):
        value = date.fromisoformat(value[:10])
    return f"{value:%a} {value.day} {value:%b}"


def degrees(value: float | int | None) -> str:
    if value is None:
        return "–"
    return f"{round(value):d}°"


def palette_hex(key: str) -> str:
    from app.colours import BY_KEY

    swatch = BY_KEY.get(key)
    return swatch.hex if swatch else "#cccccc"


def build_templates(tz: str) -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["au_date"] = au_date
    templates.env.filters["au_day"] = au_day
    templates.env.filters["degrees"] = degrees
    templates.env.globals["tz"] = tz
    templates.env.globals["palette_hex"] = palette_hex
    return templates


def today_in(tz: str) -> date:
    return datetime.now(ZoneInfo(tz)).date()


def render(templates: Jinja2Templates, request: Request, name: str, **context):
    context.setdefault("user", getattr(request.state, "user", None))
    context.setdefault("flash", getattr(request.state, "flash", None))
    return templates.TemplateResponse(request, name, context)
