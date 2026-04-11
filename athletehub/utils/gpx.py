from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from pathlib import Path


def summarize_gpx(path: str | Path) -> dict:
    file_path = Path(path).expanduser()
    root = ET.parse(file_path).getroot()

    points: list[tuple[float, float, float | None]] = []
    for node in root.findall(".//{*}trkpt"):
        lat = float(node.attrib["lat"])
        lon = float(node.attrib["lon"])
        elevation_node = node.find("{*}ele")
        elevation = float(elevation_node.text) if elevation_node is not None else None
        points.append((lat, lon, elevation))

    distance_m = 0.0
    elevation_gain_m = 0.0

    for current, nxt in zip(points, points[1:]):
        distance_m += _haversine_m(current[0], current[1], nxt[0], nxt[1])
        if current[2] is not None and nxt[2] is not None and nxt[2] > current[2]:
            elevation_gain_m += nxt[2] - current[2]

    return {
        "points": len(points),
        "distance_km": round(distance_m / 1000.0, 2),
        "elevation_gain_m": round(elevation_gain_m, 0),
    }


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_m = 6_371_000
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)

    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(d_lon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius_m * c
