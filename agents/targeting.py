"""Select the target building on a frame; this does not prove cadastral identity.

Order: a manual region > the house under the flight point (agents/geo.py) > the house in the
central zone of the frame > only the central zone when every house is off the centre.
"""

from agents.geo import project
from agents.readiness.config import classes_config


def overlap_fraction(box: tuple, region: tuple) -> float:
    x1, y1, x2, y2 = box
    a1, b1, a2, b2 = region
    area = (x2 - x1) * (y2 - y1)
    intersection = max(0, min(x2, a2) - max(x1, a1)) * max(0, min(y2, b2) - max(y1, b1))
    return intersection / area if area > 0 else 0.0


def exclusion_reason(box: tuple, image: dict) -> str | None:
    if image.get("target_bbox") is not None:
        # Conservatively discard detections crossing the selected boundary.
        return None if overlap_fraction(box, image["target_bbox"]) >= 0.9 else "OUTSIDE_TARGET"
    return None


ROOFS = ("roof_frame", "roof_covered")
WALLS = ("walls_partial", "walls_full")


def _stacked(upper, lower, rules) -> bool:
    """Roof on walls or walls on a foundation of one house in an oblique view.

    Such boxes sit one above the other and barely overlap, so the overlap rule misses them.
    """
    pair_ok = (upper.cls in ROOFS and (lower.cls in WALLS or lower.cls == "foundation")) or (
        upper.cls in WALLS and lower.cls == "foundation"
    )
    if not pair_ok:
        return False
    ux1, uy1, ux2, uy2 = upper.bbox
    lx1, ly1, lx2, ly2 = lower.bbox
    narrow = min(ux2 - ux1, lx2 - lx1)
    shared = min(ux2, lx2) - max(ux1, lx1)
    gap = ly1 - uy2  # negative when the boxes overlap
    return (
        uy1 + uy2 < ly1 + ly2
        and narrow > 0
        and shared >= rules.stack_overlap * narrow
        and gap <= rules.stack_gap * min(uy2 - uy1, ly2 - ly1)
    )


def _same_house(a, b, rules) -> bool:
    return (
        overlap_fraction(a.bbox, b.bbox) >= rules.merge_overlap
        or overlap_fraction(b.bbox, a.bbox) >= rules.merge_overlap
        or _stacked(a, b, rules)
        or _stacked(b, a, rules)
    )


def _anchors(detections: list, rules) -> list:
    return [
        row for row in detections if row.cls in rules.anchor_classes and row.conf >= rules.min_conf
    ]


def _house_region(anchors: list, chosen, rules) -> tuple:
    """Merge the boxes of the chosen house and expand them by the margin."""
    house = [chosen]
    # Two rounds: walls -> roof on them -> the roof's own duplicate boxes, without long chains.
    for _ in range(2):
        house += [
            row
            for row in anchors
            if all(row is not member for member in house)
            and any(_same_house(row, member, rules) for member in house)
        ]
    x1, y1 = min(row.bbox[0] for row in house), min(row.bbox[1] for row in house)
    x2, y2 = max(row.bbox[2] for row in house), max(row.bbox[3] for row in house)
    mx, my = (x2 - x1) * rules.margin, (y2 - y1) * rules.margin
    return max(0.0, x1 - mx), max(0.0, y1 - my), min(1.0, x2 + mx), min(1.0, y2 + my)


def _distance(row, x: float, y: float) -> tuple[float, float]:
    """Zero when the box covers the point; ties go to the box whose centre is closer."""
    x1, y1, x2, y2 = row.bbox
    dx, dy = max(x1 - x, 0, x - x2), max(y1 - y, 0, y - y2)
    return (dx * dx + dy * dy) ** 0.5, abs((x1 + x2) / 2 - x) + abs((y1 + y2) / 2 - y)


def point_target(detections: list, point: tuple[float, float]) -> tuple | None:
    """Region of the house under the flight point on this frame, or None if none is near it."""
    rules, limit = classes_config().auto_target, classes_config().flight_point
    if rules is None or limit is None or not (0 <= point[0] <= 1 and 0 <= point[1] <= 1):
        return None
    anchors = _anchors(detections, rules)
    if not anchors:
        return None
    chosen = min(anchors, key=lambda row: _distance(row, *point))
    if _distance(chosen, *point)[0] > limit.max_point_distance:
        return None
    return _house_region(anchors, chosen, rules)


def central_target(detections: list) -> tuple[tuple | None, str | None]:
    """Target region on a frame and its source, chosen without a manual region.

    Drone frames show neighbouring houses too; the pilot aims the camera at the target, so the
    building in the central zone of the frame is taken. Its roof, walls and foundation boxes are
    merged into one region, expanded by a margin so windows, doors and finish on the edges stay
    inside. Returns:
      (region, "auto_center")  — a building in the central zone;
      (zone, "centre_zone")    — buildings only off the centre: no neighbour is substituted,
                                 only the central zone counts and the flight needs review;
      (None, None)             — no reliable building at all (empty plot): nothing to separate.
    """
    rules = classes_config().auto_target
    if rules is None:
        return None, None
    anchors = _anchors(detections, rules)
    if not anchors:
        return None, None
    low, high = 0.5 - rules.centre_zone, 0.5 + rules.centre_zone
    central = [
        row
        for row in anchors
        if row.bbox[0] <= high and row.bbox[2] >= low and row.bbox[1] <= high and row.bbox[3] >= low
    ]
    if not central:
        return (low, low, high, high), "centre_zone"
    chosen = min(central, key=lambda row: _distance(row, 0.5, 0.5))
    return _house_region(anchors, chosen, rules), "auto_center"


def locate_target(detections: list, image: dict, point: tuple | None) -> tuple:
    """Target region of a frame without a manual one: flight point first, then the centre.

    Returns (region, source, projected point). Source is "flight_point" when the object point of
    the flight landed on a house, otherwise the result of central_target.
    """
    projected = project(image, point) if point is not None else None
    if projected is not None:
        region = point_target(detections, projected)
        if region is not None:
            return region, "flight_point", projected
    region, source = central_target(detections)
    return region, source, projected
