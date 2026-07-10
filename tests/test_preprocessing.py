"""Invariants and enrichment behavior the downstream engines depend on."""
import pytest

from src import data_loader, preprocessing
from src.airports import AIRPORTS, resolve_city


@pytest.fixture(scope="module")
def flights():
    return data_loader.load_flights()


@pytest.fixture(scope="module")
def enriched(flights):
    return preprocessing.enrich_flights(flights)


@pytest.fixture(scope="module")
def store(enriched):
    return preprocessing.build_flight_store(enriched)


@pytest.fixture(scope="module")
def users():
    return preprocessing.parse_users(data_loader.load_users())


# ---- dataset invariants ---------------------------------------------------

def test_flight_invariants_hold(flights):
    preprocessing.validate_flights(flights)


def test_user_references_hold(users, flights):
    preprocessing.validate_users(users, flights)


def test_multi_flight_numbers_only_on_connections(enriched):
    multi = enriched["flight_numbers_list"].str.len() > 1
    assert (enriched.loc[multi, "stops"] > 0).all()


# ---- enrichment -----------------------------------------------------------

def test_local_hour_and_buckets(enriched):
    assert enriched["departure_local_hour"].between(0, 23).all()
    assert set(enriched["time_of_day"]) <= {"morning", "afternoon", "evening", "night"}
    # redeye is exactly the night bucket
    assert (enriched["is_redeye"] == (enriched["time_of_day"] == "night")).all()


def test_layover_lists_match_stops(enriched):
    assert (enriched["layover_airports_list"].str.len() == enriched["stops"]).all()


# ---- flight store ---------------------------------------------------------

def test_route_index_partitions_all_flights(store):
    total = sum(len(store.flights_for_route(o, d)) for o, d in store.od_pairs)
    assert total == len(store.flights)


def test_route_lookup_sorted_and_scoped(store):
    r = store.flights_for_route("MAA", "NRT")
    assert len(r) > 0
    assert (r["origin"] == "MAA").all() and (r["destination"] == "NRT").all()
    assert r["departure_utc"].is_monotonic_increasing


def test_missing_route_returns_empty(store):
    # NRT->MEL is one of the 18 OD pairs absent from the dataset.
    assert not store.route_exists("NRT", "MEL")
    assert store.flights_for_route("NRT", "MEL").empty


def test_seasonal_uplift_is_real(store):
    # Verified during analysis: MAA->NRT spring_break median sits well above shoulder.
    uplift = store.seasonal_uplift("MAA", "NRT", "spring_break")
    assert uplift is not None and uplift > 0.2


def test_benchmark_trap_route_has_no_directs(store):
    # B05: LIS->SYD exists but offers zero direct flights (the relaxation case).
    r = store.flights_for_route("LIS", "SYD")
    assert len(r) > 0 and (r["stops"] > 0).all()


# ---- users ----------------------------------------------------------------

def test_airline_and_history_parsing(users):
    u01 = users.set_index("user_id").loc["U01"]
    assert u01["preferred_airlines_list"] == ["AA"]
    assert len(u01["history_snippets"]) == 3
    assert u01["history_snippets"][0] == "always book business, hate connections"


@pytest.mark.parametrize(
    "text,checked,stroller",
    [
        ("carry-on only", 0, False),
        ("1 checked", 1, False),
        ("2 checked + stroller", 2, True),
        ("3 checked", 3, False),
    ],
)
def test_baggage_parsing(text, checked, stroller):
    parsed = preprocessing.parse_baggage(text)
    assert parsed == {"checked_bags": checked, "stroller": stroller}


# ---- airports reference ---------------------------------------------------

def test_reference_covers_dataset(flights):
    assert set(flights["origin"]) | set(flights["destination"]) <= set(AIRPORTS)


def test_city_resolution():
    assert resolve_city("Tokyo") == "NRT"
    assert resolve_city("new york") == "JFK"
    assert resolve_city("NYC") == "JFK"
    assert resolve_city("Bali") == "DPS"
    assert resolve_city("lhr") == "LHR"
    assert resolve_city("Atlantis") is None
