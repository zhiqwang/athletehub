from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

from athletehub.config import get_settings
from athletehub.db.db import fetch_all, fetch_one
from athletehub.utils.elevation import climbing_density, compute_vam
from athletehub.utils.gpx import (
    detect_technical_sections as _gpx_detect,
    detect_technical_sections_from_records as _records_detect,
)
from athletehub.utils.metrics import format_duration, format_pace, hr_zone_for_bpm, safe_mean
from athletehub.utils.weather import heat_adjustment_seconds_per_km

# Default minimum speed (m/s) to classify a sample as "running" vs "hiking".
# ~1.852 m/s corresponds to ~9:00 min/km.
_DEFAULT_HIKING_THRESHOLD_MIN_PER_KM = 9.0
_DEFAULT_MIN_RUNNING_SPEED_MPS = 1000.0 / (_DEFAULT_HIKING_THRESHOLD_MIN_PER_KM * 60.0)

# Maximum number of activities to analyse when fetching per-record data in
# trail_hiking_ratio() and trail_cutoff_risk().  Limits memory usage and
# keeps latency predictable for users with many recorded activities.
_MAX_ACTIVITIES_FOR_RECORDS = 50


def _iter_record_deltas(records: list[dict]) -> Iterator[tuple[float, float | None]]:
    """Yield ``(distance_delta, speed_mps)`` for each record.

    Computes per-sample distance using ``distance_m`` (preferred) or
    ``speed_mps * Δelapsed_time_s`` as a fallback.  The first sample
    always yields ``delta = 0.0`` to avoid spurious large deltas when
    records start mid-activity with non-zero cumulative values.

    When ``distance_m`` appears for the first time (``prev_dist`` is
    ``None``), the speed×Δt fallback is used for that single sample so
    that the interval distance is not dropped.

    When ``distance_m`` disappears (becomes ``None``), ``prev_dist`` is
    reset so that a later reappearance of ``distance_m`` is treated as
    a fresh baseline rather than double-counting the gap interval.
    """
    prev_dist: float | None = None
    prev_time: float | None = None
    for rec in records:
        spd = rec.get("speed_mps")
        d = rec.get("distance_m")
        t = rec.get("elapsed_time_s")  # keep None when missing
        if d is not None and prev_dist is not None:
            delta = max(d - prev_dist, 0.0)
            prev_dist = d
        elif d is not None:
            # First appearance of distance_m — use speed×Δt if available
            # so the interval is not lost, then start tracking cumulative.
            if spd is not None and spd > 0 and prev_time is not None and t is not None:
                delta = spd * max(t - prev_time, 0.0)
            else:
                delta = 0.0
            prev_dist = d
        elif spd is not None and spd > 0:
            if prev_time is not None and t is not None:
                dt = max(t - prev_time, 0.0)
            else:
                dt = 0.0
            delta = spd * dt
            # Reset prev_dist so a later reappearance of distance_m
            # is treated as a fresh baseline, avoiding double-counting.
            prev_dist = None
        else:
            delta = 0.0
            prev_dist = None
        if t is not None:
            prev_time = t
        yield delta, spd


# ---------------------------------------------------------------------------
# Existing tools
# ---------------------------------------------------------------------------


def trail_difficulty(
    distance_km: float,
    elevation_gain_m: float,
    technicality: int = 3,
    max_altitude_m: float | None = None,
) -> dict:
    technicality_score = max(1, min(technicality, 5))
    climb_per_km = climbing_density(distance_km, elevation_gain_m)
    altitude_penalty = 0.0 if max_altitude_m is None else max(max_altitude_m - 2000.0, 0.0) / 200.0
    score = distance_km * 0.6 + climb_per_km * 0.05 + technicality_score * 6 + altitude_penalty

    return {
        "distance_km": round(distance_km, 2),
        "elevation_gain_m": round(elevation_gain_m, 0),
        "technicality": technicality_score,
        "climb_per_km_m": round(climb_per_km, 1),
        "max_altitude_m": max_altitude_m,
        "difficulty_score": round(score, 1),
        "difficulty_label": _difficulty_label(score),
    }


def trail_profile(days: int = 90) -> dict:
    window = max(days, 1)
    since = (date.today() - timedelta(days=window - 1)).isoformat()

    summary = (
        fetch_one(
            """
        SELECT
            COUNT(*) AS trail_sessions,
            ROUND(COALESCE(SUM(distance_m), 0) / 1000.0, 2) AS total_distance_km,
            ROUND(COALESCE(SUM(elevation_gain_m), 0), 0) AS total_climb_m,
            ROUND(COALESCE(
                AVG(elevation_gain_m / NULLIF(distance_m / 1000.0, 0)), 0
            ), 1) AS avg_climb_per_km_m
        FROM activities
        WHERE date(started_at) >= ?
          AND sport IN ('trail_run', 'hike', 'ultra_trail')
        """,
            (since,),
        )
        or {}
    )

    total_climb = float(summary.get("total_climb_m") or 0.0)
    total_distance = float(summary.get("total_distance_km") or 0.0)

    return {
        "period_days": window,
        **summary,
        "climb_density_m_per_km": round(climbing_density(total_distance, total_climb), 1),
        "recommendation": _trail_recommendation(total_distance, total_climb),
    }


# ---------------------------------------------------------------------------
# 1. trail_technical_section_detector
# ---------------------------------------------------------------------------


def trail_technical_section_detector(
    gpx_path: str | None = None,
    activity_id: int | None = None,
    grade_threshold: float = 15.0,
    min_section_length_m: float = 50.0,
) -> dict:
    """Identify technical sections (steep slopes, rocky terrain)
    from a GPX file *or* from stored activity records."""

    if gpx_path is not None and activity_id is not None:
        return {"source": "input", "error": "Provide either gpx_path or activity_id, not both"}

    if gpx_path is not None:
        # Restrict to safe relative paths within raw_data_dir.
        p = Path(gpx_path)
        if p.is_absolute() or ".." in p.parts or (p.parts and p.parts[0].startswith("~")):
            return {
                "source": "input",
                "error": (
                    "gpx_path must be a relative path without"
                    " '..' components and must not start with '~'"
                ),
            }
        if p.suffix.lower() != ".gpx":
            return {"source": "input", "error": "gpx_path must have a .gpx extension"}
        base_dir = get_settings().raw_data_dir
        resolved = (base_dir / p).resolve()
        resolved_base = base_dir.resolve()
        if not resolved.is_relative_to(resolved_base):
            return {
                "source": "input",
                "error": "gpx_path resolves outside the allowed data directory",
            }
        if not resolved.is_file():
            return {"source": "gpx", "error": f"GPX file not found: {gpx_path}"}
        # Validate numeric parameters before calling the parser so that
        # any ValueError from the GPX parser itself (e.g. bad lat/lon)
        # is correctly attributed to source="gpx".
        if grade_threshold <= 0:
            return {
                "source": "input",
                "error": "grade_threshold must be positive",
            }
        if min_section_length_m < 0:
            return {
                "source": "input",
                "error": "min_section_length_m must be non-negative",
            }
        try:
            result = _gpx_detect(
                str(resolved),
                grade_threshold=grade_threshold,
                min_section_length_m=min_section_length_m,
            )
        except (FileNotFoundError, OSError) as exc:
            return {"source": "gpx", "error": f"Cannot read GPX file: {exc}"}
        except ET.ParseError as exc:
            return {"source": "gpx", "error": f"Invalid GPX XML: {exc}"}
        except (ValueError, KeyError) as exc:
            return {"source": "gpx", "error": f"Invalid GPX data: {exc}"}
        result["source"] = "gpx"
        return result

    if activity_id is not None:
        records = fetch_all(
            """
            SELECT latitude, longitude, altitude_m
            FROM activity_records
            WHERE activity_id = ?
            ORDER BY sample_index
            """,
            (activity_id,),
        )
        if not records:
            return {
                "source": "activity",
                "activity_id": activity_id,
                "error": "No records found for activity_id",
            }
        try:
            result = _records_detect(
                records,
                grade_threshold=grade_threshold,
                min_section_length_m=min_section_length_m,
            )
        except ValueError as exc:
            return {"source": "input", "activity_id": activity_id, "error": str(exc)}
        result["source"] = "activity"
        result["activity_id"] = activity_id
        return result

    return {"source": "input", "error": "Provide either gpx_path or activity_id"}


# ---------------------------------------------------------------------------
# 2. trail_climb_efficiency
# ---------------------------------------------------------------------------


def trail_climb_efficiency(
    activity_id: int,
    climb_grade_threshold: float = 5.0,
) -> dict:
    """Analyse climbing efficiency: VAM, HR zone distribution, cadence."""

    records = fetch_all(
        """
        SELECT altitude_m, heart_rate_bpm, cadence_spm, elapsed_time_s,
               distance_m, speed_mps
        FROM activity_records
        WHERE activity_id = ?
        ORDER BY sample_index
        """,
        (activity_id,),
    )
    if not records:
        return {"source": "activity", "activity_id": activity_id, "error": "No records found"}

    profile = _get_athlete_profile_for_activity(activity_id)
    max_hr = profile.get("max_hr_bpm") if profile else None
    threshold_hr = profile.get("threshold_hr_bpm") if profile else None

    # Compute per-record grade and identify climbing segments
    enriched = _enrich_records_with_grade(records)
    climb_segs = _extract_segments(enriched, lambda r: r["grade"] >= climb_grade_threshold)
    flat_segs = _extract_segments(enriched, lambda r: abs(r["grade"]) < climb_grade_threshold)

    total_climb_m = 0.0
    total_climb_time = 0.0
    segments_out: list[dict] = []

    for seg in climb_segs:
        gain = _segment_elevation_gain(seg)
        time_s = _segment_time(seg)
        vam = compute_vam(gain, time_s)
        hrs = [r["heart_rate_bpm"] for r in seg if r["heart_rate_bpm"] is not None]
        cadences = [r["cadence_spm"] for r in seg if r["cadence_spm"] is not None]
        avg_grade = safe_mean([r["grade"] for r in seg[1:]]) or 0.0

        total_climb_m += gain
        total_climb_time += time_s

        segments_out.append(
            {
                "start_km": round(seg[0]["_cumulative_dist_m"] / 1000.0, 2),
                "end_km": round(seg[-1]["_cumulative_dist_m"] / 1000.0, 2),
                "gain_m": round(gain, 1),
                "avg_grade": round(avg_grade, 1),
                "vam": round(vam, 0),
                "avg_hr": round(safe_mean(hrs) or 0, 1),
                "avg_cadence": round(safe_mean(cadences) or 0, 1),
                "hr_zone_distribution": _hr_zone_dist(hrs, max_hr, threshold_hr),
            }
        )

    overall_vam = compute_vam(total_climb_m, total_climb_time)

    # Aggregate HR/cadence on climbs vs flats
    climb_hrs = [
        r["heart_rate_bpm"] for s in climb_segs for r in s if r["heart_rate_bpm"] is not None
    ]
    climb_cadences = [
        r["cadence_spm"] for s in climb_segs for r in s if r["cadence_spm"] is not None
    ]
    flat_recs = [r for s in flat_segs for r in s]
    flat_cadences = [r["cadence_spm"] for r in flat_recs if r["cadence_spm"] is not None]

    avg_hr_climbs = safe_mean(climb_hrs) or 0.0
    avg_cad_climbs = safe_mean(climb_cadences) or 0.0
    avg_cad_flats = safe_mean(flat_cadences) or 0.0
    cad_ratio = (avg_cad_climbs / avg_cad_flats) if avg_cad_flats > 0 else 0.0
    efficiency = (overall_vam / avg_hr_climbs) if avg_hr_climbs > 0 else 0.0

    return {
        "activity_id": activity_id,
        "total_climbing_m": round(total_climb_m, 1),
        "total_climbing_time_s": round(total_climb_time, 0),
        "overall_vam_m_per_h": round(overall_vam, 0),
        "climb_segments": segments_out,
        "aggregate": {
            "avg_vam": round(overall_vam, 0),
            "avg_hr_on_climbs": round(avg_hr_climbs, 1),
            "avg_cadence_on_climbs": round(avg_cad_climbs, 1),
            "avg_cadence_on_flats": round(avg_cad_flats, 1),
            "cadence_climb_vs_flat_ratio": round(cad_ratio, 2),
            "hr_zone_distribution": _hr_zone_dist(climb_hrs, max_hr, threshold_hr),
            "efficiency_ratio": round(efficiency, 2),
        },
    }


# ---------------------------------------------------------------------------
# 3. trail_downhill_risk
# ---------------------------------------------------------------------------


def trail_downhill_risk(
    activity_id: int,
    descent_grade_threshold: float = -5.0,
) -> dict:
    """Score downhill risk for each descent segment in an activity."""

    records = fetch_all(
        """
        SELECT altitude_m, heart_rate_bpm, cadence_spm, speed_mps,
               elapsed_time_s, distance_m
        FROM activity_records
        WHERE activity_id = ?
        ORDER BY sample_index
        """,
        (activity_id,),
    )
    if not records:
        return {"source": "activity", "activity_id": activity_id, "error": "No records found"}

    enriched = _enrich_records_with_grade(records)
    descent_segs = _extract_segments(enriched, lambda r: r["grade"] <= descent_grade_threshold)

    overall_cadences = [r["cadence_spm"] for r in enriched if r["cadence_spm"] is not None]
    overall_avg_cadence = safe_mean(overall_cadences) or 0.0

    total_descent_m = 0.0
    segments_out: list[dict] = []
    weighted_risk = 0.0
    total_seg_dist = 0.0

    for seg in descent_segs:
        loss = _segment_elevation_loss(seg)
        total_descent_m += loss
        grades = [r["grade"] for r in seg[1:]]
        avg_grade = safe_mean(grades) or 0.0

        # HR drift: compare first-quarter HR to last-quarter HR
        hrs = [r["heart_rate_bpm"] for r in seg if r["heart_rate_bpm"] is not None]
        hr_drift = _hr_drift(hrs)

        # Cadence change vs overall
        seg_cadences = [r["cadence_spm"] for r in seg if r["cadence_spm"] is not None]
        avg_cad = safe_mean(seg_cadences) or 0.0
        cadence_change_pct = (
            ((avg_cad - overall_avg_cadence) / overall_avg_cadence * 100.0)
            if overall_avg_cadence > 0
            else 0.0
        )

        # Speed consistency (CV)
        seg_speeds = [
            r["speed_mps"] for r in seg if r["speed_mps"] is not None and r["speed_mps"] > 0
        ]
        speed_cv = _coefficient_of_variation(seg_speeds)

        # Compute risk score components
        grade_score = min(abs(avg_grade) / 30.0 * 40.0, 40.0)
        hr_drift_score = min(max(hr_drift, 0.0) / 20.0 * 20.0, 20.0)
        cadence_drop_pct = max(-cadence_change_pct, 0.0)  # negative change = drop
        cadence_score_val = min(cadence_drop_pct / 20.0 * 20.0, 20.0)
        speed_score = min(speed_cv * 100.0, 20.0)
        risk_score = grade_score + hr_drift_score + cadence_score_val + speed_score

        start_dist = seg[0]["_cumulative_dist_m"]
        end_dist = seg[-1]["_cumulative_dist_m"]
        seg_dist = max(end_dist - start_dist, 0.0)
        weighted_risk += risk_score * seg_dist
        total_seg_dist += seg_dist

        segments_out.append(
            {
                "start_km": round(start_dist / 1000.0, 2),
                "end_km": round(end_dist / 1000.0, 2),
                "loss_m": round(loss, 1),
                "avg_grade": round(avg_grade, 1),
                "risk_score": round(risk_score, 1),
                "risk_label": _risk_label(risk_score),
                "hr_drift_bpm": round(hr_drift, 1),
                "cadence_change_pct": round(cadence_change_pct, 1),
                "speed_cv": round(speed_cv, 3),
            }
        )

    overall_risk = (weighted_risk / total_seg_dist) if total_seg_dist > 0 else 0.0
    recommendations = _downhill_recommendations(segments_out)

    return {
        "activity_id": activity_id,
        "total_descent_m": round(total_descent_m, 1),
        "descent_segments": segments_out,
        "overall_risk_score": round(overall_risk, 1),
        "overall_risk_label": _risk_label(overall_risk),
        "recommendations": recommendations,
    }


# ---------------------------------------------------------------------------
# 4. trail_hiking_ratio
# ---------------------------------------------------------------------------


def trail_hiking_ratio(
    race_distance_km: float,
    race_elevation_gain_m: float,
    days: int = 180,
    hiking_pace_threshold_min_per_km: float = _DEFAULT_HIKING_THRESHOLD_MIN_PER_KM,
) -> dict:
    """Predict run/hike ratio for a race based on training history."""

    if race_distance_km <= 0:
        return {"source": "input", "error": "race_distance_km must be a positive number"}
    if race_elevation_gain_m < 0:
        return {"source": "input", "error": "race_elevation_gain_m must be non-negative"}
    if hiking_pace_threshold_min_per_km <= 0:
        return {
            "source": "input",
            "error": "hiking_pace_threshold_min_per_km must be a positive number",
        }

    window = max(days, 1)
    since = (date.today() - timedelta(days=window - 1)).isoformat()

    # Speed threshold: convert min/km to m/s
    speed_threshold = (
        1000.0 / (hiking_pace_threshold_min_per_km * 60.0)
        if hiking_pace_threshold_min_per_km > 0
        else 0.0
    )

    activities = fetch_all(
        """
        SELECT id, distance_m, elevation_gain_m
        FROM activities
        WHERE date(started_at) >= ?
          AND sport IN ('trail_run', 'hike', 'ultra_trail')
        ORDER BY started_at DESC
        LIMIT ?
        """,
        (since, _MAX_ACTIVITIES_FOR_RECORDS),
    )

    race_cd = climbing_density(race_distance_km, race_elevation_gain_m)

    if not activities:
        return _hiking_ratio_no_data(race_distance_km, race_elevation_gain_m, race_cd)

    activity_ids = [a["id"] for a in activities]
    placeholders = ",".join("?" * len(activity_ids))
    all_records = fetch_all(
        f"""
        SELECT ar.activity_id, ar.speed_mps, ar.distance_m, ar.elapsed_time_s
        FROM activity_records ar
        WHERE ar.activity_id IN ({placeholders})
        ORDER BY ar.activity_id, ar.sample_index
        """,
        tuple(activity_ids),
    )
    records_by_activity: dict[int, list[dict]] = {}
    for rec in all_records:
        activity_id = rec["activity_id"]
        records_by_activity.setdefault(activity_id, []).append(rec)

    # Per-activity analysis
    activity_stats: list[dict] = []
    all_running_speeds: list[float] = []
    all_hiking_speeds: list[float] = []

    for act in activities:
        records = records_by_activity.get(act["id"], [])
        if not records:
            continue

        run_dist = 0.0
        hike_dist = 0.0
        for delta, spd in _iter_record_deltas(records):
            if spd is not None and spd > 0:
                if spd >= speed_threshold:
                    run_dist += delta
                    all_running_speeds.append(spd)
                else:
                    hike_dist += delta
                    all_hiking_speeds.append(spd)

        total = run_dist + hike_dist
        if total > 0:
            # Prefer activities.distance_m; fall back to record-derived distance
            act_distance_m = act["distance_m"] if act["distance_m"] is not None else total
            act_distance_km = act_distance_m / 1000.0
            act_cd = climbing_density(act_distance_km, act.get("elevation_gain_m") or 0.0)
            activity_stats.append(
                {
                    "running_pct": run_dist / total * 100.0,
                    "hiking_pct": hike_dist / total * 100.0,
                    "climbing_density": act_cd,
                }
            )

    if not activity_stats:
        return _hiking_ratio_no_data(race_distance_km, race_elevation_gain_m, race_cd)

    # Simple linear regression: hiking_pct ~ climbing_density
    cds = [s["climbing_density"] for s in activity_stats]
    hps = [s["hiking_pct"] for s in activity_stats]
    predicted_hiking = _linear_predict(cds, hps, race_cd)
    predicted_hiking = max(0.0, min(100.0, predicted_hiking))

    avg_running_pct = safe_mean([s["running_pct"] for s in activity_stats]) or 0.0
    avg_hiking_pct = safe_mean([s["hiking_pct"] for s in activity_stats]) or 0.0

    # Average paces
    avg_run_speed = safe_mean(all_running_speeds) or 0.0
    avg_hike_speed = safe_mean(all_hiking_speeds) or 0.0

    run_pace = (1000.0 / avg_run_speed / 60.0) if avg_run_speed > 0 else 0.0
    hike_pace = (1000.0 / avg_hike_speed / 60.0) if avg_hike_speed > 0 else 0.0

    # Estimated finish time — fall back to threshold speed when a
    # fraction is non-zero but no training samples exist for that mode.
    run_frac = (100.0 - predicted_hiking) / 100.0
    hike_frac = predicted_hiking / 100.0

    eff_run_speed = avg_run_speed or speed_threshold or avg_hike_speed
    eff_hike_speed = avg_hike_speed or speed_threshold or avg_run_speed

    est_time_s = 0.0
    if eff_run_speed > 0 and run_frac > 0:
        est_time_s += (race_distance_km * 1000.0 * run_frac) / eff_run_speed
    if eff_hike_speed > 0 and hike_frac > 0:
        est_time_s += (race_distance_km * 1000.0 * hike_frac) / eff_hike_speed

    confidence = (
        "low" if len(activity_stats) < 3 else ("medium" if len(activity_stats) < 8 else "high")
    )

    return {
        "race_distance_km": round(race_distance_km, 2),
        "race_elevation_gain_m": round(race_elevation_gain_m, 0),
        "race_climbing_density": round(race_cd, 1),
        "training_data": {
            "activities_analyzed": len(activity_stats),
            "avg_running_pct": round(avg_running_pct, 1),
            "avg_hiking_pct": round(avg_hiking_pct, 1),
        },
        "predicted_running_pct": round(100.0 - predicted_hiking, 1),
        "predicted_hiking_pct": round(predicted_hiking, 1),
        "avg_running_pace_min_per_km": round(run_pace, 2),
        "avg_hiking_pace_min_per_km": round(hike_pace, 2),
        "estimated_finish_time_s": round(est_time_s, 0),
        "estimated_finish_time_formatted": format_duration(est_time_s),
        "confidence": confidence,
    }


# ---------------------------------------------------------------------------
# 5. trail_cutoff_risk
# ---------------------------------------------------------------------------


def trail_cutoff_risk(
    race_distance_km: float,
    race_elevation_gain_m: float,
    cutoff_time_minutes: float,
    intermediate_cutoffs: list[dict] | None = None,
    days: int = 180,
    expected_temperature_c: float | None = None,
) -> dict:
    """Predict whether the athlete risks missing race cutoffs."""

    if race_distance_km <= 0:
        return {"source": "input", "error": "race_distance_km must be a positive number"}
    if race_elevation_gain_m < 0:
        return {"source": "input", "error": "race_elevation_gain_m must be non-negative"}
    if cutoff_time_minutes <= 0:
        return {"source": "input", "error": "cutoff_time_minutes must be a positive number"}

    window = max(days, 1)
    since = (date.today() - timedelta(days=window - 1)).isoformat()

    activities = fetch_all(
        """
        SELECT id, distance_m, elevation_gain_m
        FROM activities
        WHERE date(started_at) >= ?
          AND sport IN ('trail_run', 'hike', 'ultra_trail')
        ORDER BY started_at DESC
        LIMIT ?
        """,
        (since, _MAX_ACTIVITIES_FOR_RECORDS),
    )

    # Gather pace data by terrain type
    flat_paces: list[float] = []  # seconds per km
    climb_paces: list[float] = []
    descent_paces: list[float] = []

    records_by_activity: dict[int, list[dict]] = {}
    if activities:
        activity_ids = [a["id"] for a in activities]
        placeholders = ",".join("?" * len(activity_ids))
        all_records = fetch_all(
            f"""
            SELECT ar.activity_id, ar.altitude_m, ar.speed_mps,
                   ar.distance_m, ar.elapsed_time_s
            FROM activity_records ar
            WHERE ar.activity_id IN ({placeholders})
            ORDER BY ar.activity_id, ar.sample_index
            """,
            tuple(activity_ids),
        )
    else:
        all_records = []
    for rec in all_records:
        records_by_activity.setdefault(rec["activity_id"], []).append(rec)

    contributing_activities = 0
    for act in activities:
        records = records_by_activity.get(act["id"], [])
        if len(records) < 2:
            continue
        enriched = _enrich_records_with_grade(records)
        contributed = False
        for rec in enriched:
            spd = rec.get("speed_mps")
            if spd is None or spd <= 0:
                continue
            contributed = True
            pace_s_per_km = 1000.0 / spd
            grade = rec["grade"]
            if grade >= 5.0:
                climb_paces.append(pace_s_per_km)
            elif grade <= -5.0:
                descent_paces.append(pace_s_per_km)
            else:
                flat_paces.append(pace_s_per_km)
        if contributed:
            contributing_activities += 1

    avg_flat = safe_mean(flat_paces) or 420.0  # 7:00/km default
    avg_climb = safe_mean(climb_paces) or 600.0  # 10:00/km default
    avg_descent = safe_mean(descent_paces) or 330.0  # 5:30/km default

    # Estimate terrain split
    race_cd = climbing_density(race_distance_km, race_elevation_gain_m)
    climb_frac = min(race_cd / 200.0, 0.50)  # cap at 50%
    descent_frac = climb_frac * 0.9
    flat_frac = 1.0 - climb_frac - descent_frac

    # Compute hiking percentage from already-fetched data to avoid redundant
    # DB queries that trail_hiking_ratio() would perform.
    # Uses the same default threshold (~9:00 min/km) as trail_hiking_ratio.
    hiking_pct_raw = _compute_hiking_pct_from_records(records_by_activity, activities)
    hiking_pct_estimated = hiking_pct_raw is None
    hiking_pct = 30.0 if hiking_pct_estimated else hiking_pct_raw

    # Base estimated time
    est_time_s = (
        race_distance_km * flat_frac * avg_flat
        + race_distance_km * climb_frac * avg_climb
        + race_distance_km * descent_frac * avg_descent
    )

    # Heat penalty
    heat_penalty = heat_adjustment_seconds_per_km(expected_temperature_c)
    est_time_s += heat_penalty * race_distance_km

    # Fatigue factor: pace degrades ~2% per hour beyond 4 hours
    est_hours = est_time_s / 3600.0
    fatigue_factor = 1.0
    if est_hours > 4.0:
        fatigue_factor = 1.0 + (est_hours - 4.0) * 0.02
        est_time_s *= fatigue_factor
        est_hours = est_time_s / 3600.0

    est_finish_min = est_time_s / 60.0
    margin_pct = (cutoff_time_minutes - est_finish_min) / cutoff_time_minutes * 100.0
    risk_label_val = _cutoff_risk_label(margin_pct)

    # Intermediate checkpoints
    checkpoints_out = None
    if intermediate_cutoffs:
        checkpoints_out = []
        for cp in intermediate_cutoffs:
            if not isinstance(cp, dict):
                continue  # skip non-dict entries
            try:
                cp_km = float(cp.get("km", 0.0))
                cp_cutoff = float(cp.get("cutoff_minutes", 0.0))
            except (TypeError, ValueError):
                continue  # skip malformed entries
            if cp_cutoff <= 0 or cp_km <= 0:
                continue  # skip entries with non-positive cutoff or distance
            if cp_km > race_distance_km:
                cp_km = race_distance_km
            frac = cp_km / race_distance_km if race_distance_km > 0 else 0.0
            cp_est_min = est_finish_min * frac
            cp_margin = ((cp_cutoff - cp_est_min) / cp_cutoff * 100.0) if cp_cutoff > 0 else 0.0
            checkpoints_out.append(
                {
                    "km": cp_km,
                    "cutoff_minutes": cp_cutoff,
                    "estimated_arrival_min": round(cp_est_min, 1),
                    "margin_pct": round(cp_margin, 1),
                    "status": _cutoff_risk_label(cp_margin),
                }
            )

    recommendations = _cutoff_recommendations(
        margin_pct, est_hours, avg_climb, hiking_pct, hiking_pct_estimated
    )

    return {
        "race_distance_km": round(race_distance_km, 2),
        "race_elevation_gain_m": round(race_elevation_gain_m, 0),
        "cutoff_time_minutes": round(cutoff_time_minutes, 1),
        "estimated_finish_minutes": round(est_finish_min, 1),
        "estimated_finish_formatted": format_duration(est_time_s),
        "margin_pct": round(margin_pct, 1),
        "risk_label": risk_label_val,
        "training_basis": {
            "activities_analyzed": contributing_activities,
            "avg_flat_pace": format_pace(avg_flat),
            "avg_climb_pace": format_pace(avg_climb),
            "avg_descent_pace": format_pace(avg_descent),
            "training_hiking_pct": round(hiking_pct, 1),
            "hiking_pct_is_estimated": hiking_pct_estimated,
        },
        "adjustments": {
            "heat_penalty_s_per_km": round(heat_penalty, 1),
            "fatigue_factor": round(fatigue_factor, 3),
        },
        "intermediate_checkpoints": checkpoints_out,
        "recommendations": recommendations,
    }


# ---------------------------------------------------------------------------
# Private helpers – existing
# ---------------------------------------------------------------------------


def _difficulty_label(score: float) -> str:
    if score < 35:
        return "moderate"
    if score < 55:
        return "challenging"
    if score < 75:
        return "hard"
    return "extreme"


def _trail_recommendation(total_distance_km: float, total_climb_m: float) -> str:
    if total_distance_km == 0:
        return "No trail-specific sessions found in the selected window."
    if total_climb_m < 1000:
        return "Climbing volume is still low. Add vertical-specific work if racing trails soon."
    return (
        "Recent trail volume includes meaningful climbing."
        " Focus next on terrain specificity and downhill resilience."
    )


# ---------------------------------------------------------------------------
# Private helpers – new
# ---------------------------------------------------------------------------


def _get_athlete_profile_for_activity(activity_id: int) -> dict | None:
    return fetch_one(
        """
        SELECT ap.max_hr_bpm, ap.threshold_hr_bpm
        FROM athlete_profiles ap
        JOIN activities a ON a.athlete_id = ap.athlete_id
        WHERE a.id = ?
        """,
        (activity_id,),
    )


def _enrich_records_with_grade(records: list[dict]) -> list[dict]:
    """Add ``grade`` (percent) and ``_cumulative_dist_m`` to each record.

    ``_cumulative_dist_m`` is a derived cumulative distance that preserves
    the absolute ``distance_m`` baseline when the first record has a
    non-zero value (e.g., records start mid-activity).  Distance deltas
    are computed by :func:`_iter_record_deltas` which correctly resets
    its ``prev_dist`` tracker when ``distance_m`` disappears and snaps
    the baseline when it reappears, preventing cumulative drift in
    activities with intermittent ``distance_m`` data.

    When ``distance_m`` is NULL, horizontal distance for the grade
    computation is also estimated from ``speed_mps * Δelapsed_time_s``
    so that activities with missing cumulative distance still produce
    meaningful grade values.
    """
    enriched: list[dict] = []
    # Preserve the absolute baseline when the first record has distance_m.
    first_dist = records[0].get("distance_m") if records else None
    cumulative_dist_m = first_dist if first_dist is not None else 0.0
    deltas = _iter_record_deltas(records)
    for i, (rec, (dist, _spd)) in enumerate(zip(records, deltas)):
        r = dict(rec)
        r["grade"] = 0.0
        if i > 0 and dist > 0:
            alt = rec.get("altitude_m")
            prev_alt = records[i - 1].get("altitude_m")
            if alt is not None and prev_alt is not None:
                r["grade"] = ((alt - prev_alt) / dist) * 100.0
        cumulative_dist_m += dist
        r["_cumulative_dist_m"] = cumulative_dist_m
        enriched.append(r)
    return enriched


def _extract_segments(
    records: list[dict],
    predicate,
) -> list[list[dict]]:
    """Group consecutive records matching *predicate* into segments.

    When a segment starts at record *i*, record *i-1* is prepended so that the
    first interval (i-1 → i) is included in segment metrics (elevation change,
    time, distance).
    """
    segments: list[list[dict]] = []
    current: list[dict] = []
    for i, rec in enumerate(records):
        if predicate(rec):
            if not current and i > 0:
                current.append(records[i - 1])
            current.append(rec)
        else:
            if current:
                segments.append(current)
                current = []
    if current:
        segments.append(current)
    return segments


def _segment_elevation_gain(seg: list[dict]) -> float:
    gain = 0.0
    for i in range(1, len(seg)):
        a = seg[i - 1].get("altitude_m")
        b = seg[i].get("altitude_m")
        if a is not None and b is not None and b > a:
            gain += b - a
    return gain


def _segment_elevation_loss(seg: list[dict]) -> float:
    loss = 0.0
    for i in range(1, len(seg)):
        a = seg[i - 1].get("altitude_m")
        b = seg[i].get("altitude_m")
        if a is not None and b is not None and a > b:
            loss += a - b
    return loss


def _segment_time(seg: list[dict]) -> float:
    if len(seg) < 2:
        return 0.0
    t0 = seg[0].get("elapsed_time_s")
    t1 = seg[-1].get("elapsed_time_s")
    if t0 is not None and t1 is not None:
        return max(float(t1 - t0), 0.0)
    return 0.0


def _hr_zone_dist(
    hrs: list[float],
    max_hr: float | None,
    threshold_hr: float | None,
) -> dict[str, float]:
    if not hrs:
        return {f"z{i}": 0.0 for i in range(1, 6)}
    counts: dict[int, int] = {i: 0 for i in range(1, 6)}
    for hr in hrs:
        zone = hr_zone_for_bpm(hr, max_hr_bpm=max_hr, threshold_hr_bpm=threshold_hr)
        if zone in counts:
            counts[zone] += 1
    total = len(hrs)
    return {f"z{z}": round(c / total * 100.0, 1) for z, c in counts.items()}


def _hr_drift(hrs: list[float]) -> float:
    """Return the increase in HR from first quarter to last quarter."""
    if len(hrs) < 4:
        return 0.0
    q = len(hrs) // 4
    first_q = safe_mean(hrs[:q]) or 0.0
    last_q = safe_mean(hrs[-q:]) or 0.0
    return last_q - first_q


def _coefficient_of_variation(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    if mean <= 0:
        return 0.0
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(variance) / mean


def _risk_label(score: float) -> str:
    if score < 25:
        return "low"
    if score < 50:
        return "moderate"
    if score < 75:
        return "high"
    return "critical"


def _downhill_recommendations(segments: list[dict]) -> list[str]:
    recs: list[str] = []
    if not segments:
        return ["No significant descent segments detected."]

    avg_hr_drift = safe_mean([s["hr_drift_bpm"] for s in segments]) or 0.0
    avg_cad_change = safe_mean([s["cadence_change_pct"] for s in segments]) or 0.0
    avg_speed_cv = safe_mean([s["speed_cv"] for s in segments]) or 0.0

    if avg_hr_drift > 10:
        recs.append(
            "HR drift is high on descents — train eccentric downhill"
            " running to improve muscular resilience."
        )
    if avg_cad_change < -10:
        recs.append(
            "Cadence drops significantly on descents —"
            " practice quick turnover on technical terrain."
        )
    if avg_speed_cv > 0.25:
        recs.append("Speed is erratic on descents — work on consistent pacing and foot placement.")
    if not recs:
        recs.append("Downhill metrics look reasonable. Maintain current technique training.")
    return recs


def _hiking_ratio_no_data(
    race_distance_km: float,
    race_elevation_gain_m: float,
    race_cd: float,
) -> dict:
    return {
        "race_distance_km": round(race_distance_km, 2),
        "race_elevation_gain_m": round(race_elevation_gain_m, 0),
        "race_climbing_density": round(race_cd, 1),
        "training_data": {
            "activities_analyzed": 0,
            "avg_running_pct": 0.0,
            "avg_hiking_pct": 0.0,
        },
        "predicted_running_pct": 50.0,
        "predicted_hiking_pct": 50.0,
        "avg_running_pace_min_per_km": 0.0,
        "avg_hiking_pace_min_per_km": 0.0,
        "estimated_finish_time_s": 0.0,
        "estimated_finish_time_formatted": "00:00:00",
        "confidence": "low",
    }


def _linear_predict(xs: list[float], ys: list[float], x_pred: float) -> float:
    """Simple linear regression prediction."""
    n = len(xs)
    if n == 0:
        return 0.0
    if n == 1:
        return ys[0]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)
    if den == 0:
        return mean_y
    slope = num / den
    intercept = mean_y - slope * mean_x
    return slope * x_pred + intercept


def _cutoff_risk_label(margin_pct: float) -> str:
    if margin_pct > 20:
        return "safe"
    if margin_pct > 10:
        return "comfortable"
    if margin_pct > 5:
        return "tight"
    if margin_pct > 0:
        return "at_risk"
    return "likely_dnf"


def _cutoff_recommendations(
    margin_pct: float,
    estimated_hours: float,
    avg_climb_pace: float,
    hiking_pct: float,
    hiking_pct_is_estimated: bool = False,
) -> list[str]:
    recs: list[str] = []
    if margin_pct < 0:
        recs.append(
            "Estimated time exceeds the cutoff. Focus on overall pace improvement, "
            "especially climbing efficiency."
        )
    if margin_pct < 5:
        recs.append(
            "Margin is very tight. Consider adding long trail runs above 4-5 hours "
            "to build race-day endurance."
        )
    if avg_climb_pace > 540:  # slower than 9:00/km uphill
        recs.append(
            "Climbing pace is slow. Add dedicated uphill interval sessions or "
            "hike-with-poles training."
        )
    if hiking_pct > 50:
        if hiking_pct_is_estimated:
            recs.append(
                "Estimated hiking share is high (no speed data available). "
                "Work on running more uphills to shift the run/hike ratio."
            )
        else:
            recs.append(
                "Training data shows a high hiking share. Work on running "
                "more uphills to shift the run/hike ratio."
            )
    if estimated_hours > 8 and margin_pct < 10:
        recs.append(
            "For a race of this duration, nutrition and sleep management are critical. "
            "Practice race-day fueling in long training runs."
        )
    if not recs:
        recs.append("Current training supports a comfortable finish within the cutoff.")
    return recs


def _compute_hiking_pct_from_records(
    records_by_activity: dict[int, list[dict]],
    activities: list[dict],
    min_running_speed_mps: float = _DEFAULT_MIN_RUNNING_SPEED_MPS,
) -> float | None:
    """Estimate hiking percentage from already-fetched records.

    Records with speed at or above *min_running_speed_mps* (default
    ~1.852 m/s ≈ 9:00 min/km) are counted as running; slower records
    are counted as hiking.  When ``distance_m`` is NULL but
    ``elapsed_time_s`` and ``speed_mps`` are available, distance is
    estimated as ``speed_mps * Δt``.  Returns ``None`` when no usable
    speed data is available, letting callers decide on a fallback.
    """
    total_run = 0.0
    total_hike = 0.0
    for act in activities:
        records = records_by_activity.get(act["id"], [])
        for delta, spd in _iter_record_deltas(records):
            if spd is not None and spd > 0:
                if spd >= min_running_speed_mps:
                    total_run += delta
                else:
                    total_hike += delta
    total = total_run + total_hike
    if total <= 0:
        return None
    return total_hike / total * 100.0
