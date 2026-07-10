"""TripSpec x TravelerProfile x NOW -> ResolvedTrip.

Deterministic resolution of everything the recommendation engine needs:
concrete airports, concrete date windows (relative phrases anchored to the
simulated NOW), trip shape, and a profile copy with request-level signals
applied (the current ask outranks stored history).

Every resolution decision is appended to `notes` — the explanation engine
turns these into sentences, so nothing here is silent.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

from . import config
from .airports import AIRPORTS, REGIONS, resolve_city
from .preference_extractor import PreferenceSignal
from .preprocessing import FlightStore
from .request_parser import DateKind, TripSpec
from .traveler_profile import TravelerProfile

# Resolution defaults, documented in README Assumptions.
LEAD_DAYS = 7            # earliest departure when no date is given
DEFAULT_WINDOW_DAYS = 14  # minimum search-window width
WEEKDAY_SEARCH_WEEKS = 5  # how many candidate weeks a weekday pattern scans
STAY_DAYS_MULTI_CITY = 3  # default nights per city
STAY_DAYS_ROUND_TRIP = 5  # default trip length when unstated
REGION_TRIP_CITIES = 3    # destinations picked for "a multi-city <region> trip"


@dataclass(frozen=True)
class DateWindow:
    start: date
    end: date  # inclusive

    def __str__(self) -> str:
        return f"{self.start.isoformat()}..{self.end.isoformat()}"


@dataclass(frozen=True)
class WeekdayConstraints:
    """'for a Tuesday meeting, back Thursday' -> arrive by Tue, return Thu."""
    out_weekday: int | None    # 0=Monday
    return_weekday: int | None


@dataclass
class ResolvedTrip:
    origin: str
    destinations: list[str]              # IATA; mention order (engine may reorder)
    trip_type: str                       # one_way | round_trip | multi_city
    depart_window: DateWindow
    weekday: WeekdayConstraints | None
    stay_days: int
    advise_only: bool
    profile: TravelerProfile             # request signals already applied
    request_signals: list[PreferenceSignal]
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Date-window resolution
# ---------------------------------------------------------------------------

def _resolve_window(spec: TripSpec, profile: TravelerProfile, now: date,
                    notes: list[str]) -> DateWindow:
    phrase = spec.date_phrase
    if phrase is None:
        width = max(DEFAULT_WINDOW_DAYS, 2 * profile.flexibility.date_flexibility_days)
        window = DateWindow(now + timedelta(days=LEAD_DAYS),
                            now + timedelta(days=LEAD_DAYS + width))
        notes.append(
            f"no dates given — searching {window} ({LEAD_DAYS}d lead, width from "
            f"date_flexibility_days={profile.flexibility.date_flexibility_days})")
        return window

    if phrase.kind is DateKind.NEXT_MONTH:
        year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
        window = DateWindow(date(year, month, 1),
                            date(year, month, calendar.monthrange(year, month)[1]))
        notes.append(f"'next month' resolved to {window} (today: {now.isoformat()})")
    elif phrase.kind is DateKind.SUMMER:
        year = now.year if now.month < 9 else now.year + 1
        window = DateWindow(max(date(year, 6, 1), now + timedelta(days=1)), date(year, 8, 31))
        notes.append(f"'summer' resolved to {window}")
    elif phrase.kind is DateKind.HOLIDAYS:
        year = now.year if now < date(now.year, 12, 20) else now.year + 1
        window = DateWindow(date(year, 12, 15), date(year + 1, 1, 5))
        notes.append(f"'the holidays' resolved to the year-end window {window}")
    elif phrase.kind is DateKind.FLEX_WEEKS:
        window = DateWindow(now + timedelta(days=LEAD_DAYS),
                            now + timedelta(days=LEAD_DAYS + 7 * phrase.flex_weeks))
        notes.append(f"'{phrase.evidence}' resolved to {window}")
    else:  # WEEKDAY_PATTERN — scan the next few matching weeks
        window = DateWindow(now + timedelta(days=2),
                            now + timedelta(days=2 + 7 * WEEKDAY_SEARCH_WEEKS))
        notes.append(
            f"weekday pattern '{phrase.evidence}' — scanning {WEEKDAY_SEARCH_WEEKS} "
            f"candidate weeks in {window}")
    return window


# ---------------------------------------------------------------------------
# Destination resolution
# ---------------------------------------------------------------------------

def _resolve_destinations(spec: TripSpec, profile: TravelerProfile, store: FlightStore,
                          window: DateWindow, notes: list[str]) -> list[str]:
    dests: list[str] = []
    for name in spec.destination_names:
        iata = resolve_city(name)
        if iata is None:
            notes.append(f"could not resolve '{name}' to an airport — skipped")
        elif iata != profile.home_airport:
            dests.append(iata)
    if dests:
        return dests

    if spec.region:
        pool = REGIONS[spec.region] - {profile.home_airport}
        ranked = sorted(
            pool,
            key=lambda d: (-_flights_in_window(store, profile.home_airport, d, window), d),
        )
        picked = ranked[:REGION_TRIP_CITIES]
        names = ", ".join(f"{AIRPORTS[p].city} ({p})" for p in picked)
        notes.append(
            f"'{spec.region}' region trip — picked {names} by flight availability "
            f"from {profile.home_airport} in the window")
        return picked

    raise ValueError(f"no destination found in request: {spec.raw_text!r}")


def _flights_in_window(store: FlightStore, origin: str, dest: str, window: DateWindow) -> int:
    flights = store.flights_for_route(origin, dest)
    if flights.empty:
        return 0
    dates = flights["departure_date_local"]
    return int(((dates >= window.start) & (dates <= window.end)).sum())


# ---------------------------------------------------------------------------
# Request signals -> profile adjustments
# ---------------------------------------------------------------------------

_WEIGHT_BUMPS = {
    ("budget", "minimize"): ("price", 0.15),
    ("trip_purpose", "business"): ("time", 0.05),
    ("stops", "avoid"): ("convenience", 0.10),
    ("comfort", "prioritize"): ("comfort", 0.10),
}


def _apply_request_signals(profile: TravelerProfile,
                           signals: list[PreferenceSignal]) -> TravelerProfile:
    """Copy of the profile with request-level evidence folded in."""
    if not signals:
        return profile
    weights, rationale = profile.weights, list(profile.weight_rationale)
    for s in signals:
        bump = _WEIGHT_BUMPS.get((s.dimension, s.value))
        if bump:
            key, delta = bump
            weights = weights.adjusted(**{key: delta})
            rationale.append(f"{key} +{delta:.2f}: request says '{s.evidence}'")
    return replace(profile, weights=weights, weight_rationale=rationale,
                   signals=profile.signals + signals)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def resolve(spec: TripSpec, profile: TravelerProfile, store: FlightStore,
            now: date = config.SIMULATED_NOW) -> ResolvedTrip:
    notes: list[str] = []
    adjusted = _apply_request_signals(profile, spec.signals)

    window = _resolve_window(spec, adjusted, now, notes)
    destinations = _resolve_destinations(spec, adjusted, store, window, notes)

    if spec.multi_city or len(destinations) > 1:
        trip_type = "multi_city"
    elif spec.round_trip:
        trip_type = "round_trip"
    else:
        trip_type = "one_way"

    weekday = None
    stay_days = STAY_DAYS_MULTI_CITY if trip_type == "multi_city" else STAY_DAYS_ROUND_TRIP
    phrase = spec.date_phrase
    if phrase and phrase.kind is DateKind.WEEKDAY_PATTERN:
        weekday = WeekdayConstraints(phrase.out_weekday, phrase.return_weekday)
        if phrase.out_weekday is not None and phrase.return_weekday is not None:
            stay_days = (phrase.return_weekday - phrase.out_weekday) % 7
            notes.append(
                f"stay derived from weekday pattern: {stay_days} nights "
                f"({calendar.day_name[phrase.out_weekday]} -> "
                f"{calendar.day_name[phrase.return_weekday]})")

    return ResolvedTrip(
        origin=profile.home_airport,
        destinations=destinations,
        trip_type=trip_type,
        depart_window=window,
        weekday=weekday,
        stay_days=stay_days,
        advise_only=spec.advise_only,
        profile=adjusted,
        request_signals=spec.signals,
        notes=notes,
    )
