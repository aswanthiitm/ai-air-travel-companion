"""Parser + inference engine against all six real benchmark prompts."""
from datetime import date

import pytest

from src import data_loader, preprocessing
from src.airports import REGIONS
from src.inference_engine import resolve
from src.request_parser import DateKind, parse_request
from src.traveler_profile import build_all_profiles

NOW = date(2025, 5, 15)  # config.SIMULATED_NOW, pinned here so tests are explicit


@pytest.fixture(scope="module")
def store():
    return preprocessing.build_flight_store(
        preprocessing.enrich_flights(data_loader.load_flights()))


@pytest.fixture(scope="module")
def profiles():
    return build_all_profiles(preprocessing.parse_users(data_loader.load_users()))


@pytest.fixture(scope="module")
def benchmarks():
    return {b["prompt_id"]: b for b in data_loader.load_benchmarks()}


@pytest.fixture(scope="module")
def resolved(store, profiles, benchmarks):
    def _run(prompt_id):
        b = benchmarks[prompt_id]
        return resolve(parse_request(b["request"]), profiles[b["user_id"]], store, NOW)
    return _run


# ---- B01: "from home to Tokyo next month" (U01, CPT) ----------------------

def test_b01_tokyo_next_month(resolved):
    trip = resolved("B01")
    assert trip.origin == "CPT"
    assert trip.destinations == ["NRT"]
    assert trip.trip_type == "one_way"
    assert trip.depart_window.start == date(2025, 6, 1)
    assert trip.depart_window.end == date(2025, 6, 30)
    assert any("next month" in n for n in trip.notes)


# ---- B02: "London + Paris + Rome in one journey" (U02, MEX) ----------------

def test_b02_multi_city_in_mention_order(resolved):
    trip = resolved("B02")
    assert trip.origin == "MEX"
    assert trip.destinations == ["LHR", "CDG", "FCO"]
    assert trip.trip_type == "multi_city"
    assert trip.stay_days == 3


# ---- B03: "Cheapest option to Bali ... over the summer" (U03, AMS) ---------

def test_b03_summer_window_and_cheapest_signal(resolved, profiles):
    trip = resolved("B03")
    assert trip.destinations == ["DPS"]
    assert trip.depart_window == type(trip.depart_window)(date(2025, 6, 1), date(2025, 8, 31))
    # the request's "cheapest" outranks the stored profile: price weight rises
    assert trip.profile.weights.price > profiles["U03"].weights.price
    assert any("cheapest" in r for r in trip.profile.weight_rationale)


# ---- B04: "Tuesday meeting, back Thursday" (U04, MEL) ----------------------

def test_b04_weekday_round_trip(resolved):
    trip = resolved("B04")
    assert trip.origin == "MEL"
    assert trip.destinations == ["JFK"]
    assert trip.trip_type == "round_trip"
    assert trip.weekday is not None
    assert trip.weekday.out_weekday == 1      # Tuesday
    assert trip.weekday.return_weekday == 3   # Thursday
    assert trip.stay_days == 2                # Tue -> Thu
    # "meeting" is a business signal; time weight gets a request bump
    assert any(s.dimension == "trip_purpose" for s in trip.request_signals)


# ---- B05: "Sydney around the holidays — what should I expect?" (U05, LIS) --

def test_b05_holidays_advise_mode(resolved):
    trip = resolved("B05")
    assert trip.destinations == ["SYD"]
    assert trip.advise_only
    assert trip.depart_window.start == date(2025, 12, 15)
    assert trip.depart_window.end == date(2026, 1, 5)


# ---- B06: "multi-city Asia trip, three weeks of flexibility" (U06, MAA) ----

def test_b06_region_trip(resolved):
    trip = resolved("B06")
    assert trip.origin == "MAA"
    assert trip.trip_type == "multi_city"
    assert len(trip.destinations) == 3
    assert set(trip.destinations) <= REGIONS["asia"]
    assert "MAA" not in trip.destinations
    window_days = (trip.depart_window.end - trip.depart_window.start).days
    assert window_days == 21  # "about three weeks of flexibility"
    assert any("region trip" in n for n in trip.notes)


# ---- every resolution is explainable ---------------------------------------

def test_all_benchmarks_resolve_with_notes(resolved, benchmarks):
    for pid in benchmarks:
        trip = resolved(pid)
        assert trip.notes, f"{pid}: no resolution notes"
        assert trip.destinations, f"{pid}: no destinations"


# ---- parser unit behavior ---------------------------------------------------

def test_parser_ignores_unknown_and_home_cities(store, profiles):
    spec = parse_request("Get me from home to Tokyo")
    assert spec.destination_names == ["tokyo"]
    trip = resolve(spec, profiles["U01"], store, NOW)
    assert trip.destinations == ["NRT"]


def test_no_destination_raises(store, profiles):
    with pytest.raises(ValueError):
        resolve(parse_request("I want to go somewhere nice"), profiles["U01"], store, NOW)


def test_next_month_across_year_boundary(store, profiles):
    trip = resolve(parse_request("Tokyo next month"), profiles["U01"], store,
                   now=date(2025, 12, 10))
    assert trip.depart_window.start == date(2026, 1, 1)
    assert trip.depart_window.end == date(2026, 1, 31)


def test_flex_weeks_number_words():
    spec = parse_request("somewhere in Asia, two weeks of wiggle room")
    assert spec.date_phrase.kind is DateKind.FLEX_WEEKS
    assert spec.date_phrase.flex_weeks == 2


def test_direct_only_request_signal():
    spec = parse_request("Nonstop to London please")
    assert any(s.dimension == "stops" and s.value == "avoid" for s in spec.signals)
