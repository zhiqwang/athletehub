"""Integration tests for the five new trail MCP tools.

Each test sets up an in-memory (temp-file) SQLite database, seeds minimal
data, then calls the tool function directly.
"""

from __future__ import annotations

import os
import tempfile
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    """Migrate and seed the temp database once for the module."""
    tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_db.close()

    old_val = os.environ.get("ATHLETEHUB_DB_PATH")
    os.environ["ATHLETEHUB_DB_PATH"] = tmp_db.name

    from athletehub.config import get_settings

    get_settings.cache_clear()

    from athletehub.db.migrate import migrate

    migrate(db_path=tmp_db.name)
    _seed_test_data(tmp_db.name)
    yield
    get_settings.cache_clear()
    if old_val is None:
        os.environ.pop("ATHLETEHUB_DB_PATH", None)
    else:
        os.environ["ATHLETEHUB_DB_PATH"] = old_val
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
        assert result.get("source") == "activity"
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
        assert result.get("source") == "activity"
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


# ---------------------------------------------------------------------------
# 6. trail_itra_score
# ---------------------------------------------------------------------------


class TestTrailItraScore:
    def test_from_activity(self):
        from athletehub.mcp.trail_tools import trail_itra_score

        result = trail_itra_score(activity_id=_get_activity_id())
        assert "itra_score" in result
        assert 0 <= result["itra_score"] <= 1000
        assert "km_effort" in result
        assert "itra_category" in result
        assert "level" in result
        assert "speed_km_effort_per_h" in result
        assert "finish_time_formatted" in result
        assert result["activity_id"] == _get_activity_id()

    def test_manual_params(self):
        from athletehub.mcp.trail_tools import trail_itra_score

        result = trail_itra_score(
            distance_km=50.0,
            elevation_gain_m=3000.0,
            finish_time_minutes=480.0,  # 8 hours
        )
        assert "itra_score" in result
        assert 0 <= result["itra_score"] <= 1000
        assert result["km_effort"] == 80.0  # 50 + 3000/100
        assert result["itra_category"] == "M"
        assert "activity_id" not in result

    def test_manual_no_elevation(self):
        from athletehub.mcp.trail_tools import trail_itra_score

        result = trail_itra_score(
            distance_km=42.195,
            finish_time_minutes=240.0,  # 4 hours
        )
        assert "itra_score" in result
        assert result["elevation_gain_m"] == 0.0
        assert result["km_effort"] == 42.2  # 42.195 rounded

    def test_utmb_elite(self):
        """Cross-check: UTMB-like elite performance → ~950+ score."""
        from athletehub.mcp.trail_tools import trail_itra_score

        result = trail_itra_score(
            distance_km=171.0,
            elevation_gain_m=10000.0,
            finish_time_minutes=19.0 * 60 + 50,  # ~19:50
        )
        assert result["itra_score"] >= 900
        assert result["level"] == "world_elite"
        assert result["itra_category"] == "XXL"

    def test_both_params_error(self):
        from athletehub.mcp.trail_tools import trail_itra_score

        result = trail_itra_score(
            activity_id=1,
            distance_km=10.0,
        )
        assert "error" in result

        # Also reject activity_id with only elevation or time
        result2 = trail_itra_score(
            activity_id=1,
            elevation_gain_m=500.0,
        )
        assert "error" in result2

    def test_no_params_error(self):
        from athletehub.mcp.trail_tools import trail_itra_score

        result = trail_itra_score()
        assert "error" in result

    def test_invalid_distance(self):
        from athletehub.mcp.trail_tools import trail_itra_score

        result = trail_itra_score(distance_km=-5.0, finish_time_minutes=60.0)
        assert "error" in result

    def test_missing_time(self):
        from athletehub.mcp.trail_tools import trail_itra_score

        result = trail_itra_score(distance_km=10.0)
        assert "error" in result

    def test_nonexistent_activity(self):
        from athletehub.mcp.trail_tools import trail_itra_score

        result = trail_itra_score(activity_id=99999)
        assert result.get("source") == "activity"
        assert "error" in result

    def test_score_increases_with_speed(self):
        """Faster finish time → higher score for the same course."""
        from athletehub.mcp.trail_tools import trail_itra_score

        slow = trail_itra_score(
            distance_km=50.0,
            elevation_gain_m=3000.0,
            finish_time_minutes=600.0,
        )
        fast = trail_itra_score(
            distance_km=50.0,
            elevation_gain_m=3000.0,
            finish_time_minutes=300.0,
        )
        assert fast["itra_score"] > slow["itra_score"]

    # --- New fields present even without calibration ---

    def test_formula_only_new_fields(self):
        """Without past_race_scores the new meta-fields are present."""
        from athletehub.mcp.trail_tools import trail_itra_score

        result = trail_itra_score(
            distance_km=50.0,
            elevation_gain_m=3000.0,
            finish_time_minutes=480.0,
        )
        assert result["method"] == "formula_only"
        assert result["calibration_factor"] == 1.0
        assert result["reference_races_used"] == 0
        assert result["confidence"] == "low"
        # formula_only: itra_score == base_formula_score
        assert result["itra_score"] == result["base_formula_score"]
        assert "training_summary" in result
        assert "reference_races" not in result

    # --- Race-calibrated path ---

    def test_race_calibrated_single_ref(self):
        """One past race → confidence=medium, score shifts by calibration factor."""
        from athletehub.mcp.trail_tools import trail_itra_score

        # Suppose the formula gives ~636 for this race; if the athlete
        # actually scored 680, calibration_factor ≈ 1.069.
        result = trail_itra_score(
            distance_km=50.0,
            elevation_gain_m=3000.0,
            finish_time_minutes=480.0,
            past_race_scores=[
                {
                    "distance_km": 50.0,
                    "elevation_gain_m": 3000.0,
                    "finish_time_minutes": 480.0,
                    "itra_score": 680,
                }
            ],
        )
        assert result["method"] == "race_calibrated"
        assert result["reference_races_used"] == 1
        assert result["confidence"] == "medium"
        assert result["calibration_factor"] > 1.0
        assert result["itra_score"] > result["base_formula_score"]
        assert "reference_races" in result
        assert len(result["reference_races"]) == 1
        ref = result["reference_races"][0]
        assert ref["known_itra_score"] == 680
        assert ref["proximity_weight"] == 1.0  # identical km-effort → weight=1

    def test_race_calibrated_three_refs_high_confidence(self):
        """Three reference races → confidence=high."""
        from athletehub.mcp.trail_tools import trail_itra_score

        result = trail_itra_score(
            distance_km=80.0,
            elevation_gain_m=4000.0,
            finish_time_minutes=600.0,
            past_race_scores=[
                {
                    "distance_km": 50.0,
                    "elevation_gain_m": 3000.0,
                    "finish_time_minutes": 480.0,
                    "itra_score": 630,
                },
                {
                    "distance_km": 80.0,
                    "elevation_gain_m": 4500.0,
                    "finish_time_minutes": 620.0,
                    "itra_score": 645,
                },
                {
                    "distance_km": 60.0,
                    "elevation_gain_m": 3500.0,
                    "finish_time_minutes": 510.0,
                    "itra_score": 640,
                },
            ],
        )
        assert result["method"] == "race_calibrated"
        assert result["reference_races_used"] == 3
        assert result["confidence"] == "high"
        assert 0 <= result["itra_score"] <= 1000

    def test_calibration_factor_clamped(self):
        """An extreme known_itra_score must not push factor outside [0.6, 1.4]."""
        from athletehub.mcp.trail_tools import trail_itra_score

        result = trail_itra_score(
            distance_km=50.0,
            elevation_gain_m=3000.0,
            finish_time_minutes=480.0,
            past_race_scores=[
                {
                    "distance_km": 50.0,
                    "elevation_gain_m": 3000.0,
                    "finish_time_minutes": 480.0,
                    "itra_score": 1000,  # artificially perfect
                }
            ],
        )
        assert result["calibration_factor"] <= 1.4

    def test_proximity_weight_decreases_with_km_effort_gap(self):
        """A reference race far in km-effort contributes less (lower weight)."""
        import math

        from athletehub.mcp.trail_tools import _calibrate_from_past_races

        _, refs_near = _calibrate_from_past_races(
            80.0,
            [
                {
                    "distance_km": 80.0,
                    "elevation_gain_m": 0.0,
                    "finish_time_minutes": 600.0,
                    "itra_score": 650,
                }
            ],
        )
        _, refs_far = _calibrate_from_past_races(
            80.0,
            [
                {
                    "distance_km": 30.0,
                    "elevation_gain_m": 0.0,
                    "finish_time_minutes": 300.0,
                    "itra_score": 650,
                }
            ],
        )
        assert refs_near[0]["proximity_weight"] > refs_far[0]["proximity_weight"]
        # Identical km-effort → weight must be exactly 1.0 (Gaussian peak)
        assert refs_near[0]["proximity_weight"] == 1.0
        # 50 km-effort gap (80 - 30) with σ=30 → weight ≈ exp(-0.5*(50/30)²) ≈ 0.057
        expected_far = round(math.exp(-0.5 * (50.0 / 30.0) ** 2), 3)
        assert refs_far[0]["proximity_weight"] == pytest.approx(expected_far, abs=0.001)

    def test_invalid_past_race_entries_skipped(self):
        """Malformed or out-of-range past_race_scores entries are silently ignored."""
        from athletehub.mcp.trail_tools import trail_itra_score

        result = trail_itra_score(
            distance_km=50.0,
            elevation_gain_m=3000.0,
            finish_time_minutes=480.0,
            past_race_scores=[
                {"distance_km": -1.0, "finish_time_minutes": 300.0, "itra_score": 600},
                {"distance_km": 50.0, "finish_time_minutes": 300.0, "itra_score": 2000},
                "not a dict",
                None,
            ],
        )
        # All entries invalid → falls back to formula_only
        assert result["method"] == "formula_only"
        assert result["reference_races_used"] == 0

    def test_training_summary_included(self):
        """training_summary is always present and has expected keys."""
        from athletehub.mcp.trail_tools import trail_itra_score

        result = trail_itra_score(
            activity_id=_get_activity_id(),
        )
        ts = result["training_summary"]
        assert "activities_analyzed" in ts
        assert "period_days" in ts
        assert "avg_trail_speed_km_effort_per_h" in ts
        # The seeded activity was inserted 30 days ago, default days=180
        assert ts["activities_analyzed"] >= 1
        assert ts["avg_trail_speed_km_effort_per_h"] is not None
        assert ts["avg_trail_speed_km_effort_per_h"] > 0
