import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from test_readiness import VIEWS, new_object, photo

from agents.readiness.views import promote_low_overhead
from agents.targeting import central_target, exclusion_reason
from api.config import Settings
from api.main import create_app
from schemas.readiness import Detection
from worker.service import Worker

TARGET = (0.35, 0.35, 0.65, 0.65)
# A finished neighbour in the corner of the frame: it must not finish our house.
NEIGHBOUR = [
    ("roof_covered", (0.0, 0.0, 0.25, 0.2)),
    ("walls_full", (0.0, 0.1, 0.25, 0.3)),
    ("window_installed", (0.05, 0.15, 0.1, 0.2)),
    ("door_installed", (0.15, 0.15, 0.18, 0.25)),
    ("facade_finish", (0.0, 0.1, 0.25, 0.3)),
]


def found(cls, box, conf=0.9, image_id="img"):
    return Detection(image_id=image_id, cls=cls, conf=conf, bbox=box)


def test_house_in_centre_is_kept_and_neighbour_is_excluded():
    detections = [
        found("walls_partial", TARGET),
        found("roof_covered", (0.33, 0.25, 0.67, 0.5)),  # roof of the same house
        found("window_installed", (0.4, 0.5, 0.45, 0.6)),
        *[found(cls, box) for cls, box in NEIGHBOUR],
    ]
    region, source = central_target(detections)
    assert source == "auto_center"
    image = {"target_bbox": region}
    kept = [row.cls for row in detections if exclusion_reason(row.bbox, image) is None]
    assert kept == ["walls_partial", "roof_covered", "window_installed"]


def test_box_covering_the_centre_wins_over_a_box_centred_closer():
    covering = found("roof_covered", (0.45, 0.3, 0.9, 0.6))  # centre (0.68, 0.45), covers 0.5
    near = found("walls_full", (0.3, 0.3, 0.48, 0.48))  # centre (0.39, 0.39), misses 0.5
    image = {"target_bbox": central_target([near, covering])[0]}
    assert exclusion_reason(covering.bbox, image) is None
    assert exclusion_reason(near.bbox, image) == "OUTSIDE_TARGET"


def test_roof_standing_on_the_walls_belongs_to_the_same_house():
    # dataset/2 frame 0005: in an oblique view the roof sits above a thin strip of walls and
    # barely overlaps it, so overlap alone dropped the roof of the target house.
    walls = found("walls_full", (0.37, 0.455, 0.57, 0.485))
    roof = found("roof_covered", (0.36, 0.24, 0.64, 0.46))
    window = found("window_installed", (0.45, 0.46, 0.48, 0.48))
    other_roof = found("roof_covered", (0.36, 0.6, 0.64, 0.75))  # a house further down
    region, source = central_target([walls, roof, window, other_roof])
    image = {"target_bbox": region}
    assert source == "auto_center"
    assert exclusion_reason(roof.bbox, image) is None
    assert exclusion_reason(window.bbox, image) is None
    assert exclusion_reason(other_roof.bbox, image) == "OUTSIDE_TARGET"


def test_neighbour_is_not_substituted_when_no_house_is_in_the_centre():
    detections = [found(cls, box) for cls, box in NEIGHBOUR]
    region, source = central_target(detections)
    assert source == "centre_zone" and region == pytest.approx((0.4, 0.4, 0.6, 0.6))
    assert all(exclusion_reason(row.bbox, {"target_bbox": region}) for row in detections)


@pytest.mark.parametrize(
    "detections",
    [[], [found("window_installed", TARGET)], [found("walls_full", TARGET, conf=0.3)]],
)
def test_without_reliable_building_the_whole_frame_counts(detections):
    assert central_target(detections) == (None, None)


class NeighbourhoodDetector:
    """Our house has partial walls in the centre; a finished neighbour stands in the corner."""

    version, is_demo = "test-neighbourhood@v1", False

    def detect(self, picture, image_id):
        return [found("walls_partial", TARGET, image_id=image_id)] + [
            found(cls, box, image_id=image_id) for cls, box in NEIGHBOUR
        ]


def upload(client, obj, meta=None):
    data = {"views": json.dumps(VIEWS), "target_confirmed": "true"}
    if meta is not None:
        data["meta"] = json.dumps(meta)
    response = client.post(
        f"/api/v1/objects/{obj}/flights",
        data=data,
        files=[("files", (f"{view}.jpg", photo(index))) for index, view in enumerate(VIEWS)],
    )
    assert response.status_code == 202, response.text
    return response.json()["flight_id"]


def test_worker_does_not_take_the_stage_from_a_neighbour(tmp_path):
    settings = Settings(storage_dir=tmp_path / "storage")
    with TestClient(create_app(settings)) as client:
        flight = upload(client, new_object(client))
        assert Worker(settings, detector=NeighbourhoodDetector()).process_one()
        result = client.get(f"/api/v1/flights/{flight}").json()["result"]
        assert result["stage"] == "walls"
        assert not result["elements"]["roof_covered"]["present"]
        for image in client.get(f"/api/v1/flights/{flight}/images").json():
            assert image["target_region_source"] == "auto_center"
            counted = [row["class"] for row in image["detections"] if row["counted"]]
            excluded = {row["target_exclusion_reason"] for row in image["detections"][1:]}
            assert counted in ([], ["walls_partial"]) and excluded == {"OUTSIDE_TARGET"}


class OffCentreDetector:
    """The flight shows only a neighbour in the corner; the target itself is not in the centre."""

    version, is_demo = "test-off-centre@v1", False

    def detect(self, picture, image_id):
        return [found(cls, box, image_id=image_id) for cls, box in NEIGHBOUR]


def test_off_centre_frames_do_not_borrow_the_neighbour_and_need_review(tmp_path):
    settings = Settings(storage_dir=tmp_path / "storage")
    with TestClient(create_app(settings)) as client:
        flight = upload(client, new_object(client))
        assert Worker(settings, detector=OffCentreDetector()).process_one()
        result = client.get(f"/api/v1/flights/{flight}").json()["result"]
        assert result["stage"] == "none" and not result["elements"]["roof_covered"]["present"]
        assert "TARGET_OFF_CENTRE" in result["review_reasons"]
        assert result["status"] != "determined"
        assert "не в центре кадра" in result["explanation"]
        images = client.get(f"/api/v1/flights/{flight}/images").json()
        assert {image["target_region_source"] for image in images} == {"centre_zone"}


def test_manual_region_wins_over_the_centre(tmp_path):
    settings = Settings(storage_dir=tmp_path / "storage")
    with TestClient(create_app(settings)) as client:
        # The operator says the target is the corner house.
        flight = upload(client, new_object(client), meta=[{"target_bbox": [0, 0, 0.3, 0.35]}] * 6)
        assert Worker(settings, detector=NeighbourhoodDetector()).process_one()
        result = client.get(f"/api/v1/flights/{flight}").json()["result"]
        assert result["elements"]["roof_covered"]["present"]
        images = client.get(f"/api/v1/flights/{flight}/images").json()
        assert {image["target_region_source"] for image in images} == {"provided"}


def nadir(index, altitude, view="top_high", source="exif"):
    return {"id": str(index), "view": view, "view_source": source, "relative_altitude": altitude}


@pytest.mark.parametrize(
    "shots,expected",
    [
        # dataset/2: 39.8 m is the low nadir, but the fixed 30 m threshold calls it top_high.
        ([nadir(0, 39.8), nadir(1, 50.8)], ["top_low", "top_high"]),
        # dataset/8: four overview shots at nearly one height stay overview shots.
        ([nadir(0, 46.3), nadir(1, 55.1)], ["top_high", "top_high"]),
        ([nadir(0, 40.0)], ["top_high"]),
        (
            [nadir(0, 18.0, "top_low"), nadir(1, 40.0), nadir(2, 60.0)],
            ["top_low", "top_high", "top_high"],
        ),
        ([nadir(0, 40.0, source="manual"), nadir(1, 60.0)], ["top_high", "top_high"]),
    ],
)
def test_lowest_nadir_becomes_top_low_only_when_heights_clearly_differ(shots, expected):
    promote_low_overhead(shots)
    assert [shot["view"] for shot in shots] == expected


def drone_photo(seed, *, pitch, yaw, altitude):
    """JPEG with EXIF and DJI-style XMP attributes read by the upload endpoint."""
    exif = Image.Exif()
    exif[306] = "2026:08:07 12:00:00"
    xmp = (
        f'drone-dji:GimbalPitchDegree="{pitch}" drone-dji:GimbalYawDegree="{yaw}" '
        f'drone-dji:RelativeAltitude="{altitude}"'
    )
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), (seed * 40, 90, 120)).save(
        buffer, "JPEG", exif=exif.tobytes(), comment=xmp.encode()
    )
    return buffer.getvalue()


def test_upload_takes_views_from_the_camera_when_none_are_given(tmp_path):
    shots = [
        drone_photo(0, pitch=-90, yaw=0, altitude=40),
        drone_photo(1, pitch=-90, yaw=0, altitude=52),
        *[
            drone_photo(2 + i, pitch=-45, yaw=yaw, altitude=18)
            for i, yaw in enumerate((0, 90, 180, 270))
        ],
    ]
    with TestClient(create_app(Settings(storage_dir=tmp_path / "storage"))) as client:
        obj = new_object(client)
        response = client.post(
            f"/api/v1/objects/{obj}/flights",
            files=[("files", (f"{i}.jpg", shot)) for i, shot in enumerate(shots)],
        )
        assert response.status_code == 202, response.text
        images = client.get(f"/api/v1/flights/{response.json()['flight_id']}/images").json()
        views = [image["view"] for image in images]
        assert views[:2] == ["top_low", "top_high"]
        # The camera looks north at the southern facade, and so on around the house.
        assert views[2:] == ["S", "W", "N", "E"]
        assert {image["view_source"] for image in images} == {"exif"}
