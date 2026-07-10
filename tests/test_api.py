"""API contract tests via FastAPI's TestClient (no server needed)."""
import pytest
from fastapi.testclient import TestClient

from src.api import app


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_users_endpoint(client):
    users = client.get("/api/users").json()
    assert len(users) == 50
    assert {"user_id", "home_city", "driver"} <= set(users[0])


def test_profile_endpoint(client):
    p = client.get("/api/profile/U05").json()
    assert p["hard"]["cabin_strict"] is True
    assert sum(p["weights"].values()) == pytest.approx(1.0, abs=0.01)
    assert all(s["evidence"] for s in p["signals"])


def test_profile_404(client):
    assert client.get("/api/profile/U99").status_code == 404


def test_benchmarks_endpoint(client):
    assert len(client.get("/api/benchmarks").json()) == 6


def test_recommend_b05_full_payload(client):
    r = client.post("/api/recommend",
                    json={"user_id": "U05",
                          "request": "I want to visit Sydney around the holidays"})
    assert r.status_code == 200
    data = r.json()
    rec = data["recommendation"]
    assert rec["feasible"] and rec["top"]["legs"][0]["stops"] >= 1
    assert any("110-minute layover" in c for c in rec["relaxations"])
    assert rec["funnel"][0] == {"stage": "all flights", "count": 50000}
    assert data["explanation"]["headline"]
    assert "narrative" in data


def test_recommend_weight_sliders_change_ranking(client):
    base = {"user_id": "U35", "request": "Get me to Tokyo next month"}
    normal = client.post("/api/recommend", json=base).json()
    cheap = client.post("/api/recommend",
                        json={**base, "weights": {"price": 5.0}}).json()
    assert cheap["profile"]["weights"]["price"] > normal["profile"]["weights"]["price"]
    assert (cheap["recommendation"]["top"]["total_price"]
            <= normal["recommendation"]["top"]["total_price"])


def test_recommend_bad_request(client):
    r = client.post("/api/recommend",
                    json={"user_id": "U01", "request": "somewhere nice please"})
    assert r.status_code == 400
