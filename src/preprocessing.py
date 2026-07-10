"""Cleaning, enrichment, and indexing of the raw datasets.

Three public entry points:

    enrich_flights(df)      -> flights with derived columns (local time,
                               time-of-day bucket, redeye flag, route key, legs)
    build_flight_store(df)  -> FlightStore: O(1) route lookups + per-route
                               seasonal price statistics
    parse_users(df)         -> users with list/struct fields normalized

`validate_flights` / `validate_users` assert the dataset invariants the whole
system depends on; they run in the test suite and can be called at load time.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .airports import AIRPORTS

# Local-hour buckets for departure-time preferences ("morning departures").
TIME_OF_DAY_BUCKETS = {
    "morning": range(5, 12),
    "afternoon": range(12, 17),
    "evening": range(17, 22),
}
# Anything else (22:00-04:59 local) is "night" and counts as a redeye.


def _time_of_day(hour: int) -> str:
    for bucket, hours in TIME_OF_DAY_BUCKETS.items():
        if hour in hours:
            return bucket
    return "night"


def enrich_flights(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of the flights frame with derived columns added."""
    out = df.copy()
    out["route"] = out["origin"] + "-" + out["destination"]

    # Pre-flattened multi-leg itineraries: ';'-separated fields -> lists.
    out["flight_numbers_list"] = out["flight_numbers"].str.split(";")
    out["layover_airports_list"] = (
        out["layover_airports"].fillna("").str.split(";").map(lambda x: [] if x == [""] else x)
    )

    # Times are UTC; departure-time preferences are local. Convert with the
    # airports' fixed standard-time offsets (DST ignored — see README).
    offsets = out["origin"].map(lambda a: AIRPORTS[a].utc_offset_hours)
    local_dep = out["departure_utc"] + pd.to_timedelta(offsets, unit="h")
    out["departure_local_hour"] = local_dep.dt.hour
    out["departure_date_local"] = local_dep.dt.date
    out["time_of_day"] = out["departure_local_hour"].map(_time_of_day)
    out["is_redeye"] = out["time_of_day"] == "night"

    out["price_per_hour"] = out["price"] / (out["duration_minutes"] / 60.0)
    return out


@dataclass
class FlightStore:
    """Enriched flights plus the lookup structures every engine will share."""

    flights: pd.DataFrame
    od_pairs: set[tuple[str, str]] = field(default_factory=set)
    _route_groups: dict[tuple[str, str], np.ndarray] = field(default_factory=dict)
    _season_median: dict[tuple[str, str, str], float] = field(default_factory=dict)

    def flights_for_route(self, origin: str, destination: str) -> pd.DataFrame:
        """All offers for an OD pair, sorted by departure (empty if none)."""
        idx = self._route_groups.get((origin, destination))
        if idx is None:
            return self.flights.iloc[0:0]
        return self.flights.iloc[idx]

    def route_exists(self, origin: str, destination: str) -> bool:
        return (origin, destination) in self.od_pairs

    def seasonal_uplift(self, origin: str, destination: str, season: str) -> float | None:
        """Median price of `season` vs the route's shoulder baseline.

        Returns e.g. +0.48 for "48% above shoulder", or None when either
        median is unavailable for this route.
        """
        seasonal = self._season_median.get((origin, destination, season))
        baseline = self._season_median.get((origin, destination, "shoulder"))
        if seasonal is None or not baseline:
            return None
        return seasonal / baseline - 1.0


def build_flight_store(flights: pd.DataFrame) -> FlightStore:
    """Index an *enriched* flights frame (positions, so .iloc stays valid)."""
    df = flights.sort_values("departure_utc").reset_index(drop=True)
    store = FlightStore(flights=df)

    grouped = df.groupby(["origin", "destination"], sort=False)
    store._route_groups = {od: idx.to_numpy() for od, idx in grouped.groups.items()}
    store.od_pairs = set(store._route_groups)

    medians = df.groupby(["origin", "destination", "season"])["price"].median()
    store._season_median = medians.to_dict()
    return store


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------

_BAGGAGE_CHECKED = re.compile(r"(\d+)\s*checked")


def parse_baggage(text: str) -> dict:
    """'2 checked + stroller' -> {'checked_bags': 2, 'stroller': True}."""
    m = _BAGGAGE_CHECKED.search(text or "")
    return {
        "checked_bags": int(m.group(1)) if m else 0,
        "stroller": "stroller" in (text or "").lower(),
    }


def parse_users(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with list/struct fields normalized for downstream use."""
    out = df.copy()
    out["preferred_airlines_list"] = (
        out["preferred_airlines"].fillna("").str.split(";").map(lambda x: [a for a in x if a])
    )
    out["history_snippets"] = (
        out["raw_history"].fillna("").str.split("|").map(lambda parts: [p.strip() for p in parts if p.strip()])
    )
    baggage = out["baggage_preference"].map(parse_baggage)
    out["checked_bags"] = baggage.map(lambda b: b["checked_bags"])
    out["has_stroller"] = baggage.map(lambda b: b["stroller"])
    return out


# --------------------------------------------------------------------------
# Validation — the invariants everything downstream relies on.
# --------------------------------------------------------------------------


def validate_flights(df: pd.DataFrame) -> None:
    """Raise AssertionError if a core flight-data invariant is broken."""
    assert df["flight_id"].is_unique, "duplicate flight_id"
    assert not (df["origin"] == df["destination"]).any(), "self-loop route"

    elapsed = (df["arrival_utc"] - df["departure_utc"]).dt.total_seconds() / 60
    assert (elapsed - df["duration_minutes"]).abs().max() <= 1, "duration mismatch"

    direct = df["stops"] == 0
    assert (df.loc[direct, "layover_minutes"] == 0).all(), "direct flight with layover time"
    assert df.loc[direct, "layover_airports"].isna().all(), "direct flight with layover airport"
    n_layovers = df.loc[~direct, "layover_airports"].str.split(";").str.len()
    assert (n_layovers == df.loc[~direct, "stops"]).all(), "stops != layover airport count"

    unknown = set(df["origin"]) | set(df["destination"]) - set(AIRPORTS)
    assert set(df["origin"]) <= set(AIRPORTS) and set(df["destination"]) <= set(AIRPORTS), (
        f"airports missing from reference table: {unknown}"
    )
    assert (df["currency"] == "USD").all(), "non-USD fares present"
    assert (df["seats_available"].between(1, 9)).all(), "seats outside GDS 1-9 range"


def validate_users(users: pd.DataFrame, flights: pd.DataFrame) -> None:
    """Raise AssertionError if users reference data the flight set lacks."""
    assert users["user_id"].is_unique, "duplicate user_id"
    assert set(users["home_airport"]) <= set(flights["origin"]), "home airport with no departures"

    airlines = {a for s in users["preferred_airlines"].dropna() for a in s.split(";")}
    assert airlines <= set(flights["airline_code"]), "preferred airline absent from flights"
    assert set(users["preferred_cabin"]) <= set(flights["cabin_class"]), "unknown cabin class"
