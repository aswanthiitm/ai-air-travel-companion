"""Explanation engine + benchmark runner over the real pipeline output."""
import pytest

from src import config, data_loader, evaluation


@pytest.fixture(scope="module")
def results():
    return evaluation.run_benchmarks()


@pytest.fixture(scope="module")
def by_id(results):
    return {r.prompt_id: r for r in results}


@pytest.fixture(scope="module")
def users_raw():
    return data_loader.load_users().set_index("user_id")


def test_all_six_explained(results):
    assert len(results) == 6
    for r in results:
        e = r.explanation
        assert e.headline
        assert e.traveler_reading and e.why_top and e.itinerary
        assert e.funnel_line.startswith("50,000")


def test_every_quote_is_real_evidence(results, users_raw):
    """Anything quoted in the narrative must literally exist in the user's
    data or the request — the glass box must not hallucinate."""
    import re
    for r in results:
        row = users_raw.loc[r.rec.trip.profile.user_id]
        source_text = (str(row["raw_history"]) + " " + str(row.to_dict())
                       + " " + r.request).lower()
        for section in (r.explanation.traveler_reading, r.explanation.why_top,
                        r.explanation.caveats):
            for line in section:
                for quote in re.findall(r'"([^"]+)"', line):
                    assert quote.lower() in source_text, \
                        f"{r.prompt_id}: fabricated quote {quote!r}"


def test_b05_explanation_is_the_honest_negotiation(by_id):
    text = by_id["B05"].text
    assert "110-minute layover" in text and "90-minute cap" in text
    assert "holiday" in text.lower()
    assert "seat(s) left" in text
    assert "what should" not in text  # narrative, not an echo of the request


def test_b02_worth_it_math_in_prose(by_id):
    text = by_id["B02"].text
    assert "$17/hr" in text
    assert "took a 7hr layover in SIN to save $120" in text
    assert "not worth it" in text or "worth it" in text


def test_b01_admits_the_date_gap(by_id):
    text = by_id["B01"].text
    assert "nearest date this route flies" in text
    assert "aisle seat" in text  # unsupported wish acknowledged


def test_caveats_deduplicated(by_id):
    caveats = by_id["B05"].explanation.caveats
    assert len(caveats) == len(set(caveats))


def test_rubric_self_check_complete(results):
    for r in results:
        assert len(r.rubric) == 7, f"{r.prompt_id}: rubric incomplete"
        assert all(how for _, how in r.rubric)
        quotes_line = dict(r.rubric)["Explain WHY, citing evidence"]
        assert not quotes_line.startswith("0 "), f"{r.prompt_id}: no evidence quotes"


def test_report_written(results, tmp_path, monkeypatch):
    monkeypatch.setattr(evaluation, "REPORT_PATH", tmp_path / "BENCHMARKS.md")
    evaluation.write_report(results)
    content = (tmp_path / "BENCHMARKS.md").read_text()
    assert content.count("## B") == 6
    assert "Rubric self-check" in content
    assert config.SIMULATED_NOW.isoformat() in content
