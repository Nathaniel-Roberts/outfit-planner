from __future__ import annotations

from io import BytesIO

from PIL import Image


def make_jpeg(
    width=800, height=1200, colour=(31, 42, 68), with_gps=True, orientation=None
) -> bytes:
    """A JPEG with EXIF (including a GPS block) so we can prove it gets stripped."""
    img = Image.new("RGB", (width, height), colour)
    exif = Image.Exif()
    exif[0x010F] = "TestPhone"  # Make
    exif[0x0110] = "Mirror 1"  # Model
    if orientation:
        exif[0x0112] = orientation
    if with_gps:
        gps = exif.get_ifd(0x8825)
        gps[1] = "S"
        gps[2] = (33.0, 25.0, 48.0)
        gps[3] = "E"
        gps[4] = (151.0, 20.0, 24.0)
    buf = BytesIO()
    img.save(buf, format="JPEG", exif=exif.tobytes(), quality=90)
    return buf.getvalue()


def two_tone_jpeg(top=(31, 42, 68), bottom=(210, 169, 42), width=600, height=900) -> bytes:
    img = Image.new("RGB", (width, height), top)
    for y in range(height // 2, height):
        for x in range(width):
            img.putpixel((x, y), bottom)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()
