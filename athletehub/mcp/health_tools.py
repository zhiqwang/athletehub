from __future__ import annotations

from datetime import date, timedelta

from athletehub.db.db import fetch_one


def recovery_status(days: int = 7) -> dict:
    window = max(days, 1)
    since = (date.today() - timedelta(days=window - 1)).isoformat()

    latest_health = fetch_one(
        """
        SELECT
            day,
            resting_hr_bpm,
            hrv_ms,
            ROUND(COALESCE(sleep_seconds, 0) / 3600.0, 2) AS sleep_hours,
            stress_score,
            body_battery,
            training_readiness,
            vo2max
        FROM health_daily
        ORDER BY day DESC
        LIMIT 1
        """
    )

    recent_baseline = fetch_one(
        """
        SELECT
            COUNT(*) AS recorded_days,
            ROUND(COALESCE(AVG(resting_hr_bpm), 0), 1) AS avg_resting_hr_bpm,
            ROUND(COALESCE(AVG(hrv_ms), 0), 1) AS avg_hrv_ms,
            ROUND(COALESCE(AVG(sleep_seconds), 0) / 3600.0, 2) AS avg_sleep_hours,
            ROUND(COALESCE(AVG(stress_score), 0), 1) AS avg_stress_score
        FROM health_daily
        WHERE day >= ?
        """,
        (since,),
    ) or {}

    load_balance = fetch_one(
        """
        SELECT
            day,
            ctl,
            atl,
            tsb,
            fatigue_flag,
            base_fitness,
            load_impact,
            intensity_trend_pct,
            training_status
        FROM load_daily
        ORDER BY day DESC
        LIMIT 1
        """
    )

    return {
        "period_days": window,
        "latest_health": latest_health,
        "recent_baseline": recent_baseline,
        "load_balance": load_balance,
        "recovery_label": _label_recovery(latest_health, load_balance),
    }


def _label_recovery(latest_health: dict | None, load_balance: dict | None) -> str:
    if not latest_health:
        return "No health metrics available yet."

    readiness = latest_health.get("training_readiness")
    sleep_hours = latest_health.get("sleep_hours")
    hrv_ms = latest_health.get("hrv_ms")

    if load_balance and load_balance.get("fatigue_flag"):
        return "Fatigue flag present. Treat this as a recovery day."

    if isinstance(readiness, (int, float)) and readiness >= 75:
        return "Recovery markers are good enough for a quality session."

    if isinstance(sleep_hours, (int, float)) and sleep_hours < 6:
        return "Sleep is low. Keep training easy or shorten the session."

    if isinstance(hrv_ms, (int, float)) and hrv_ms < 40:
        return "HRV is suppressed. Monitor fatigue and avoid stacking hard days."

    return "Recovery looks neutral. Keep planned training but watch subjective fatigue."
