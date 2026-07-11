"""v4 API endpoints: /api/plan, /api/feedback, /api/twin (offline mode)."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    import os
    os.environ["TWIN_DB_PATH"] = str(tmp_path_factory.mktemp("twin") / "twin.db")
    from src import api
    api._agent_state.cache_clear()
    return TestClient(api.app)


def test_plan_form_only(client):
    r = client.post("/api/plan", json={
        "user_id": "U06",
        "fields": {"origin": "Chennai", "destination": "Bali", "dates": "2025-06"}})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "complete"
    assert data["reasoning"]["intent"] == "SEARCH"
    assert data["recommendation"]["top"]
    assert any(t["step"] == "get_traveler_twin" for t in data["trace"])
    assert data["slots"]["destination"]["source"] == "form"


def test_plan_text_only_clarify_and_resume(client):
    r = client.post("/api/plan", json={
        "user_id": "U22", "conversation_id": "api-c1",
        "message": "I want to get away for a few days"})
    assert r.json()["status"] == "clarify"
    assert r.json()["question"]
    r2 = client.post("/api/plan", json={
        "user_id": "U22", "conversation_id": "api-c1", "message": "Tokyo I guess"})
    assert r2.json()["status"] == "complete"


def test_plan_preference_update_acknowledged(client):
    r = client.post("/api/plan", json={
        "user_id": "U12", "message": "I hate long layovers these days"})
    data = r.json()
    assert data["status"] == "acknowledged"
    assert data["twin_updates"]
    twin = client.get("/api/twin/U12").json()
    assert twin["event_count"] >= 1
    assert any("layover" in c["description"] for c in twin["changelog"])


def test_plan_validation_errors_are_400(client):
    r = client.post("/api/plan", json={
        "user_id": "U01", "fields": {"destination": "Narnia"}})
    assert r.status_code == 400
    assert "Narnia" in r.json()["detail"]


def test_plan_requires_some_input(client):
    assert client.post("/api/plan", json={"user_id": "U01"}).status_code == 400


def test_feedback_endpoint_and_twin_reset(client):
    for _ in range(4):
        r = client.post("/api/feedback", json={
            "user_id": "U32", "event_type": "recommendation_rejected",
            "payload": {"itinerary": {"is_redeye": True}}})
        assert r.status_code == 200
    twin = client.get("/api/twin/U32").json()
    assert twin["event_count"] == 4
    redeye = [s for s in twin["profile"]["signals"]
              if s["dimension"] == "redeye" and s["source"] == "behavior"]
    assert redeye and redeye[0]["value"] == "avoid"

    client.delete("/api/twin/U32")
    assert client.get("/api/twin/U32").json()["event_count"] == 0


def test_feedback_bad_event_type(client):
    r = client.post("/api/feedback", json={
        "user_id": "U01", "event_type": "telepathy", "payload": {}})
    assert r.status_code == 400


def test_v1_recommend_endpoint_still_untouched(client):
    r = client.post("/api/recommend",
                    json={"user_id": "U05",
                          "request": "I want to visit Sydney around the holidays"})
    assert r.status_code == 200
    assert any("110-minute layover" in c
               for c in r.json()["recommendation"]["relaxations"])
