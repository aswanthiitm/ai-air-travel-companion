"""Natural-language input normalizer: unit coverage + backward-compat guards."""
from datetime import date

import pytest

from src import data_loader, preprocessing
from src.input_normalizer import (normalize_budget, normalize_date, normalize_party,
                                  normalize_tone, normalize_trip_type)
from src.planner_validators import parse_dates_field, validate_fields
from src.travel_intelligence import TravelIntelligenceAgent
from src.twin_store import TwinStore

NOW = date(2025, 5, 15)  # == config.SIMULATED_NOW, pinned for explicit assertions


# ---- dates ------------------------------------------------------------------

@pytest.mark.parametrize("text,start,end", [
    ("tomorrow", "2025-05-16", "2025-05-16"),
    ("day after tomorrow", "2025-05-17", "2025-05-17"),
    ("this weekend", "2025-05-17", "2025-05-18"),
    ("next weekend", "2025-05-24", "2025-05-25"),
    ("next week", "2025-05-19", "2025-05-25"),
    ("first week of July", "2025-07-01", "2025-07-07"),
    ("late July", "2025-07-21", "2025-07-31"),
    ("mid August", "2025-08-11", "2025-08-20"),
    ("early September", "2025-09-01", "2025-09-10"),
    ("in July", "2025-07-01", "2025-07-31"),
    ("around Christmas", "2025-12-22", "2025-12-28"),
    ("around New Year", "2025-12-29", "2026-01-04"),
    ("after Diwali", "2025-10-21", "2025-11-03"),
    ("before Pongal", "2025-12-31", "2026-01-13"),
    ("in 3 days", "2025-05-18", "2025-05-18"),
])
def test_date_windows(text, start, end):
    nd = normalize_date(text, NOW)
    assert nd.window == (date.fromisoformat(start), date.fromisoformat(end)), text


def test_vague_date_asks_for_clarification():
    nd = normalize_date("I want to travel sometime", NOW)
    assert nd.window is None and nd.ambiguous and nd.question


def test_existing_phrases_are_left_to_the_old_parser():
    # never invented here -> None, so request_parser/inference keep owning them
    for phrase in ("next month", "over the summer", "around the holidays",
                   "back Thursday", "three weeks"):
        assert normalize_date(phrase, NOW).window is None


def test_unparseable_date_is_not_invented():
    assert normalize_date("whenever the stars align", NOW).window is None


# ---- budgets ----------------------------------------------------------------

@pytest.mark.parametrize("text,val", [
    ("under $500", 500), ("below 700", 700), ("less than 900", 900),
    ("around 1000", 1000), ("about $1200", 1200), ("within 600", 600),
    ("maximum 750", 750), ("budget of 900", 900), ("$1500", 1500),
    ("2000 dollars", 2000),
])
def test_budgets(text, val):
    assert normalize_budget(text) == val


def test_budget_never_invented():
    assert normalize_budget("a nice trip") is None
    assert normalize_budget("-5") is None  # negatives are not budgets


# ---- passenger counts -------------------------------------------------------

@pytest.mark.parametrize("text,n", [
    ("just me", 1), ("travelling alone", 1), ("solo", 1),
    ("me and my wife", 2), ("just the two of us", 2),
    ("family of four", 4), ("two adults", 2),
    ("3 adults and 2 children", 5), ("5 people", 5),
])
def test_party(text, n):
    assert normalize_party(text) == n


def test_party_out_of_range_and_unknown_are_none():
    assert normalize_party("family of 40") is None
    assert normalize_party("a flight please") is None


# ---- trip type / tone -------------------------------------------------------

def test_trip_type_and_tone():
    assert normalize_trip_type("business trip") == "business"
    assert normalize_trip_type("honeymoon") == "leisure"
    assert normalize_trip_type("weekend getaway") == "leisure"
    assert normalize_tone("fastest") == "fastest"
    assert normalize_tone("cheap") == "cheapest"
    assert normalize_tone("comfortable") == "comfort"
    assert normalize_tone("best value") == "balanced"
    assert normalize_tone("get me to Tokyo") is None


# ---- backward compatibility of the validation choke point -------------------

def test_iso_and_existing_formats_unchanged():
    win, phrase, err = parse_dates_field("2025-06-10 to 2025-06-20")
    assert win and win[1].day == 20 and not err
    _, phrase, _ = parse_dates_field("next month")
    assert phrase is not None                      # still a DatePhrase, not a window
    _, _, err = parse_dates_field("whenever the stars align")
    assert err                                     # still an error, not invented


def test_validate_fields_still_rejects_garbage():
    _, errors = validate_fields({"travellers": 40, "budget": -5, "cabin": "sofa"})
    assert len(errors) == 3


def test_validate_fields_accepts_natural_language():
    slots, errors = validate_fields({
        "destination": "Bali", "dates": "mid August",
        "travellers": "family of four", "budget": "under $2000"})
    assert not errors
    assert slots["dates"].value == (date(2025, 8, 11), date(2025, 8, 20))
    assert slots["travellers"].value == 4
    assert slots["budget"].value == 2000.0


# ---- end-to-end through the agent (offline) ---------------------------------

@pytest.fixture(scope="module")
def agent(tmp_path_factory):
    flights = preprocessing.build_flight_store(
        preprocessing.enrich_flights(data_loader.load_flights()))
    users = preprocessing.parse_users(data_loader.load_users())
    twin = TwinStore(users, db_path=tmp_path_factory.mktemp("t") / "twin.db")
    return TravelIntelligenceAgent(flights, twin, llm=None)


def test_message_date_and_party_harvested(agent):
    out = agent.plan("U01", message="just me to Tokyo mid August")
    assert out.status == "complete"
    assert str(out.trip.depart_window.start) == "2025-08-11"
    assert out.trip.profile.hard.required_seats == 1


def test_message_budget_drives_strategy(agent):
    out = agent.plan("U02", message="London next week under $1500")
    assert out.status == "complete"
    assert out.reasoning.strategy == "CHEAPEST_FIRST"


def test_fastest_tone_selects_schedule(agent):
    out = agent.plan("U01", message="fastest flight to Tokyo next week")
    assert out.status == "complete"
    assert out.reasoning.strategy == "SCHEDULE_FIRST"


def test_vague_message_date_triggers_clarify(agent):
    out = agent.plan("U01", message="I want to go to Tokyo sometime")
    assert out.status == "clarify"
    assert "dates" in out.missing


def test_benchmarks_unregressed(agent):
    # the six prompts must resolve exactly as before the normalizer existed
    expected = {
        "B01": ("CPT", ["NRT"], "one_way"),
        "B02": ("MEX", ["LHR", "CDG", "FCO"], "multi_city"),
        "B03": ("AMS", ["DPS"], "one_way"),
        "B04": ("MEL", ["JFK"], "round_trip"),
        "B05": ("LIS", ["SYD"], "one_way"),
        "B06": ("MAA", ["SIN", "PVG", "BKK"], "multi_city"),
    }
    for b in data_loader.load_benchmarks():
        out = agent.plan(b["user_id"], message=b["request"])
        origin, dests, ttype = expected[b["prompt_id"]]
        assert (out.trip.origin, out.trip.destinations, out.trip.trip_type) \
            == (origin, dests, ttype), b["prompt_id"]
