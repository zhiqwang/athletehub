from __future__ import annotations

from datetime import date, timedelta

from athletehub.db.db import fetch_all, fetch_one


def recent_activities(limit: int = 10, days: int = 30) -> list[dict]:
    window = max(days, 1)
    capped_limit = max(1, min(limit, 100))
    since = (date.today() - timedelta(days=window - 1)).isoformat()

    return fetch_all(
        """
        SELECT
            id,
            sport,
            COALESCE(title, sport) AS title,
            started_at,
            ROUND(COALESCE(distance_m, 0) / 1000.0, 2) AS distance_km,
            ROUND(COALESCE(moving_time_s, 0) / 60.0, 1) AS moving_minutes,
            ROUND(COALESCE(elevation_gain_m, 0), 0) AS elevation_gain_m,
            ROUND(COALESCE(training_load, 0), 1) AS training_load
        FROM activities
        WHERE date(started_at) >= ?
        ORDER BY started_at DESC
        LIMIT ?
        """,
        (since, capped_limit),
    )


def activity_details(activity_id: int) -> dict:
    activity = fetch_one(
        """
        SELECT
            id,
            athlete_id,
            source_id,
            source_activity_id,
            sport,
            sport_subtype,
            workout_type,
            title,
            started_at,
            ended_at,
            timezone,
            ROUND(COALESCE(distance_m, 0) / 1000.0, 2) AS distance_km,
            moving_time_s,
            elapsed_time_s,
            elevation_gain_m,
            elevation_loss_m,
            average_speed_mps,
            average_hr_bpm,
            max_hr_bpm,
            average_power_w,
            normalized_power_w,
            average_cadence_spm,
            average_temperature_c,
            calories_kcal,
            training_load,
            intensity_factor,
            lap_count,
            device_name,
            load_source,
            perceived_effort,
            notes
        FROM activities
        WHERE id = ?
        """,
        (activity_id,),
    )

    if not activity:
        return {"activity_id": activity_id, "found": False}

    splits = fetch_all(
        """
        SELECT
            split_index,
            distance_m,
            elapsed_time_s,
            moving_time_s,
            avg_pace_s_per_km,
            avg_hr_bpm,
            elevation_gain_m
        FROM activity_splits
        WHERE activity_id = ?
        ORDER BY split_index
        """,
        (activity_id,),
    )

    files = fetch_all(
        """
        SELECT file_type, path, checksum, created_at
        FROM activity_files
        WHERE activity_id = ?
        ORDER BY created_at ASC
        """,
        (activity_id,),
    )

    activity["found"] = True
    activity["splits"] = splits
    activity["files"] = files
    return activity
