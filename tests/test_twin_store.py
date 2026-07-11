"""Living Twin: event folding, confidence math, and engine compatibility."""
import pytest

from src import data_loader, preprocessing
from src.inference_engine import resolve
from src.recommendation_engine import recommend
from src.request_parser import parse_request
from src.traveler_profile import TravelerProfile
from src.twin_store import ALPHA, BETA, Overlay, TwinStore, fold_event


@pytest.fixture(scope="module")
def users():
    return preprocessing.parse_users(data_loader.load_users())


@pytest.fixture()
def store(users, tmp_path):
    return TwinStore(users, db_path=tmp_path / "twin.db")


# ---- confidence math --------------------------------------------------------

def test_confirmation_builds_confidence_asymmetrically():
    ov = Overlay()
    fold_event(ov, "preference_stated", {"message": "I hate long layovers"})
    c1 = ov.dims["layover_tolerance"]["c"]
    assert c1 == pytest.approx(ALPHA["stated"], abs=0.01)
    fold_event(ov, "preference_stated", {"message": "avoid long layovers please"})
    c2 = ov.dims["layover_tolerance"]["c"]
    assert c2 == pytest.approx(c1 + ALPHA["stated"] * (1 - c1), abs=0.01)


def test_contradiction_cuts_faster_than_confirmation_builds():
    ov = Overlay()
    for _ in range(4):
        fold_event(ov, "recommendation_rejected", {"itinerary": {"is_redeye": True}})
    c_before = ov.dims["redeye"]["c"]
    assert ov.dims["redeye"]["value"] == "avoid"
    fold_event(ov, "recommendation_accepted", {"itinerary": {"is_redeye": True}})
    st = ov.dims["redeye"]
    assert st["c"] < c_before * (1 - BETA) + 0.01  # one contradiction dents hard


def test_value_flip_is_recorded_not_silent():
    ov = Overlay()
    fold_event(ov, "recommendation_rejected", {"itinerary": {"is_redeye": True}})
    # strong repeated stated evidence the other way
    for _ in range(3):
        fold_event(ov, "preference_stated", {"message": "ok with redeye if it's cheaper"})
    st = ov.dims["redeye"]
    assert st["value"] == "accept"
    assert any(f["dim"] == "redeye" for f in ov.flips)


# ---- event side effects -----------------------------------------------------

def test_repeated_airline_becomes_affinity(store):
    store.record("U09", "recommendation_accepted", {"itinerary": {"airlines": ["TK"]}})
    changes = store.record("U09", "booking_completed", {"itinerary": {"airlines": ["TK"]}})
    assert any("TK" in c.description for c in changes)
    profile = store.effective_profile("U09")
    assert "TK" in profile.soft.airlines


def test_worth_it_trade_updates_value_of_time(store):
    # U02's baseline VOT is ~17.14; a richer accepted trade shifts the median
    store.record("U02", "alternative_chosen",
                 {"label": "cheapest", "worth_it_trade": {"extra_hours": 4, "savings": 200}})
    profile = store.effective_profile("U02")
    assert profile.flexibility.value_of_time_usd_per_hr == pytest.approx(33.57, abs=0.1)


def test_slider_steer_remembered_at_half_strength(store):
    base = store.effective_profile("U05").weights.price
    store.record("U05", "weights_steered", {"deltas": {"price": 0.2}})
    assert store.effective_profile("U05").weights.price > base


# ---- learning changes real recommendations ----------------------------------

def test_stated_preference_changes_profile_and_engine_input(store):
    changes = store.record(
        "U02", "preference_stated",
        {"message": "I hate long layovers and don't mind paying extra for comfort"})
    assert changes  # the Twin visibly learned something
    profile = store.effective_profile("U02")
    assert profile.soft.layover_tolerance == "avoid_long"
    # comfort-over-price feedback shifts weights off U02's baseline (0.579 price)
    assert profile.weights.price < store.baseline("U02").weights.price
    assert isinstance(profile, TravelerProfile)  # same dataclass the engine eats


def test_evolved_profile_flows_through_engine(store, users):
    flights = preprocessing.build_flight_store(
        preprocessing.enrich_flights(data_loader.load_flights()))
    # four consistent rejections establish the trait (0.18 -> 0.55, crosses 0.5)
    for _ in range(4):
        store.record("U32", "recommendation_rejected",
                     {"itinerary": {"is_redeye": True}})
    evolved = store.effective_profile("U32")
    assert evolved.soft.redeye_policy == "avoid"          # baseline was "accept"
    trip = resolve(parse_request("Get me to Tokyo next month"), evolved, flights)
    rec = recommend(trip, flights)
    assert rec.feasible  # the engine consumes the living profile untouched


# ---- persistence & audit ----------------------------------------------------

def test_replay_matches_snapshot(store):
    store.record("U10", "preference_stated", {"message": "no overnight flights for me"})
    store.record("U10", "recommendation_rejected",
                 {"itinerary": {"is_redeye": True}, "reason": "too early in the morning"})
    assert store.replay("U10").dims == store._overlay("U10").dims


def test_changelog_carries_receipts(store):
    store.record("U10", "preference_stated", {"message": "I hate long layovers"})
    log = store.changelog("U10")
    assert log and any("layover" in entry["description"] for entry in log)


def test_baseline_untouched_by_learning(store):
    before = store.baseline("U02").weights.as_dict()
    store.record("U02", "preference_stated", {"message": "money is no object now"})
    assert store.baseline("U02").weights.as_dict() == before


def test_trip_scoped_dimensions_never_persist(store):
    store.record("U03", "preference_stated", {"message": "this is our honeymoon"})
    assert "occasion" not in store._overlay("U03").dims


def test_unknown_event_type_rejected(store):
    with pytest.raises(ValueError):
        store.record("U01", "mind_reading", {})
