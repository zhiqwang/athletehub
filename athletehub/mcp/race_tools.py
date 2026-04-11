from __future__ import annotations

from athletehub.db.db import fetch_all
from athletehub.utils.metrics import format_pace
from athletehub.utils.weather import heat_adjustment_seconds_per_km


def upcoming_races(limit: int = 5) -> list[dict]:
    capped_limit = max(1, min(limit, 20))
    return fetch_all(
        """
        SELECT
            id,
            name,
            race_type,
            scheduled_for,
            distance_km,
            elevation_gain_m,
            target_finish_seconds,
            goal_notes
        FROM race_events
        WHERE scheduled_for >= date('now')
        ORDER BY scheduled_for ASC
        LIMIT ?
        """,
        (capped_limit,),
    )


def race_strategy(
    distance_km: float = 42.195,
    target_finish_minutes: float = 240.0,
    elevation_gain_m: float = 0.0,
    expected_temperature_c: float | None = None,
) -> dict:
    goal_seconds = max(target_finish_minutes, 1.0) * 60.0
    base_pace_s_per_km = goal_seconds / max(distance_km, 0.1)
    climb_penalty = (elevation_gain_m / max(distance_km, 1.0)) * 0.08
    heat_penalty = heat_adjustment_seconds_per_km(expected_temperature_c)

    adjusted_base = base_pace_s_per_km + climb_penalty + heat_penalty
    conservative_start = adjusted_base + 6.0
    settled_pace = adjusted_base + 2.0
    finish_window = max(adjusted_base - 2.0, 0.0)

    carbs_per_hour = min(90, max(40, round(distance_km * 1.4)))
    fluid_ml_per_hour = 500 if expected_temperature_c is None else int(450 + max(expected_temperature_c - 12, 0) * 25)

    return {
        "distance_km": round(distance_km, 2),
        "target_finish_minutes": round(target_finish_minutes, 1),
        "base_pace": format_pace(base_pace_s_per_km),
        "conservative_start_pace": format_pace(conservative_start),
        "settled_pace": format_pace(settled_pace),
        "finish_window_pace": format_pace(finish_window),
        "elevation_adjustment_s_per_km": round(climb_penalty, 1),
        "heat_adjustment_s_per_km": round(heat_penalty, 1),
        "fueling": {
            "carbs_per_hour_g": carbs_per_hour,
            "fluid_per_hour_ml": fluid_ml_per_hour,
            "note": "Start fueling early and avoid waiting for perceived depletion.",
        },
    }
