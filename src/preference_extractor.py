"""Evidence extraction: user record -> list[PreferenceSignal].

The extractor never decides anything — it only collects evidence. Two
extractors feed one output shape:

- structured fields (high confidence, one signal per meaningful column)
- raw_history snippets, matched against a curated lexicon built from the
  actual corpus. A snippet may fire several rules ("traveling w/ 2 kids,
  direct is worth paying for" -> party AND stops). Snippets that match no
  rule are kept as dimension="unclassified" so nothing is silently dropped.

Conflict resolution and weighting happen downstream in traveler_profile.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Callable

import pandas as pd


class Source(Enum):
    STRUCTURED_FIELD = "structured_field"
    RAW_HISTORY = "raw_history"
    REQUEST = "request"
    FEEDBACK = "feedback"    # stated in a live interaction ("too early for me")
    BEHAVIOR = "behavior"    # derived from accept/reject/booking patterns


@dataclass(frozen=True)
class PreferenceSignal:
    dimension: str
    value: object
    source: Source
    evidence: str  # verbatim column=value or history snippet
    confidence: float


# ---------------------------------------------------------------------------
# Structured-field extraction
# ---------------------------------------------------------------------------

def extract_structured(user: pd.Series) -> list[PreferenceSignal]:
    """One high-confidence signal per meaningful structured column."""

    def sig(dimension: str, value: object, column: str, confidence: float = 0.9):
        return PreferenceSignal(
            dimension, value, Source.STRUCTURED_FIELD, f"{column}={user[column]}", confidence
        )

    signals = [
        sig("budget", user["price_sensitivity"], "price_sensitivity"),
        sig("stops", user["direct_preference"], "direct_preference"),
        sig("layover_cap", int(user["max_layover_minutes"]), "max_layover_minutes"),
        sig("airlines", list(user["preferred_airlines_list"]), "preferred_airlines"),
        sig("cabin", user["preferred_cabin"], "preferred_cabin"),
        sig("baggage", {"checked_bags": int(user["checked_bags"]), "stroller": bool(user["has_stroller"])},
            "baggage_preference"),
        sig("dates", int(user["date_flexibility_days"]), "date_flexibility_days"),
        sig("multi_city", user["multi_city_tendency"], "multi_city_tendency"),
        sig("trip_purpose", user["trip_purpose"], "trip_purpose"),
        sig("season_pattern", user["seasonal_pattern"], "seasonal_pattern", 0.7),
    ]
    if user["preferred_departure"] != "any":
        signals.append(sig("departure_time", user["preferred_departure"], "preferred_departure"))
    # 'none' means no program; the literal placeholder 'Frequent Flyer' is
    # treated as unknown — raw history often contradicts both (kept as a
    # low-confidence signal so the profile builder can flag the conflict).
    ff = user["frequent_flyer"]
    if ff not in ("none", "Frequent Flyer"):
        signals.append(sig("loyalty", "program", "frequent_flyer"))
    else:
        signals.append(sig("loyalty", "none", "frequent_flyer", 0.4))
    return signals


# ---------------------------------------------------------------------------
# Raw-history lexicon
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Rule:
    dimension: str
    pattern: re.Pattern
    value: object | Callable[[re.Match], object]
    confidence: float


def _rule(dimension: str, pattern: str, value, confidence: float = 0.7) -> Rule:
    return Rule(dimension, re.compile(pattern), value, confidence)


# Built from the full 50-user corpus (10 archetypes x 5 variants); matching is
# lowercase. tests/test_preference_extractor.py asserts 100% snippet coverage.
LEXICON: list[Rule] = [
    # budget
    _rule("budget", r"cheapest|broke student", "minimize", 0.8),
    _rule("budget", r"money'?s not the constraint|comfort over cost", "unconstrained", 0.8),
    _rule("budget", r"value matters|moderate budget|will pay a bit|if it saves", "value"),
    # stops / connections
    _rule("stops", r"hate connections|direct is worth paying|direct whenever it exists|simple and direct",
          "avoid", 0.8),
    _rule("stops", r"one stop (ok|fine)|single short connection", "limit_1"),
    _rule("stops", r"don'?t care about stops|dont care about stops|2 stops fine", "accept"),
    # redeyes
    _rule("redeye", r"redeyes? kill", "avoid", 0.8),
    _rule("redeye", r"ok with redeye", "accept", 0.8),
    # departure time of day
    _rule("departure_time", r"morning departures|morning flights feel safer", "morning"),
    _rule("departure_time", r"evening departures", "evening"),
    # cabin
    _rule("cabin_strict", r"first or business only|always book business", True, 0.8),
    _rule("cabin", r"premium economy is the sweet spot", "Premium Economy"),
    # baggage & party
    _rule("baggage", r"carry-on only|no bags beyond a backpack", {"checked_bags": 0, "stroller": False}),
    _rule("baggage", r"(\d+) checked bags",
          lambda m: {"checked_bags": int(m.group(1)), "stroller": "stroller" in m.string}),
    _rule("party", r"w/ (\d+) kids", lambda m: {"children": int(m.group(1))}, 0.8),
    # loyalty
    _rule("loyalty", r"stick to alliance|top-tier status|segments/yr|gold w/", "strong", 0.8),
    _rule("loyalty", r"loyal to ([a-z]{2})\b", "strong", 0.8),
    _rule("loyalty", r"prefer ([a-z]{2})\b|like ([a-z]{2}) but open", "preference", 0.6),
    _rule("loyalty", r"no loyalty|whatever airline fits the schedule", "none"),
    # revealed value of time: "took a 7hr layover in SIN to save $120"
    _rule("value_of_time", r"(\d+)\s*hr layover in [a-z]{3} to save \$(\d+)",
          lambda m: round(int(m.group(2)) / int(m.group(1)), 2), 0.8),
    _rule("layover_tolerance", r"overnight layovers|layover in [a-z]{3} to save", "high"),
    _rule("layover_tolerance", r"skip a \d+\s*hr layover|hate long layovers|"
                               r"long layovers? stress|avoid long layovers", "avoid_long"),
    # live-feedback phrasings (the corpus never uses these; travelers do)
    _rule("redeye", r"(don'?t like|hate|avoid|no) overnight flight", "avoid", 0.8),
    _rule("budget", r"pay(ing)? extra for comfort|money is no object", "unconstrained", 0.8),
    _rule("occasion", r"honeymoon|anniversary|babymoon", "special", 0.8),
    _rule("departure_time", r"too early in the morning|not (so|too) early|later in the day",
          "later", 0.7),
    # date flexibility
    _rule("dates", r"flexible dates|dates are flexible|date flexibility|super flexible|whole summer free"
                   r"|weeks of window", "flexible"),
    _rule("dates", r"fixed-ish|dates locked|school breaks only", "fixed"),
    # multi-city behavior (explicit mention or chained airport codes)
    _rule("multi_city", r"multi-city|\b[a-z]{3}-[a-z]{3}\b", "high"),
    # seasons
    _rule("season", r"avoid holiday crowds", "avoid_peak"),
    _rule("season", r"happy in peak season", "peak_ok"),
    # connection hub base
    _rule("hub", r"\b([a-z]{3}) is my connection base", lambda m: m.group(1).upper(), 0.8),
    # risk / experience
    _rule("connection_anxiety", r"scared of missing connections|short layovers stress", True, 0.8),
    _rule("experience", r"new to flying", "novice"),
    # amenities the flight dataset cannot satisfy -> profile.unsupported
    _rule("amenity", r"aisle seat", "aisle_seat", 0.6),
    _rule("amenity", r"lounge access|spa lounge", "lounge", 0.6),
    _rule("amenity", r"wifi onboard", "wifi", 0.6),
    _rule("amenity", r"chauffeur", "ground_services", 0.6),
    # travel patterns (explanation color + priors)
    _rule("travel_pattern", r"mon out fri back|every week", "weekly_commuter", 0.6),
    _rule("travel_pattern", r"for a festival|conference/wedding", "event_driven", 0.6),
    _rule("travel_pattern", r"last min for a steal", "opportunistic", 0.6),
    _rule("travel_pattern", r"retired", "retired", 0.6),
]


def extract_history(snippets: list[str]) -> list[PreferenceSignal]:
    """Match every snippet against the lexicon; unmatched -> 'unclassified'."""
    signals: list[PreferenceSignal] = []
    for snippet in snippets:
        text = snippet.lower()
        matched = False
        for rule in LEXICON:
            m = rule.pattern.search(text)
            if m:
                value = rule.value(m) if callable(rule.value) else rule.value
                signals.append(PreferenceSignal(rule.dimension, value, Source.RAW_HISTORY,
                                                snippet, rule.confidence))
                matched = True
        if not matched:
            signals.append(PreferenceSignal("unclassified", None, Source.RAW_HISTORY, snippet, 0.1))
    return signals


def extract_user(user: pd.Series) -> list[PreferenceSignal]:
    """All evidence for one parsed user row (see preprocessing.parse_users)."""
    return extract_structured(user) + extract_history(user["history_snippets"])
