from __future__ import annotations

from datetime import date, timedelta

from athletehub.db.db import fetch_all, fetch_one


def summarize_training_load(days: int = 7) -> dict:
    window = max(days, 1)
    since = (date.today() - timedelta(days=window - 1)).isoformat()

    summary = fetch_one(
        """
        SELECT
            COUNT(*) AS activity_count,
            ROUND(COALESCE(SUM(distance_m), 0) / 1000.0, 2) AS total_distance_km,
            ROUND(COALESCE(SUM(moving_time_s), 0) / 3600.0, 2) AS moving_time_hours,
            ROUND(COALESCE(SUM(elevation_gain_m), 0), 0) AS total_elevation_gain_m,
            ROUND(COALESCE(SUM(training_load), 0), 1) AS total_training_load,
            ROUND(COALESCE(AVG(training_load), 0), 1) AS avg_training_load
        FROM activities
        WHERE date(started_at) >= ?
        """,
        (since,),
    ) or {}

    longest_session = fetch_one(
        """
        SELECT ROUND(COALESCE(MAX(distance_m), 0) / 1000.0, 2) AS longest_session_km
        FROM activities
        WHERE date(started_at) >= ?
        """,
        (since,),
    ) or {"longest_session_km": 0.0}

    sports_breakdown = fetch_all(
        """
        SELECT
            sport,
            COUNT(*) AS activity_count,
            ROUND(COALESCE(SUM(distance_m), 0) / 1000.0, 2) AS distance_km
        FROM activities
        WHERE date(started_at) >= ?
        GROUP BY sport
        ORDER BY distance_km DESC, activity_count DESC
        """,
        (since,),
    )

    load_balance = fetch_one(
        """
        SELECT
            day,
            ctl,
            atl,
            tsb,
            fatigue_flag,
            day_training_load,
            base_fitness,
            load_impact,
            intensity_trend_pct,
            training_status
        FROM load_daily
        ORDER BY day DESC
        LIMIT 1
        """
    )

    total_distance = float(summary.get("total_distance_km") or 0.0)
    activity_count = int(summary.get("activity_count") or 0)

    return {
        "period_days": window,
        **summary,
        **longest_session,
        "average_daily_distance_km": round(total_distance / window, 2),
        "sports_breakdown": sports_breakdown,
        "load_balance": load_balance,
        "recommendation": _recommend_training_focus(activity_count, total_distance, load_balance),
    }


def _recommend_training_focus(
    activity_count: int,
    total_distance_km: float,
    load_balance: dict | None,
) -> str:
    if activity_count == 0:
        return "No activities in the selected window. Import data before drawing training conclusions."

    if load_balance and load_balance.get("fatigue_flag"):
        return "Recent load data shows a fatigue flag. Reduce intensity and prioritize recovery."

    tsb = (load_balance or {}).get("tsb")
    if isinstance(tsb, (int, float)) and tsb < -15:
        return "Training stress balance is deeply negative. Keep the next block aerobic and conservative."

    if total_distance_km < 30:
        return "Volume is still modest. Build consistency before adding more intensity."

    return "Load looks stable enough for a normal endurance progression, assuming recovery metrics stay positive."
