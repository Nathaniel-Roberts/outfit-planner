"""Photo processing: strip metadata, fix orientation, generate web and thumb sizes.

Files live in ``DATA_DIR/photos/<outfit_id>/`` as ``<uuid>.jpg`` (original size,
re-encoded without EXIF), ``<uuid>_web.jpg`` and ``<uuid>_thumb.jpg``.
Re-encoding is the simplest reliable way to drop EXIF location data.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

ORIGINAL_MAX = 2400
WEB_MAX = 1200
THUMB_MAX = 480
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

Image.MAX_IMAGE_PIXELS = 80_000_000


class BadImage(ValueError):
    """The upload was not an image we can read."""


@dataclass(frozen=True)
class ProcessedPhoto:
    filename: str
    width: int
    height: int


def web_name(filename: str) -> str:
    return filename.removesuffix(".jpg") + "_web.jpg"


def thumb_name(filename: str) -> str:
    return filename.removesuffix(".jpg") + "_thumb.jpg"


def variant_names(filename: str) -> tuple[str, str, str]:
    return filename, web_name(filename), thumb_name(filename)


def open_image(data: bytes) -> Image.Image:
    if len(data) > MAX_UPLOAD_BYTES:
        raise BadImage("Photo is too large (25 MB limit).")
    try:
        img = Image.open(BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise BadImage("That file is not a photo we can read.") from exc
    # Apply the EXIF orientation so the pixels are upright, then drop the metadata.
    img = ImageOps.exif_transpose(img) or img
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    elif img.mode == "L":
        img = img.convert("RGB")
    return img


def _save_jpeg(img: Image.Image, path: Path, max_edge: int, quality: int) -> tuple[int, int]:
    copy = img.copy()
    copy.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    # No exif= argument, so nothing from the source metadata is written.
    copy.save(path, format="JPEG", quality=quality, optimize=True, progressive=True)
    return copy.size


def process_upload(data: bytes, dest_dir: Path) -> ProcessedPhoto:
    img = open_image(data)
    dest_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.jpg"
    width, height = _save_jpeg(img, dest_dir / filename, ORIGINAL_MAX, 92)
    _save_jpeg(img, dest_dir / web_name(filename), WEB_MAX, 84)
    _save_jpeg(img, dest_dir / thumb_name(filename), THUMB_MAX, 80)
    return ProcessedPhoto(filename=filename, width=width, height=height)


def delete_files(dest_dir: Path, filename: str) -> None:
    for name in variant_names(filename):
        try:
            (dest_dir / name).unlink()
        except FileNotFoundError:
            pass


def safe_filename(filename: str) -> bool:
    """Only names we generated: hex uuid plus optional variant suffix."""
    base = filename.removesuffix(".jpg")
    for suffix in ("_web", "_thumb"):
        base = base.removesuffix(suffix)
    return (
        filename.endswith(".jpg") and len(base) == 32 and all(c in "0123456789abcdef" for c in base)
    )
