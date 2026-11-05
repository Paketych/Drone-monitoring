from math import atan2, cos, degrees, hypot, radians, sin

from agents.readiness.config import classes_config

SIDES = ("N", "E", "S", "W")


def overhead_view(relative_altitude: float | None) -> str | None:
    """Role of a nadir shot without a manual view; None when it cannot be told."""
    threshold = classes_config().top_high_min_relative_altitude_m
    if threshold is None or relative_altitude is None:
        return None
    return "top_high" if relative_altitude >= threshold else "top_low"


def promote_low_overhead(images: list[dict]) -> None:
    """Make the lowest automatic nadir top_low when the fixed threshold found none.

    Relative altitude counts from the take-off point, so a low nadir over a house on a slope or
    a tall house can be above the fixed threshold. Nadirs at clearly different heights in one
    flight still tell which one is the close-up of the roof.
    """
    ratio = classes_config().top_low_relative_ratio
    if ratio is None or any(image.get("view") == "top_low" for image in images):
        return
    auto = [
        image
        for image in images
        if image.get("view_source") == "exif"
        and image.get("view") == "top_high"
        and (image.get("relative_altitude") or 0) > 0
    ]
    if len(auto) < 2:
        return
    low = min(auto, key=lambda image: image["relative_altitude"])
    high = max(auto, key=lambda image: image["relative_altitude"])
    if high["relative_altitude"] / low["relative_altitude"] >= ratio:
        low["view"] = "top_low"


def assign_facade_sides(images: list[dict]) -> None:
    """Set N/E/S/W for side shots without a manual view, from the camera azimuth.

    A facade faces the camera, so its direction is the gimbal yaw + 180°. Facades of one house
    are 90° apart but rarely aligned with north, so the 90° grid is fitted to this flight first:
    a house turned by 45° still gets four different sides instead of a random split at a sector
    border. Shots too far from the grid (corners, odd angles) keep view = None.
    """
    tolerance = classes_config().facade_side_tolerance_deg
    candidates = [
        image
        for image in images
        if image["view"] is None
        and not image["is_overhead"]
        and image.get("camera_pitch") is not None
        and image.get("camera_yaw") is not None
    ]
    if tolerance is None or not candidates:
        return
    facing = {image["id"]: (image["camera_yaw"] + 180) % 360 for image in candidates}
    # Multiplying angles by 4 maps the four grid directions onto one, so their mean is the grid.
    x = sum(cos(radians(4 * angle)) for angle in facing.values())
    y = sum(sin(radians(4 * angle)) for angle in facing.values())
    if hypot(x, y) < 1e-6 * len(facing):
        return
    offset = degrees(atan2(y, x)) / 4
    for image in candidates:
        steps = (facing[image["id"]] - offset) / 90
        if abs(steps - round(steps)) * 90 > tolerance:
            continue
        image.update(view=SIDES[round(steps) % 4], view_source="exif")
