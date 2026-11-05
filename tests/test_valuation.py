import json
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_material_service import new_object, upload

from agents.analysis.context import build_context, fingerprint
from agents.valuation.calculator import calculate_valuation
from agents.valuation.config import valuation_config
from api.config import Settings
from api.main import create_app
from worker.service import Worker


def house(percent=40):
    return {
        "project": {"wall_material": "aerated_concrete", "total_area_m2": 100},
        "last_flight": {
            "flight_id": "fl_test",
            "status": "done",
            "target_confirmed": True,
            "result": {
                "status": "determined",
                "is_demo": False,
                "readiness_pct": percent,
                "bank_readiness": 100,
            },
        },
    }


def test_demo_cost_example_and_ranges():
    result = calculate_valuation(house())
    assert result["status"] == "estimated" and result["is_demo"]
    assert result["full_cost_rub"] == 5_000_000
    assert result["completed_cost_rub"] == 2_000_000
    assert result["remaining_cost_rub"] == 3_000_000
    assert result["full_range_rub"] == [3_500_000, 6_500_000]
    assert result["completed_range_rub"] == [1_400_000, 2_600_000]
    assert result["remaining_range_rub"] == [2_100_000, 3_900_000]
    assert "100 м²" in result["explanation"]
    assert result["breakdown"]["readiness_pct"] == 40  # Not bank scale 100.


@pytest.mark.parametrize("percent,completed,remaining", [(0, 0, 5_000_000), (100, 5_000_000, 0)])
def test_zero_and_full_readiness(percent, completed, remaining):
    result = calculate_valuation(house(percent))
    assert result["completed_cost_rub"] == completed
    assert result["remaining_cost_rub"] == remaining
    assert result["missing_data"] == []


@pytest.mark.parametrize(
    "change",
    [
        {"target_confirmed": False},
        {"status": "queued"},
        {"status": "failed"},
        {"result": {"status": "determined", "is_demo": True, "readiness_pct": 40}},
        {"result": {"status": "needs_review", "is_demo": False, "readiness_pct": 40}},
        {"result": {"status": "model_unavailable", "is_demo": False}},
    ],
)
def test_untrusted_readiness_never_turns_into_money(change):
    obj = house()
    obj["last_flight"].update(change)
    result = calculate_valuation(obj)
    assert result["status"] == "partial" and result["full_cost_rub"] == 5_000_000
    assert result["completed_cost_rub"] is None and result["remaining_cost_rub"] is None
    assert result["value_rub"] is None
    assert result["missing_data"] == ["confirmed_readiness"]


def test_human_review_overrides_unavailable_model():
    obj = house()
    obj["last_flight"].update(
        target_confirmed=False,
        result={"status": "model_unavailable", "is_demo": True},
        readiness_review={"readiness_pct": 45, "bank_readiness": 50},
    )
    result = calculate_valuation(obj)
    assert result["completed_cost_rub"] == 2_250_000
    assert result["breakdown"]["readiness_source"] == "employee"


def test_area_priorities_and_no_implicit_floor():
    obj = house()
    obj["project"].update(footprint_m2=80, floors=2)
    assert calculate_valuation(obj)["breakdown"]["area_m2"] == 100
    assert calculate_valuation(obj, {"area_m2": 120})["full_cost_rub"] == 6_000_000
    obj["project"].pop("total_area_m2")
    result = calculate_valuation(obj)
    assert result["full_cost_rub"] == 8_000_000
    assert result["breakdown"]["area_source"] == "project_footprint_times_floors"
    obj["project"].pop("floors")
    assert calculate_valuation(obj)["full_cost_rub"] is None
    assert "area_m2" in calculate_valuation(obj)["missing_data"]


def test_material_priority_and_custom_rate_without_material():
    obj = house()
    assert calculate_valuation(obj)["breakdown"]["material_source"] == "project"
    obj["material"] = {"observed": "brick"}
    assert calculate_valuation(obj)["full_cost_rub"] == 6_000_000
    assert calculate_valuation(obj)["breakdown"]["material_source"] == "fixed"
    obj["project"].pop("wall_material")
    obj["material"] = {"observed": "hidden"}
    result = calculate_valuation(obj)
    assert result["status"] == "insufficient_data" and result["value_rub"] is None
    result = calculate_valuation(
        obj, {"unit_cost_rub_m2": 30000, "rate_note": "Contractor estimate"}
    )
    assert result["full_cost_rub"] == 3_000_000 and not result["is_demo"]
    assert result["breakdown"]["rate_source"] == "manual"


@pytest.mark.parametrize(
    "material,expected",
    [
        ("brick", 6_000_000),
        ("aerated_concrete", 5_000_000),
        ("ceramic_block", 5_500_000),
        ("timber", 4_800_000),
        ("frame", 4_000_000),
        ("sip", 4_200_000),
        ("monolith", 6_500_000),
    ],
)
def test_full_cost_can_be_calculated_before_photos(material, expected):
    obj = {"project": None, "material": None, "last_flight": None}
    result = calculate_valuation(obj, {"area_m2": 100, "wall_material": material})
    assert result["full_cost_rub"] == expected
    assert result["status"] == "partial"
    assert result["completed_cost_rub"] is None
    assert result["remaining_cost_rub"] is None
    assert result["missing_data"] == ["confirmed_readiness"]
    assert result["breakdown"]["material_source"] == "manual"
    assert result["breakdown"]["readiness_status"] == "no_flight"


def test_cost_material_override_is_separate_from_inspection_and_project():
    obj = house()
    obj["material"] = {"observed": "aerated_concrete"}
    before = deepcopy(obj)
    result = calculate_valuation(obj, {"wall_material": "brick"})
    assert result["full_cost_rub"] == 6_000_000
    assert result["completed_cost_rub"] == 2_400_000
    assert result["remaining_cost_rub"] == 3_600_000
    assert obj == before
    assert calculate_valuation(obj, {"wall_material": None})["full_cost_rub"] == 5_000_000
    assert (
        calculate_valuation(obj, {"wall_material": "brick", "unit_cost_rub_m2": 30000})[
            "full_cost_rub"
        ]
        == 3_000_000
    )


def test_demo_tariff_snapshot_matches_authoritative_yaml():
    path = Path(__file__).resolve().parents[1] / "front/js/valuation-demo-config.js"
    snapshot = json.loads(
        path.read_text(encoding="utf-8").split("export default ", 1)[1].strip().removesuffix(";")
    )
    assert snapshot == valuation_config().model_dump(mode="json")


def test_land_stays_separate_and_rounding_adds_up():
    result = calculate_valuation(house(33), {"area_m2": "99.999", "land_rub": 700000})
    assert result["completed_cost_rub"] + result["remaining_cost_rub"] == result["full_cost_rub"]
    assert result["value_rub"] == result["completed_cost_rub"] + 700000
    assert result["value_includes_land"] is True
    without_land = calculate_valuation(house(33), {"area_m2": "99.999"})
    for key in ["full_cost_rub", "completed_cost_rub", "remaining_cost_rub"]:
        assert result[key] == without_land[key]


@pytest.fixture
def setup(tmp_path):
    settings = Settings(storage_dir=tmp_path / "storage")
    with TestClient(create_app(settings)) as client:
        object_id = new_object(
            client, project={"wall_material": "aerated_concrete", "total_area_m2": 100}
        )
        flight_id = upload(client, object_id).json()["flight_id"]
        assert Worker(settings).process_one()
        response = client.post(
            f"/api/v1/flights/{flight_id}/review",
            json={
                "action": "correct",
                "stage": "walls",
                "readiness_pct": 40,
                "bank_readiness": 50,
                "comment": "Checked photos",
            },
        )
        assert response.status_code == 201
        yield client, settings, object_id, flight_id


def test_api_persistence_reset_history_and_refresh_after_review(setup):
    client, settings, obj, flight = setup
    url = f"/api/v1/objects/{obj}/valuation"
    assert client.get("/api/v1/meta").json()["capabilities"]["valuation"] is True
    calculated = client.post(
        url, headers={"X-User": "reviewer"}, json={"area_m2": 120, "land_rub": 500000}
    ).json()
    assert calculated["completed_cost_rub"] == 2_400_000
    assert calculated["author"] == "reviewer"
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(url).json()["full_cost_rub"] == 6_000_000
        assert restarted.get(url + "/history").json()[0]["stale"] is False
        assert (
            restarted.post(url, json={"unit_cost_rub_m2": 40000}).json()["full_cost_rub"]
            == 4_800_000
        )
        assert (
            restarted.post(url, json={"area_m2": None, "unit_cost_rub_m2": None}).json()[
                "full_cost_rub"
            ]
            == 5_000_000
        )
    old = client.get(url + "/history").json()
    assert len(old) == 3 and old[0]["stale"] is False and old[-1]["stale"] is True
    client.post(
        f"/api/v1/flights/{flight}/review",
        json={
            "action": "correct",
            "stage": "roof",
            "readiness_pct": 60,
            "comment": "New inspection",
        },
    )
    assert client.get(url).json()["completed_cost_rub"] == 3_000_000
    assert all(run["stale"] for run in client.get(url + "/history").json())
    assert (
        client.get(url + "/history").json()[0]["completed_cost_rub"] == old[0]["completed_cost_rub"]
    )
    client.post(
        f"/api/v1/flights/{flight}/material/review",
        json={
            "action": "correct",
            "material": "brick",
            "comment": "Checked documents",
        },
    )
    assert (
        client.get(f"/api/v1/objects/{obj}").json()["valuation"]["completed_cost_rub"] == 3_600_000
    )


def test_api_material_selection_persists_resets_and_invalidates_history(setup):
    client, settings, obj, _ = setup
    url = f"/api/v1/objects/{obj}/valuation"
    selected = client.post(url, json={"wall_material": "brick"}).json()
    assert selected["full_cost_rub"] == 6_000_000
    assert selected["completed_cost_rub"] == 2_400_000
    card = client.get(f"/api/v1/objects/{obj}").json()
    assert card["valuation_inputs"]["wall_material"] == "brick"
    assert card["project"]["wall_material"] == "aerated_concrete"
    assert card["material"] is None
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(url).json()["full_cost_rub"] == 6_000_000
        assert restarted.post(url, json={"area_m2": 120}).json()["full_cost_rub"] == 7_200_000
        assert (
            restarted.post(url, json={"wall_material": None}).json()["full_cost_rub"] == 6_000_000
        )
    history = client.get(url + "/history").json()
    assert history[-1]["full_cost_rub"] == 6_000_000
    assert history[-1]["stale"] is True
    assert history[0]["breakdown"]["material_source"] == "project"


def test_api_empty_object_only_needs_area_and_material_for_full_cost(tmp_path):
    with TestClient(create_app(Settings(storage_dir=tmp_path / "storage"))) as client:
        obj = new_object(client)
        response = client.post(
            f"/api/v1/objects/{obj}/valuation",
            json={"area_m2": 120, "wall_material": "aerated_concrete"},
        )
        assert response.status_code == 200
        value = response.json()
        assert value["full_cost_rub"] == 6_000_000
        assert value["completed_cost_rub"] is None
        assert set(value["material_rates"]) == set(valuation_config().rates_rub_m2)


@pytest.mark.parametrize(
    "payload",
    [
        {"area_m2": 0},
        {"area_m2": -1},
        {"area_m2": "NaN"},
        {"area_m2": "Infinity"},
        {"area_m2": 1000001},
        {"unit_cost_rub_m2": 0},
        {"unit_cost_rub_m2": -100},
        {"land_rub": -1},
        {"collateral_value_rub": 10000},
        {"readiness_pct": 100},
        {"wall_material": "hidden"},
        {"wall_material": "unclear"},
        {"wall_material": "unknown"},
        {"wall_material": 13},
    ],
)
def test_api_rejects_bad_inputs_without_changing_saved_data(tmp_path, payload):
    with TestClient(create_app(Settings(storage_dir=tmp_path / "s"))) as client:
        obj = new_object(client)
        url = f"/api/v1/objects/{obj}/valuation"
        assert client.post(url, json=payload).status_code == 400
        assert client.get(url + "/history").json() == []
        assert client.get(f"/api/v1/objects/{obj}").json()["valuation_inputs"] == {}
        assert client.get("/api/v1/objects/absent/valuation").status_code == 404


def test_bad_optional_tariffs_do_not_break_object_access_or_worker(setup, monkeypatch, tmp_path):
    client, settings, obj, _ = setup
    from agents.valuation import config

    bad = tmp_path / "bad.yaml"
    bad.write_text("rates_rub_m2: [", encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", bad)
    result = client.get(f"/api/v1/objects/{obj}")
    assert result.status_code == 200
    assert result.json()["valuation"]["status"] == "configuration_error"
    flight = upload(client, obj).json()["flight_id"]
    assert Worker(settings).process_one()
    assert client.get(f"/api/v1/flights/{flight}").json()["status"] == "done"


def test_budget_facts_invalidate_ai_summary_without_leaking_free_text():
    obj = house()
    obj["valuation"] = calculate_valuation(
        obj, {"rate_note": "PRIVATE_NOTE", "unit_cost_rub_m2": 30000}
    )
    context = build_context(obj)
    assert "PRIVATE_NOTE" not in json.dumps(context)
    assert context["facts"]["budget.full_cost_rub"] == 3_000_000
    changed = deepcopy(obj)
    changed["valuation"] = calculate_valuation(obj, {"unit_cost_rub_m2": 40000})
    assert fingerprint(context) != fingerprint(build_context(changed))


def test_tariff_version_changes_fingerprint_without_overwriting_snapshot():
    config = valuation_config()
    result = calculate_valuation(house(), config=config)
    changed = calculate_valuation(
        house(), config=config.model_copy(update={"version": "new-version"})
    )
    assert result["fingerprint"] != changed["fingerprint"]
