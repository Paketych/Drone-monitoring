import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from test_material_service import new_object, photo, upload

from agents.material.aggregation import aggregate
from agents.material.yolo import YoloMaterialPredictor, padded_bbox
from agents.readiness.aggregation import aggregate as aggregate_readiness
from agents.targeting import exclusion_reason
from api.config import Settings
from api.main import create_app
from api.presentation import material_result_response
from schemas.material import ViewObservation
from schemas.readiness import Detection
from worker.service import Worker


def wall(image_id="img", *, box=(0.1, 0.2, 0.6, 0.7), conf=0.91, cls="walls_full", included=True):
    return Detection(image_id=image_id, cls=cls, conf=conf, bbox=box, target_included=included)


def classifier(tmp_path, monkeypatch, *, name="brick", conf=0.84):
    calls = []
    # Deliberately different indices from configs/materials.yaml.
    names = {0: "aerated_concrete", 1: "brick", 2: "hidden", 3: "unclear"}
    index = next(key for key, value in names.items() if value == name)

    def predict(**kwargs):
        calls.append({**kwargs, "size": kwargs["source"].size})
        return [SimpleNamespace(probs=SimpleNamespace(top1=index, top1conf=conf))]

    model = SimpleNamespace(task="classify", names=names, predict=predict)
    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=lambda _: model))
    weights = tmp_path / "material.pt"
    weights.write_bytes(b"test fixture, not model weights")
    return YoloMaterialPredictor(weights), calls


def test_material_uses_actual_class_names_and_original_crop_with_margin(tmp_path, monkeypatch):
    predictor, calls = classifier(tmp_path, monkeypatch)
    image = Image.new("RGB", (1000, 800))
    prediction = predictor.predict_walls(image, "img", [wall(), wall(cls="window_installed")])[0]
    assert len(calls) == 1 and calls[0]["imgsz"] == 384
    assert calls[0]["size"] == (600, 480)
    assert prediction.material == "brick"  # Index 1 is NOT aerated_concrete here.
    assert prediction.bbox == wall().bbox
    assert prediction.crop_bbox == pytest.approx((0.05, 0.15, 0.65, 0.75))
    assert prediction.confidence == prediction.material_conf == 0.84
    assert prediction.detector_confidence == 0.91
    public = material_result_response({"per_view": [prediction.model_dump(mode="json")]})[
        "per_view"
    ][0]
    assert public["cls"] == "walls_full" and public["material"] == "brick"
    assert public["conf"] == 0.91 and public["material_conf"] == 0.84
    assert public["class"] == "brick"  # Legacy material alias.


def test_crop_margin_is_clamped_to_original_image():
    assert padded_bbox((0, 0, 1, 1)) == (0, 0, 1, 1)


@pytest.mark.parametrize("name", ["hidden", "unclear"])
def test_hidden_and_unclear_do_not_vote(tmp_path, monkeypatch, name):
    predictor, _ = classifier(tmp_path, monkeypatch, name=name, conf=0.99)
    value = predictor.predict_walls(Image.new("RGB", (200, 200)), "img", [wall()])[0]
    result = aggregate(
        [ViewObservation(**value.model_dump())],
        [{"id": "img", "view": "N", "is_overhead": False, "sha256": "a"}],
        model=predictor.version,
        is_demo=False,
        project_material="brick",
        target_confirmed=True,
    )
    assert result.votes == {} and not result.fixed_on_object
    assert result.per_view[0].material == name
    assert result.status == ("hidden" if name == "hidden" else "insufficient_data")


def test_walls_outside_target_are_not_sent_to_classifier(tmp_path, monkeypatch):
    predictor, calls = classifier(tmp_path, monkeypatch)
    result = predictor.predict_walls(
        Image.new("RGB", (200, 200)), "img", [wall(included=False), wall(cls="fence")]
    )
    assert calls == [] and result[0].visibility == "no_walls"


def test_tiny_wall_is_not_sent_to_classifier(tmp_path, monkeypatch):
    predictor, calls = classifier(tmp_path, monkeypatch, conf=0.99)
    image = Image.new("RGB", (200, 200))
    # With the 10% margin: 0.12 * 200 = 24 px < 32 px, the training cut-off.
    tiny = wall(box=(0.1, 0.1, 0.2, 0.2))
    value = predictor.predict_walls(image, "img", [tiny])[0]
    assert calls == []
    assert value.material == value.visibility == "unclear"
    assert value.predicted_material is None and value.material_conf is None
    assert value.detector_confidence == tiny.conf and value.crop_bbox is not None
    result = aggregate(
        [ViewObservation(**value.model_dump())],
        [{"id": "img", "view": "N", "is_overhead": False, "sha256": "a"}],
        model=predictor.version,
        is_demo=False,
        project_material=None,
        target_confirmed=True,
    )
    assert result.votes == {} and result.needs_reshoot
    assert result.review_reasons == ["NO_RELIABLE_WALL_VIEWS"]
    # A wall of normal size on the same frame is still classified.
    assert predictor.predict_walls(image, "img", [tiny, wall()])[1].material == "brick"
    assert len(calls) == 1


def test_low_wall_confidence_cannot_become_strong_material(tmp_path, monkeypatch):
    predictor, _ = classifier(tmp_path, monkeypatch, conf=0.99)
    value = predictor.predict_walls(Image.new("RGB", (200, 200)), "img", [wall(conf=0.3)])[0]
    result = aggregate(
        [ViewObservation(**value.model_dump())],
        [{"id": "img", "view": "N", "is_overhead": False, "sha256": "a"}],
        model=predictor.version,
        is_demo=False,
        project_material=None,
        target_confirmed=True,
    )
    assert not result.fixed_on_object and result.votes == {}
    assert result.per_view[0].exclusion_reason == "LOW_DETECTOR_CONFIDENCE"


def test_brick_overlapping_finish_requires_structure_review(tmp_path, monkeypatch):
    predictor, _ = classifier(tmp_path, monkeypatch, conf=0.94)
    observations, images = [], []
    for view in ["N", "E"]:
        found = predictor.predict_walls(
            Image.new("RGB", (200, 200)), view, [wall(view), wall(view, cls="facade_finish")]
        )[0]
        observations.append(ViewObservation(**found.model_dump()))
        images.append({"id": view, "view": view, "is_overhead": False, "sha256": view})
    result = aggregate(
        observations,
        images,
        model=predictor.version,
        is_demo=False,
        project_material="brick",
        target_confirmed=True,
    )
    assert result.status == "needs_review" and not result.fixed_on_object
    assert "CLADDING_MAY_HIDE_STRUCTURE" in result.review_reasons


def test_neighbor_is_excluded_from_readiness():
    image = {
        "id": "img",
        "view": "N",
        "sha256": "a",
        "is_overhead": False,
        "target_bbox": (0, 0, 0.5, 1),
    }
    detections = [
        wall(box=(0.05, 0.2, 0.45, 0.9), cls="foundation"),
        wall(box=(0.6, 0.1, 0.95, 0.9)),
    ]
    for item in detections:
        item.target_exclusion_reason = exclusion_reason(item.bbox, image)
        item.target_included = item.target_exclusion_reason is None
    result = aggregate_readiness(
        detections, [image], model="fixture", is_demo=False, target_confirmed=True
    )
    assert result.stage == "foundation" and result.readiness_pct == 15
    assert detections[1].target_exclusion_reason == "OUTSIDE_TARGET"
    assert exclusion_reason((0.4, 0.1, 0.8, 0.9), image) == "OUTSIDE_TARGET"


def test_worker_shares_detections_with_classifier_and_keeps_neighbor_evidence(
    tmp_path, monkeypatch
):
    predictor, calls = classifier(tmp_path, monkeypatch, name="aerated_concrete", conf=0.95)

    class Detector:
        version, is_demo, calls = "fixture", False, 0

        def detect(self, image, image_id):
            self.calls += 1
            return [
                wall(image_id, box=(0.05, 0.1, 0.45, 0.9)),
                wall(image_id, box=(0.6, 0.1, 0.95, 0.9)),
            ]

    detector = Detector()
    settings = Settings(storage_dir=tmp_path / "storage")
    with TestClient(create_app(settings)) as client:
        obj = new_object(client)
        response = client.post(
            f"/api/v1/objects/{obj}/flights",
            headers={"X-User": "tester"},
            data={
                "views": '["N", "E"]',
                "target_confirmed": "true",
                "meta": '[{"target_bbox":[0,0,0.5,1]},{"target_bbox":[0,0,0.5,1]}]',
            },
            files=[("files", ("n.jpg", photo())), ("files", ("e.jpg", photo("blue")))],
        )
        assert response.status_code == 202, response.text
        flight = response.json()["flight_id"]
        assert Worker(settings, predictor=predictor, detector=detector).process_one()
        result = client.get(f"/api/v1/flights/{flight}").json()
        assert result["material_result"]["material"] == "aerated_concrete"
        assert result["material_result"]["fixed_on_object"]
        assert (
            detector.calls == len(calls) == 2
        )  # One detector pass per image; one selected wall per image.
        for im in client.get(f"/api/v1/flights/{flight}/images").json():
            assert im["target_bbox"] == [0, 0, 0.5, 1] and im["target_selected_by"] == "tester"
            assert len(im["detections"]) == 2
            assert im["detections"][1]["target_included"] is False
            assert im["detections"][1]["counted"] is False


def test_classifier_does_not_invent_walls_if_detector_is_unavailable(tmp_path, monkeypatch):
    predictor, calls = classifier(tmp_path, monkeypatch)
    settings = Settings(storage_dir=tmp_path / "storage", mock=False)
    with TestClient(create_app(settings)) as client:
        obj = new_object(client)
        flight = upload(client, obj).json()["flight_id"]
        assert Worker(settings, predictor=predictor).process_one()
        result = client.get(f"/api/v1/flights/{flight}").json()
        assert result["status"] == "done" and calls == []
        assert result["material_result"]["status"] == "model_unavailable"
        assert result["module_errors"]["material"]["code"] == "DETECTOR_REQUIRED"


@pytest.mark.parametrize(
    "box", [[-0.1, 0, 1, 1], [0.9, 0, 0.1, 1], [0, 0, 0, 1], [0, 0, 1], [0, 0, 2, 1]]
)
def test_invalid_target_regions_are_rejected(tmp_path, box):
    import json

    with TestClient(create_app(Settings(storage_dir=tmp_path / "s"))) as client:
        obj = new_object(client)
        response = client.post(
            f"/api/v1/objects/{obj}/flights",
            data={"views": '["N"]', "meta": json.dumps([{"target_bbox": box}])},
            files=[("files", ("n.jpg", photo()))],
        )
        assert response.status_code == 400
        assert client.get(f"/api/v1/objects/{obj}/flights").json() == []


def test_material_requires_local_weights(tmp_path):
    with pytest.raises(ValueError, match="MATERIAL_WEIGHTS"):
        YoloMaterialPredictor(tmp_path / "missing.pt")
