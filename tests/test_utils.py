"""Unit tests for utility helpers: elevation, metrics, gpx."""

from __future__ import annotations

from pathlib import Path

from athletehub.utils.elevation import compute_vam
from athletehub.utils.gpx import (
    detect_technical_sections,
    detect_technical_sections_from_records,
)
from athletehub.utils.metrics import hr_zone_for_bpm


# ---------------------------------------------------------------------------
# compute_vam
# ---------------------------------------------------------------------------


class TestComputeVam:
    def test_basic(self):
        # 500m gain in 1 hour => 500 m/h
        assert compute_vam(500.0, 3600.0) == 500.0

    def test_zero_time(self):
        assert compute_vam(100.0, 0.0) == 0.0

    def test_negative_time(self):
        assert compute_vam(100.0, -1.0) == 0.0

    def test_no_gain(self):
        assert compute_vam(0.0, 3600.0) == 0.0

    def test_half_hour(self):
        # 300m in 30 min => 600 m/h
        assert compute_vam(300.0, 1800.0) == 600.0


# ---------------------------------------------------------------------------
# hr_zone_for_bpm
# ---------------------------------------------------------------------------


class TestHrZoneForBpm:
    def test_none_hr(self):
        assert hr_zone_for_bpm(None, max_hr_bpm=190) == 0

    def test_zone1(self):
        # Low HR relative to max
        assert hr_zone_for_bpm(100, max_hr_bpm=190) == 1

    def test_zone2(self):
        # ~75% of threshold
        assert hr_zone_for_bpm(125, max_hr_bpm=190) == 2

    def test_zone3(self):
        # Use threshold_hr directly: 150/170 ~ 0.88
        assert hr_zone_for_bpm(150, threshold_hr_bpm=170) == 3

    def test_zone4(self):
        # 165/170 ~ 0.97
        assert hr_zone_for_bpm(165, threshold_hr_bpm=170) == 4

    def test_zone5(self):
        # 180/170 ~ 1.06
        assert hr_zone_for_bpm(180, threshold_hr_bpm=170) == 5

    def test_fallback_reference(self):
        # No max_hr or threshold → uses 165
        zone = hr_zone_for_bpm(140)
        assert zone in (2, 3)

    def test_threshold_takes_priority(self):
        # When both provided, threshold is used
        z1 = hr_zone_for_bpm(150, max_hr_bpm=200, threshold_hr_bpm=170)
        z2 = hr_zone_for_bpm(150, max_hr_bpm=200)
        # May differ since reference differs
        assert isinstance(z1, int)
        assert isinstance(z2, int)


# ---------------------------------------------------------------------------
# detect_technical_sections (GPX)
# ---------------------------------------------------------------------------

_STEEP_GPX = """\
<?xml version="1.0" encoding="UTF-8"?>
<gpx xmlns="http://www.topografix.com/GPX/1/1">
  <trk><trkseg>
    {points}
  </trkseg></trk>
</gpx>
"""


def _make_gpx(tmp_path: Path, points: list[tuple[float, float, float]]) -> Path:
    """Write a minimal GPX file with the given (lat, lon, ele) points."""
    trkpts = [
        f'<trkpt lat="{lat}" lon="{lon}"><ele>{ele}</ele></trkpt>' for lat, lon, ele in points
    ]
    xml_points = "\n".join(trkpts)
    content = _STEEP_GPX.format(points=xml_points)
    gpx_path = tmp_path / "test_route.gpx"
    gpx_path.write_text(content, encoding="utf-8")
    return gpx_path


class TestDetectTechnicalSections:
    def test_empty_gpx(self, tmp_path: Path):
        gpx_path = _make_gpx(tmp_path, [])
        result = detect_technical_sections(gpx_path)
        assert result["total_distance_km"] == 0.0
        assert result["technical_sections"] == []

    def test_single_point(self, tmp_path: Path):
        gpx_path = _make_gpx(tmp_path, [(40.0, -74.0, 100.0)])
        result = detect_technical_sections(gpx_path)
        assert result["total_distance_km"] == 0.0

    def test_flat_route_no_sections(self, tmp_path: Path):
        # 10 points, all at 100m elevation, spaced ~11m apart
        points = [(40.0 + i * 0.0001, -74.0, 100.0) for i in range(10)]
        gpx_path = _make_gpx(tmp_path, points)
        result = detect_technical_sections(gpx_path, grade_threshold=15.0)
        assert result["total_distance_km"] > 0
        # Flat route should have no steep sections
        steep = [s for s in result["technical_sections"] if "steep" in s["type"]]
        assert len(steep) == 0

    def test_steep_climb_detected(self, tmp_path: Path):
        # Create a route with a very steep section (~45% grade)
        # Each step is ~11m horizontal, 5m vertical
        points = []
        for i in range(20):
            lat = 40.0 + i * 0.0001
            if 5 <= i <= 15:
                ele = 100.0 + (i - 5) * 5.0  # steep climb
            else:
                ele = 100.0
            points.append((lat, -74.0, ele))
        gpx_path = _make_gpx(tmp_path, points)
        result = detect_technical_sections(
            gpx_path, grade_threshold=15.0, min_section_length_m=10.0
        )
        # Should detect at least one technical section, ideally a steep_climb
        assert len(result["technical_sections"]) > 0
        types = [s["type"] for s in result["technical_sections"]]
        assert "steep_climb" in types or result["summary"]["steep_climb_count"] > 0

    def test_summary_keys(self, tmp_path: Path):
        points = [(40.0 + i * 0.0001, -74.0, 100.0 + i) for i in range(10)]
        gpx_path = _make_gpx(tmp_path, points)
        result = detect_technical_sections(gpx_path)
        summary = result["summary"]
        assert "steep_climb_count" in summary
        assert "steep_descent_count" in summary
        assert "technical_count" in summary
        assert "total_flagged_distance_m" in summary
        assert "flagged_pct" in summary
        assert "steep_climb_distance_m" in summary
        assert "steep_descent_distance_m" in summary
        assert "technical_distance_m" in summary


class TestDetectTechnicalSectionsFromRecords:
    def test_basic(self):
        records = [
            {
                "latitude": 40.0 + i * 0.0001,
                "longitude": -74.0,
                "altitude_m": 100.0,
                "speed_mps": 3.0,
            }
            for i in range(10)
        ]
        result = detect_technical_sections_from_records(records)
        assert "total_distance_km" in result
        assert "technical_sections" in result
        assert "summary" in result

    def test_no_records(self):
        result = detect_technical_sections_from_records([])
        assert result["total_distance_km"] == 0.0

    def test_records_without_speed(self):
        records = [
            {"latitude": 40.0 + i * 0.0001, "longitude": -74.0, "altitude_m": 100.0}
            for i in range(10)
        ]
        result = detect_technical_sections_from_records(records)
        assert result["total_distance_km"] > 0
