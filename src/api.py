"""FastAPI layer: the deterministic backend serialized for the flight-deck UI.

Thin by design — no logic lives here. Endpoints expose exactly what the
engines already produce (profiles with evidence, funnel counts, audited
concessions, Worth-It math) as JSON.

Run:  python3 -m uvicorn src.api:app --port 8010
"""
from __future__ import annotations

from dataclasses import replace
from functools import lru_cache

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import config, data_loader, preprocessing
from .explanation_engine import Explanation, explain, render_text
from .inference_engine import ResolvedTrip, resolve
from .recommendation_engine import Itinerary, RecommendationSet, TradeOff, recommend
from .request_parser import parse_request
from .traveler_profile import TravelerProfile, Weights, build_all_profiles

app = FastAPI(title="Traveler Twin API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # hackathon prototype; the UI dev server proxies anyway
    allow_methods=["*"],
    allow_headers=["*"],
)


@lru_cache(maxsize=1)
def _state():
    store = preprocessing.build_flight_store(
        preprocessing.enrich_flights(data_loader.load_flights()))
    users = preprocessing.parse_users(data_loader.load_users())
    profiles = build_all_profiles(users)
    return store, users, profiles


# ---------------------------------------------------------------------------
# Serialization (dataclasses/pandas -> JSON-safe dicts)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/users")
def list_users() -> list[dict]:
    _, users, profiles = _state()
    out = []
    for _, row in users.iterrows():
        p = profiles[row["user_id"]]
        top_weight = max(p.weights.as_dict(), key=p.weights.as_dict().get)
        out.append({
            "user_id": row["user_id"],
            "home_city": row["home_city"],
            "home_airport": row["home_airport"],
            "age": int(row["age"]),
            "trip_purpose": row["trip_purpose"],
            "preferred_cabin": row["preferred_cabin"],
            "driver": top_weight,  # what dominates this traveler's decisions
        })
    return out


@app.get("/api/profile/{user_id}")
def get_profile(user_id: str) -> dict:
    _, _, profiles = _state()
    if user_id not in profiles:
        raise HTTPException(404, f"unknown user {user_id}")
    return _profile_json(profiles[user_id])


@app.get("/api/airports")
def list_airports() -> dict[str, dict]:
    from .airports import AIRPORTS
    return {a.iata: {"city": a.city, "country": a.country, "lat": a.lat, "lon": a.lon}
            for a in AIRPORTS.values()}


@app.get("/api/benchmarks")
def list_benchmarks() -> list[dict]:
    return [{"prompt_id": b["prompt_id"], "user_id": b["user_id"], "request": b["request"]}
            for b in data_loader.load_benchmarks()]


class RecommendBody(BaseModel):
    user_id: str
    request: str
    weights: dict[str, float] | None = None  # slider overrides, normalized here


@app.post("/api/recommend")
def post_recommend(body: RecommendBody) -> dict:
    store, _, profiles = _state()
    if body.user_id not in profiles:
        raise HTTPException(404, f"unknown user {body.user_id}")
    profile = profiles[body.user_id]

    if body.weights:
        merged = {**profile.weights.as_dict(), **{
            k: max(0.0, float(v)) for k, v in body.weights.items()
            if k in profile.weights.as_dict()}}
        total = sum(merged.values()) or 1.0
        weights = Weights(**{k: round(v / total, 3) for k, v in merged.items()})
        rationale = profile.weight_rationale + ["weights overridden from the UI sliders"]
        profile = replace(profile, weights=weights, weight_rationale=rationale)

    try:
        trip = resolve(parse_request(body.request), profile, store)
    except ValueError as e:
        raise HTTPException(400, str(e))
    rec = recommend(trip, store)
    expl = explain(rec)
    return {
        "simulated_now": config.SIMULATED_NOW.isoformat(),
        "profile": _profile_json(trip.profile),
        "trip": _trip_json(trip),
        "recommendation": _recommendation_json(rec),
        "explanation": _explanation_json(expl),
        "narrative": render_text(expl),
    }
