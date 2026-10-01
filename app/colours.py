"""A fixed palette of named colours, and a simple dominant-colour extractor.

The palette is deliberately short so tagging stays quick. Extraction is a
median-cut quantise on the centre of the thumbnail (where the body usually is),
with each quantised colour snapped to its nearest palette swatch.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image


@dataclass(frozen=True)
class Swatch:
    key: str
    label: str
    hex: str

    @property
    def rgb(self) -> tuple[int, int, int]:
        h = self.hex.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    @property
    def is_light(self) -> bool:
        r, g, b = self.rgb
        return (0.299 * r + 0.587 * g + 0.114 * b) > 150


PALETTE: list[Swatch] = [
    Swatch("black", "Black", "#1a1a1a"),
    Swatch("charcoal", "Charcoal", "#4a4a4a"),
    Swatch("grey", "Grey", "#9a9a9a"),
    Swatch("white", "White", "#f5f5f5"),
    Swatch("cream", "Cream", "#f1e9d6"),
    Swatch("tan", "Tan", "#c8a57a"),
    Swatch("brown", "Brown", "#6b4a2f"),
    Swatch("navy", "Navy", "#1f2a44"),
    Swatch("blue", "Blue", "#3b63a8"),
    Swatch("light-blue", "Light blue", "#9cc0e3"),
    Swatch("teal", "Teal", "#2f7f85"),
    Swatch("green", "Green", "#2f6b3a"),
    Swatch("olive", "Olive", "#6e7b3a"),
    Swatch("sage", "Sage", "#9fb49a"),
    Swatch("mustard", "Mustard", "#d2a92a"),
    Swatch("rust", "Rust", "#b4532a"),
    Swatch("red", "Red", "#b8252a"),
    Swatch("burgundy", "Burgundy", "#6f1f32"),
    Swatch("pink", "Pink", "#e07a9a"),
    Swatch("lilac", "Lilac", "#b39ad6"),
]

BY_KEY: dict[str, Swatch] = {s.key: s for s in PALETTE}
KEYS: list[str] = [s.key for s in PALETTE]


def is_valid(key: str) -> bool:
    return key in BY_KEY


def label(key: str) -> str:
    swatch = BY_KEY.get(key)
    return swatch.label if swatch else key


def clean_keys(keys: list[str] | tuple[str, ...]) -> list[str]:
    """Drop unknown keys and duplicates, keep order."""
    seen: set[str] = set()
    out: list[str] = []
    for k in keys:
        k = (k or "").strip().lower()
        if k in BY_KEY and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    """'Redmean' weighted RGB distance. Cheap and close enough to perceptual."""
    rmean = (a[0] + b[0]) / 2
    dr, dg, db = a[0] - b[0], a[1] - b[1], a[2] - b[2]
    return ((2 + rmean / 256) * dr * dr + 4 * dg * dg + (2 + (255 - rmean) / 256) * db * db) ** 0.5


def nearest(rgb: tuple[int, int, int]) -> Swatch:
    return min(PALETTE, key=lambda s: _distance(rgb, s.rgb))


def extract_colours(image: Image.Image, count: int = 3) -> list[str]:
    """Top palette colours in the image, most dominant first."""
    img = image.convert("RGB")
    w, h = img.size
    # Centre crop: 60% of the width, 80% of the height. Mirror selfies put the
    # outfit in the middle and the wall at the edges.
    left, right = int(w * 0.2), int(w * 0.8)
    top, bottom = int(h * 0.1), int(h * 0.9)
    if right - left > 8 and bottom - top > 8:
        img = img.crop((left, top, right, bottom))
    img.thumbnail((96, 96))
    quantised = img.quantize(colors=12, method=Image.Quantize.MEDIANCUT)
    palette = quantised.getpalette() or []
    counts = quantised.getcolors(maxcolors=4096) or []

    tally: dict[str, int] = {}
    for n, index in counts:
        rgb = tuple(palette[index * 3 : index * 3 + 3])
        if len(rgb) != 3:
            continue
        key = nearest(rgb).key  # type: ignore[arg-type]
        tally[key] = tally.get(key, 0) + n
    ranked = sorted(tally.items(), key=lambda kv: kv[1], reverse=True)
    return [key for key, _ in ranked[:count]]
