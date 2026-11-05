import io
import json
import sqlite3
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageFilter
from pydantic import ValidationError

from agents.readiness.aggregation import aggregate, annotate
from agents.readiness.config import classes_config, stages_config
from agents.readiness.model import load_detector
from agents.readiness.parcel import outside_parcel
from agents.readiness.quality import quality_issues
from agents.readiness.views import assign_facade_sides, overhead_view
from api.config import Settings
from api.database import Database
from api.main import create_app
from schemas.readiness import Detection, ReadinessReview
from worker.service import Worker

BOX = (0.1, 0.1, 0.9, 0.9)


def image(view, index=0, *, overhead=None, pitch=-40.0):
    return {
        "id": f"img_{view}_{index}",
        "view": view,
        "sha256": f"{view}{index}",
        "is_overhead": view in {"top_low", "top_high"} if overhead is None else overhead,
        "camera_pitch": pitch,
    }


FULL_FLIGHT = [image(view) for view in ("N", "E", "S", "W", "top_low", "top_high")]


def found(view, cls, conf, index=0):
    return Detection(image_id=f"img_{view}_{index}", cls=cls, conf=conf, bbox=BOX)


def run(detections, images=FULL_FLIGHT, target=True):
    return aggregate(detections, images, model="test", is_demo=False, target_confirmed=target)


def test_configs_are_consistent():
    assert len(classes_config().classes) == 13
    assert classes_config().classes[12].id == "fence"
    assert classes_config().classes[11].id == "paving"
    assert classes_config().classes[11].ml_id == 11
    assert [item.id for item in stages_config().stages][0] == "none"


def test_walls_and_rafters_match_documented_example():
    result = run(
        [
            found("N", "walls_full", 0.93),
            found("E", "walls_full", 0.9),
            found("S", "walls_full", 0.88),
            found("top_low", "roof_frame", 0.81),
            found("N", "roof_covered", 0.12),
        ]
    )
    assert result.stage == "roof"
    assert result.readiness_pct == 55
    assert result.bank_readiness == 50
    assert result.elements["foundation"].implied and result.elements["foundation"].present
    assert result.elements["walls_full"].views == ["E", "N", "S"]
    assert not result.elements["roof_covered"].present
    assert result.confidence == pytest.approx(0.81)
    assert result.status == "determined"
    assert "стропила установлены, кровельного покрытия нет" in result.explanation


def test_finished_house_reaches_bank_100():
    detections = [found("top_low", "roof_covered", 0.95), found("N", "door_installed", 0.9)]
    for view in ("N", "E", "S", "W"):
        detections += [
            found(view, "window_installed", 0.9),
            found(view, "window_installed", 0.85),
            found(view, "facade_finish", 0.8),
        ]
    result = run(detections)
    assert result.stage == "done"
    assert result.readiness_pct == 100
    assert result.bank_readiness == 100
    assert result.windows_ratio == 1
    assert result.elements["walls_full"].implied


def test_bank_100_does_not_require_facade_finish():
    detections = [found("top_low", "roof_covered", 0.95), found("S", "door_installed", 0.9)]
    detections += [found(view, "window_installed", 0.9) for view in ("N", "E", "S", "W")]
    result = run(detections)
    assert result.stage == "openings"
    assert result.bank_readiness == 100
    assert result.readiness_pct == 80


def test_empty_openings_lower_windows_ratio():
    detections = [found("top_low", "roof_covered", 0.9), found("N", "window_installed", 0.9)]
    detections += [found("E", "opening_empty", 0.9), found("S", "opening_empty", 0.9)]
    result = run(detections)
    assert result.windows_ratio == pytest.approx(1 / 3, abs=1e-4)
    assert result.bank_readiness == 50
    assert result.readiness_pct == 15 + 30 + 20 + 3


def test_view_mask_ignores_elements_from_wrong_views():
    detections = [found("top_high", "walls_full", 0.95), found("top_low", "window_installed", 0.95)]
    result = run(detections)
    assert result.stage == "none"
    assert result.readiness_pct == 0
    assert result.bank_readiness == 0
    marked = annotate(detections, FULL_FLIGHT)
    assert marked["img_top_high_0"][0]["counted"] is False
    assert marked["img_top_high_0"][0]["class"] == "walls_full"
    assert marked["img_top_high_0"][0]["label"] == "Стены выведены"


def test_repeated_photos_of_one_facade_are_not_independent():
    images = [*FULL_FLIGHT, image("N", 1)]
    result = run([found("N", "walls_full", 0.4), found("N", "walls_full", 0.4, index=1)], images)
    assert result.stage == "none"
    other_facade = run([found("N", "walls_full", 0.4), found("E", "walls_full", 0.4)])
    assert other_facade.stage == "walls"


def test_unknown_nadir_counts_only_foundation_classes():
    images = [image(None, 1, overhead=True, pitch=-90), image(None, 2, overhead=False, pitch=None)]
    result = run(
        [
            found(None, "walls_full", 0.9, index=1),
            found(None, "foundation", 0.9, index=1),
            found(None, "walls_full", 0.9, index=2),
        ],
        images,
    )
    assert result.stage == "foundation"
    assert result.readiness_pct == 15
    assert set(result.reshoot_reasons) >= {"NO_TOP_LOW", "NO_TOP_HIGH", "FEW_FACADES"}
    assert result.status == "needs_reshoot"


def test_site_without_construction_and_low_confidence():
    result = run([found("top_high", "site_prep", 0.7), found("N", "foundation", 0.45)])
    assert result.stage == "none"
    assert result.readiness_pct == 5
    assert result.confidence == pytest.approx(0.55)
    assert result.reshoot_reasons == ["LOW_CONFIDENCE"]


def test_conflicting_roof_views_and_unconfirmed_target():
    result = run(
        [found("top_low", "roof_covered", 0.9), found("N", "roof_frame", 0.9)], target=False
    )
    assert "VIEWS_CONFLICT" in result.reshoot_reasons
    assert result.review_reasons == ["TARGET_NOT_CONFIRMED"]
    clean = run([found("top_low", "roof_frame", 0.9)], target=False)
    assert clean.status == "needs_review"


def test_overhead_role_from_relative_altitude():
    assert overhead_view(45.3) == "top_high"
    assert overhead_view(16.7) == "top_low"
    assert overhead_view(None) is None


def test_review_contract():
    with pytest.raises(ValidationError):
        ReadinessReview(action="correct", stage="walls", readiness_pct=40)
    with pytest.raises(ValidationError):
        ReadinessReview(
            action="correct", stage="none", readiness_pct=0, bank_readiness=50, comment="x"
        )
    with pytest.raises(ValidationError):
        ReadinessReview(action="accept", stage="walls")
    review = ReadinessReview(action="correct", stage="done", readiness_pct=100, comment="x")
    assert review.bank_readiness == 100


def textured(seed=0, size=(400, 300)):
    # Random noise passes the sharpness check, unlike a solid-colour test picture.
    return Image.effect_noise(size, 60 + seed).convert("RGB")


def photo(seed=0):
    buffer = io.BytesIO()
    textured(seed).save(buffer, "JPEG", quality=95)
    return buffer.getvalue()


VIEWS = ["N", "E", "S", "W", "top_low", "top_high"]
COLOURS = [0, 1, 2, 3, 4, 5]


class RafterDetector:
    """Fixed detections exercise integration; this is not an actual trained model."""

    version = "test-detector@v1"
    is_demo = False

    def __init__(self):
        self.views = {}

    def detect(self, picture, image_id):
        view = self.views[image_id]
        if view in {"N", "E", "S", "W"}:
            return [{"image_id": image_id, "class": "walls_full", "conf": 0.9, "bbox": BOX}]
        if view == "top_low":
            return [Detection(image_id=image_id, cls="roof_frame", conf=0.85, bbox=BOX)]
        return [Detection(image_id=image_id, cls="window_installed", conf=0.9, bbox=BOX)]


@pytest.fixture
def settings(tmp_path):
    return Settings(storage_dir=tmp_path / "storage")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as value:
        yield value


def upload_full(client, object_id, target=True):
    response = client.post(
        f"/api/v1/objects/{object_id}/flights",
        data={"views": json.dumps(VIEWS), "target_confirmed": str(target).lower()},
        files=[("files", (f"{v}.jpg", photo(c))) for v, c in zip(VIEWS, COLOURS)],
    )
    assert response.status_code == 202, response.text
    return response.json()["flight_id"]


def new_object(client):
    return client.post("/api/v1/objects", json={"address": "Test house"}).json()["id"]


def test_demo_mode_reports_unavailable_stage(client, settings):
    object_id = new_object(client)
    assert client.get(f"/api/v1/objects/{object_id}").json()["data_status"] == "no_data"
    flight_id = upload_full(client, object_id)
    assert client.get(f"/api/v1/objects/{object_id}").json()["data_status"] == "processing"
    early = client.post(f"/api/v1/flights/{flight_id}/review", json={"action": "accept"})
    assert early.status_code == 409
    Worker(settings).process_one()
    result = client.get(f"/api/v1/flights/{flight_id}").json()["result"]
    assert result["status"] == "model_unavailable" and result["is_demo"]
    assert result["stage"] is None
    assert client.get(f"/api/v1/objects/{object_id}").json()["data_status"] == "no_data"
    accept = client.post(f"/api/v1/flights/{flight_id}/review", json={"action": "accept"})
    assert accept.json()["error"]["code"] == "STAGE_NOT_DETERMINED"


def test_detector_result_review_and_images(client, settings):
    object_id = new_object(client)
    flight_id = upload_full(client, object_id)
    detector = RafterDetector()
    images = client.get(f"/api/v1/flights/{flight_id}/images").json()
    detector.views = {item["id"]: item["view"] for item in images}
    Worker(settings, detector=detector).process_one()
    flight = client.get(f"/api/v1/flights/{flight_id}").json()
    original = flight["result"]
    assert original["stage"] == "roof"
    assert original["readiness_pct"] == 55
    assert original["status"] == "determined"
    assert flight["material_result"]["status"] == "model_unavailable"
    card = client.get(f"/api/v1/objects/{object_id}").json()
    assert card["data_status"] == "ok"
    images = client.get(f"/api/v1/flights/{flight_id}/images").json()
    top_high = next(item for item in images if item["view"] == "top_high")
    assert top_high["detections"][0]["class"] == "window_installed"
    assert top_high["detections"][0]["counted"] is False

    correction = client.post(
        f"/api/v1/flights/{flight_id}/review",
        headers={"X-User": "ivanov"},
        json={"action": "correct", "stage": "walls", "readiness_pct": 45, "comment": "Доски"},
    )
    assert correction.status_code == 201, correction.text
    body = correction.json()
    assert body["id"].startswith("rev_") and body["flight_id"] == flight_id
    assert body["author"] == "ivanov" and body["bank_readiness"] == 50
    flight = client.get(f"/api/v1/flights/{flight_id}").json()
    assert flight["result"] == original
    assert flight["readiness_review"]["stage"] == "walls"
    accepted = client.post(f"/api/v1/flights/{flight_id}/review", json={"action": "accept"})
    assert accepted.json()["stage"] == "roof"
    invalid = client.post(f"/api/v1/flights/{flight_id}/review", json={"action": "correct"})
    assert invalid.status_code == 400


def test_meta_lists_stage_dictionaries(client):
    meta = client.get("/api/v1/meta").json()
    assert [item["id"] for item in meta["stages"]][-1] == "done"
    assert [item["id"] for item in meta["bank_readiness"]] == [0, 50, 100]
    assert meta["classes"][3] == {
        "id": "walls_full",
        "ml_id": 3,
        "label": "Стены выведены",
        "color": "#E53935",
    }
    assert meta["views"][0] == {"id": "top_low", "label": "Сверху: дом"}


def test_material_only_database_gets_readiness_columns(settings):
    settings.storage_dir.mkdir(parents=True)
    with sqlite3.connect(settings.database_path) as db:
        db.execute(
            "CREATE TABLE flights (id TEXT PRIMARY KEY, object_id TEXT NOT NULL, "
            "status TEXT NOT NULL, uploaded_at TEXT NOT NULL, flown_at TEXT, "
            "target_confirmed INTEGER NOT NULL, is_demo INTEGER NOT NULL, result TEXT, "
            "error TEXT, lease_token TEXT, lease_until REAL, attempts INTEGER NOT NULL DEFAULT 0)"
        )
    Database(settings.database_path).initialize()
    with sqlite3.connect(settings.database_path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(flights)")}
    assert {"readiness", "detections"} <= columns


def test_real_mode_requires_detector(settings):
    with pytest.raises(ValueError, match="DETECTOR_FACTORY"):
        load_detector(replace(settings, mock=False))


# Yaws of the facade shots of dataset/2: a house turned by about 45 degrees to north.
TURNED_HOUSE = [-129.0, -39.9, 140.3, 57.0]


def side_shot(index, yaw, view=None, overhead=False):
    return {
        "id": f"img_{index}",
        "view": view,
        "is_overhead": overhead,
        "camera_pitch": -90.0 if overhead else -50.0,
        "camera_yaw": yaw,
    }


def test_facade_sides_follow_the_house_not_the_compass_sectors():
    shots = [side_shot(index, yaw) for index, yaw in enumerate(TURNED_HOUSE)]
    assign_facade_sides(shots)
    assert sorted(shot["view"] for shot in shots) == ["E", "N", "S", "W"]
    assert {shot["view_source"] for shot in shots} == {"exif"}


def test_facade_sides_keep_repeats_together_and_skip_unclear_shots():
    shots = [
        side_shot(0, 0.0),  # camera looks north: the southern facade
        side_shot(1, 3.0),  # the same facade again
        side_shot(2, 90.0),  # camera looks east: the western facade
        side_shot(3, 225.0),  # a corner shot between two facades
        side_shot(4, 10.0, view="E"),
        side_shot(5, 10.0, overhead=True),
        {
            "id": "img_6",
            "view": None,
            "is_overhead": False,
            "camera_pitch": None,
            "camera_yaw": None,
        },
    ]
    assign_facade_sides(shots)
    assert [shot["view"] for shot in shots] == ["S", "S", "W", None, "E", None, None]


def test_parcel_buffer():
    parcel = {
        "type": "Polygon",
        "coordinates": [
            [
                [82.870, 55.011],
                [82.872, 55.011],
                [82.872, 55.013],
                [82.870, 55.013],
                [82.870, 55.011],
            ]
        ],
    }
    inside, near, far = [55.012, 82.871], [55.0132, 82.871], [55.020, 82.871]
    assert outside_parcel(parcel, [inside, near, far], 30) == [2]
    assert outside_parcel(parcel, [near], 10) == [0]


def test_out_of_parcel_and_bad_image_send_flight_to_reshoot():
    parcel = {
        "type": "Polygon",
        "coordinates": [
            [
                [82.870, 55.011],
                [82.872, 55.011],
                [82.872, 55.013],
                [82.870, 55.013],
                [82.870, 55.011],
            ]
        ],
    }
    images = [{**item, "gps": [55.012, 82.871]} for item in FULL_FLIGHT]
    detections = [found("top_low", "roof_frame", 0.9)]
    assert (
        aggregate(
            detections, images, model="t", is_demo=False, target_confirmed=True, parcel=parcel
        ).reshoot_reasons
        == []
    )
    # A side shot is taken 12-38 m from the house: its GPS says nothing about the parcel.
    images[0] = {**images[0], "gps": [55.05, 82.871], "quality_issues": ["BLURRY"]}
    result = aggregate(
        detections, images, model="t", is_demo=False, target_confirmed=True, parcel=parcel
    )
    assert result.reshoot_reasons == ["BAD_IMAGE"]
    # Over a nadir the drone hangs above the house: a far nadir means the wrong place.
    images[4] = {**images[4], "gps": [55.05, 82.871]}
    result = aggregate(
        detections, images, model="t", is_demo=False, target_confirmed=True, parcel=parcel
    )
    assert result.reshoot_reasons == ["OUT_OF_PARCEL", "BAD_IMAGE"]
    assert "не над участком" in result.explanation


def test_quality_issues():
    sharp = textured()
    assert quality_issues(sharp) == []
    assert quality_issues(sharp.filter(ImageFilter.GaussianBlur(4))) == ["BLURRY"]
    assert "OVEREXPOSED" in quality_issues(Image.new("RGB", (400, 300), "white"))
    assert "UNDEREXPOSED" in quality_issues(Image.new("RGB", (400, 300), "black"))


def test_object_accepts_parcel_and_rejects_open_ring(client, settings):
    ring = [[82.870, 55.011], [82.872, 55.011], [82.872, 55.013], [82.870, 55.011]]
    created = client.post(
        "/api/v1/objects",
        json={
            "address": "Test house",
            "parcel_geojson": {"type": "Polygon", "coordinates": [ring]},
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["parcel_geojson"]["coordinates"][0][0] == [82.870, 55.011]
    broken = client.post(
        "/api/v1/objects",
        json={
            "address": "Test house",
            "parcel_geojson": {"type": "Polygon", "coordinates": [ring[:3]]},
        },
    )
    assert broken.status_code == 400
    object_id = created.json()["id"]
    flight_id = upload_full(client, object_id)
    Worker(settings).process_one()
    images = client.get(f"/api/v1/flights/{flight_id}/images").json()
    assert all(item["quality_ok"] and item["quality_issues"] == [] for item in images)
