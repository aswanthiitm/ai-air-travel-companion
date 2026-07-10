"""Profile builder: constraints, weights, conflicts over the real corpus."""
import pytest

from src import data_loader, preprocessing
from src.traveler_profile import build_all_profiles


@pytest.fixture(scope="module")
def profiles():
    users = preprocessing.parse_users(data_loader.load_users())
    return build_all_profiles(users)


def test_all_50_profiles_build(profiles):
    assert len(profiles) == 50


def test_weights_normalized(profiles):
    for p in profiles.values():
        assert sum(p.weights.as_dict().values()) == pytest.approx(1.0, abs=0.01)
        assert all(w >= 0 for w in p.weights.as_dict().values())
        assert p.weight_rationale  # every weight is explainable


def test_weight_ordering_matches_personas(profiles):
    # U05 (First, money-no-object) vs U06 (broke student)
    assert profiles["U05"].weights.price < profiles["U06"].weights.price
    assert profiles["U05"].weights.comfort > profiles["U06"].weights.comfort
    # U01 (hates connections) vs U02 (doesn't care about stops)
    assert profiles["U01"].weights.convenience > profiles["U02"].weights.convenience


def test_hard_constraints(profiles):
    # U33 travels with 2 kids -> 3 seats needed
    assert profiles["U33"].party.children == 2
    assert profiles["U33"].hard.required_seats == 3
    # U10 is scared of tight connections -> raised minimum layover floor
    assert profiles["U10"].hard.min_layover_minutes == 90
    assert profiles["U01"].hard.min_layover_minutes == 45
    # structured layover cap flows through
    assert profiles["U05"].hard.max_layover_minutes == 90


def test_cabin_strict_for_premium_personas(profiles):
    assert profiles["U05"].hard.cabin_strict      # "first or business only"
    assert profiles["U01"].hard.cabin_strict      # "always book business"
    assert not profiles["U06"].hard.cabin_strict  # broke student


def test_max_stops_resolution(profiles):
    assert profiles["U01"].soft.max_stops == 0  # strong + "hate connections"
    assert profiles["U19"].soft.max_stops == 1  # "one stop fine, not two"
    assert profiles["U02"].soft.max_stops == 2  # "dont care about stops"


def test_redeye_policies(profiles):
    assert profiles["U01"].soft.redeye_policy == "avoid"
    assert profiles["U02"].soft.redeye_policy == "accept"


def test_hub_and_value_of_time(profiles):
    assert profiles["U08"].soft.hub == "DXB"
    assert profiles["U02"].flexibility.value_of_time_usd_per_hr == pytest.approx(17.14, abs=0.01)
    assert profiles["U01"].flexibility.value_of_time_usd_per_hr is None


def test_loyalty_conflict_detected_u21(profiles):
    # frequent_flyer='none' but history says "gold w/ none, ~80 segments/yr"
    conflicts = [c for c in profiles["U21"].conflicts if c.dimension == "loyalty"]
    assert len(conflicts) == 1
    assert "gold" in conflicts[0].kept.evidence
    assert conflicts[0].discarded.evidence == "frequent_flyer=none"


def test_retired_age_conflict_u37(profiles):
    # U37 is 23 with "retired so dates are flexible" in history (template noise)
    assert any(c.dimension == "travel_pattern" for c in profiles["U37"].conflicts)


def test_unsupported_wishes_are_acknowledged(profiles):
    # U01 wants an aisle seat; the dataset has no seat maps — captured, not lost
    assert any(s.value == "aisle_seat" for s in profiles["U01"].unsupported)
    values = {s.value for s in profiles["U41"].unsupported}
    assert "lounge" in values and "wifi" in values


def test_profiles_keep_full_audit_trail(profiles):
    for p in profiles.values():
        assert p.signals, f"{p.user_id} has no signals"
        history = [s for s in p.signals if s.source.value == "raw_history"]
        assert history, f"{p.user_id} extracted nothing from raw_history"
