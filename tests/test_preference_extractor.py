"""Extractor behavior over the real 50-user corpus."""
import pytest

from src import data_loader, preprocessing
from src.preference_extractor import Source, extract_history, extract_user


@pytest.fixture(scope="module")
def users():
    return preprocessing.parse_users(data_loader.load_users()).set_index("user_id", drop=False)


def _dims(signals):
    return {s.dimension for s in signals}


def test_full_corpus_lexicon_coverage(users):
    """Every raw_history snippet in the dataset must match at least one rule."""
    unmatched = []
    for _, user in users.iterrows():
        for s in extract_history(user["history_snippets"]):
            if s.dimension == "unclassified":
                unmatched.append((user["user_id"], s.evidence))
    assert not unmatched, f"lexicon gaps: {unmatched}"


def test_unknown_snippet_is_kept_not_dropped():
    signals = extract_history(["I only fly on Tuesdays wearing red socks"])
    assert signals[0].dimension == "unclassified"
    assert signals[0].evidence == "I only fly on Tuesdays wearing red socks"


def test_business_persona_u01(users):
    signals = extract_user(users.loc["U01"])
    history = [s for s in signals if s.source is Source.RAW_HISTORY]
    assert any(s.dimension == "stops" and s.value == "avoid" for s in history)
    assert any(s.dimension == "redeye" and s.value == "avoid" for s in history)
    assert any(s.dimension == "cabin_strict" and s.value is True for s in history)
    # "aisle seat, front of cabin" -> amenity signal the dataset can't satisfy
    assert any(s.dimension == "amenity" and s.value == "aisle_seat" for s in history)


def test_value_of_time_extracted_u02(users):
    signals = extract_user(users.loc["U02"])
    vot = [s for s in signals if s.dimension == "value_of_time"]
    assert len(vot) == 1
    assert vot[0].value == pytest.approx(120 / 7, abs=0.05)  # ~$17.14/hr
    assert "7hr layover in SIN" in vot[0].evidence


def test_family_persona_u03(users):
    signals = extract_user(users.loc["U03"])
    party = [s for s in signals if s.dimension == "party"]
    assert party and party[0].value == {"children": 2}
    assert any(s.dimension == "departure_time" and s.value == "morning" for s in signals)
    assert any(s.dimension == "baggage" and s.value.get("stroller") for s in signals
               if isinstance(s.value, dict))


def test_hub_extraction_u08(users):
    signals = extract_user(users.loc["U08"])
    hubs = [s.value for s in signals if s.dimension == "hub"]
    assert hubs == ["DXB"]


def test_connection_anxiety_u10(users):
    signals = extract_user(users.loc["U10"])
    assert any(s.dimension == "connection_anxiety" for s in signals)


def test_every_signal_carries_evidence(users):
    for uid in ["U01", "U06", "U33", "U50"]:
        for s in extract_user(users.loc[uid]):
            assert s.evidence, f"{uid}: signal {s.dimension} lacks evidence"
            assert 0 < s.confidence <= 1
