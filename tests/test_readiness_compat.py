import json

import pytest
from fastapi.testclient import TestClient

from agents.analysis.context import fingerprint
from agents.analysis.service import AnalysisService
from api.config import Settings
from api.database import Database, encode
from api.main import create_app
from schemas.readiness import ReadinessResult

LEVEL = "readiness_level" if "readiness_level" in ReadinessResult.model_fields else "bank_readiness"
FACT = "readiness.level" if LEVEL == "readiness_level" else "readiness.bank_readiness"


@pytest.fixture(params=["bank_readiness", "readiness_level"])
def saved_inspection(tmp_path, request):
    settings = Settings(storage_dir=tmp_path / "storage", mock=False)
    database = Database(settings.database_path)
    database.initialize()
    obj = database.create_object(
        {
            "address": "Synthetic house",
            "started_at": "2026-01-01",
            "planned_completion_at": "2027-01-01",
            "project": {"wall_material": "brick", "total_area_m2": 100},
        }
    )["id"]
    flight = "fl_compat"
    database.enqueue(
        {
            "id": flight,
            "object_id": obj,
            "flown_at": "2026-11-05T12:00:00+07:00",
            "target_confirmed": True,
            "is_demo": False,
        },
        [],
    )
    result = {
        "agent": "readiness",
        "model": "synthetic-detector",
        "rules": "test-rules",
        "is_demo": False,
        "status": "determined",
        "stage": "walls",
        "readiness_pct": 45,
        request.param: 50,
        "confidence": 0.9,
        "explanation": "Stored result",
    }
    raw = encode(result)
    with database.connection() as db:
        db.execute("UPDATE flights SET status='done',readiness=? WHERE id=?", (raw, flight))
    return settings, database, obj, flight, raw, request.param


def test_both_stored_formats_reach_card_history_and_ai_without_rewriting_audit(saved_inspection):
    settings, database, obj, flight, raw, _ = saved_inspection
    with TestClient(create_app(settings)) as client:
        card = client.get(f"/api/v1/objects/{obj}").json()
        result = client.get(f"/api/v1/flights/{flight}").json()["result"]
        assert card[LEVEL] == 50
        assert result["bank_readiness"] == result["readiness_level"] == 50
        assert client.get(f"/api/v1/objects/{obj}/history").json()["fact"][0][LEVEL] == 50
        assert client.get(f"/api/v1/objects/{obj}/analysis").json()["facts"][FACT] == 50
        assert card["valuation"]["full_cost_rub"] == 6_000_000
        assert card["valuation"]["completed_cost_rub"] == 2_700_000
    with database.connection() as db:
        assert (
            db.execute("SELECT readiness FROM flights WHERE id=?", (flight,)).fetchone()[0] == raw
        )


def test_accept_supports_both_stored_formats_and_preserves_original(saved_inspection):
    settings, database, obj, flight, raw, _ = saved_inspection
    with TestClient(create_app(settings)) as client:
        accepted = client.post(f"/api/v1/flights/{flight}/review", json={"action": "accept"})
        assert accepted.status_code == 201, accepted.text
        assert accepted.json()[LEVEL] == 50
        assert client.get(f"/api/v1/objects/{obj}").json()[LEVEL] == 50
    with database.connection() as db:
        assert (
            db.execute("SELECT readiness FROM flights WHERE id=?", (flight,)).fetchone()[0] == raw
        )
        saved = json.loads(db.execute("SELECT payload FROM readiness_reviews").fetchone()[0])
        assert saved[LEVEL] == 50


def test_employee_review_supports_both_stored_formats_in_all_views(saved_inspection):
    settings, database, obj, flight, _, field = saved_inspection
    review = {
        "action": "correct",
        "stage": "roof",
        "readiness_pct": 55,
        field: 100,
        "author": "test",
        "comment": "Preserve audit",
        "source": "employee",
        "created_at": "2026-11-05T13:00:00+07:00",
    }
    raw = encode(review)
    with database.connection() as db:
        db.execute("INSERT INTO readiness_reviews(flight_id,payload) VALUES (?,?)", (flight, raw))
    with TestClient(create_app(settings)) as client:
        card = client.get(f"/api/v1/objects/{obj}").json()
        assert card[LEVEL] == 100
        assert card["last_flight"]["review"] == card["last_flight"]["readiness_review"]
        assert card["last_flight"]["review"]["bank_readiness"] == 100
        assert card["last_flight"]["review"]["readiness_level"] == 100
        assert client.get(f"/api/v1/objects/{obj}/history").json()["fact"][0][LEVEL] == 100
        action = client.get(f"/api/v1/objects/{obj}/actions").json()[0]
        assert action["bank_readiness"] == action["readiness_level"] == 100
        assert client.get(f"/api/v1/objects/{obj}/analysis").json()["facts"][FACT] == 100
    with database.connection() as db:
        assert db.execute("SELECT payload FROM readiness_reviews").fetchone()[0] == raw


@pytest.mark.parametrize("level", [0, 50, 100, None])
def test_new_field_has_priority_in_mixed_records_including_zero_and_null(saved_inspection, level):
    _, database, obj, flight, raw, _ = saved_inspection
    data = {**json.loads(raw), "bank_readiness": 50, "readiness_level": level}
    with database.connection() as db:
        db.execute("UPDATE flights SET readiness=? WHERE id=?", (encode(data), flight))
    result = database.flight(flight)["result"]
    assert result["readiness_level"] == result["bank_readiness"] == level
    assert database.object(obj)[LEVEL] == level


@pytest.mark.parametrize("missing", ["level", "readiness_pct", "stage", "is_demo"])
def test_incomplete_result_cannot_be_accepted_and_does_not_cause_500(saved_inspection, missing):
    settings, database, _, flight, raw, _ = saved_inspection
    data = json.loads(raw)
    if missing == "level":
        data.pop("bank_readiness", None)
        data.pop("readiness_level", None)
    else:
        data.pop(missing)
    with database.connection() as db:
        db.execute("UPDATE flights SET readiness=? WHERE id=?", (encode(data), flight))
    with TestClient(create_app(settings)) as client:
        response = client.post(f"/api/v1/flights/{flight}/review", json={"action": "accept"})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "STAGE_NOT_DETERMINED"
    with database.connection() as db:
        assert db.execute("SELECT count(*) FROM readiness_reviews").fetchone()[0] == 0


def test_summary_with_other_fact_format_is_stale_and_audit_is_preserved(saved_inspection):
    settings, database, obj, _, _, _ = saved_inspection
    service = AnalysisService(settings, database)
    context = service.context(obj)
    other = "readiness.bank_readiness" if FACT == "readiness.level" else "readiness.level"
    context["facts"][other] = context["facts"].pop(FACT)
    context["required_refs"] = [
        other if value == FACT else value for value in context["required_refs"]
    ]
    context["fallback"]["evidence_refs"] = [
        other if value == FACT else value for value in context["fallback"]["evidence_refs"]
    ]
    record = service.repository.enqueue(obj, context, fingerprint(context), "old-model")
    job = service.repository.claim(900)
    assert service.repository.finish(
        job[0], job[1], llm_status="accepted", recommendation=context["fallback"]
    )
    public = service.latest(obj)
    assert public["stale"] and public["llm_status"] == "rejected"
    assert public["decision_source"] == "rules_fallback" and public["recommendation"] is None
    assert FACT in public["facts"] and other not in public["facts"]
    assert service.repository.get(record["id"])["llm_status"] == "accepted"
