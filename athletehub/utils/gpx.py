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


# ---------------------------------------------------------------------------
# Technical-section detection
# ---------------------------------------------------------------------------

def detect_technical_sections(
    path: str | Path,
    grade_threshold: float = 15.0,
    min_section_length_m: float = 50.0,
    oscillation_threshold_m: float = 3.0,
    window_size: int = 5,
) -> dict:
    """Detect technical sections in a GPX file.

    Returns a dict with ``total_distance_km``, ``technical_sections`` list and
    a ``summary`` of detected section types.
    """

    points = _parse_gpx_trackpoints(path)
    return _detect_sections_from_points(
        points,
        grade_threshold=grade_threshold,
        min_section_length_m=min_section_length_m,
        oscillation_threshold_m=oscillation_threshold_m,
        window_size=window_size,
    )


def detect_technical_sections_from_records(
    records: list[dict],
    grade_threshold: float = 15.0,
    min_section_length_m: float = 50.0,
    oscillation_threshold_m: float = 3.0,
    window_size: int = 5,
) -> dict:
    """Same analysis but using pre-fetched activity_records (list of dicts).

    Each dict should contain at minimum ``latitude``, ``longitude``,
    ``altitude_m``.
    """

    points: list[_TrackPoint] = []
    cumulative_distance = 0.0
    for i, rec in enumerate(records):
        lat = rec.get("latitude")
        lon = rec.get("longitude")
        ele = rec.get("altitude_m")
        if lat is None or lon is None:
            continue
        if i > 0 and points:
            prev = points[-1]
            cumulative_distance += _haversine_m(prev.lat, prev.lon, lat, lon)
        points.append(_TrackPoint(lat=lat, lon=lon, elevation=ele, cumulative_m=cumulative_distance))

    return _detect_sections_from_points(
        points,
        grade_threshold=grade_threshold,
        min_section_length_m=min_section_length_m,
        oscillation_threshold_m=oscillation_threshold_m,
        window_size=window_size,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class _TrackPoint:
    __slots__ = ("lat", "lon", "elevation", "cumulative_m")

    def __init__(
        self,
        lat: float,
        lon: float,
        elevation: float | None,
        cumulative_m: float,
    ) -> None:
        self.lat = lat
        self.lon = lon
        self.elevation = elevation
        self.cumulative_m = cumulative_m


def _parse_gpx_trackpoints(path: str | Path) -> list[_TrackPoint]:
    """Parse a GPX file into a list of ``_TrackPoint``."""
    file_path = Path(path).expanduser()
    root = ET.parse(file_path).getroot()

    raw_points: list[tuple[float, float, float | None]] = []
    for node in root.findall(".//{*}trkpt"):
        lat = float(node.attrib["lat"])
        lon = float(node.attrib["lon"])
        ele_node = node.find("{*}ele")
        elevation = float(ele_node.text) if ele_node is not None else None
        raw_points.append((lat, lon, elevation))

    points: list[_TrackPoint] = []
    cumulative = 0.0
    for i, (lat, lon, ele) in enumerate(raw_points):
        if i > 0:
            prev = raw_points[i - 1]
            cumulative += _haversine_m(prev[0], prev[1], lat, lon)
        points.append(_TrackPoint(lat=lat, lon=lon, elevation=ele, cumulative_m=cumulative))
    return points


def _detect_sections_from_points(
    points: list[_TrackPoint],
    grade_threshold: float,
    min_section_length_m: float,
    oscillation_threshold_m: float,
    window_size: int,
) -> dict:
    if len(points) < 2:
        return {
            "total_distance_km": 0.0,
            "technical_sections": [],
            "summary": _empty_summary(),
        }

    if window_size < 3 or window_size % 2 == 0:
        raise ValueError("window_size must be an odd integer >= 3")

    total_distance_m = points[-1].cumulative_m

    # --- Compute per-window classifications ---
    window_flags: list[dict] = []

    half = window_size // 2
    for i in range(half, len(points) - half):
        seg_start = points[i - half]
        seg_end = points[i + half]
        dist = seg_end.cumulative_m - seg_start.cumulative_m
        if dist <= 0:
            continue

        window_pts = points[i - half : i + half + 1]

        # Grade
        ele_start = seg_start.elevation
        ele_end = seg_end.elevation
        grade = 0.0
        if ele_start is not None and ele_end is not None:
            grade = ((ele_end - ele_start) / dist) * 100.0

        # Elevation oscillation (std-dev of consecutive deltas)
        deltas: list[float] = []
        for a, b in zip(window_pts, window_pts[1:]):
            if a.elevation is not None and b.elevation is not None:
                deltas.append(b.elevation - a.elevation)
        osc_std = _std(deltas)

        # Classify
        section_type: str | None = None
        if grade >= grade_threshold:
            section_type = "steep_climb"
        elif grade <= -grade_threshold:
            section_type = "steep_descent"
        elif osc_std > oscillation_threshold_m:
            section_type = "technical"

        window_flags.append(
            {
                "index": i,
                "start_m": seg_start.cumulative_m,
                "end_m": seg_end.cumulative_m,
                "grade": grade,
                "section_type": section_type,
                "elevation": points[i].elevation,
            }
        )

    # --- Merge adjacent windows of the same type into sections ---
    raw_sections: list[dict] = []
    current: dict | None = None
    for wf in window_flags:
        if wf["section_type"] is None:
            if current is not None:
                raw_sections.append(current)
                current = None
            continue
        if current is not None and current["type"] == wf["section_type"]:
            current["end_m"] = wf["end_m"]
            current["grades"].append(wf["grade"])
            if wf["elevation"] is not None:
                current["elevations"].append(wf["elevation"])
        else:
            if current is not None:
                raw_sections.append(current)
            current = {
                "type": wf["section_type"],
                "start_m": wf["start_m"],
                "end_m": wf["end_m"],
                "grades": [wf["grade"]],
                "elevations": [wf["elevation"]] if wf["elevation"] is not None else [],
            }
    if current is not None:
        raw_sections.append(current)

    # --- Filter by minimum length and build output ---
    sections: list[dict] = []
    for sec in raw_sections:
        length = sec["end_m"] - sec["start_m"]
        if length < min_section_length_m:
            continue
        grades = sec["grades"]
        elevations = sec["elevations"]
        ele_change = (elevations[-1] - elevations[0]) if len(elevations) >= 2 else 0.0
        sections.append(
            {
                "start_km": round(sec["start_m"] / 1000.0, 2),
                "end_km": round(sec["end_m"] / 1000.0, 2),
                "length_m": round(length, 0),
                "type": sec["type"],
                "avg_grade": round(sum(grades) / len(grades), 1) if grades else 0.0,
                "max_grade": round(max(abs(g) for g in grades), 1) if grades else 0.0,
                "elevation_change_m": round(ele_change, 1),
            }
        )

    summary = _build_summary(sections, total_distance_m)
    return {
        "total_distance_km": round(total_distance_m / 1000.0, 2),
        "technical_sections": sections,
        "summary": summary,
    }


def _build_summary(sections: list[dict], total_distance_m: float) -> dict:
    steep_climb = sum(1 for s in sections if s["type"] == "steep_climb")
    steep_descent = sum(1 for s in sections if s["type"] == "steep_descent")
    technical = sum(1 for s in sections if s["type"] == "technical")
    total_tech_m = sum(s["length_m"] for s in sections if s["type"] == "technical")
    pct = (total_tech_m / total_distance_m * 100.0) if total_distance_m > 0 else 0.0
    return {
        "steep_climb_count": steep_climb,
        "steep_descent_count": steep_descent,
        "technical_count": technical,
        "total_technical_distance_m": round(total_tech_m, 0),
        "technical_pct": round(pct, 1),
    }


def _empty_summary() -> dict:
    return {
        "steep_climb_count": 0,
        "steep_descent_count": 0,
        "technical_count": 0,
        "total_technical_distance_m": 0.0,
        "technical_pct": 0.0,
    }


def _std(values: list[float]) -> float:
    """Population standard deviation."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(variance)


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
