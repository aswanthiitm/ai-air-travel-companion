"""JSON serialization of the pipeline dataclasses (shared by API + bundle)."""
from __future__ import annotations

import pandas as pd

from .explanation_engine import Explanation
from .inference_engine import ResolvedTrip
from .recommendation_engine import Itinerary, RecommendationSet, TradeOff
from .traveler_profile import TravelerProfile

_LEG_FIELDS = [
    "flight_id", "airline_code", "airline_name", "alliance", "origin", "destination",
    "origin_city", "destination_city", "duration_minutes", "stops", "layover_minutes",
    "cabin_class", "price", "seats_available", "aircraft_type", "on_time_performance",
    "baggage_included", "refundable", "season", "is_holiday_season", "is_redeye",
    "time_of_day", "departure_local_hour",
]


def _leg_json(leg: pd.Series) -> dict:
    out = {k: leg[k] for k in _LEG_FIELDS}
    out["departure_utc"] = leg["departure_utc"].isoformat()
    out["arrival_utc"] = leg["arrival_utc"].isoformat()
    out["departure_date_local"] = leg["departure_date_local"].isoformat()
    out["layover_airports"] = list(leg["layover_airports_list"])
    out["flight_numbers"] = list(leg["flight_numbers_list"])
    # numpy scalars -> python natives
    return {k: (v.item() if hasattr(v, "item") else v) for k, v in out.items()}


def _itinerary_json(it: Itinerary) -> dict:
    return {
        "flight_ids": list(it.flight_ids),
        "legs": [_leg_json(leg) for leg in it.legs],
        "total_price": it.total_price,
        "total_minutes": it.total_minutes,
        "score": it.score,
        "components": {"convenience": round(it.convenience, 4),
                       "comfort": round(it.comfort, 4),
                       "loyalty": round(it.loyalty, 4)},
        "max_stops": int(it.max_stops),
        "scarce": bool(it.scarce),
        "annotations": it.annotations,
    }


def _tradeoff_json(alt: TradeOff) -> dict:
    return {
        "label": alt.label,
        "itinerary": _itinerary_json(alt.itinerary),
        "delta_price": alt.delta_price,
        "delta_minutes": alt.delta_minutes,
        "worth_it": alt.worth_it,
    }


def _signal_json(s) -> dict:
    return {"dimension": s.dimension, "value": s.value, "source": s.source.value,
            "evidence": s.evidence, "confidence": s.confidence}


def _profile_json(p: TravelerProfile) -> dict:
    return {
        "user_id": p.user_id,
        "home_airport": p.home_airport,
        "home_city": p.home_city,
        "trip_purpose": p.trip_purpose,
        "party": {"adults": p.party.adults, "children": p.party.children},
        "weights": p.weights.as_dict(),
        "weight_rationale": p.weight_rationale,
        "hard": {"max_layover_minutes": p.hard.max_layover_minutes,
                 "min_layover_minutes": p.hard.min_layover_minutes,
                 "required_seats": p.hard.required_seats,
                 "cabin_strict": p.hard.cabin_strict},
        "soft": {"airlines": p.soft.airlines, "cabin": p.soft.cabin,
                 "max_stops": p.soft.max_stops, "departure_time": p.soft.departure_time,
                 "redeye_policy": p.soft.redeye_policy, "checked_bags": p.soft.checked_bags,
                 "stroller": p.soft.stroller, "hub": p.soft.hub,
                 "season_policy": p.soft.season_policy},
        "flexibility": {"date_flexibility_days": p.flexibility.date_flexibility_days,
                        "multi_city_tendency": p.flexibility.multi_city_tendency,
                        "value_of_time_usd_per_hr": p.flexibility.value_of_time_usd_per_hr},
        "signals": [_signal_json(s) for s in p.signals],
        "conflicts": [{"dimension": c.dimension, "reason": c.reason,
                       "kept": _signal_json(c.kept), "discarded": _signal_json(c.discarded)}
                      for c in p.conflicts],
        "unsupported": [_signal_json(s) for s in p.unsupported],
    }


def _trip_json(trip: ResolvedTrip) -> dict:
    return {
        "origin": trip.origin,
        "destinations": trip.destinations,
        "trip_type": trip.trip_type,
        "depart_window": {"start": trip.depart_window.start.isoformat(),
                          "end": trip.depart_window.end.isoformat()},
        "stay_days": trip.stay_days,
        "advise_only": trip.advise_only,
        "notes": trip.notes,
        "request_signals": [_signal_json(s) for s in trip.request_signals],
    }


def _recommendation_json(rec: RecommendationSet) -> dict:
    return {
        "feasible": rec.feasible,
        "top": _itinerary_json(rec.top) if rec.top is not None else None,
        "ranked": [_itinerary_json(it) for it in rec.ranked],
        "alternatives": [_tradeoff_json(a) for a in rec.alternatives],
        "relaxations": rec.relaxations,
        "window_used": {"start": rec.window_used.start.isoformat(),
                        "end": rec.window_used.end.isoformat()},
        "funnel": [{"stage": label, "count": n} for label, n in rec.funnel],
    }


def _explanation_json(e: Explanation) -> dict:
    return {
        "headline": e.headline,
        "traveler_reading": e.traveler_reading,
        "why_top": e.why_top,
        "tradeoffs": e.tradeoffs,
        "concessions": e.concessions,
        "market_context": e.market_context,
        "caveats": e.caveats,
        "itinerary": e.itinerary,
        "funnel_line": e.funnel_line,
    }


