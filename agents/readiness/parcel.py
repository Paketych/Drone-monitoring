from math import cos, hypot, radians

METERS_PER_DEGREE_LAT = 110_540
METERS_PER_DEGREE_LON = 111_320


def outside_parcel(parcel: dict, points: list[list[float]], buffer_m: float) -> list[int]:
    """Indexes of GPS points [lat, lon] farther than buffer_m from the parcel polygon.

    The parcel is a GeoJSON Polygon with [lon, lat] vertices; only the outer ring is used.
    A local flat projection is accurate to centimetres at the size of a land plot.
    """
    ring = parcel["coordinates"][0]
    lon0, lat0 = ring[0]
    scale = cos(radians(lat0)) * METERS_PER_DEGREE_LON

    def project(lon: float, lat: float) -> tuple[float, float]:
        return (lon - lon0) * scale, (lat - lat0) * METERS_PER_DEGREE_LAT

    polygon = [project(lon, lat) for lon, lat in ring]
    edges = list(zip(polygon, polygon[1:]))
    far = []
    for index, (lat, lon) in enumerate(points):
        px, py = project(lon, lat)
        inside = False
        for (x1, y1), (x2, y2) in edges:
            if (y1 > py) != (y2 > py) and px < x1 + (py - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
        if inside:
            continue
        distance = min(_to_segment(px, py, x1, y1, x2, y2) for (x1, y1), (x2, y2) in edges)
        if distance > buffer_m:
            far.append(index)
    return far


def _to_segment(px: float, py: float, x1: float, y1: float, x2: float, y2: float) -> float:
    dx, dy = x2 - x1, y2 - y1
    length = dx * dx + dy * dy
    t = 0.0 if length == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / length))
    return hypot(px - (x1 + t * dx), py - (y1 + t * dy))
