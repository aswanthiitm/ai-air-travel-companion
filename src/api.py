"""FastAPI layer: the deterministic backend serialized for the flight-deck UI.

Thin by design — no logic lives here. Endpoints expose exactly what the
engines already produce (profiles with evidence, funnel counts, audited
concessions, Worth-It math) as JSON.

Run:  python3 -m uvicorn src.api:app --port 8010
"""
from __future__ import annotations

import os
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
from .serializers import (_explanation_json, _itinerary_json, _profile_json,
                          _recommendation_json, _signal_json, _tradeoff_json, _trip_json)
from .travel_intelligence import PlanOutcome, TravelIntelligenceAgent
from .traveler_profile import TravelerProfile, Weights, build_all_profiles
from .twin_store import DEFAULT_DB, TwinStore

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


@lru_cache(maxsize=1)
def _agent_state():
    store, users, _ = _state()
    twin = TwinStore(users, db_path=os.environ.get("TWIN_DB_PATH", DEFAULT_DB))
    return twin, TravelIntelligenceAgent(store, twin)


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


# ---------------------------------------------------------------------------
# v4: AI-assisted planning, feedback, and the Living Twin
# ---------------------------------------------------------------------------

class PlanBody(BaseModel):
    user_id: str
    conversation_id: str | None = None
    fields: dict | None = None      # origin/destination/dates/travellers/cabin/budget
    message: str | None = None      # the natural-language box


def _outcome_json(out: PlanOutcome) -> dict:
    body = {
        "status": out.status,
        "simulated_now": config.SIMULATED_NOW.isoformat(),
        "question": out.question,
        "missing": out.missing,
        "errors": out.errors,
        "slots": out.slots,
        "narrative": out.narrative,
        "llm_used": out.llm_used,
        "grounding_violations": out.grounding_violations,
        "twin_updates": out.twin_updates,
        "trace": out.trace,
    }
    if out.status == "complete":
        body.update({
            "reasoning": {
                "intent": out.reasoning.intent,
                "purpose": out.reasoning.purpose,
                "strategy": out.reasoning.strategy,
                "strategy_rationale": out.reasoning.strategy_rationale,
                "contradictions": out.reasoning.contradictions,
                "planning_context": out.reasoning.planning_context,
                "refinements": out.reasoning.refinements,
            },
            "profile": _profile_json(out.trip.profile),
            "trip": _trip_json(out.trip),
            "recommendation": _recommendation_json(out.recommendation),
            "explanation": _explanation_json(out.explanation),
        })
    return body


@app.post("/api/plan")
def post_plan(body: PlanBody) -> dict:
    _, agent = _agent_state()
    _, _, profiles = _state()
    if body.user_id not in profiles:
        raise HTTPException(404, f"unknown user {body.user_id}")
    if not (body.fields or (body.message or "").strip()):
        raise HTTPException(400, "provide form fields, a message, or both")
    out = agent.plan(body.user_id, fields=body.fields,
                     message=(body.message or "").strip(),
                     conversation_id=body.conversation_id)
    if out.status == "error":
        raise HTTPException(400, "; ".join(out.errors))
    return _outcome_json(out)


class FeedbackBody(BaseModel):
    user_id: str
    conversation_id: str | None = None
    event_type: str
    payload: dict = {}


@app.post("/api/feedback")
def post_feedback(body: FeedbackBody) -> dict:
    twin, _ = _agent_state()
    try:
        changes = twin.record(body.user_id, body.event_type, body.payload,
                              body.conversation_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"twin_updates": [c.as_dict() for c in changes]}


@app.get("/api/twin/{user_id}")
def get_twin(user_id: str) -> dict:
    twin, _ = _agent_state()
    _, _, profiles = _state()
    if user_id not in profiles:
        raise HTTPException(404, f"unknown user {user_id}")
    return {
        "profile": _profile_json(twin.effective_profile(user_id)),
        "baseline_weights": profiles[user_id].weights.as_dict(),
        "changelog": twin.changelog(user_id),
        "event_count": twin.event_count(user_id),
    }


@app.delete("/api/twin/{user_id}")
def reset_twin(user_id: str) -> dict:
    """Demo convenience: forget everything learned live for this traveler."""
    twin, _ = _agent_state()
    twin.reset(user_id)
    return {"status": "reset"}
