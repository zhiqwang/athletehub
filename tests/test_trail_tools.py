"""Integration tests for the five new trail MCP tools.

Each test sets up an in-memory (temp-file) SQLite database, seeds minimal
data, then calls the tool function directly.
"""

from __future__ import annotations

import tempfile
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture(scope="module", autouse=True)
def _setup_db(monkeypatch):
    """Migrate and seed the temp database once for the module."""
    tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_db.close()
    monkeypatch.setenv("ATHLETEHUB_DB_PATH", tmp_db.name)

    from athletehub.core.config import get_settings

    get_settings.cache_clear()

    from athletehub.db.migrate import migrate

    migrate(db_path=tmp_db.name)
    _seed_test_data(tmp_db.name)
    yield
    Path(tmp_db.name).unlink(missing_ok=True)


def _seed_test_data(db_path: str):
    """Insert a trail_run activity with records to exercise all tools."""
    from athletehub.db.db import get_connection

    conn = get_connection(db_path)
    try:
        # Check default athlete exists
        athlete = conn.execute("SELECT id FROM athletes LIMIT 1").fetchone()
        athlete_id = athlete["id"] if athlete else 1

        # Update profile with HR data
        conn.execute(
            """
            UPDATE athlete_profiles
            SET max_hr_bpm = 190, threshold_hr_bpm = 170
            WHERE athlete_id = ?
            """,
            (athlete_id,),
        )

        # Insert a trail_run activity
        started_at = datetime.combine(
            date.today() - timedelta(days=30),
            time(hour=8),
            tzinfo=timezone.utc,
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute(
            """
            INSERT INTO activities (
                athlete_id, sport, title, started_at, distance_m,
                moving_time_s, elevation_gain_m, average_hr_bpm
            ) VALUES (?, 'trail_run', 'Test Trail Run', ?,
                      10000, 4200, 600, 155)
            """,
            (athlete_id, started_at),
        )
        activity_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Insert 100 sample records simulating a trail run
        for i in range(100):
            distance = i * 100.0  # 0 to 9900m
            elapsed = i * 42  # ~42s per 100m
            # Simulate elevation: flat → climb → descent → flat
            if i < 25:
                altitude = 500.0
            elif i < 50:
                altitude = 500.0 + (i - 25) * 8.0  # +200m climb
            elif i < 75:
                altitude = 700.0 - (i - 50) * 8.0  # -200m descent
            else:
                altitude = 500.0

            # HR rises on climbs
            hr = 140 + (i % 30)

            # Cadence varies
            cadence = 170 + (i % 10)

            # Speed: slower on climbs, faster on descents
            if 25 <= i < 50:
                speed = 1.8  # ~9:15/km (hiking)
            elif 50 <= i < 75:
                speed = 4.0  # fast descent
            else:
                speed = 3.0  # normal

            conn.execute(
                """
                INSERT INTO activity_records (
                    activity_id, sample_index, elapsed_time_s, distance_m,
                    latitude, longitude, altitude_m, heart_rate_bpm,
                    cadence_spm, speed_mps
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    activity_id,
                    i,
                    elapsed,
                    distance,
                    40.0 + i * 0.0001,
                    -74.0,
                    altitude,
                    hr,
                    cadence,
                    speed,
                ),
            )

        conn.commit()
    finally:
        conn.close()


def _get_activity_id() -> int:
    from athletehub.db.db import fetch_one

    row = fetch_one("SELECT id FROM activities WHERE sport = 'trail_run' LIMIT 1")
    assert row is not None
    return row["id"]


# ---------------------------------------------------------------------------
# 1. trail_technical_section_detector
# ---------------------------------------------------------------------------


class TestTrailTechnicalSectionDetector:
    def test_from_activity_records(self):
        from athletehub.mcp.trail_tools import trail_technical_section_detector

        result = trail_technical_section_detector(activity_id=_get_activity_id())
        assert result["source"] == "activity"
        assert "total_distance_km" in result
        assert "technical_sections" in result
        assert "summary" in result

    def test_missing_both_params(self):
        from athletehub.mcp.trail_tools import trail_technical_section_detector

        result = trail_technical_section_detector()
        assert "error" in result

    def test_nonexistent_activity(self):
        from athletehub.mcp.trail_tools import trail_technical_section_detector

        result = trail_technical_section_detector(activity_id=99999)
        assert result.get("source") == "activity"
        assert "error" in result


# ---------------------------------------------------------------------------
# 2. trail_climb_efficiency
# ---------------------------------------------------------------------------


class TestTrailClimbEfficiency:
    def test_basic(self):
        from athletehub.mcp.trail_tools import trail_climb_efficiency

        result = trail_climb_efficiency(activity_id=_get_activity_id())
        assert result["activity_id"] == _get_activity_id()
        assert "total_climbing_m" in result
        assert "overall_vam_m_per_h" in result
        assert "climb_segments" in result
        assert "aggregate" in result
        agg = result["aggregate"]
        assert "avg_vam" in agg
        assert "hr_zone_distribution" in agg
        assert "efficiency_ratio" in agg

    def test_nonexistent_activity(self):
        from athletehub.mcp.trail_tools import trail_climb_efficiency

        result = trail_climb_efficiency(activity_id=99999)
        assert "error" in result


# ---------------------------------------------------------------------------
# 3. trail_downhill_risk
# ---------------------------------------------------------------------------


class TestTrailDownhillRisk:
    def test_basic(self):
        from athletehub.mcp.trail_tools import trail_downhill_risk

        result = trail_downhill_risk(activity_id=_get_activity_id())
        assert result["activity_id"] == _get_activity_id()
        assert "total_descent_m" in result
        assert "descent_segments" in result
        assert "overall_risk_score" in result
        assert "overall_risk_label" in result
        assert "recommendations" in result

    def test_nonexistent_activity(self):
        from athletehub.mcp.trail_tools import trail_downhill_risk

        result = trail_downhill_risk(activity_id=99999)
        assert "error" in result


# ---------------------------------------------------------------------------
# 4. trail_hiking_ratio
# ---------------------------------------------------------------------------


class TestTrailHikingRatio:
    def test_basic(self):
        from athletehub.mcp.trail_tools import trail_hiking_ratio

        result = trail_hiking_ratio(
            race_distance_km=50.0,
            race_elevation_gain_m=3000.0,
        )
        assert result["race_distance_km"] == 50.0
        assert "predicted_running_pct" in result
        assert "predicted_hiking_pct" in result
        assert "estimated_finish_time_formatted" in result
        assert "confidence" in result
        assert result["training_data"]["activities_analyzed"] >= 1

    def test_no_training_data(self):
        from athletehub.mcp.trail_tools import trail_hiking_ratio

        # Use 0 day window — no activities
        result = trail_hiking_ratio(
            race_distance_km=30.0,
            race_elevation_gain_m=1500.0,
            days=0,
        )
        # Should still return valid structure with low confidence
        assert result["confidence"] == "low"
        assert result["training_data"]["activities_analyzed"] == 0


# ---------------------------------------------------------------------------
# 5. trail_cutoff_risk
# ---------------------------------------------------------------------------


class TestTrailCutoffRisk:
    def test_basic(self):
        from athletehub.mcp.trail_tools import trail_cutoff_risk

        result = trail_cutoff_risk(
            race_distance_km=50.0,
            race_elevation_gain_m=3000.0,
            cutoff_time_minutes=720.0,  # 12 hours
        )
        assert result["race_distance_km"] == 50.0
        assert "estimated_finish_minutes" in result
        assert "margin_pct" in result
        assert "risk_label" in result
        assert "training_basis" in result
        assert "recommendations" in result

    def test_with_intermediate_cutoffs(self):
        from athletehub.mcp.trail_tools import trail_cutoff_risk

        result = trail_cutoff_risk(
            race_distance_km=50.0,
            race_elevation_gain_m=3000.0,
            cutoff_time_minutes=720.0,
            intermediate_cutoffs=[
                {"km": 15.0, "cutoff_minutes": 200.0},
                {"km": 30.0, "cutoff_minutes": 420.0},
            ],
        )
        assert result["intermediate_checkpoints"] is not None
        assert len(result["intermediate_checkpoints"]) == 2
        for cp in result["intermediate_checkpoints"]:
            assert "km" in cp
            assert "estimated_arrival_min" in cp
            assert "margin_pct" in cp
            assert "status" in cp

    def test_with_heat(self):
        from athletehub.mcp.trail_tools import trail_cutoff_risk

        result_cool = trail_cutoff_risk(
            race_distance_km=50.0,
            race_elevation_gain_m=3000.0,
            cutoff_time_minutes=720.0,
            expected_temperature_c=10.0,
        )
        result_hot = trail_cutoff_risk(
            race_distance_km=50.0,
            race_elevation_gain_m=3000.0,
            cutoff_time_minutes=720.0,
            expected_temperature_c=35.0,
        )
        # Hot race should have worse (lower) margin
        assert result_hot["margin_pct"] < result_cool["margin_pct"]

    def test_tight_cutoff(self):
        from athletehub.mcp.trail_tools import trail_cutoff_risk

        result = trail_cutoff_risk(
            race_distance_km=100.0,
            race_elevation_gain_m=6000.0,
            cutoff_time_minutes=60.0,  # 1 hour — impossibly tight
        )
        assert result["risk_label"] == "likely_dnf"
