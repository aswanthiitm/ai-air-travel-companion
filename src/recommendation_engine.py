"""Recommendation engine: ResolvedTrip -> RecommendationSet.

Deterministic core of the Traveler Twin. Four stages:

1. Candidate generation per leg (route + date window + always-on filters).
2. A two-level relaxation ladder when nothing satisfies the traveler:
   inner = preference concessions in priority order (airlines -> stops ->
   layover cap -> weekday pattern -> cabin floor), outer = date-window
   widening. Trying the asked dates with relaxed preferences BEFORE moving
   dates keeps concessions minimal; every applied step is recorded and
   becomes an explanation sentence ("honest negotiation").
3. Weighted scoring with the profile's weights; itineraries for multi-leg
   trips are assembled with a small beam search over per-leg candidates.
4. Named alternatives (cheapest / fastest / most convenient) with computed
   deltas and Worth-It math against the traveler's revealed value of time.

The funnel (dataset -> route -> dates -> ... -> assembled) is recorded for
the winning attempt and feeds the UI's Reasoning Funnel directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from itertools import permutations

import pandas as pd

from . import config
from .inference_engine import DateWindow, ResolvedTrip
from .preprocessing import FlightStore
from .traveler_profile import TravelerProfile

CABIN_ORDER = ["Economy", "Premium Economy", "Business", "First"]

BEAM_PER_LEG = 6       # candidates kept per partial itinerary per leg
BEAM_PARTIALS = 10     # partial itineraries kept between legs
POOL_CAP = 20          # assembled itineraries kept for ranking/alternatives
STAY_SLACK_BEFORE = 2  # next leg may depart stay_days-2 .. stay_days+11 after arrival
STAY_SLACK_AFTER = 11  # generous: the dataset averages ~2 flights/route/month
HOMEBOUND_SLACK_AFTER = 25  # the last leg home may come later still — the data
                            # clusters routes into disjoint months, and coming home
                            # late beats not coming home at all (audited honestly)
DATE_FIT_SCALE_DAYS = 120  # graded penalty for dates outside the asked window
DATE_FIT_MAX = 2.0         # so "nearest available" beats "cheapest, 8 months away"


# ---------------------------------------------------------------------------
# Relaxation ladder
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RelaxState:
    use_airlines: bool = True
    extra_stops: int = 0
    layover_multiplier: float = 1.0
    layover_uncapped: bool = False
    stay_stretch_days: int = 0  # extends per-city stay windows (multi-leg only)
    use_weekday: bool = True
    use_cabin_floor: bool = True


# (state transform, human-readable concession) — applied cumulatively.
LADDER: list[tuple[dict, str]] = [
    ({"use_airlines": False}, "searched beyond the preferred airlines"),
    ({"extra_stops": 1}, "allowed one more stop than the traveler prefers"),
    ({"extra_stops": 2}, "allowed up to two stops"),
    ({"layover_multiplier": 2.0}, "raised the layover cap to 2x the traveler's usual limit"),
    ({"layover_uncapped": True}, "removed the layover cap entirely"),
    ({"stay_stretch_days": 16}, "allowed longer city stays (up to ~4 weeks) to align with "
                                "available flight dates"),
    ({"use_weekday": False}, "relaxed the requested weekday pattern"),
    ({"use_cabin_floor": False}, "searched below the traveler's usual cabin"),
]


def _ladder_states() -> list[tuple[RelaxState, list[str]]]:
    """Cumulative relax states: strict first, then one more concession each."""
    states = [(RelaxState(), [])]
    current, concessions = RelaxState(), []
    for changes, description in LADDER:
        current = replace(current, **changes)
        concessions = concessions + [description]
        states.append((current, concessions))
    return states


def _windows(base: DateWindow, now: date, horizon_end: date) -> list[tuple[DateWindow, str | None]]:
    """The asked window, then progressively widened versions (outer ladder)."""
    floor = now + timedelta(days=1)

    def widen(days: int) -> DateWindow:
        return DateWindow(max(floor, base.start - timedelta(days=days)),
                          min(horizon_end, base.end + timedelta(days=days)))

    return [
        (base, None),
        (widen(14), "widened the date window by ±2 weeks (no options in the asked dates)"),
        (widen(42), "widened the date window by ±6 weeks"),
        (DateWindow(floor, horizon_end), "searched all available dates in the dataset"),
    ]


# ---------------------------------------------------------------------------
# Per-leg candidate filtering (funnel-aware)
# ---------------------------------------------------------------------------

FUNNEL_STAGES = ["all flights", "on this route", "in the date window", "enough seats",
                 "cabin", "preferred airlines", "stops", "layover", "weekday pattern"]


def _cabin_floor(p: TravelerProfile) -> int:
    """Strict-cabin floor. The corpus evidence is 'first or business only' /
    'always book business', so even First-preferring travelers accept
    Business — the floor is Business, never First."""
    return min(CABIN_ORDER.index(p.soft.cabin), CABIN_ORDER.index("Business"))


def _leg_candidates(store: FlightStore, origin: str, dest: str, window: DateWindow | None,
                    trip: ResolvedTrip, state: RelaxState, leg_role: str,
                    funnel: list[tuple[str, int]] | None = None) -> pd.DataFrame:
    """Filter one leg's pool (window=None skips the date filter, for caching);
    optionally record the funnel stage counts."""
    p = trip.profile

    def stage(label: str, df: pd.DataFrame) -> pd.DataFrame:
        if funnel is not None:
            funnel.append((label, len(df)))
        return df

    stage("all flights", store.flights)
    df = stage("on this route", store.flights_for_route(origin, dest))

    if window is not None:
        dates = df["departure_date_local"]
        df = df[(dates >= window.start) & (dates <= window.end)]
    df = stage("in the date window", df)
    df = stage("enough seats", df[df["seats_available"] >= p.hard.required_seats])

    if state.use_cabin_floor and p.hard.cabin_strict:
        df = df[df["cabin_class"].map(CABIN_ORDER.index) >= _cabin_floor(p)]
    df = stage("cabin", df)

    if state.use_airlines and p.soft.airlines:
        df = df[df["airline_code"].isin(p.soft.airlines)]
    df = stage("preferred airlines", df)

    max_stops = min(2, p.soft.max_stops + state.extra_stops)
    df = stage("stops", df[df["stops"] <= max_stops])

    connecting = df["stops"] > 0
    ok = ~connecting
    if not state.layover_uncapped:
        cap = p.hard.max_layover_minutes * state.layover_multiplier
        ok = ok | (df["layover_minutes"] <= cap)
    else:
        ok = ok | connecting
    # minimum-connection floor is never relaxed: it protects the traveler
    floor_ok = ~connecting | (df["layover_minutes"] >= p.hard.min_layover_minutes * df["stops"])
    df = stage("layover", df[ok & floor_ok])

    if state.use_weekday and trip.weekday:
        if leg_role == "outbound" and trip.weekday.out_weekday is not None:
            arrive = df["arrival_utc"].dt.weekday
            df = df[arrive.isin({trip.weekday.out_weekday, (trip.weekday.out_weekday - 1) % 7})]
        if leg_role == "return" and trip.weekday.return_weekday is not None:
            depart = df["departure_date_local"].map(lambda d: d.weekday())
            df = df[depart == trip.weekday.return_weekday]
    df = stage("weekday pattern", df)
    return df


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _norm(series: pd.Series) -> pd.Series:
    lo, hi = series.min(), series.max()
    return (series - lo) / (hi - lo) if hi > lo else series * 0.0


def _score_pool(df: pd.DataFrame, trip: ResolvedTrip,
                alliance_of: dict[str, str]) -> pd.DataFrame:
    """Add per-component and weighted score columns (0 = best) to a leg pool."""
    p, w = trip.profile, trip.profile.weights
    out = df.copy()

    stops_c = out["stops"] / 2
    layover_c = (out["layover_minutes"] / 360).clip(upper=1.0)
    dep_fit = (0.0 if p.soft.departure_time is None
               else (out["time_of_day"] != p.soft.departure_time).astype(float))
    redeye_c = out["is_redeye"].astype(float) if p.soft.redeye_policy == "avoid" else 0.0
    otp_c = ((96 - out["on_time_performance"]) / 32).clip(0, 1)
    dates = out["departure_date_local"]
    days_out = (dates.map(lambda d: (trip.depart_window.start - d).days).clip(lower=0)
                + dates.map(lambda d: (d - trip.depart_window.end).days).clip(lower=0))
    date_fit = (days_out / DATE_FIT_SCALE_DAYS).clip(upper=DATE_FIT_MAX)
    out["convenience_c"] = (0.30 * stops_c + 0.20 * layover_c + 0.15 * dep_fit
                            + 0.10 * redeye_c + 0.10 * otp_c + 0.15 * date_fit)

    cabin_dist = (out["cabin_class"].map(CABIN_ORDER.index)
                  - CABIN_ORDER.index(p.soft.cabin)).abs() / 3
    bag_pen = (~out["baggage_included"]).astype(float) if p.soft.checked_bags > 0 else 0.0
    refund_pen = (~out["refundable"]).astype(float) if p.trip_purpose == "business" else 0.0
    out["comfort_c"] = 0.60 * cabin_dist + 0.25 * bag_pen + 0.15 * refund_pen

    preferred = out["airline_code"].isin(p.soft.airlines)
    pref_alliances = {alliance_of[a] for a in p.soft.airlines
                      if alliance_of.get(a, "none") != "none"}
    same_alliance = out["airline_code"].map(alliance_of).isin(pref_alliances)
    out["loyalty_c"] = 1.0 - preferred.astype(float) - 0.6 * (same_alliance & ~preferred)

    out["leg_score"] = (w.price * _norm(out["price"]) + w.time * _norm(out["duration_minutes"])
                        + w.convenience * out["convenience_c"] + w.comfort * out["comfort_c"]
                        + w.loyalty * out["loyalty_c"])
    return out


# ---------------------------------------------------------------------------
# Itinerary assembly
# ---------------------------------------------------------------------------

@dataclass
class Itinerary:
    legs: list[pd.Series]
    total_price: float
    total_minutes: int
    convenience: float  # mean of leg components
    comfort: float
    loyalty: float
    score: float = 0.0
    annotations: list[dict] = field(default_factory=list)

    @property
    def flight_ids(self) -> list[str]:
        return [leg["flight_id"] for leg in self.legs]

    @property
    def max_stops(self) -> int:
        return max(leg["stops"] for leg in self.legs)

    @property
    def scarce(self) -> bool:
        return min(leg["seats_available"] for leg in self.legs) <= 3


def _leg_sequence(trip: ResolvedTrip, order: tuple[str, ...],
                  include_return: bool = True) -> list[tuple[str, str]]:
    stops = [trip.origin, *order]
    if include_return and trip.trip_type in ("round_trip", "multi_city"):
        stops.append(trip.origin)
    return list(zip(stops, stops[1:]))


def _leg_role(trip: ResolvedTrip, index: int, total: int) -> str:
    if index == 0:
        return "outbound"
    if trip.trip_type == "round_trip" and index == total - 1:
        return "return"
    return "mid"


def _destination_orders(trip: ResolvedTrip, store: FlightStore) -> list[tuple[str, ...]]:
    if trip.trip_type != "multi_city" or len(trip.destinations) == 1:
        return [tuple(trip.destinations)]
    valid = [
        p for p in permutations(trip.destinations)
        if all(store.route_exists(o, d) for o, d in _leg_sequence(trip, p))
    ]
    return valid or list(permutations(trip.destinations))


def _diverse_top(pool: pd.DataFrame, k: int) -> pd.DataFrame:
    """Best k candidates, at most one per departure date first.

    With ~2 flights/route/month, a beam of same-date picks shares one (often
    empty) window for the next leg and the whole chain dies; date diversity
    is what makes multi-leg assembly survive this dataset's sparsity.
    """
    ranked = pool.sort_values("leg_score")
    picks = ranked.drop_duplicates("departure_date_local").head(k)
    if len(picks) < k:
        rest = ranked.drop(picks.index).head(k - len(picks))
        picks = pd.concat([picks, rest])
    return picks


def _cached_pool(store: FlightStore, origin: str, dest: str, trip: ResolvedTrip,
                 state: RelaxState, role: str, alliance_of: dict[str, str],
                 cache: dict) -> pd.DataFrame:
    """Fully filtered + scored pool for a leg, date-unbounded (cached)."""
    key = (origin, dest, state, role)
    if key not in cache:
        df = _leg_candidates(store, origin, dest, None, trip, state, role)
        cache[key] = _score_pool(df, trip, alliance_of) if not df.empty else df
    return cache[key]


def _stay_window(trip: ResolvedTrip, arrival, is_final_leg: bool, stretch: int = 0) -> DateWindow:
    lo = max(trip.stay_days - STAY_SLACK_BEFORE, 1)
    hi = trip.stay_days + (HOMEBOUND_SLACK_AFTER if is_final_leg else STAY_SLACK_AFTER) + stretch
    return DateWindow(arrival.date() + timedelta(days=lo), arrival.date() + timedelta(days=hi))


def _connectable(base: pd.DataFrame, arrival, window: DateWindow) -> pd.DataFrame:
    """Rows of `base` reachable after `arrival` within the stay window."""
    dates = base["departure_date_local"]
    mask = (dates >= window.start) & (dates <= window.end) & (base["departure_utc"] > arrival)
    return base[mask]


def _feasible_bases(bases: list[pd.DataFrame], trip: ResolvedTrip,
                    stretch: int = 0) -> list[pd.DataFrame] | None:
    """Backward feasibility propagation over the leg pools.

    The dataset clusters each route's flights into disjoint months, so a
    forward beam alone fills up with branches that only fail at the last
    homebound leg. Working backward, keep exactly the flights from which the
    rest of the chain can still be completed — if any chain exists at all,
    the beam will find one. Pools are ~10^1-10^2 rows, so the quadratic
    row-vs-next-pool check is trivial.
    """
    n = len(bases)
    feasible = [bases[-1]]
    for i in range(n - 2, -1, -1):
        nxt = feasible[0]
        if nxt.empty:
            return None
        nxt_dates, nxt_deps = nxt["departure_date_local"], nxt["departure_utc"]
        lo = max(trip.stay_days - STAY_SLACK_BEFORE, 1)
        hi = (trip.stay_days + stretch
              + (HOMEBOUND_SLACK_AFTER if i + 1 == n - 1 else STAY_SLACK_AFTER))

        def can_continue(arrival) -> bool:
            d0 = arrival.date() + timedelta(days=lo)
            d1 = arrival.date() + timedelta(days=hi)
            return bool(((nxt_dates >= d0) & (nxt_dates <= d1) & (nxt_deps > arrival)).any())

        kept = bases[i][bases[i]["arrival_utc"].map(can_continue)]
        if kept.empty:
            return None
        feasible.insert(0, kept)
    return feasible


def _assemble(store: FlightStore, trip: ResolvedTrip, order: tuple[str, ...],
              window: DateWindow, state: RelaxState, alliance_of: dict[str, str],
              cache: dict, include_return: bool = True) -> list[list[pd.Series]]:
    """Beam search over per-leg candidate pools pruned to completable flights."""
    legs = _leg_sequence(trip, order, include_return)
    feas_key = ("feasible", order, state, include_return)
    if feas_key in cache:
        bases = cache[feas_key]
    else:
        bases = [
            _cached_pool(store, o, d, trip, state, _leg_role(trip, i, len(legs)),
                         alliance_of, cache)
            for i, (o, d) in enumerate(legs)
        ]
        if any(b.empty for b in bases):
            bases = None
        elif len(legs) > 1:
            bases = _feasible_bases(bases, trip, state.stay_stretch_days)
        cache[feas_key] = bases
    if bases is None:
        return []

    partials: list[tuple[list[pd.Series], float]] = [([], 0.0)]
    for i in range(len(legs)):
        is_final = i == len(legs) - 1
        grown: list[tuple[list[pd.Series], float]] = []
        for chosen, score_sum in partials:
            if i == 0:
                dates = bases[0]["departure_date_local"]
                pool = bases[0][(dates >= window.start) & (dates <= window.end)]
            else:
                pool = _connectable(bases[i], chosen[-1]["arrival_utc"],
                                    _stay_window(trip, chosen[-1]["arrival_utc"], is_final,
                                                 state.stay_stretch_days))
            if pool.empty:
                continue
            for _, row in _diverse_top(pool, BEAM_PER_LEG).iterrows():
                grown.append((chosen + [row], score_sum + row["leg_score"]))
        # keep the beam date-diverse at the partial level too
        grown.sort(key=lambda t: t[1] / (i + 1))
        seen_dates, partials, overflow = set(), [], []
        cap = POOL_CAP if is_final else BEAM_PARTIALS
        for chosen, score_sum in grown:
            key = chosen[-1]["departure_date_local"]
            (partials if key not in seen_dates else overflow).append((chosen, score_sum))
            seen_dates.add(key)
        partials = (partials + overflow)[:cap]
        if not partials:
            return []
    return [chosen for chosen, _ in partials]


# ---------------------------------------------------------------------------
# Final ranking, alternatives, annotations
# ---------------------------------------------------------------------------

def _build_itinerary(legs: list[pd.Series], store: FlightStore) -> Itinerary:
    annotations = []
    for leg in legs:
        uplift = store.seasonal_uplift(leg["origin"], leg["destination"], leg["season"])
        annotations.append({
            "flight_id": leg["flight_id"],
            "route": f"{leg['origin']}-{leg['destination']}",
            "season": leg["season"],
            "seasonal_uplift": None if uplift is None else round(uplift, 3),
            "is_holiday_season": bool(leg["is_holiday_season"]),
            "seats_available": int(leg["seats_available"]),
            "is_redeye": bool(leg["is_redeye"]),
        })
    return Itinerary(
        legs=legs,
        total_price=round(float(sum(leg["price"] for leg in legs)), 2),
        total_minutes=int(sum(leg["duration_minutes"] for leg in legs)),
        convenience=float(pd.Series([leg["convenience_c"] for leg in legs]).mean()),
        comfort=float(pd.Series([leg["comfort_c"] for leg in legs]).mean()),
        loyalty=float(pd.Series([leg["loyalty_c"] for leg in legs]).mean()),
        annotations=annotations,
    )


def _rank(pool: list[Itinerary], profile: TravelerProfile) -> list[Itinerary]:
    w = profile.weights
    prices = pd.Series([it.total_price for it in pool])
    minutes = pd.Series([it.total_minutes for it in pool])
    price_n, minutes_n = _norm(prices), _norm(minutes)
    for i, it in enumerate(pool):
        it.score = round(float(w.price * price_n[i] + w.time * minutes_n[i]
                               + w.convenience * it.convenience + w.comfort * it.comfort
                               + w.loyalty * it.loyalty), 4)
    return sorted(pool, key=lambda it: it.score)


@dataclass
class TradeOff:
    label: str  # cheapest | fastest | most_convenient
    itinerary: Itinerary
    delta_price: float       # vs top pick (negative = saves money)
    delta_minutes: int       # vs top pick (negative = saves time)
    worth_it: dict | None    # Worth-It math when a value of time is known


def _worth_it(delta_price: float, delta_minutes: int, vot: float | None) -> dict | None:
    if vot is None or (delta_price == 0 and delta_minutes == 0):
        return None
    savings, extra_hours = -delta_price, delta_minutes / 60
    time_cost = round(extra_hours * vot, 2)
    return {
        "savings_usd": round(savings, 2),
        "extra_hours": round(extra_hours, 2),
        "value_of_time_usd_per_hr": vot,
        "time_cost_usd": time_cost,
        "verdict": "worth_it" if savings > time_cost else "not_worth_it",
    }


def _alternatives(ranked: list[Itinerary], profile: TravelerProfile) -> list[TradeOff]:
    top, vot = ranked[0], profile.flexibility.value_of_time_usd_per_hr
    picks = {
        "cheapest": min(ranked, key=lambda it: it.total_price),
        "fastest": min(ranked, key=lambda it: it.total_minutes),
        "most_convenient": min(ranked, key=lambda it: it.convenience),
    }
    out = []
    for label, it in picks.items():
        if it.flight_ids == top.flight_ids:
            continue
        dp = round(it.total_price - top.total_price, 2)
        dm = int(it.total_minutes - top.total_minutes)
        out.append(TradeOff(label, it, dp, dm, _worth_it(dp, dm, vot)))
    return out


def _actual_concessions(top: Itinerary, trip: ResolvedTrip) -> list[str]:
    """Audit the winning itinerary against the ORIGINAL constraints.

    The ladder is linear, so the search state may be more relaxed than the
    winner needs; reporting what the top pick actually violates keeps the
    'honest negotiation' story precise ('accepted a 110-min layover, above
    your usual 90') instead of over-confessing.
    """
    p, out = trip.profile, []

    first = top.legs[0]["departure_date_local"]
    if not (trip.depart_window.start <= first <= trip.depart_window.end):
        out.append(f"no options in the asked dates ({trip.depart_window}) — "
                   f"nearest departure is {first.isoformat()}")
    if p.soft.airlines:
        off = sorted({leg["airline_code"] for leg in top.legs} - set(p.soft.airlines))
        if off:
            out.append(f"used airlines outside the preferred list ({', '.join(off)})")
    if top.max_stops > p.soft.max_stops:
        out.append(f"accepted {top.max_stops} stop(s) vs the usual max of {p.soft.max_stops}")
    worst_layover = max((int(leg["layover_minutes"]) for leg in top.legs), default=0)
    if worst_layover > p.hard.max_layover_minutes:
        out.append(f"accepted a {worst_layover}-minute layover, above the usual "
                   f"{p.hard.max_layover_minutes}-minute cap")
    if trip.weekday and trip.weekday.out_weekday is not None:
        arrive_wd = top.legs[0]["arrival_utc"].weekday()
        if arrive_wd not in {trip.weekday.out_weekday, (trip.weekday.out_weekday - 1) % 7}:
            out.append("could not match the requested arrival weekday")
    if trip.weekday and trip.weekday.return_weekday is not None and len(top.legs) > 1:
        ret_wd = top.legs[-1]["departure_date_local"].weekday()
        if ret_wd != trip.weekday.return_weekday:
            out.append("could not match the requested return weekday")
    if p.hard.cabin_strict:
        floor = _cabin_floor(p)
        if any(CABIN_ORDER.index(leg["cabin_class"]) < floor for leg in top.legs):
            out.append(f"booked below the usual {CABIN_ORDER[floor]} cabin")
    for prev, nxt in zip(top.legs, top.legs[1:]):
        gap = (nxt["departure_date_local"] - prev["arrival_utc"].date()).days
        if gap > trip.stay_days + STAY_SLACK_AFTER:
            out.append(f"stayed {gap} days in {prev['destination_city']} to align with "
                       "available flight dates")
    if (trip.trip_type == "multi_city" and trip.destinations
            and top.legs[-1]["destination"] != trip.origin):
        out.append(f"no complete loop home exists in this dataset — the itinerary ends in "
                   f"{top.legs[-1]['destination_city']}; the final leg home must be booked "
                   "separately")
    return out


@dataclass
class RecommendationSet:
    trip: ResolvedTrip
    top: Itinerary | None
    ranked: list[Itinerary]            # full pool for UI re-ranking sliders
    alternatives: list[TradeOff]
    relaxations: list[str]             # concessions made, in order
    window_used: DateWindow
    funnel: list[tuple[str, int]]      # winning attempt, first leg + assembled
    feasible: bool = True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def recommend(trip: ResolvedTrip, store: FlightStore,
              now: date = config.SIMULATED_NOW) -> RecommendationSet:
    alliance_of = dict(store.flights[["airline_code", "alliance"]]
                       .drop_duplicates().itertuples(index=False, name=None))
    horizon_end = store.flights["departure_date_local"].max()
    orders = _destination_orders(trip, store)
    cache: dict = {}

    # Pass 1: complete trips. Pass 2 (multi-city only): open-jaw — better an
    # honest partial itinerary than an INFEASIBLE.
    passes = [True] + ([False] if trip.trip_type == "multi_city" else [])
    for include_return in passes:
        for window, window_note in _windows(trip.depart_window, now, horizon_end):
            for state, _concessions in _ladder_states():
                assembled: list[list[pd.Series]] = []
                for order in orders:
                    assembled.extend(_assemble(store, trip, order, window, state,
                                               alliance_of, cache, include_return))
                if not assembled:
                    continue

                pool = _rank([_build_itinerary(legs, store) for legs in assembled],
                             trip.profile)[:POOL_CAP]
                relaxations = _actual_concessions(pool[0], trip)
                funnel: list[tuple[str, int]] = []
                first_leg = _leg_sequence(trip, orders[0])[0]
                _leg_candidates(store, first_leg[0], first_leg[1], window, trip, state,
                                "outbound", funnel)
                funnel.append(("assembled itineraries", len(pool)))
                return RecommendationSet(
                    trip=trip, top=pool[0], ranked=pool,
                    alternatives=_alternatives(pool, trip.profile),
                    relaxations=relaxations, window_used=window, funnel=funnel,
                )

    return RecommendationSet(
        trip=trip, top=None, ranked=[], alternatives=[],
        relaxations=["no itinerary exists in this dataset even with every constraint relaxed"],
        window_used=trip.depart_window, funnel=[], feasible=False,
    )
