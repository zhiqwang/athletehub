from __future__ import annotations

from datetime import date, timedelta

from athletehub.db.db import fetch_one
from athletehub.utils.elevation import climbing_density


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
