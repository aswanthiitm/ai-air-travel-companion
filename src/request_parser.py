"""Free-text request -> TripSpec.

Deterministic rule-based parsing tuned to how travelers actually phrase
requests (city gazetteer scan, relative-date phrases, weekday patterns,
intent keywords). Emits request-level PreferenceSignals (source=REQUEST,
confidence 1.0) — the current ask always outranks stored history.

An LLM parser can be slotted behind the same TripSpec contract later; this
module is the offline-safe fallback the demo relies on.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from .airports import CITY_ALIASES, CITY_TO_IATA, REGION_ALIASES
from .preference_extractor import PreferenceSignal, Source


class DateKind(Enum):
    NEXT_MONTH = "next_month"
    SUMMER = "summer"
    HOLIDAYS = "holidays"
    WEEKDAY_PATTERN = "weekday_pattern"
    FLEX_WEEKS = "flex_weeks"


@dataclass(frozen=True)
class DatePhrase:
    kind: DateKind
    out_weekday: int | None = None     # 0=Monday (WEEKDAY_PATTERN)
    return_weekday: int | None = None
    flex_weeks: int | None = None      # (FLEX_WEEKS)
    evidence: str = ""


@dataclass
class TripSpec:
    raw_text: str
    destination_names: list[str] = field(default_factory=list)  # in mention order
    region: str | None = None
    date_phrase: DatePhrase | None = None
    round_trip: bool = False
    multi_city: bool = False
    advise_only: bool = False  # "what should I expect" -> lead with expectations
    signals: list[PreferenceSignal] = field(default_factory=list)


_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_WEEKDAY_RE = "|".join(_WEEKDAYS)
_WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}

# Longest names first so multi-word cities win over shorter overlaps.
_CITY_LOOKUP = sorted({**CITY_TO_IATA, **CITY_ALIASES}.items(), key=lambda kv: -len(kv[0]))


def _find_cities(text: str) -> list[str]:
    """City names in mention order; overlapping shorter matches are dropped."""
    found: list[tuple[int, int, str]] = []  # (start, end, name)
    for name, _ in _CITY_LOOKUP:
        for m in re.finditer(rf"\b{re.escape(name)}\b", text):
            overlaps = any(s < m.end() and m.start() < e for s, e, _ in found)
            if not overlaps:
                found.append((m.start(), m.end(), name))
    return [name for _, _, name in sorted(found)]


def _find_region(text: str) -> str | None:
    for alias, region in REGION_ALIASES.items():
        if re.search(rf"\b{alias}\b", text):
            return region
    return None


def _find_date_phrase(text: str) -> DatePhrase | None:
    if "next month" in text:
        return DatePhrase(DateKind.NEXT_MONTH, evidence="next month")
    if re.search(r"\bsummer\b", text):
        return DatePhrase(DateKind.SUMMER, evidence="summer")
    if "holiday" in text:
        return DatePhrase(DateKind.HOLIDAYS, evidence="holidays")

    flex = re.search(rf"\b({'|'.join(_WORD_NUMBERS)}|\d+)\s+weeks?\b", text)
    if flex:
        token = flex.group(1)
        weeks = _WORD_NUMBERS.get(token) or (int(token) if token.isdigit() else None)
        if weeks:
            return DatePhrase(DateKind.FLEX_WEEKS, flex_weeks=weeks, evidence=flex.group(0))

    ret = re.search(rf"(?:back|return(?:ing)?)(?:\s+on)?\s+({_WEEKDAY_RE})", text)
    outbound = next(
        (m for m in re.finditer(rf"\b({_WEEKDAY_RE})\b", text)
         if not (ret and m.start(1) == ret.start(1))),
        None,
    )
    if outbound or ret:
        return DatePhrase(
            DateKind.WEEKDAY_PATTERN,
            out_weekday=_WEEKDAYS.index(outbound.group(1)) if outbound else None,
            return_weekday=_WEEKDAYS.index(ret.group(1)) if ret else None,
            evidence=" / ".join(m.group(0) for m in (outbound, ret) if m),
        )
    return None


def _request_signal(dimension: str, value: object, evidence: str) -> PreferenceSignal:
    return PreferenceSignal(dimension, value, Source.REQUEST, evidence, 1.0)


# Intent keywords -> request-level signals. Kept small and readable; each
# entry is (regex, dimension, value).
_SIGNAL_RULES: list[tuple[str, str, object]] = [
    (r"cheapest|cheap as possible|lowest (?:price|fare)", "budget", "minimize"),
    (r"flexible (?:on|with) dates|dates? (?:are|is) flexible", "dates", "flexible"),
    (r"meeting|business trip|work trip|conference", "trip_purpose", "business"),
    (r"direct only|no (?:stops|connections|layovers)|non-?stop", "stops", "avoid"),
    (r"comfort|business class|first class", "comfort", "prioritize"),
]


def parse_request(raw: str) -> TripSpec:
    """Parse one free-text travel request into a TripSpec."""
    text = raw.lower()

    signals = [
        _request_signal(dim, value, re.search(pattern, text).group(0))
        for pattern, dim, value in _SIGNAL_RULES
        if re.search(pattern, text)
    ]

    cities = _find_cities(text)
    spec = TripSpec(
        raw_text=raw,
        destination_names=cities,
        region=_find_region(text),
        date_phrase=_find_date_phrase(text),
        round_trip=bool(re.search(r"\bback\b|\breturn\b|round.?trip", text)),
        multi_city=bool(re.search(r"multi.?city|one journey|one trip", text)) or len(cities) > 1,
        advise_only=bool(re.search(r"what should i expect|what to expect", text)),
        signals=signals,
    )
    return spec
