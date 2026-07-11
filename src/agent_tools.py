"""LangChain tool wrappers over the deterministic core.

Thin adapters only — every implementation is an existing, tested function.
These power the fully agentic tool loop (`build_agent`); the default
production path is the structured orchestrator in travel_intelligence.py,
which calls the same functions directly with the LLM at fixed judgment
points (deterministic order, bounded refinements — the demo-safe choice).
Both paths share one contract: the LLM decides *when*; Python decides *what
the numbers are*.
"""
from __future__ import annotations

import json

from . import data_loader, preprocessing
from .explanation_engine import explain, render_text
from .inference_engine import resolve
from .planner_validators import validate_fields
from .recommendation_engine import recommend
from .request_parser import parse_request
from .serializers import _recommendation_json
from .twin_store import TwinStore

try:
    from langchain_core.tools import tool
    HAS_LANGCHAIN = True
except ImportError:  # pragma: no cover
    HAS_LANGCHAIN = False

    def tool(fn=None, **_kw):  # no-op decorator so the module always imports
        return fn if fn else (lambda f: f)

_STORE = None
_TWIN: TwinStore | None = None


def init_tools(store, twin: TwinStore) -> None:
    global _STORE, _TWIN
    _STORE, _TWIN = store, twin


@tool
def get_traveler_twin(user_id: str) -> str:
    """Fetch the traveler's current Twin: weights, hard limits, evidence-backed
    preference signals, and what it recently learned. Call this first."""
    p = _TWIN.effective_profile(user_id)
    return json.dumps({
        "home": f"{p.home_city} ({p.home_airport})",
        "purpose": p.trip_purpose,
        "weights": p.weights.as_dict(),
        "hard_limits": {"max_layover_min": p.hard.max_layover_minutes,
                        "seats": p.hard.required_seats,
                        "cabin_strict": p.hard.cabin_strict},
        "signals": [{"dim": s.dimension, "value": str(s.value),
                     "evidence": s.evidence, "confidence": s.confidence}
                    for s in p.signals if s.dimension != "unclassified"][:25],
        "recently_learned": _TWIN.changelog(user_id, limit=5),
    }, default=str)


@tool
def verify_slots(fields_json: str) -> str:
    """Validate structured fields (origin, destination, dates, travellers,
    cabin, budget) against the gazetteer and dataset. Returns verified slots
    and errors — anything failing here must become a clarifying question."""
    slots, errors = validate_fields(json.loads(fields_json))
    return json.dumps({"slots": {k: s.as_dict() for k, s in slots.items()},
                       "errors": errors})


@tool
def search_flights(user_id: str, request_text: str) -> str:
    """Run the deterministic pipeline (resolve + recommend) for a request.
    Returns ranked itineraries, alternatives with deltas, concessions and
    market context. Never re-rank or alter these results."""
    profile = _TWIN.effective_profile(user_id)
    trip = resolve(parse_request(request_text), profile, _STORE)
    rec = recommend(trip, _STORE)
    return json.dumps({"recommendation": _recommendation_json(rec),
                       "narrative_fallback": render_text(explain(rec))}, default=str)


@tool
def record_feedback(user_id: str, event_type: str, payload_json: str) -> str:
    """Append an interaction event to the Living Twin (preference_stated,
    recommendation_rejected, ...). Returns the trait changes it caused."""
    changes = _TWIN.record(user_id, event_type, json.loads(payload_json))
    return json.dumps([c.as_dict() for c in changes])


ALL_TOOLS = [get_traveler_twin, verify_slots, search_flights, record_feedback]


def build_agent(llm):
    """Experimental fully-agentic loop (tool-calling). The production path is
    travel_intelligence.TravelIntelligenceAgent; this exists to demonstrate
    the same tools behind LangChain's agent executor."""
    if not HAS_LANGCHAIN:  # pragma: no cover
        raise RuntimeError("langchain-core not installed")
    return llm.bind_tools(ALL_TOOLS)


def default_tools_state():
    """Convenience initializer for standalone/experimental use."""
    store = preprocessing.build_flight_store(
        preprocessing.enrich_flights(data_loader.load_flights()))
    users = preprocessing.parse_users(data_loader.load_users())
    return store, TwinStore(users)
