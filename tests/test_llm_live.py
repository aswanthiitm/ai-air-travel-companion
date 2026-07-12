"""Live LLM-mode tests. Skipped unless an API key is configured.

Run with:  OPENROUTER_API_KEY=... pytest tests/test_llm_live.py -q
(or GROQ_API_KEY). These hit the network and a rate-limited free tier —
they are validation tests, not CI tests. ~6 model calls total.
"""
import os

import pytest

from src import data_loader, preprocessing
from src.travel_intelligence import TravelIntelligenceAgent, get_llm
from src.twin_store import TwinStore

pytestmark = pytest.mark.skipif(
    not (os.environ.get("OPENROUTER_API_KEY") or os.environ.get("GROQ_API_KEY")),
    reason="no LLM API key configured")


@pytest.fixture(scope="module")
def agent(tmp_path_factory):
    flights = preprocessing.build_flight_store(
        preprocessing.enrich_flights(data_loader.load_flights()))
    users = preprocessing.parse_users(data_loader.load_users())
    twin = TwinStore(users, db_path=tmp_path_factory.mktemp("twin") / "twin.db")
    llm = get_llm()
    assert llm is not None
    return TravelIntelligenceAgent(flights, twin, llm=llm)


def test_live_honeymoon_full_loop(agent):
    out = agent.plan("U06", fields={"destination": "Bali", "dates": "2025-06"},
                     message="This is our honeymoon. I don't mind paying extra "
                             "for comfort. I don't like overnight flights.")
    assert out.status == "complete"
    assert out.reasoning.strategy == "COMFORT_FIRST"
    assert out.reasoning.contradictions
    # LLM prose must be grounded, or the system must have failed closed
    if out.llm_used:
        assert out.grounding_violations == []
        assert len(out.narrative) > 200
        assert not out.narrative.startswith("###")
    else:
        assert out.narrative.startswith("###")  # template fallback engaged


def test_live_text_only_b01(agent):
    out = agent.plan("U01",
                     message="I need to get from home to Tokyo next month")
    assert out.status == "complete"
    assert out.trip.destinations == ["NRT"]
    assert out.trip.trip_type == "one_way"  # model over-generation guarded
    if out.llm_used:
        assert out.grounding_violations == []


def test_live_understanding_never_breaks_planning(agent):
    # even for odd phrasing, the turn must end in a valid outcome
    out = agent.plan("U22", message="somewhere sunny but im broke, thoughts?")
    assert out.status in ("complete", "clarify", "acknowledged")
