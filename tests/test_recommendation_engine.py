"""Recommendation engine over all six real benchmarks (deterministic)."""
import pytest

from src import data_loader, preprocessing
from src.inference_engine import resolve
from src.recommendation_engine import _worth_it, recommend
from src.request_parser import parse_request
from src.traveler_profile import build_all_profiles


@pytest.fixture(scope="module")
def store():
    return preprocessing.build_flight_store(
        preprocessing.enrich_flights(data_loader.load_flights()))


@pytest.fixture(scope="module")
def profiles():
    return build_all_profiles(preprocessing.parse_users(data_loader.load_users()))


@pytest.fixture(scope="module")
def recs(store, profiles):
    out = {}
    for b in data_loader.load_benchmarks():
        trip = resolve(parse_request(b["request"]), profiles[b["user_id"]], store)
        out[b["prompt_id"]] = recommend(trip, store)
    return out


# ---- every benchmark produces a recommendation -----------------------------

def test_all_benchmarks_feasible(recs):
    for pid, rec in recs.items():
        assert rec.feasible and rec.top is not None, f"{pid} infeasible"
        assert rec.ranked, f"{pid} empty pool"


def test_legs_always_chain(recs):
    for pid, rec in recs.items():
        legs = rec.top.legs
        for prev, nxt in zip(legs, legs[1:]):
            assert prev["destination"] == nxt["origin"], f"{pid} legs don't chain"
            assert nxt["departure_utc"] > prev["arrival_utc"], f"{pid} time travel"


def test_funnel_counts_monotone(recs):
    for pid, rec in recs.items():
        leg_counts = [n for label, n in rec.funnel if label != "assembled itineraries"]
        assert leg_counts[0] == 50000
        assert all(a >= b for a, b in zip(leg_counts, leg_counts[1:])), f"{pid} funnel grew"


# ---- B01: sparse dates handled honestly ------------------------------------

def test_b01_respects_cabin_floor_and_admits_date_gap(recs):
    rec = recs["B01"]
    # CPT->NRT has zero flights May-Aug 2025; the engine must say so
    assert any("no options in the asked dates" in c for c in rec.relaxations)
    assert rec.top.legs[0]["stops"] == 0                      # U01 hates connections
    assert rec.top.legs[0]["cabin_class"] in ("Business", "First")  # strict cabin
    cheapest = [a for a in rec.alternatives if a.label == "cheapest"]
    assert cheapest and cheapest[0].delta_price < 0


# ---- B02: multi-city loop with stay concession ------------------------------

def test_b02_full_loop_with_honest_stay_concession(recs):
    rec = recs["B02"]
    legs = rec.top.legs
    assert legs[0]["origin"] == "MEX" and legs[-1]["destination"] == "MEX"
    visited = {leg["destination"] for leg in legs[:-1]}
    assert visited == {"LHR", "CDG", "FCO"}
    # no loop exists with normal stays (verified against the raw data)
    assert any("stayed" in c and "align" in c for c in rec.relaxations)


# ---- B04: weekday pattern impossible in the data -> stated, not hidden ------

def test_b04_round_trip_admits_weekday_mismatch(recs):
    rec = recs["B04"]
    assert len(rec.top.legs) == 2
    assert rec.top.legs[0]["destination"] == "JFK"
    assert any("weekday" in c for c in rec.relaxations)


# ---- B05: the relaxation-ladder showcase ------------------------------------

def test_b05_trap_produces_precise_concessions(recs):
    rec = recs["B05"]
    assert rec.top.legs[0]["stops"] >= 1  # no direct LIS->SYD exists, ever
    joined = " | ".join(rec.relaxations)
    assert "1 stop(s)" in joined
    assert "110-minute layover" in joined and "90-minute cap" in joined
    # holiday window was satisfiable, so no date concession
    assert not any("asked dates" in c for c in rec.relaxations)
    assert rec.top.scarce  # few seats left -> urgency signal


# ---- B06: region multi-city assembles and stays in-window -------------------

def test_b06_visits_all_cities_and_returns_home(recs):
    rec = recs["B06"]
    legs = rec.top.legs
    assert legs[0]["origin"] == "MAA" and legs[-1]["destination"] == "MAA"
    assert len(legs) == 4
    assert not any("asked dates" in c for c in rec.relaxations)


# ---- personalization: same query, different traveler, different answer ------

def test_same_query_different_travelers(store, profiles):
    # U22 chases rock-bottom fares; U35 is strict First/Business, price-blind.
    # Both live in Delhi. Same request, radically different answers.
    results = {}
    for uid in ("U22", "U35"):
        trip = resolve(parse_request("Get me to Tokyo next month"), profiles[uid], store)
        results[uid] = recommend(trip, store)
    cheap, premium = results["U22"].top, results["U35"].top
    assert cheap.flight_ids != premium.flight_ids
    assert premium.legs[0]["cabin_class"] in ("Business", "First")
    assert cheap.total_price < premium.total_price


# ---- Worth-It math ----------------------------------------------------------

def test_worth_it_replays_u02_revealed_trade():
    # U02 once took a 7-hour layover to save $120 (~$17.14/hr). The identical
    # trade must come back as worth it; a worse one must not.
    same_trade = _worth_it(delta_price=-120, delta_minutes=420, vot=17.14)
    assert same_trade["verdict"] == "worth_it"
    bad_trade = _worth_it(delta_price=-50, delta_minutes=600, vot=17.14)
    assert bad_trade["verdict"] == "not_worth_it"
    assert _worth_it(-100, 60, None) is None  # no revealed value of time


def test_alternative_deltas_signs(recs):
    for pid, rec in recs.items():
        for alt in rec.alternatives:
            if alt.label == "cheapest":
                assert alt.delta_price <= 0, f"{pid}: cheapest costs more than top"
            if alt.label == "fastest":
                assert alt.delta_minutes <= 0, f"{pid}: fastest is slower than top"
