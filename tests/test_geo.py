import io
import json
from math import cos, radians

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from test_readiness import new_object

from agents.geo import flight_point, nadir_spread_m, project
from agents.readiness.aggregation import aggregate
from agents.targeting import exclusion_reason, locate_target
from api.config import Settings
from api.main import create_app
from schemas.readiness import Detection
from worker.service import Worker

HOUSE = (55.0, 83.0)


def shifted(east=0.0, north=0.0, origin=HOUSE):
    return (
        origin[0] + north / 110_540,
        origin[1] + east / (cos(radians(origin[0])) * 111_320),
    )


def shot(gps, *, pitch=-90.0, yaw=0.0, altitude=50.0, image_id="img", view=None):
    return {
        "id": image_id,
        "sha256": image_id,
        "view": view,
        "is_overhead": pitch <= -75,
        "gps": list(gps),
        "camera_pitch": pitch,
        "camera_yaw": yaw,
        "relative_altitude": altitude,
        "calibrated_focal_length_px": 1000.0,
        "width": 2000,
        "height": 1500,
    }


def found(cls, box, image_id="img"):
    return Detection(image_id=image_id, cls=cls, conf=0.9, bbox=box)


def test_point_under_a_nadir_is_the_frame_centre_and_north_is_up():
    assert project(shot(HOUSE), HOUSE) == pytest.approx((0.5, 0.5))
    # 10 m north of the drone at 50 m with f = 1000 px: 200 px above the centre.
    assert project(shot(HOUSE), shifted(north=10)) == pytest.approx((0.5, 550 / 1500))
    # The camera turned east: north is on the left of the frame.
    assert project(shot(HOUSE, yaw=90), shifted(north=10)) == pytest.approx((0.4, 0.5))


def test_oblique_shot_sees_the_point_it_is_aimed_at_in_the_centre():
    # 20 m south of the house at 20 m altitude, looking north and 45 degrees down.
    camera = shot(shifted(north=-20), pitch=-45, yaw=0, altitude=20)
    assert project(camera, HOUSE) == pytest.approx((0.5, 0.5))


@pytest.mark.parametrize(
    "camera",
    [
        shot(HOUSE, pitch=0.0, altitude=1.7),  # from the ground (dataset/13)
        shot(HOUSE, pitch=-41.4, altitude=-3.7),  # below the take-off point (dataset/1)
        {**shot(HOUSE), "calibrated_focal_length_px": None},  # no camera calibration
        {**shot(HOUSE), "camera_yaw": None},  # only GPS
    ],
)
def test_unreliable_pose_is_not_projected(camera):
    assert project(camera, HOUSE) is None


def test_flight_point_is_the_median_of_nadirs_and_ignores_side_shots():
    images = [
        shot(HOUSE),
        shot(shifted(east=2)),
        shot(shifted(east=-1)),
        shot(shifted(north=-25), pitch=-45, altitude=20),
    ]
    assert flight_point(images) == pytest.approx(shifted(east=0), abs=1e-7)


def test_without_nadirs_the_side_cameras_point_at_the_object():
    images = [
        shot(shifted(north=-20), pitch=-45, yaw=0, altitude=20),
        shot(shifted(east=-20), pitch=-45, yaw=90, altitude=20),
    ]
    assert flight_point(images) == pytest.approx(HOUSE, abs=1e-6)


def test_nadirs_over_different_places_send_the_flight_to_reshoot():
    # dataset/12: part of the nadirs is over a supermarket 38-48 m away from the house.
    images = [
        shot(HOUSE, image_id="a", view="top_high"),
        shot(HOUSE, image_id="b", view="top_low"),
        shot(shifted(east=45), image_id="c", view="top_high"),
    ]
    assert nadir_spread_m(images) == pytest.approx(45, rel=0.01)
    result = aggregate([], images, model="t", is_demo=False, target_confirmed=True)
    assert "MIXED_LOCATIONS" in result.reshoot_reasons
    assert "над разными местами" in result.explanation
    close = [shot(HOUSE, image_id="a"), shot(shifted(east=4), image_id="b")]
    assert (
        "MIXED_LOCATIONS"
        not in aggregate([], close, model="t", is_demo=False, target_confirmed=True).reshoot_reasons
    )


def test_house_under_the_flight_point_wins_over_a_neighbour_in_the_centre():
    # The drone drifted 15 m west: the object is right of the centre, a neighbour is in it.
    camera = shot(shifted(east=-15))
    target = found("roof_covered", (0.6, 0.45, 0.7, 0.55))
    neighbour = found("walls_full", (0.42, 0.42, 0.55, 0.58))
    region, source, point = locate_target([neighbour, target], camera, HOUSE)
    assert source == "flight_point" and point == pytest.approx((0.65, 0.5))
    assert exclusion_reason(target.bbox, {"target_bbox": region}) is None
    assert exclusion_reason(neighbour.bbox, {"target_bbox": region}) == "OUTSIDE_TARGET"


def test_point_far_from_any_house_falls_back_to_the_centre():
    camera = shot(shifted(east=-15))  # the object point lands at x = 0.65
    central = found("walls_full", (0.42, 0.42, 0.52, 0.58))  # 0.13 from the point
    far = found("roof_covered", (0.0, 0.0, 0.1, 0.1))
    region, source, _ = locate_target([central, far], camera, HOUSE)
    assert source == "auto_center"
    assert exclusion_reason(central.bbox, {"target_bbox": region}) is None
    region, source, point = locate_target([central], shot(HOUSE, pitch=0.0), HOUSE)
    assert source == "auto_center" and point is None


def drone_photo(seed, *, pitch, yaw, altitude, focal):
    exif = Image.Exif()
    exif[306] = "2026:08:07 12:00:00"
    xmp = (
        f'drone-dji:GimbalPitchDegree="{pitch}" drone-dji:GimbalYawDegree="{yaw}" '
        f'drone-dji:RelativeAltitude="{altitude}" drone-dji:CalibratedFocalLength="{focal}"'
    )
    buffer = io.BytesIO()
    Image.new("RGB", (120, 80), (seed * 40, 90, 120)).save(
        buffer, "JPEG", exif=exif.tobytes(), comment=xmp.encode()
    )
    return buffer.getvalue()


def test_upload_to_worker_uses_the_flight_point(tmp_path):
    # Two low nadirs over the house and one taken 15 m west of it: there the house is off-centre
    # (x = 0.875 at 20 m with f = 60 px on a 120 px frame) and a neighbour stands in the centre.
    gps = [HOUSE, HOUSE, shifted(east=-15)]
    settings = Settings(storage_dir=tmp_path / "storage")
    with TestClient(create_app(settings)) as client:
        response = client.post(
            f"/api/v1/objects/{new_object(client)}/flights",
            data={
                "target_confirmed": "true",
                "meta": json.dumps([{"gps": list(point)} for point in gps]),
            },
            files=[
                ("files", (f"{i}.jpg", drone_photo(i, pitch=-90, yaw=0, altitude=20, focal=60)))
                for i in range(3)
            ],
        )
        assert response.status_code == 202, response.text
        flight = response.json()["flight_id"]
        ids = [image["id"] for image in client.get(f"/api/v1/flights/{flight}/images").json()]

        class Detector:
            version, is_demo = "test-geo@v1", False

            def detect(self, picture, image_id):
                if image_id != ids[2]:
                    return [found("roof_covered", (0.4, 0.4, 0.6, 0.6), image_id)]
                return [
                    found("roof_covered", (0.82, 0.42, 0.93, 0.58), image_id),
                    found("walls_full", (0.42, 0.42, 0.55, 0.58), image_id),
                ]

        assert Worker(settings, detector=Detector()).process_one()
        images = client.get(f"/api/v1/flights/{flight}/images").json()
        drifted = images[2]
        assert drifted["target_region_source"] == "flight_point"
        assert drifted["target_point"] == pytest.approx([0.875, 0.5], abs=0.01)
        assert drifted["view"] == "top_low"
        assert [row["counted"] for row in drifted["detections"]] == [True, False]
