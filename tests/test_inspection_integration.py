import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from test_material_service import ReadyModel
from test_readiness import RafterDetector, upload_full

from agents.readiness.yolo import YoloDetector
from api.config import Settings
from api.main import create_app
from worker.service import Worker


@pytest.fixture
def settings(tmp_path):
    return Settings(storage_dir=tmp_path / "storage")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as value:
        yield value


def prepare(client):
    obj = client.post("/api/v1/objects", json={"address": "Integration house"}).json()["id"]
    flight = upload_full(client, obj)
    detector = RafterDetector()
    detector.views = {
        item["id"]: item["view"] for item in client.get(f"/api/v1/flights/{flight}/images").json()
    }
    return obj, flight, detector


def test_readiness_can_run_without_material_model(client, settings):
    _, flight, detector = prepare(client)
    worker = Worker(replace(settings, mock=False), detector=detector)
    assert worker.process_one()
    value = client.get(f"/api/v1/flights/{flight}").json()
    assert value["status"] == "done"
    assert value["result"]["stage"] == "roof" and not value["result"]["is_demo"]
    assert value["material_result"]["status"] == "model_unavailable"
    assert value["module_errors"]["material"]["code"] == "MODEL_NOT_CONFIGURED"


@pytest.mark.parametrize("failed", ["material", "readiness"])
def test_model_failure_keeps_other_result(client, settings, failed):
    _, flight, detector = prepare(client)

    class Broken:
        version = "broken@test"
        is_demo = False

        def predict(self, *args):
            raise RuntimeError("test model failure")

        detect = predict

    worker = Worker(
        settings,
        predictor=Broken() if failed == "material" else ReadyModel(),
        detector=Broken() if failed == "readiness" else detector,
    )
    assert worker.process_one()
    value = client.get(f"/api/v1/flights/{flight}").json()
    assert value["status"] == "done"
    assert value["module_errors"][failed]["code"] == "INFERENCE_FAILED"
    if failed == "material":
        assert value["result"]["stage"] == "roof"
        assert value["material_result"]["status"] == "model_unavailable"
    else:
        assert value["material_result"]["material"] == "timber"
        assert value["material_result"]["fixed_on_object"]
        assert value["result"]["status"] == "model_unavailable"


def test_review_contract_is_separate_and_accepts_null_comment(client, settings):
    obj, flight, detector = prepare(client)
    Worker(settings, predictor=ReadyModel(), detector=detector).process_one()
    original = client.get(f"/api/v1/flights/{flight}").json()
    accepted = client.post(
        f"/api/v1/flights/{flight}/review", json={"action": "accept", "comment": None}
    )
    assert accepted.status_code == 201
    assert accepted.json()["comment"] == ""
    correction = client.post(
        f"/api/v1/flights/{flight}/material/review",
        json={"action": "correct", "material": "brick", "comment": "Confirmed"},
    )
    assert correction.status_code == 201
    value = client.get(f"/api/v1/flights/{flight}").json()
    assert value["review"] == value["readiness_review"]
    assert value["review"]["stage"] == "roof"
    assert value["material_review"]["material"] == "brick"
    assert value["result"] == original["result"]
    assert value["material_result"] == original["material_result"]
    assert {item["kind"] for item in client.get(f"/api/v1/objects/{obj}/actions").json()} == {
        "material",
        "readiness",
    }


def test_manual_readiness_without_model_updates_card_history_and_filters(client, settings):
    obj, flight, _ = prepare(client)
    Worker(settings).process_one()
    response = client.post(
        f"/api/v1/flights/{flight}/review",
        json={
            "action": "correct",
            "stage": "walls",
            "readiness_pct": 45,
            "bank_readiness": 50,
            "comment": "Checked manually",
        },
    )
    assert response.status_code == 201
    with TestClient(create_app(settings)) as restarted:
        card = restarted.get(f"/api/v1/objects/{obj}").json()
        assert card["data_status"] == card["readiness_data_status"] == "ok"
        assert card["stage"] == "walls" and card["readiness_pct"] == 45
        assert card["last_flight"]["result"]["stage"] is None
        fact = restarted.get(f"/api/v1/objects/{obj}/history").json()["fact"][0]
        assert fact["reviewed"] and fact["readiness_pct"] == 45
        assert (
            restarted.get("/api/v1/objects?q=integration&stage=walls&data_status=ok").json()[
                "total"
            ]
            == 1
        )
        assert restarted.get("/api/v1/objects?stage=roof").json()["total"] == 0


def test_yolo_maps_names_from_weights_instead_of_enum_order(tmp_path, monkeypatch):
    calls = []

    class Tensor:
        def __init__(self, values):
            self.values = values

        def cpu(self):
            return self

        def tolist(self):
            return self.values

    boxes = SimpleNamespace(
        cls=Tensor([0, 11]), conf=Tensor([0.9, 0.8]), xyxyn=Tensor([[0.1, 0.1, 0.9, 0.9]] * 2)
    )
    model = SimpleNamespace(
        names={0: "paving", 11: "walls_full"},
        predict=lambda **kwargs: calls.append(kwargs) or [SimpleNamespace(boxes=boxes)],
    )
    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=lambda _: model))
    weights = tmp_path / "test.pt"
    weights.write_bytes(b"test")
    result = YoloDetector(weights).detect(Image.new("RGB", (10, 10)), "img_test")
    assert [row.cls for row in result] == ["paving", "walls_full"]
    assert calls[0]["imgsz"] == 1280


def test_yolo_requires_existing_local_weights(tmp_path):
    with pytest.raises(ValueError, match="READINESS_WEIGHTS"):
        YoloDetector(tmp_path / "missing.pt")
