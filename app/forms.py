"""Small helpers for reading HTML form data."""

from __future__ import annotations

from starlette.datastructures import FormData, UploadFile

from app.outfits import Outfit, OutfitInput, input_from


def _float_or_none(value: str | None) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return None


def _bool(form: FormData, key: str) -> bool:
    value = form.get(key)
    return value is not None and str(value) not in ("", "0", "false", "off")


def _split_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in str(value).replace("\n", ",").split(",") if part.strip()]


def outfit_input_from_form(form: FormData, existing: Outfit | None = None) -> OutfitInput:
    data = input_from(existing) if existing else OutfitInput()
    if "name" in form:
        data.name = str(form.get("name") or "")
    if "notes" in form:
        data.notes = str(form.get("notes") or "")
    if "temp_min" in form:
        data.temp_min = _float_or_none(form.get("temp_min"))  # type: ignore[arg-type]
    if "temp_max" in form:
        data.temp_max = _float_or_none(form.get("temp_max"))  # type: ignore[arg-type]
    if data.temp_min is not None and data.temp_max is not None and data.temp_min > data.temp_max:
        data.temp_min, data.temp_max = data.temp_max, data.temp_min
    # Checkbox groups: the form only knows about them if the marker field is present.
    if _bool(form, "has_flags"):
        data.rain_ok = _bool(form, "rain_ok")
        data.windy_ok = _bool(form, "windy_ok")
        data.humid_ok = _bool(form, "humid_ok")
        data.layers_removable = _bool(form, "layers_removable")
        data.favourite = _bool(form, "favourite")
    if _bool(form, "has_tags"):
        ids: list[int] = []
        for raw in form.getlist("tag_ids"):
            if str(raw).isdigit():
                ids.append(int(str(raw)))
        data.tag_ids = ids
    if _bool(form, "has_colours"):
        data.colours = [str(c) for c in form.getlist("colours")]
    if "garments" in form:
        data.garment_names = _split_list(str(form.get("garments") or ""))
    return data


def new_tag_names(form: FormData) -> list[str]:
    return _split_list(str(form.get("new_tags") or ""))


async def read_upload(value: object) -> bytes | None:
    if isinstance(value, UploadFile) and value.filename:
        data = await value.read()
        return data or None
    return None
