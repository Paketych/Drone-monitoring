"""Where the whole flight looks: the object point on the ground and its place on every frame.

A flight is made around one object: over a nadir the drone hangs above the house (nadirs of one
flight in dataset/ are 0-5 m apart) and side shots are aimed at it from every side. The median of
nadir GPS positions is the object point; it is projected into each frame with the DJI camera pose
(GPS, relative altitude, gimbal pitch and yaw, calibrated focal length) under a flat-ground model.
"""

from math import cos, hypot, radians, sin, tan
from statistics import median

from agents.readiness.config import classes_config
from agents.readiness.parcel import METERS_PER_DEGREE_LAT, METERS_PER_DEGREE_LON

NADIR_PITCH = -75.0


def _offset_m(origin: tuple[float, float], point: tuple[float, float]) -> tuple[float, float]:
    """East and north metres from origin to point, both [lat, lon]."""
    east = (point[1] - origin[1]) * cos(radians(origin[0])) * METERS_PER_DEGREE_LON
    north = (point[0] - origin[0]) * METERS_PER_DEGREE_LAT
    return east, north


def _shift(origin: tuple[float, float], east: float, north: float) -> tuple[float, float]:
    lat = origin[0] + north / METERS_PER_DEGREE_LAT
    lon = origin[1] + east / (cos(radians(origin[0])) * METERS_PER_DEGREE_LON)
    return lat, lon


def _nadirs(images: list[dict]) -> list[tuple[float, float]]:
    return [
        tuple(image["gps"])
        for image in images
        if image.get("gps")
        and image.get("camera_pitch") is not None
        and image["camera_pitch"] <= NADIR_PITCH
    ]


def _axis_hit(image: dict) -> tuple[float, float] | None:
    """Where the optical axis of a tilted shot meets the ground, as [lat, lon]."""
    pitch, yaw = image.get("camera_pitch"), image.get("camera_yaw")
    altitude = image.get("relative_altitude")
    rules = classes_config().flight_point
    if (
        rules is None
        or not image.get("gps")
        or pitch is None
        or yaw is None
        or altitude is None
        or altitude < rules.min_altitude_m
        or -pitch < rules.min_tilt_deg
        or pitch <= NADIR_PITCH
    ):
        return None
    distance = altitude / tan(radians(-pitch))
    return _shift(tuple(image["gps"]), distance * sin(radians(yaw)), distance * cos(radians(yaw)))


def flight_point(images: list[dict]) -> tuple[float, float] | None:
    """Object point [lat, lon]: median of nadirs, else where the side cameras look."""
    points = _nadirs(images) or [hit for image in images if (hit := _axis_hit(image))]
    if not points:
        return None
    return median(point[0] for point in points), median(point[1] for point in points)


def nadir_spread_m(images: list[dict]) -> float | None:
    """Largest distance of a nadir from their median; None with fewer than two nadirs."""
    points = _nadirs(images)
    if len(points) < 2:
        return None
    centre = median(point[0] for point in points), median(point[1] for point in points)
    return max(hypot(*_offset_m(centre, point)) for point in points)


def project(image: dict, point: tuple[float, float]) -> tuple[float, float] | None:
    """Normalised [x, y] of a ground point on the frame, or None when the pose is unreliable.

    Shots from the ground, near-horizontal shots and frames with a broken altitude (the drone
    below its take-off point) cannot be projected onto the ground plane.
    """
    rules = classes_config().flight_point
    pitch, yaw = image.get("camera_pitch"), image.get("camera_yaw")
    altitude, focal = image.get("relative_altitude"), image.get("calibrated_focal_length_px")
    width, height = image.get("width"), image.get("height")
    if (
        rules is None
        or not image.get("gps")
        or None in (pitch, yaw, altitude, focal, width, height)
        or altitude < rules.min_altitude_m
        or -pitch < rules.min_tilt_deg
    ):
        return None
    east, north = _offset_m(tuple(image["gps"]), point)
    psi, theta = radians(yaw), radians(pitch)
    forward = (sin(psi) * cos(theta), cos(psi) * cos(theta), sin(theta))
    right = (cos(psi), -sin(psi), 0.0)
    up = (
        right[1] * forward[2] - right[2] * forward[1],
        right[2] * forward[0] - right[0] * forward[2],
        right[0] * forward[1] - right[1] * forward[0],
    )
    ray = (east, north, -altitude)

    def dot(a, b):
        return sum(x * y for x, y in zip(a, b))

    depth = dot(ray, forward)
    if depth <= 0:
        return None
    cx = image.get("optical_center_x_px") or width / 2
    cy = image.get("optical_center_y_px") or height / 2
    x = (cx + focal * dot(ray, right) / depth) / width
    y = (cy - focal * dot(ray, up) / depth) / height
    return x, y
