from __future__ import annotations

import math
from datetime import date, timedelta

from athletehub.db.db import fetch_all, fetch_one
from athletehub.utils.elevation import climbing_density, compute_vam
from athletehub.utils.gpx import (
    detect_technical_sections as _gpx_detect,
    detect_technical_sections_from_records as _records_detect,
)
from athletehub.utils.metrics import format_duration, format_pace, hr_zone_for_bpm, safe_mean
from athletehub.utils.weather import heat_adjustment_seconds_per_km


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

    summary = fetch_one(
        """
        SELECT
            COUNT(*) AS trail_sessions,
            ROUND(COALESCE(SUM(distance_m), 0) / 1000.0, 2) AS total_distance_km,
            ROUND(COALESCE(SUM(elevation_gain_m), 0), 0) AS total_climb_m,
            ROUND(COALESCE(AVG(elevation_gain_m / NULLIF(distance_m / 1000.0, 0)), 0), 1) AS avg_climb_per_km_m
        FROM activities
        WHERE date(started_at) >= ?
          AND sport IN ('trail_run', 'hike', 'ultra_trail')
        """,
        (since,),
    ) or {}

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
    """Identify technical sections (steep slopes, rocky terrain, pace anomalies)
    from a GPX file *or* from stored activity records."""

    if gpx_path is not None:
        result = _gpx_detect(
            gpx_path,
            grade_threshold=grade_threshold,
            min_section_length_m=min_section_length_m,
        )
        result["source"] = "gpx"
        return result

    if activity_id is not None:
        records = fetch_all(
            """
            SELECT latitude, longitude, altitude_m, speed_mps, distance_m
            FROM activity_records
            WHERE activity_id = ?
            ORDER BY sample_index
            """,
            (activity_id,),
        )
        if not records:
            return {"source": "activity", "error": "No records found for activity_id"}
        result = _records_detect(
            records,
            grade_threshold=grade_threshold,
            min_section_length_m=min_section_length_m,
        )
        result["source"] = "activity"
        return result

    return {"error": "Provide either gpx_path or activity_id"}


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
        return {"activity_id": activity_id, "error": "No records found"}

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
        avg_grade = safe_mean([r["grade"] for r in seg]) or 0.0

        total_climb_m += gain
        total_climb_time += time_s

        segments_out.append(
            {
                "start_km": round(seg[0]["distance_m"] / 1000.0, 2),
                "end_km": round(seg[-1]["distance_m"] / 1000.0, 2),
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
    climb_hrs = [r["heart_rate_bpm"] for s in climb_segs for r in s if r["heart_rate_bpm"] is not None]
    climb_cadences = [r["cadence_spm"] for s in climb_segs for r in s if r["cadence_spm"] is not None]
    flat_cadences = [r["cadence_spm"] for s in flat_segs for r in s if r["cadence_spm"] is not None]

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
        return {"activity_id": activity_id, "error": "No records found"}

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
        grades = [r["grade"] for r in seg]
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
        seg_speeds = [r["speed_mps"] for r in seg if r["speed_mps"] is not None and r["speed_mps"] > 0]
        speed_cv = _coefficient_of_variation(seg_speeds)

        # Compute risk score components
        grade_score = min(abs(avg_grade) / 30.0 * 40.0, 40.0)
        hr_drift_score = min(max(hr_drift, 0.0) / 20.0 * 20.0, 20.0)
        cadence_drop_pct = max(-cadence_change_pct, 0.0)  # negative change = drop
        cadence_score_val = min(cadence_drop_pct / 20.0 * 20.0, 20.0)
        speed_score = min(speed_cv * 100.0, 20.0)
        risk_score = grade_score + hr_drift_score + cadence_score_val + speed_score

        seg_dist = abs(seg[-1]["distance_m"] - seg[0]["distance_m"]) if len(seg) >= 2 else 0.0
        weighted_risk += risk_score * seg_dist
        total_seg_dist += seg_dist

        segments_out.append(
            {
                "start_km": round(seg[0]["distance_m"] / 1000.0, 2),
                "end_km": round(seg[-1]["distance_m"] / 1000.0, 2),
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
    hiking_pace_threshold_min_per_km: float = 9.0,
) -> dict:
    """Predict run/hike ratio for a race based on training history."""

    window = max(days, 1)
    since = (date.today() - timedelta(days=window - 1)).isoformat()

    # Speed threshold: convert min/km to m/s
    speed_threshold = 1000.0 / (hiking_pace_threshold_min_per_km * 60.0) if hiking_pace_threshold_min_per_km > 0 else 0.0

    activities = fetch_all(
        """
        SELECT id, distance_m, elevation_gain_m
        FROM activities
        WHERE date(started_at) >= ?
          AND sport IN ('trail_run', 'hike', 'ultra_trail')
        ORDER BY started_at DESC
        """,
        (since,),
    )

    race_cd = climbing_density(race_distance_km, race_elevation_gain_m)

    if not activities:
        return _hiking_ratio_no_data(race_distance_km, race_elevation_gain_m, race_cd)

    # Per-activity analysis
    activity_stats: list[dict] = []
    all_running_speeds: list[float] = []
    all_hiking_speeds: list[float] = []

    for act in activities:
        records = fetch_all(
            "SELECT speed_mps, distance_m FROM activity_records WHERE activity_id = ? ORDER BY sample_index",
            (act["id"],),
        )
        if not records:
            continue

        run_dist = 0.0
        hike_dist = 0.0
        prev_dist = 0.0
        for rec in records:
            spd = rec.get("speed_mps")
            d = rec.get("distance_m") or 0.0
            delta = max(d - prev_dist, 0.0)
            prev_dist = d
            if spd is not None and spd > 0:
                if spd >= speed_threshold:
                    run_dist += delta
                    all_running_speeds.append(spd)
                else:
                    hike_dist += delta
                    all_hiking_speeds.append(spd)

        total = run_dist + hike_dist
        if total > 0:
            act_distance_km = (act["distance_m"] or 0.0) / 1000.0
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

    # Estimated finish time
    run_frac = (100.0 - predicted_hiking) / 100.0
    hike_frac = predicted_hiking / 100.0
    est_time_s = 0.0
    if avg_run_speed > 0:
        est_time_s += (race_distance_km * 1000.0 * run_frac) / avg_run_speed
    if avg_hike_speed > 0:
        est_time_s += (race_distance_km * 1000.0 * hike_frac) / avg_hike_speed

    confidence = "low" if len(activity_stats) < 3 else ("medium" if len(activity_stats) < 8 else "high")

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

    window = max(days, 1)
    since = (date.today() - timedelta(days=window - 1)).isoformat()

    activities = fetch_all(
        """
        SELECT id, distance_m, elevation_gain_m, moving_time_s
        FROM activities
        WHERE date(started_at) >= ?
          AND sport IN ('trail_run', 'hike', 'ultra_trail')
        ORDER BY started_at DESC
        """,
        (since,),
    )

    # Gather pace data by terrain type
    flat_paces: list[float] = []   # seconds per km
    climb_paces: list[float] = []
    descent_paces: list[float] = []

    for act in activities:
        records = fetch_all(
            """
            SELECT altitude_m, speed_mps, distance_m, elapsed_time_s
            FROM activity_records
            WHERE activity_id = ?
            ORDER BY sample_index
            """,
            (act["id"],),
        )
        if len(records) < 2:
            continue
        enriched = _enrich_records_with_grade(records)
        for rec in enriched:
            spd = rec.get("speed_mps")
            if spd is None or spd <= 0:
                continue
            pace_s_per_km = 1000.0 / spd
            grade = rec["grade"]
            if grade >= 5.0:
                climb_paces.append(pace_s_per_km)
            elif grade <= -5.0:
                descent_paces.append(pace_s_per_km)
            else:
                flat_paces.append(pace_s_per_km)

    avg_flat = safe_mean(flat_paces) or 420.0   # 7:00/km default
    avg_climb = safe_mean(climb_paces) or 600.0  # 10:00/km default
    avg_descent = safe_mean(descent_paces) or 330.0  # 5:30/km default

    # Estimate terrain split
    race_cd = climbing_density(race_distance_km, race_elevation_gain_m)
    climb_frac = min(race_cd / 200.0, 0.50)  # cap at 50%
    descent_frac = climb_frac * 0.9
    flat_frac = 1.0 - climb_frac - descent_frac

    # Use hiking ratio prediction
    hike_result = trail_hiking_ratio(
        race_distance_km=race_distance_km,
        race_elevation_gain_m=race_elevation_gain_m,
        days=days,
    )
    hiking_pct = hike_result.get("predicted_hiking_pct", 30.0)

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

    est_finish_min = est_time_s / 60.0
    margin_pct = ((cutoff_time_minutes - est_finish_min) / cutoff_time_minutes * 100.0) if cutoff_time_minutes > 0 else 0.0
    risk_label_val = _cutoff_risk_label(margin_pct)

    # Intermediate checkpoints
    checkpoints_out = None
    if intermediate_cutoffs:
        checkpoints_out = []
        for cp in intermediate_cutoffs:
            cp_km = cp.get("km", 0.0)
            cp_cutoff = cp.get("cutoff_minutes", 0.0)
            frac = cp_km / race_distance_km if race_distance_km > 0 else 0.0
            cp_est_min = est_finish_min * frac
            cp_margin = ((cp_cutoff - cp_est_min) / cp_cutoff * 100.0) if cp_cutoff > 0 else 0.0
            checkpoints_out.append(
                {
                    "km": cp_km,
                    "cutoff_min": cp_cutoff,
                    "estimated_arrival_min": round(cp_est_min, 1),
                    "margin_pct": round(cp_margin, 1),
                    "status": _cutoff_risk_label(cp_margin),
                }
            )

    recommendations = _cutoff_recommendations(margin_pct, est_hours, avg_climb, hiking_pct)

    return {
        "race_distance_km": round(race_distance_km, 2),
        "race_elevation_gain_m": round(race_elevation_gain_m, 0),
        "cutoff_time_minutes": round(cutoff_time_minutes, 1),
        "estimated_finish_minutes": round(est_finish_min, 1),
        "estimated_finish_formatted": format_duration(est_time_s),
        "margin_pct": round(margin_pct, 1),
        "risk_label": risk_label_val,
        "training_basis": {
            "activities_analyzed": len(activities),
            "avg_flat_pace": format_pace(avg_flat),
            "avg_climb_pace": format_pace(avg_climb),
            "avg_descent_pace": format_pace(avg_descent),
            "predicted_hiking_pct": round(hiking_pct, 1),
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
    return "Recent trail volume includes meaningful climbing. Focus next on terrain specificity and downhill resilience."


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
    """Add a ``grade`` key (percent) to each record based on consecutive altitude/distance."""
    enriched: list[dict] = []
    for i, rec in enumerate(records):
        r = dict(rec)
        r["grade"] = 0.0
        if i > 0:
            prev = records[i - 1]
            alt = rec.get("altitude_m")
            prev_alt = prev.get("altitude_m")
            dist = (rec.get("distance_m") or 0.0) - (prev.get("distance_m") or 0.0)
            if alt is not None and prev_alt is not None and dist > 0:
                r["grade"] = ((alt - prev_alt) / dist) * 100.0
        enriched.append(r)
    return enriched


def _extract_segments(
    records: list[dict],
    predicate,
) -> list[list[dict]]:
    """Group consecutive records matching *predicate* into segments."""
    segments: list[list[dict]] = []
    current: list[dict] = []
    for rec in records:
        if predicate(rec):
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
            "HR drift is high on descents — train eccentric downhill running to improve muscular resilience."
        )
    if avg_cad_change < -10:
        recs.append(
            "Cadence drops significantly on descents — practice quick turnover on technical terrain."
        )
    if avg_speed_cv > 0.25:
        recs.append(
            "Speed is erratic on descents — work on consistent pacing and foot placement."
        )
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
        "training_data": {"activities_analyzed": 0, "avg_running_pct": 0.0, "avg_hiking_pct": 0.0},
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
        recs.append(
            "Predicted hiking percentage is high. Work on running more uphills "
            "to shift the run/hike ratio."
        )
    if estimated_hours > 8 and margin_pct < 10:
        recs.append(
            "For a race of this duration, nutrition and sleep management are critical. "
            "Practice race-day fueling in long training runs."
        )
    if not recs:
        recs.append(
            "Current training supports a comfortable finish within the cutoff."
        )
    return recs
