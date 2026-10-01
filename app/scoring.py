"""Rank outfits for a day. Forgiving scoring, never a hard filter.

Weights, roughly in the order the brief gives them:

- activity tag match is worth most (up to 50)
- temperature fit next (up to 30, with a sliding penalty when it misses)
- weather flags next (rain, wind, humidity, removable layers)
- a small bonus for favourites
- a small penalty for anything worn in the last 5 days
- a small nudge from her colour pairing rules

Every result carries a short "why" line built from the same checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.colour_rules import Rules, pair_label, pairs_in
from app.outfits import Outfit
from app.weather import DayForecast

W_TAG_FULL = 50.0
W_TAG_NONE_SELECTED = 10.0  # neutral credit when she hasn't picked any tags
W_TAG_UNTAGGED_OUTFIT = 5.0  # an outfit with no tags is "could be anything"
W_TEMP_FIT = 30.0
W_TEMP_UNKNOWN = 10.0
TEMP_PENALTY_PER_DEGREE = 5.0
TEMP_PENALTY_FLOOR = -25.0
W_RAIN_OK, W_RAIN_BAD = 10.0, -8.0
W_WIND_OK, W_WIND_BAD = 6.0, -5.0
W_HUMID_OK, W_HUMID_BAD = 8.0, -6.0
W_LAYERS = 4.0
LAYERS_SPREAD = 10.0  # degrees between min and max that makes layers useful
W_FAVOURITE = 5.0
RECENT_DAYS = 5
W_RECENT = -15.0
W_COLOUR_GOOD = 4.0
W_COLOUR_AVOID = -8.0


@dataclass
class Scored:
    outfit: Outfit
    score: float
    reasons: list[str] = field(default_factory=list)

    @property
    def why(self) -> str:
        return ", ".join(self.reasons)

    def to_dict(self, base_url: str = "") -> dict:
        data = self.outfit.to_dict(base_url)
        data["score"] = round(self.score, 1)
        data["why"] = self.why
        return data


def feel_temperature(forecast: DayForecast) -> float:
    """What the day feels like for dressing: weighted toward the max."""
    return 0.4 * forecast.temp_min + 0.6 * forecast.temp_max


def _temp_score(outfit: Outfit, forecast: DayForecast | None) -> tuple[float, str | None]:
    if forecast is None:
        return 0.0, None
    if outfit.temp_min is None and outfit.temp_max is None:
        return W_TEMP_UNKNOWN, None
    t = feel_temperature(forecast)
    lo = outfit.temp_min if outfit.temp_min is not None else -99.0
    hi = outfit.temp_max if outfit.temp_max is not None else 99.0
    if lo <= t <= hi:
        return W_TEMP_FIT, f"suits {outfit.temp_label}"
    miss = (lo - t) if t < lo else (t - hi)
    penalty = max(TEMP_PENALTY_FLOOR, W_TEMP_FIT - TEMP_PENALTY_PER_DEGREE * miss)
    if miss <= 2:
        word = "a touch cool" if t < lo else "a touch warm"
    else:
        word = "too cold a day for it" if t < lo else "too warm a day for it"
    return penalty, f"{word} ({outfit.temp_label})"


def score_outfit(
    outfit: Outfit,
    selected_tag_ids: set[int],
    forecast: DayForecast | None,
    today: date,
    rules: Rules | None = None,
) -> Scored:
    score = 0.0
    reasons: list[str] = []

    # Activity tags.
    outfit_tags = outfit.tag_ids
    if selected_tag_ids:
        matched = outfit_tags & selected_tag_ids
        if matched:
            score += W_TAG_FULL * len(matched) / len(selected_tag_ids)
            names = [t.name for t in outfit.tags if t.id in matched]
            reasons.append("matches " + ", ".join(names))
        elif not outfit_tags:
            score += W_TAG_UNTAGGED_OUTFIT
        else:
            reasons.append("tagged " + ", ".join(outfit.tag_names[:2]))
    else:
        score += W_TAG_NONE_SELECTED

    # Temperature.
    temp_points, temp_reason = _temp_score(outfit, forecast)
    score += temp_points
    if temp_reason:
        reasons.append(temp_reason)

    # Weather flags.
    if forecast is not None:
        if forecast.rainy:
            score += W_RAIN_OK if outfit.rain_ok else W_RAIN_BAD
            reasons.append("rain ok" if outfit.rain_ok else "not great in rain")
        if forecast.windy:
            score += W_WIND_OK if outfit.windy_ok else W_WIND_BAD
            reasons.append("windy ok" if outfit.windy_ok else "not great in wind")
        if forecast.humid:
            score += W_HUMID_OK if outfit.humid_ok else W_HUMID_BAD
            reasons.append("fine when humid" if outfit.humid_ok else "sticky when humid")
        if outfit.layers_removable and (forecast.temp_max - forecast.temp_min) >= LAYERS_SPREAD:
            score += W_LAYERS
            reasons.append("layers for a cool start")

    # Favourite.
    if outfit.favourite:
        score += W_FAVOURITE
        reasons.append("favourite")

    # Recently worn.
    if outfit.last_worn_on is not None:
        days_ago = (today - outfit.last_worn_on).days
        if 0 <= days_ago <= RECENT_DAYS:
            score += W_RECENT
        if days_ago == 0:
            reasons.append("worn today")
        elif days_ago == 1:
            reasons.append("worn yesterday")
        elif days_ago > 1:
            reasons.append(f"worn {days_ago} days ago")
    else:
        reasons.append("not worn yet")

    # Colour rules.
    if rules:
        for pair, verdict in pairs_in(outfit.colours, rules):
            if verdict == "good":
                score += W_COLOUR_GOOD
                reasons.append(f"{pair_label(pair)} is a pairing you like")
            else:
                score += W_COLOUR_AVOID
                reasons.append(f"{pair_label(pair)} is a pairing you avoid")

    return Scored(outfit=outfit, score=score, reasons=reasons)


def rank_outfits(
    outfits: list[Outfit],
    selected_tag_ids: set[int],
    forecast: DayForecast | None,
    today: date,
    rules: Rules | None = None,
) -> list[Scored]:
    scored = [
        score_outfit(o, selected_tag_ids, forecast, today, rules) for o in outfits if not o.archived
    ]
    scored.sort(key=lambda s: (-s.score, s.outfit.last_worn_on or date.min, s.outfit.id))
    return scored
