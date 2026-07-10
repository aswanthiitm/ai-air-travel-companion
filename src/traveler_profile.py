"""Traveler Profile: signals -> constraints, preferences, weights.

This is where evidence becomes decisions. The builder groups the extractor's
signals per dimension, reconciles conflicts (keeping both sides for the
explanation engine), derives normalized scoring weights, and separates what
the flight dataset can act on from what it cannot (`unsupported`).

Downstream engines consume the profile read-only via `hard`, `soft`,
`weights`, and `flexibility`; `signals`/`conflicts`/`weight_rationale` exist
for the explanation engine and the UI's Traveler DNA panel.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .preference_extractor import PreferenceSignal, Source, extract_user

# Default minimum connection time; raised for anxious connectors so we never
# recommend the tight transfers they explicitly fear.
MIN_LAYOVER_DEFAULT = 45
MIN_LAYOVER_ANXIOUS = 90


@dataclass
class Party:
    adults: int = 1
    children: int = 0

    @property
    def seats(self) -> int:
        return self.adults + self.children


@dataclass
class HardConstraints:
    max_layover_minutes: int
    min_layover_minutes: int
    required_seats: int
    cabin_strict: bool  # "first or business only" -> preferred cabin is a floor


@dataclass
class SoftPreferences:
    airlines: list[str]
    cabin: str
    max_stops: int              # 0 avoid / 1 limit / 2 accept (min of field+history)
    departure_time: str | None  # morning / evening / None
    redeye_policy: str          # avoid / accept / neutral
    checked_bags: int
    stroller: bool
    hub: str | None             # preferred connection base, e.g. "DXB"
    season_policy: str | None   # avoid_peak / peak_ok / None
    layover_tolerance: str | None  # high / avoid_long / None


@dataclass
class Weights:
    price: float
    time: float
    convenience: float
    comfort: float
    loyalty: float

    def as_dict(self) -> dict[str, float]:
        return {"price": self.price, "time": self.time, "convenience": self.convenience,
                "comfort": self.comfort, "loyalty": self.loyalty}


@dataclass
class Flexibility:
    date_flexibility_days: int
    multi_city_tendency: str
    value_of_time_usd_per_hr: float | None  # revealed preference, if extractable


@dataclass
class Conflict:
    dimension: str
    kept: PreferenceSignal
    discarded: PreferenceSignal
    reason: str


@dataclass
class TravelerProfile:
    user_id: str
    home_airport: str
    home_city: str
    trip_purpose: str
    party: Party
    hard: HardConstraints
    soft: SoftPreferences
    weights: Weights
    flexibility: Flexibility
    signals: list[PreferenceSignal] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    unsupported: list[PreferenceSignal] = field(default_factory=list)
    weight_rationale: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers over the signal list
# ---------------------------------------------------------------------------

def _by_dim(signals: list[PreferenceSignal]) -> dict[str, list[PreferenceSignal]]:
    out: dict[str, list[PreferenceSignal]] = {}
    for s in signals:
        out.setdefault(s.dimension, []).append(s)
    return out


def _history_values(dims: dict, dimension: str) -> set:
    return {s.value for s in dims.get(dimension, []) if s.source is Source.RAW_HISTORY}


# ---------------------------------------------------------------------------
# Weight derivation — every adjustment is recorded as a rationale line.
# ---------------------------------------------------------------------------

_PRICE_W = {"none": 0.05, "low": 0.15, "medium": 0.30, "high": 0.45}
_CONVENIENCE_W = {"none": 0.10, "moderate": 0.22, "strong": 0.35}
_TIME_W = {"business": 0.30, "mixed": 0.20, "leisure": 0.15}
_COMFORT_W = {"First": 0.30, "Business": 0.25, "Premium Economy": 0.18, "Economy": 0.08}


def derive_weights(user: pd.Series, dims: dict, loyalty_level: str,
                   connection_anxiety: bool) -> tuple[Weights, list[str]]:
    rationale: list[str] = []

    price = _PRICE_W[user["price_sensitivity"]]
    rationale.append(f"price {price:.2f} from price_sensitivity={user['price_sensitivity']}")
    if "minimize" in _history_values(dims, "budget"):
        price += 0.10
        rationale.append("price +0.10: history shows hard cost-minimizing behavior")
    if "unconstrained" in _history_values(dims, "budget"):
        price = min(price, 0.05)
        rationale.append("price capped at 0.05: history says cost is not a constraint")

    convenience = _CONVENIENCE_W[user["direct_preference"]]
    rationale.append(f"convenience {convenience:.2f} from direct_preference={user['direct_preference']}")
    if connection_anxiety:
        convenience += 0.08
        rationale.append("convenience +0.08: connection anxiety in history")

    time_w = _TIME_W[user["trip_purpose"]]
    rationale.append(f"time {time_w:.2f} from trip_purpose={user['trip_purpose']}")
    if dims.get("value_of_time"):
        time_w += 0.05
        rationale.append("time +0.05: history quantifies a personal value of time")

    comfort = _COMFORT_W[user["preferred_cabin"]]
    rationale.append(f"comfort {comfort:.2f} from preferred_cabin={user['preferred_cabin']}")

    loyalty = {"strong": 0.15, "preference": 0.10, "program": 0.10, "none": 0.02}[loyalty_level]
    rationale.append(f"loyalty {loyalty:.2f} from resolved loyalty level '{loyalty_level}'")

    total = price + time_w + convenience + comfort + loyalty
    w = Weights(*(round(x / total, 3) for x in (price, time_w, convenience, comfort, loyalty)))
    return w, rationale


# ---------------------------------------------------------------------------
# Conflict resolution
# ---------------------------------------------------------------------------

def _resolve_loyalty(user: pd.Series, dims: dict, conflicts: list[Conflict]) -> str:
    """Concrete behavioral evidence beats a placeholder/none field."""
    structured = next(s for s in dims["loyalty"] if s.source is Source.STRUCTURED_FIELD)
    history = [s for s in dims.get("loyalty", []) if s.source is Source.RAW_HISTORY]
    history_strong = next((s for s in history if s.value == "strong"), None)

    if structured.value == "none" and history_strong:
        conflicts.append(Conflict(
            "loyalty", kept=history_strong, discarded=structured,
            reason="profile lists no loyalty program, but booking history shows elite "
                   "status behavior — trusting the behavior",
        ))
        return "strong"
    if history_strong:
        return "strong"
    if any(s.value == "preference" for s in history):
        return "preference"
    if structured.value == "program":
        return "program"
    if any(s.value == "none" for s in history):
        return "none"
    return "none" if structured.value == "none" else "program"


def _flag_retired_age(user: pd.Series, dims: dict, conflicts: list[Conflict]) -> None:
    """Template noise in the dataset: 'retired' snippets on 20-somethings.

    Doesn't change engine behavior (the flexibility claim stands either way);
    recorded so explanations stay honest about the data quality.
    """
    retired = next((s for s in dims.get("travel_pattern", []) if s.value == "retired"), None)
    if retired and int(user["age"]) < 50:
        age_side = PreferenceSignal("travel_pattern", f"age={user['age']}",
                                    Source.STRUCTURED_FIELD, f"age={user['age']}", 0.9)
        conflicts.append(Conflict(
            "travel_pattern", kept=retired, discarded=age_side,
            reason=f"history says 'retired' but age is {user['age']} — keeping only the "
                   "date-flexibility implication, not the label",
        ))


# ---------------------------------------------------------------------------
# Profile builder
# ---------------------------------------------------------------------------

_STOPS_RANK = {"avoid": 0, "strong": 0, "limit_1": 1, "moderate": 1, "accept": 2, "none": 2}


def build_profile(user: pd.Series) -> TravelerProfile:
    """Build the Traveler Twin for one parsed user row."""
    signals = extract_user(user)
    dims = _by_dim(signals)
    conflicts: list[Conflict] = []

    loyalty_level = _resolve_loyalty(user, dims, conflicts)
    _flag_retired_age(user, dims, conflicts)
    connection_anxiety = bool(dims.get("connection_anxiety"))

    party = Party()
    for s in dims.get("party", []):
        party.children = max(party.children, s.value.get("children", 0))

    hard = HardConstraints(
        max_layover_minutes=int(user["max_layover_minutes"]),
        min_layover_minutes=MIN_LAYOVER_ANXIOUS if connection_anxiety else MIN_LAYOVER_DEFAULT,
        required_seats=party.seats,
        cabin_strict=bool(dims.get("cabin_strict")),
    )

    max_stops = min(_STOPS_RANK[s.value] for s in dims["stops"] if s.value in _STOPS_RANK)

    departure = next((s.value for s in dims.get("departure_time", [])), None)
    redeye_values = _history_values(dims, "redeye")
    redeye_policy = "avoid" if "avoid" in redeye_values else (
        "accept" if "accept" in redeye_values else "neutral")

    soft = SoftPreferences(
        airlines=list(user["preferred_airlines_list"]),
        cabin=user["preferred_cabin"],
        max_stops=max_stops,
        departure_time=departure,
        redeye_policy=redeye_policy,
        checked_bags=int(user["checked_bags"]),
        stroller=bool(user["has_stroller"]),
        hub=next(iter(_history_values(dims, "hub")), None),
        season_policy=next(iter(_history_values(dims, "season")), None),
        layover_tolerance=next(iter(_history_values(dims, "layover_tolerance")), None),
    )

    vot = [s.value for s in dims.get("value_of_time", [])]
    flexibility = Flexibility(
        date_flexibility_days=int(user["date_flexibility_days"]),
        multi_city_tendency=user["multi_city_tendency"],
        value_of_time_usd_per_hr=vot[0] if vot else None,
    )

    weights, rationale = derive_weights(user, dims, loyalty_level, connection_anxiety)

    return TravelerProfile(
        user_id=user["user_id"],
        home_airport=user["home_airport"],
        home_city=user["home_city"],
        trip_purpose=user["trip_purpose"],
        party=party,
        hard=hard,
        soft=soft,
        weights=weights,
        flexibility=flexibility,
        signals=signals,
        conflicts=conflicts,
        unsupported=dims.get("amenity", []),
        weight_rationale=rationale,
    )


def build_all_profiles(users: pd.DataFrame) -> dict[str, TravelerProfile]:
    """Profiles for every parsed user row, keyed by user_id."""
    return {row["user_id"]: build_profile(row) for _, row in users.iterrows()}
