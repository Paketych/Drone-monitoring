import hashlib
import io
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from agents.material.aggregation import aggregate
from api.config import Settings
from api.database import Database
from api.main import create_app
from schemas.material import Observation, ViewObservation
from worker.service import Worker


def photo(colour="red", image_format="JPEG"):
    buffer = io.BytesIO()
    Image.new("RGB", (120, 80), colour).save(buffer, image_format)
    return buffer.getvalue()


@pytest.fixture
def settings(tmp_path):
    return Settings(storage_dir=tmp_path / "storage")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as value:
        yield value


def new_object(client, **extra):
    response = client.post("/api/v1/objects", json={"address": "Test house", **extra})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def upload(client, object_id, *, target=True):
    return client.post(
        f"/api/v1/objects/{object_id}/flights",
        data={
            "views": '["N", "E"]',
            "target_confirmed": str(target).lower(),
        },
        files=[("files", ("north.jpg", photo())), ("files", ("east.jpg", photo("blue")))],
    )


class ReadyModel:
    """Fixed predictions exercise integration; this is not an actual trained model."""

    version = "test-model@v1"
    is_demo = False

    def predict(self, image, image_id):
        return [
            Observation(
                image_id=image_id,
                visibility="visible",
                material="timber",
                confidence=0.92,
                crop_bbox=(0.1, 0.1, 0.9, 0.9),
            )
        ]


def test_frontend_object_payload_is_saved_across_restart(client, settings):
    payload = {
        "address": "Test house",
        "cadastral": "54:19:000001:17",
        "loan_id": "IZH-2026-17",
        "started_at": "2026-01-01",
        "planned_completion_at": "2027-10-31",
        "actual_completion_at": None,
        "overdue": False,
        "registered": True,
        "pledged": False,
        "has_aerial_photo": True,
        "project": {
            "wall_material": "timber",
            "floors": 2,
            "footprint_m2": 96,
            "total_area_m2": 180,
            "source": "Project 17",
        },
        "loan": {
            "amount": 6000000,
            "dpd": 0,
            "collateral_value_rub": 5200000,
            "tranches": [
                {"n": 1, "amount": 2000000, "paid_at": "2026-01-02"},
                {"n": 2, "amount": 2500000, "condition_stage": "walls"},
            ],
        },
        "checkpoints": [{"stage": "walls", "due": "2027-01-01"}],
    }
    response = client.post("/api/v1/objects", json=payload)
    assert response.status_code == 201, response.text
    object_id = response.json()["id"]
    with TestClient(create_app(settings)) as restarted:
        stored = restarted.get(f"/api/v1/objects/{object_id}").json()
        for key in payload.keys() - {"loan"}:
            assert stored[key] == payload[key]
        assert stored["loan"]["tranches"][1]["condition_stage"] == "walls"
        assert stored["loan"]["tranches"][0]["paid_at"] == "2026-01-02"
        assert stored["loan"]["amount"] == 6000000


@pytest.mark.parametrize(
    "payload",
    [
        {"unexpected": True},
        {"started_at": "2026-10-31", "planned_completion_at": "2026-01-01"},
        {"started_at": "2026-10-31", "actual_completion_at": "2026-01-01"},
        {"started_at": "2026-10-31", "checkpoints": [{"stage": "walls", "due": "2026-01-01"}]},
        {
            "planned_completion_at": "2026-01-01",
            "checkpoints": [{"stage": "walls", "due": "2026-10-31"}],
        },
        {"checkpoints": [{"stage": "invented", "due": "2026-10-31"}]},
        {"project": {"floors": 0}},
        {"loan": {"amount": -1}},
    ],
)
def test_object_validation_stays_strict(client, payload):
    response = client.post("/api/v1/objects", json={"address": "Test house", **payload})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert client.get("/api/v1/objects").json()["total"] == 0


def test_meta_describes_material_scope_and_display_states(client):
    meta = client.get("/api/v1/meta").json()
    assert meta["scope"] == "inspection"
    assert meta["capabilities"]["material_review"] is True
    assert all(meta["capabilities"][key] is False for key in ("risk", "alerts", "tasks"))
    assert meta["capabilities"]["history"] and meta["capabilities"]["readiness"]
    assert {"needs_review", "mixed", "model_unavailable"} <= {
        row["id"] for row in meta["material_statuses"]
    }
    assert {"hidden", "unclear"} <= {row["id"] for row in meta["materials"]}


def test_material_public_contract_and_review_comparison(client, settings):
    object_id = new_object(client, project={"wall_material": "aerated_concrete"})
    flight_id = upload(client, object_id).json()["flight_id"]
    Worker(settings, predictor=ReadyModel()).process_one()
    result = client.get(f"/api/v1/flights/{flight_id}").json()["material_result"]
    crop = result["per_view"][0]
    assert crop["class"] == crop["material"] == "timber"
    assert crop["conf"] == crop["confidence"] == 0.92
    fixed = client.get(f"/api/v1/objects/{object_id}").json()["material"]
    assert fixed["project"] == "aerated_concrete" and fixed["match"] == "mismatch"
    # Public compatibility fields do not change the stored ML contract.
    with Database(settings.database_path).connection() as db:
        raw = json.loads(
            db.execute("SELECT result FROM flights WHERE id=?", (flight_id,)).fetchone()[0]
        )
    assert "class" not in raw["per_view"][0] and "conf" not in raw["per_view"][0]
    review = client.post(
        f"/api/v1/flights/{flight_id}/material/review",
        json={"action": "correct", "material": "aerated_concrete", "comment": "Checked"},
    )
    assert review.status_code == 201
    fixed = client.get(f"/api/v1/objects/{object_id}").json()["material"]
    assert fixed["observed"] == fixed["project"] == "aerated_concrete"
    assert fixed["match"] == "match" and fixed["reviewed"]
    assert client.get(f"/api/v1/flights/{flight_id}").json()["material_result"] == result


def test_object_list_exposes_latest_flight_and_unconfirmed_result(client, settings, monkeypatch):
    monkeypatch.setattr("api.database.utc_now", lambda: "2026-10-31T10:00:00+00:00")
    object_id = new_object(client, project={"wall_material": "timber"})
    item = client.get("/api/v1/objects").json()["items"][0]
    assert item["last_flight_id"] is None and item["data_status"] == "no_data"
    first_id = upload(client, object_id, target=False).json()["flight_id"]
    assert client.get("/api/v1/objects").json()["items"][0]["data_status"] == "processing"
    Worker(settings, predictor=ReadyModel()).process_one()
    item = client.get("/api/v1/objects").json()["items"][0]
    assert item["material"] is None  # An unconfirmed candidate is not a fixed material.
    assert item["material_status"] == "needs_review" and item["data_status"] == "review"
    assert item["last_flight"]["material_result"]["material"] == "timber"
    assert "TARGET_NOT_CONFIRMED" in item["last_flight"]["material_result"]["review_reasons"]
    review = client.post(f"/api/v1/flights/{first_id}/material/review", json={"action": "accept"})
    assert review.status_code == 201
    assert client.get(f"/api/v1/objects/{object_id}").json()["data_status"] == "ok"
    second_id = upload(client, object_id).json()["flight_id"]
    item = client.get("/api/v1/objects").json()["items"][0]
    assert item["last_flight_id"] == second_id  # Upload times intentionally coincide.
    assert item["last_flight"]["flight_id"] == second_id
    assert item["data_status"] == "processing"
    assert item["material"]["fixed_at_flight_id"] == first_id
    assert client.get(f"/api/v1/objects/{object_id}").json() == item


def test_upload_worker_and_restart_preserve_originals(client, settings):
    object_id = new_object(client)
    response = upload(client, object_id)
    assert response.status_code == 202, response.text
    flight_id = response.json()["flight_id"]
    assert client.get(f"/api/v1/flights/{flight_id}").json()["status"] == "queued"
    images = client.get(f"/api/v1/flights/{flight_id}/images").json()
    assert len(images) == 2
    assert images[0]["sha256"] == hashlib.sha256(photo()).hexdigest()
    assert "storage_name" not in images[0]
    assert client.get(images[0]["thumb_url"]).status_code == 409
    assert Worker(settings).process_one()
    result = client.get(f"/api/v1/flights/{flight_id}").json()
    assert result["status"] == "done"
    assert result["material_result"]["status"] == "model_unavailable"
    assert result["material_result"]["is_demo"]
    assert result["material_result"]["material"] == "unclear"
    assert client.get(images[0]["url"]).content == photo()
    assert client.get(images[0]["thumb_url"]).headers["content-type"] == "image/jpeg"
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(f"/api/v1/flights/{flight_id}").json()["status"] == "done"
        assert restarted.get(f"/api/v1/objects/{object_id}").json()["material"] is None


def test_real_adapter_and_human_correction_keep_raw_result(client, settings):
    object_id = new_object(client, project={"wall_material": "aerated_concrete"})
    flight_id = upload(client, object_id).json()["flight_id"]
    worker = Worker(settings, predictor=ReadyModel())
    worker.process_one()
    flight = client.get(f"/api/v1/flights/{flight_id}").json()
    original = flight["material_result"]
    assert original["status"] == "determined"
    assert original["material"] == "timber"
    assert original["match"] == "mismatch"
    assert original["fixed_on_object"]
    assert client.get(original["per_view"][0]["crop_url"]).status_code == 200
    correction = client.post(
        f"/api/v1/flights/{flight_id}/material/review",
        headers={
            "X-User": "inspector",
        },
        json={"action": "correct", "material": "brick", "comment": "Checked on site"},
    )
    assert correction.status_code == 201
    assert client.get(f"/api/v1/flights/{flight_id}").json()["material_result"] == original
    current = client.get(f"/api/v1/objects/{object_id}").json()["material"]
    assert current["observed"] == "brick" and current["reviewed"]
    # A later automated result must not overwrite a human correction.
    next_flight = upload(client, object_id).json()["flight_id"]
    worker.process_one()
    next_result = client.get(f"/api/v1/flights/{next_flight}").json()["material_result"]
    assert next_result["status"] == "needs_review"
    assert "PREVIOUS_MATERIAL_CONFLICT" in next_result["review_reasons"]
    assert client.get(f"/api/v1/objects/{object_id}").json()["material"]["observed"] == "brick"


@pytest.mark.parametrize(
    "bad_field,bad_value",
    [
        ("views", "not json"),
        ("views", '["N"]'),
        ("views", '["wrong","E"]'),
        ("meta", '[{"gps":[91,10]},null]'),
        ("flown_at", "2026-10-31T10:00:00"),
    ],
)
def test_bad_upload_metadata_creates_no_job(client, settings, bad_field, bad_value):
    object_id = new_object(client)
    data = {"views": '["N","E"]', bad_field: bad_value}
    response = client.post(
        f"/api/v1/objects/{object_id}/flights",
        data=data,
        files=[
            ("files", ("a.jpg", photo())),
            ("files", ("b.jpg", photo("blue"))),
        ],
    )
    assert response.status_code == 400
    assert client.get(f"/api/v1/objects/{object_id}/flights").json() == []
    assert not list(settings.storage_dir.rglob("*.upload"))


def test_mixed_valid_and_invalid_batch_is_atomic(client, settings):
    object_id = new_object(client)
    response = client.post(
        f"/api/v1/objects/{object_id}/flights",
        data={
            "views": '["N","E"]',
        },
        files=[("files", ("good.jpg", photo())), ("files", ("fake.jpg", b"not an image"))],
    )
    assert response.status_code == 415
    assert client.get(f"/api/v1/objects/{object_id}/flights").json() == []
    assert not list(settings.storage_dir.rglob("*.jpg"))


def test_duplicate_does_not_count_as_second_facade(client):
    object_id = new_object(client)
    response = client.post(
        f"/api/v1/objects/{object_id}/flights",
        data={
            "views": '["N","E"]',
        },
        files=[("files", ("a.jpg", photo())), ("files", ("b.jpg", photo()))],
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "DUPLICATE_IMAGE"


def test_png_and_manual_gps(client, settings):
    object_id = new_object(client)
    response = client.post(
        f"/api/v1/objects/{object_id}/flights",
        data={
            "views": '["N"]',
            "meta": '[{"gps":[55.1,83.2]}]',
        },
        files=[("files", ("test.png", photo(image_format="PNG")))],
    )
    assert response.status_code == 202, response.text
    flight_id = response.json()["flight_id"]
    image = client.get(f"/api/v1/flights/{flight_id}/images").json()[0]
    assert image["gps"] == [55.1, 83.2] and image["gps_source"] == "meta"
    Worker(settings).process_one()
    assert client.get(f"/api/v1/flights/{flight_id}").json()["status"] == "done"


def test_drone_mpo_jpeg_uses_primary_frame_and_preserves_container(client, settings):
    buffer = io.BytesIO()
    Image.new("RGB", (120, 80), "red").save(
        buffer, "MPO", save_all=True, append_images=[Image.new("RGB", (40, 30), "blue")]
    )
    original = buffer.getvalue()
    object_id = new_object(client)
    response = client.post(
        f"/api/v1/objects/{object_id}/flights",
        data={"views": '["N"]'},
        files=[("files", ("DJI.JPG", original))],
    )
    assert response.status_code == 202, response.text
    flight_id = response.json()["flight_id"]
    image = client.get(f"/api/v1/flights/{flight_id}/images").json()[0]
    assert image["format"] == "JPEG" and image["container_format"] == "MPO"
    assert image["frame_count"] == 2 and image["width"] == 120
    Worker(settings).process_one()
    assert client.get(f"/api/v1/flights/{flight_id}").json()["status"] == "done"
    assert client.get(image["url"]).content == original
    with Image.open(io.BytesIO(client.get(image["thumb_url"]).content)) as preview:
        red, _, blue = preview.getpixel((50, 30))
        assert red > blue


def test_upload_size_limit_removes_partial_file(settings):
    small = replace(settings, max_file_bytes=100)
    with TestClient(create_app(small)) as client:
        object_id = new_object(client)
        response = upload(client, object_id)
        assert response.status_code == 413
        assert not list(settings.storage_dir.rglob("*.upload"))


def test_queue_claim_is_exclusive_and_expired_lease_recovers(client, settings):
    object_id = new_object(client)
    flight_id = upload(client, object_id).json()["flight_id"]
    first = Database(settings.database_path)
    second = Database(settings.database_path)
    first_job = first.claim(900)
    assert first_job[0] == flight_id
    assert second.claim(900) is None
    with first.connection() as db:
        db.execute("UPDATE flights SET lease_until=0 WHERE id=?", (flight_id,))
    recovered = second.claim(900)
    assert recovered[0] == flight_id and recovered[1] != first_job[1]
    assert not first.finish(flight_id, first_job[1], error={"code": "STALE"})


def evidence(materials=("brick", "brick"), views=("N", "E"), confidence=0.92):
    images = [
        {"id": f"img_{index}", "view": view, "sha256": str(index), "is_overhead": False}
        for index, view in enumerate(views)
    ]
    observations = [
        ViewObservation(
            image_id=image["id"],
            visibility="visible",
            material=material,
            confidence=confidence,
            crop_bbox=(0, 0, 1, 1),
        )
        for image, material in zip(images, materials)
    ]
    return observations, images


@pytest.mark.parametrize(
    "views,target,expected",
    [
        (("N", "E"), True, "determined"),
        (("N", "N"), True, "needs_review"),
        ((None, None), True, "needs_review"),
        (("N", "E"), False, "needs_review"),
        (("top_high", "top_low"), True, "insufficient_data"),
    ],
)
def test_evidence_requires_distinct_facades_and_correct_target(views, target, expected):
    observations, images = evidence(views=views)
    result = aggregate(
        observations,
        images,
        model="test",
        is_demo=False,
        project_material="timber",
        target_confirmed=target,
    )
    assert result.status == expected
    assert result.match == ("mismatch" if expected == "determined" else "unclear")


def test_conflicting_and_low_confidence_materials_are_not_fixed():
    observations, images = evidence(materials=("brick", "timber"))
    result = aggregate(
        observations,
        images,
        model="test",
        is_demo=False,
        project_material=None,
        target_confirmed=True,
    )
    assert result.status == "mixed" and not result.fixed_on_object
    observations, images = evidence(confidence=0.61)
    result = aggregate(
        observations,
        images,
        model="test",
        is_demo=False,
        project_material=None,
        target_confirmed=True,
    )
    assert result.status == "insufficient_data" and result.votes == {}
    assert all(
        item.material == "unclear" and item.predicted_material == "brick"
        for item in result.per_view
    )


@pytest.mark.parametrize("visibility,expected", [("hidden", "hidden"), ("no_walls", "no_walls")])
def test_hidden_wall_and_absent_wall_are_different(visibility, expected):
    _, images = evidence()
    observations = [
        ViewObservation(image_id=image["id"], visibility=visibility) for image in images
    ]
    result = aggregate(
        observations,
        images,
        model="test",
        is_demo=False,
        project_material=None,
        target_confirmed=True,
    )
    assert result.status == expected and not result.needs_reshoot


def test_bad_ml_bbox_is_rejected():
    with pytest.raises(ValidationError):
        Observation(
            image_id="img_1",
            visibility="visible",
            material="brick",
            confidence=0.9,
            crop_bbox=(0.8, 0, 0.2, 1),
        )


def test_demo_cannot_be_accepted_as_real_model_result(client, settings):
    object_id = new_object(client)
    flight_id = upload(client, object_id).json()["flight_id"]
    Worker(settings).process_one()
    response = client.post(
        f"/api/v1/flights/{flight_id}/material/review", json={"action": "accept"}
    )
    assert response.status_code == 409
