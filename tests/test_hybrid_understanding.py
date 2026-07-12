"""Hybrid input-understanding gate: deterministic-first, LLM only on gaps.

These run offline (llm=None): they verify which categories the deterministic
layer resolves vs. leaves as gaps, and that benchmarks are fully resolved
deterministically (so the LLM is never needed for them).
"""
import pytest

from src import data_loader, preprocessing
from src.travel_intelligence import TravelIntelligenceAgent
from src.twin_store import TwinStore


@pytest.fixture(scope="module")
def agent(tmp_path_factory):
    flights = preprocessing.build_flight_store(
        preprocessing.enrich_flights(data_loader.load_flights()))
    users = preprocessing.parse_users(data_loader.load_users())
    twin = TwinStore(users, db_path=tmp_path_factory.mktemp("t") / "twin.db")
    return TravelIntelligenceAgent(flights, twin, llm=None)  # offline


def _gaps_for(agent, message, fields=None):
    from src.planner_validators import validate_fields
    slots, _ = validate_fields(fields or {})
    und = agent._understand_rules(message, slots)
    agent._deterministic_harvest(und, message, slots)
    return agent._gaps(und, message, slots)


# ---- benchmarks are fully resolved deterministically (no gaps) --------------

def test_benchmarks_have_no_gaps(agent):
    for b in data_loader.load_benchmarks():
        gaps = _gaps_for(agent, b["request"])
        assert gaps == [], f"{b['prompt_id']} should need no LLM, got {gaps}"


# ---- phrases the deterministic normalizer already handles -> no gap ----------

@pytest.mark.parametrize("msg", [
    "Tokyo mid August", "Bali first week of July", "Sydney around Christmas",
    "Singapore after Diwali", "just me to Tokyo next weekend",
    "me and my wife to Sydney late July",
])
def test_normalizer_covers_these_without_llm(agent, msg):
    assert _gaps_for(agent, msg) == [], msg


# ---- phrases the deterministic layer misses -> a gap the LLM should fill -----

@pytest.mark.parametrize("msg,expected_gap", [
    ("Bali on a budget of five thousand", "budget"),            # spelled-out thousands
    ("Bali, budget is tight, maybe 800 bucks", "budget"),       # slang currency
    ("Tokyo the week after next", "dates"),                     # phrase normalizer lacks
    ("Bali for me, my wife, and the kids", "passengers"),       # uncounted family list
    ("a flight please", "destination"),                         # nothing resolvable
])
def test_gap_detected_for_conversational_misses(agent, msg, expected_gap):
    assert expected_gap in _gaps_for(agent, msg), msg


# ---- date-context words must NOT be mistaken for a budget cue ----------------

def test_around_about_are_not_money_cues_in_date_context(agent):
    assert "budget" not in _gaps_for(agent, "Sydney around the holidays")
    assert "budget" not in _gaps_for(agent, "Asia trip about three weeks flexibility")


# ---- offline: the LLM path is never taken (no key) --------------------------

def test_offline_never_calls_llm(agent):
    # even a gap-laden message resolves deterministically or clarifies; it must
    # not error trying to reach a model that isn't configured.
    out = agent.plan("U06", message="grab me something to Bali under two grand")
    assert out.status in ("complete", "clarify")
    detail = next(t["detail"] for t in out.trace if t["step"] == "understand")
    assert "LLM gap-fill" not in detail


# ---- backward-compat: benchmarks still produce the same planner inputs ------

def test_benchmarks_unregressed(agent):
    expected = {
        "B01": ("CPT", ["NRT"], "one_way"), "B02": ("MEX", ["LHR", "CDG", "FCO"], "multi_city"),
        "B03": ("AMS", ["DPS"], "one_way"), "B04": ("MEL", ["JFK"], "round_trip"),
        "B05": ("LIS", ["SYD"], "one_way"), "B06": ("MAA", ["SIN", "PVG", "BKK"], "multi_city"),
    }
    for b in data_loader.load_benchmarks():
        out = agent.plan(b["user_id"], message=b["request"])
        assert (out.trip.origin, out.trip.destinations, out.trip.trip_type) \
            == expected[b["prompt_id"]], b["prompt_id"]
