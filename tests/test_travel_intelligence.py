"""Travel Intelligence Agent + Evidence Bundle, offline (rule-fallback) mode."""
import pytest

from src import data_loader, preprocessing
from src.evidence_bundle import validate_grounding
from src.planner_validators import parse_dates_field, validate_fields
from src.travel_intelligence import TravelIntelligenceAgent
from src.twin_store import TwinStore


@pytest.fixture(scope="module")
def flights():
    return preprocessing.build_flight_store(
        preprocessing.enrich_flights(data_loader.load_flights()))


@pytest.fixture()
def agent(flights, tmp_path):
    users = preprocessing.parse_users(data_loader.load_users())
    twin = TwinStore(users, db_path=tmp_path / "twin.db")
    return TravelIntelligenceAgent(flights, twin, llm=False or None)  # offline


# ---- validators -------------------------------------------------------------

def test_validate_fields_happy_path():
    slots, errors = validate_fields({
        "origin": "Chennai", "destination": "Bali", "dates": "2025-06",
        "travellers": 2, "cabin": "economy", "budget": 2000})
    assert not errors
    assert slots["origin"].value == "MAA"
    assert slots["destination"].value == ["DPS"]
    assert slots["dates"].value[0].isoformat() == "2025-06-01"
    assert slots["cabin"].value == "Economy"
    assert all(s.source == "form" and s.confidence == 1.0 for s in slots.values())


def test_validate_fields_catches_garbage():
    _, errors = validate_fields({"origin": "Atlantis", "travellers": 40,
                                 "cabin": "sofa", "budget": -5})
    assert len(errors) == 4


def test_dates_field_variants():
    win, _, _ = parse_dates_field("2025-06-10 to 2025-06-20")
    assert win[1].day == 20
    _, phrase, _ = parse_dates_field("next month")
    assert phrase is not None
    _, _, err = parse_dates_field("whenever the stars align")
    assert err


# ---- the three usage patterns all enter the same workflow -------------------

def test_form_only(agent):
    out = agent.plan("U06", fields={"origin": "Chennai", "destination": "Bali",
                                    "dates": "2025-06"})
    assert out.status == "complete"
    assert out.trip.origin == "MAA" and out.trip.destinations == ["DPS"]
    assert str(out.trip.depart_window.start) == "2025-06-01"
    assert out.reasoning.intent == "SEARCH"
    assert any(t["step"] == "get_traveler_twin" for t in out.trace)  # AI participated


def test_text_only(agent):
    out = agent.plan("U01", message="I need to get from home to Tokyo next month")
    assert out.status == "complete"
    assert out.trip.destinations == ["NRT"]


def test_form_plus_text_honeymoon_contradiction(agent):
    # U06 is the broke-student persona (price weight ~0.6); the honeymoon
    # message must flip strategy to comfort AND record the contradiction.
    out = agent.plan("U06", fields={"destination": "Bali", "dates": "2025-06"},
                     message="This is our honeymoon. I don't mind paying extra "
                             "for comfort. I don't like overnight flights.")
    assert out.status == "complete"
    assert out.reasoning.strategy == "COMFORT_FIRST"
    assert out.reasoning.contradictions, "request-vs-twin tension must be recorded"
    assert "price" in out.reasoning.contradictions[0]["twin_says"]
    # comfort preset actually moved the weights used for the search
    assert out.trip.profile.weights.comfort > agent.twin.baseline("U06").weights.comfort
    # the honest contract: either the top avoids redeyes, or the agent's
    # refinement explicitly states that only overnight options exist
    # (MAA->DPS non-redeyes exist only in April/September in this dataset)
    top_redeye = any(leg["is_redeye"] for leg in out.recommendation.top.legs)
    assert (not top_redeye) or any("overnight" in r or "redeye" in r
                                   for r in out.reasoning.refinements)
    assert out.reasoning.refinements  # the agent demonstrably validated results


# ---- clarification & preference-update intents -------------------------------

def test_vague_request_clarifies_then_resumes(agent):
    out = agent.plan("U22", message="I want to get away for a few days",
                     conversation_id="c1")
    assert out.status == "clarify"
    assert "destination" in out.missing
    out2 = agent.plan("U22", message="Bali sounds nice", conversation_id="c1")
    assert out2.status == "complete"
    assert out2.trip.destinations == ["DPS"]


def test_pure_preference_statement_updates_twin_without_searching(agent):
    out = agent.plan("U02", message="I hate long layovers and don't mind "
                                    "paying extra for comfort")
    assert out.status == "acknowledged"
    assert out.twin_updates
    assert agent.twin.effective_profile("U02").soft.layover_tolerance == "avoid_long"


# ---- evidence bundle & grounding ---------------------------------------------

def test_bundle_carries_all_three_sources(agent):
    out = agent.plan("U05", message="I want to visit Sydney around the holidays")
    b = out.bundle
    assert b.reasoning.intent in ("SEARCH", "ADVICE")
    assert b.computation["recommendation"]["top"]
    assert b.twin["cited_signals"]
    assert b.trace


def test_grounding_validator_blocks_fabrication(agent):
    out = agent.plan("U01", message="Get me to Tokyo next month")
    b = out.bundle
    real_price = out.recommendation.top.total_price
    ok = validate_grounding(f"The best option costs ${real_price:,.0f}.", b)
    assert ok == []
    bad = validate_grounding("The best option costs $9,876,543 and the traveler "
                             'once said "I collect vintage propellers".', b)
    assert len(bad) == 2


def test_offline_narrative_is_the_tested_renderer(agent):
    out = agent.plan("U01", message="Get me to Tokyo next month")
    assert not out.llm_used
    assert out.narrative.startswith("###")  # render_text's headline format


# ---- the twin visibly learns from planning turns ------------------------------

def test_feedback_changes_next_plan(agent):
    for _ in range(4):
        agent.twin.record("U32", "recommendation_rejected",
                          {"itinerary": {"is_redeye": True}})
    out = agent.plan("U32", message="Get me to Tokyo next month")
    assert out.trip.profile.soft.redeye_policy == "avoid"   # baseline was accept
    top = out.recommendation.top
    assert not any(leg["is_redeye"] for leg in top.legs)
