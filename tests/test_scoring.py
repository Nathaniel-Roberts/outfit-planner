from __future__ import annotations

from datetime import date

from app.outfits import Outfit
from app.scoring import rank_outfits, score_outfit
from app.tags import Tag
from app.weather import DayForecast, manual_forecast

TODAY = date(2026, 10, 1)
ACTIVE = Tag(1, 1, "active day", None, 0)
DESK = Tag(2, 1, "desk day", None, 1)
SMART = Tag(3, 1, "smart", None, 2)


def outfit(id=1, tags=(), temp=(None, None), colours=(), last_worn=None, **flags) -> Outfit:
    return Outfit(
        id=id,
        user_id=1,
        name=f"Outfit {id}",
        notes=None,
        temp_min=temp[0],
        temp_max=temp[1],
        rain_ok=flags.get("rain_ok", False),
        windy_ok=flags.get("windy_ok", False),
        humid_ok=flags.get("humid_ok", False),
        layers_removable=flags.get("layers_removable", False),
        favourite=flags.get("favourite", False),
        archived=flags.get("archived", False),
        last_worn_on=last_worn,
        created_at="2026-09-01 00:00:00",
        updated_at="2026-09-01 00:00:00",
        tags=list(tags),
        colours=list(colours),
    )


def forecast(tmin=12.0, tmax=20.0, rain=10, wind=15.0, humidity=50) -> DayForecast:
    return DayForecast(TODAY, tmin, tmax, rain, 0.0, wind, humidity, 2)


def test_tag_match_outranks_everything_else():
    match = outfit(1, tags=[ACTIVE])
    other = outfit(2, tags=[DESK], favourite=True, temp=(10, 25))
    ranked = rank_outfits([other, match], {ACTIVE.id}, forecast(), TODAY)
    assert [s.outfit.id for s in ranked] == [1, 2]
    assert "matches active day" in ranked[0].why
    assert "tagged desk day" in ranked[1].why


def test_partial_tag_match_scores_proportionally():
    both = outfit(1, tags=[ACTIVE, SMART])
    one = outfit(2, tags=[ACTIVE])
    ranked = rank_outfits([one, both], {ACTIVE.id, SMART.id}, None, TODAY)
    assert ranked[0].outfit.id == 1
    assert ranked[0].score > ranked[1].score


def test_temperature_fit_and_miss():
    fits = outfit(1, temp=(12, 20))
    cold = outfit(2, temp=(25, 35))
    f = forecast(12, 20)
    s_fit = score_outfit(fits, set(), f, TODAY)
    s_cold = score_outfit(cold, set(), f, TODAY)
    assert s_fit.score > s_cold.score
    assert "suits 12 to 20°" in s_fit.why
    assert "too cold a day for it" in s_cold.why
    assert s_cold.score >= -25 + 10  # floor on the penalty plus neutral tag credit


def test_unknown_temperature_is_neutral_not_punished():
    unknown = outfit(1)
    wrong = outfit(2, temp=(28, 35))
    assert (
        score_outfit(unknown, set(), forecast(), TODAY).score
        > score_outfit(wrong, set(), forecast(), TODAY).score
    )


def test_weather_flags():
    rainy_day = forecast(rain=70, wind=45, tmax=30, tmin=22, humidity=80)
    good = outfit(1, rain_ok=True, windy_ok=True, humid_ok=True)
    bad = outfit(2)
    s_good = score_outfit(good, set(), rainy_day, TODAY)
    s_bad = score_outfit(bad, set(), rainy_day, TODAY)
    assert s_good.score - s_bad.score == (10 + 8) + (6 + 5) + (8 + 6)
    assert "rain ok" in s_good.why and "not great in rain" in s_bad.why
    assert "sticky when humid" in s_bad.why


def test_layers_bonus_only_on_big_spread():
    layered = outfit(1, layers_removable=True)
    assert "layers" in score_outfit(layered, set(), forecast(8, 22), TODAY).why
    assert "layers" not in score_outfit(layered, set(), forecast(18, 22), TODAY).why


def test_recent_wear_penalty_and_why():
    recent = outfit(1, last_worn=date(2026, 9, 29))
    older = outfit(2, last_worn=date(2026, 9, 20))
    never = outfit(3)
    s_recent = score_outfit(recent, set(), None, TODAY)
    s_older = score_outfit(older, set(), None, TODAY)
    s_never = score_outfit(never, set(), None, TODAY)
    assert s_older.score == s_never.score > s_recent.score
    assert "worn 2 days ago" in s_recent.why
    assert "worn 11 days ago" in s_older.why
    assert "not worn yet" in s_never.why
    yesterday = score_outfit(outfit(4, last_worn=date(2026, 9, 30)), set(), None, TODAY)
    assert "worn yesterday" in yesterday.why


def test_favourite_small_bonus():
    assert (
        score_outfit(outfit(1, favourite=True), set(), None, TODAY).score
        - score_outfit(outfit(2), set(), None, TODAY).score
        == 5
    )


def test_colour_rules_nudge():
    rules = {frozenset({"navy", "mustard"}): "good", frozenset({"black", "navy"}): "avoid"}
    liked = score_outfit(outfit(1, colours=["navy", "mustard"]), set(), None, TODAY, rules)
    avoided = score_outfit(outfit(2, colours=["black", "navy"]), set(), None, TODAY, rules)
    plain = score_outfit(outfit(3, colours=["navy", "white"]), set(), None, TODAY, rules)
    assert liked.score > plain.score > avoided.score
    assert "Mustard + Navy is a pairing you like" in liked.why
    assert "Black + Navy is a pairing you avoid" in avoided.why


def test_archived_excluded_and_ties_prefer_least_recently_worn():
    a = outfit(1, last_worn=date(2026, 9, 1))
    b = outfit(2, last_worn=date(2026, 8, 1))
    z = outfit(3, archived=True)
    ranked = rank_outfits([a, b, z], set(), None, TODAY)
    assert [s.outfit.id for s in ranked] == [2, 1]


def test_manual_forecast_maps_flags():
    f = manual_forecast(TODAY, 25, 15, rainy=True, windy=False, humid=True)
    assert (f.temp_min, f.temp_max) == (15, 25)
    assert f.rainy and not f.windy and f.humid is False  # humid needs max >= 26
    assert f.manual and f.label == "Set by you"
    assert "15 to 25°" in f.summary
