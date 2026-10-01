from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from app import colours, images
from tests.helpers import make_jpeg, two_tone_jpeg


def test_process_upload_strips_exif_and_makes_variants(tmp_path):
    data = make_jpeg()
    src = Image.open(BytesIO(data))
    assert src.getexif().get_ifd(0x8825), "fixture should carry GPS data"

    result = images.process_upload(data, tmp_path)
    original, web, thumb = images.variant_names(result.filename)
    for name in (original, web, thumb):
        path = tmp_path / name
        assert path.exists()
        with Image.open(path) as img:
            assert img.format == "JPEG"
            assert not img.getexif(), f"{name} still has EXIF"
            assert "exif" not in img.info

    with Image.open(tmp_path / web) as img:
        assert max(img.size) <= images.WEB_MAX
    with Image.open(tmp_path / thumb) as img:
        assert max(img.size) <= images.THUMB_MAX
    assert (result.width, result.height) == (800, 1200)


def test_orientation_is_applied(tmp_path):
    # Orientation 6 means rotate 90 degrees clockwise: a landscape file shows as portrait.
    data = make_jpeg(width=1200, height=800, orientation=6)
    result = images.process_upload(data, tmp_path)
    assert (result.width, result.height) == (800, 1200)


def test_large_original_is_capped(tmp_path):
    data = make_jpeg(width=3000, height=4000)
    result = images.process_upload(data, tmp_path)
    assert max(result.width, result.height) == images.ORIGINAL_MAX


def test_non_image_rejected(tmp_path):
    with pytest.raises(images.BadImage):
        images.process_upload(b"definitely not a jpeg", tmp_path)


def test_delete_files_removes_all_variants(tmp_path):
    result = images.process_upload(make_jpeg(), tmp_path)
    images.delete_files(tmp_path, result.filename)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "name, ok",
    [
        ("0123456789abcdef0123456789abcdef.jpg", True),
        ("0123456789abcdef0123456789abcdef_web.jpg", True),
        ("0123456789abcdef0123456789abcdef_thumb.jpg", True),
        ("../etc/passwd", False),
        ("0123456789abcdef0123456789abcdef.png", False),
        ("short.jpg", False),
    ],
)
def test_safe_filename(name, ok):
    assert images.safe_filename(name) is ok


def test_extract_colours_finds_dominant_palette_keys():
    img = Image.open(BytesIO(two_tone_jpeg()))
    found = colours.extract_colours(img)
    assert found[:2] in (["navy", "mustard"], ["mustard", "navy"])


def test_palette_size_and_keys_unique():
    assert 16 <= len(colours.PALETTE) <= 20
    assert len(colours.KEYS) == len(set(colours.KEYS))
    assert colours.clean_keys(["Navy", "navy", "nope", "mustard"]) == ["navy", "mustard"]
