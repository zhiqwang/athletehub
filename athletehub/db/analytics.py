from __future__ import annotations

import argparse
import math
from collections import defaultdict
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from pathlib import Path

from athletehub.db.db import fetch_all, get_connection


def recompute_load_metrics(db_path: str | Path | None = None) -> int:
    activities = fetch_all(
        """
        SELECT
            athlete_id,
            date(started_at) AS day,
            ROUND(COALESCE(SUM(training_load), 0), 1) AS day_training_load
        FROM activities
        GROUP BY athlete_id, date(started_at)
        ORDER BY athlete_id, day
        """,
        db_path=db_path,
    )

    grouped: dict[int, dict[date, float]] = defaultdict(dict)
    for row in activities:
        athlete_id = int(row["athlete_id"])
        day = date.fromisoformat(str(row["day"]))
        grouped[athlete_id][day] = float(row["day_training_load"] or 0.0)

    written_rows = 0
    with get_connection(db_path) as connection:
        for athlete_id, day_loads in grouped.items():
            if not day_loads:
                continue

            min_day = min(day_loads)
            max_day = max(max(day_loads), date.today())
            daily_series = _build_daily_series(min_day, max_day, day_loads)

            ctl = 0.0
            atl = 0.0
            ctl_alpha = 2 / (42 + 1)
            atl_alpha = 2 / (7 + 1)

            for index, (current_day, day_training_load) in enumerate(daily_series):
                trailing_loads = [value for _, value in daily_series[max(0, index - 6) : index + 1]]
                chronic_loads = [value for _, value in daily_series[max(0, index - 41) : index + 1]]

                seven_day_avg = _mean(trailing_loads)
                forty_two_day_avg = _mean(chronic_loads)
                acute_load = round(sum(trailing_loads), 1)
                chronic_load = round(sum(chronic_loads), 1)

                ctl = ctl + ctl_alpha * (day_training_load - ctl)
                atl = atl + atl_alpha * (day_training_load - atl)
                tsb = ctl - atl

                base_fitness = round(forty_two_day_avg, 1)
                load_impact = round(seven_day_avg, 1)
                intensity_trend_pct = round((load_impact / base_fitness) * 100, 1) if base_fitness > 0 else None
                monotony = _monotony(trailing_loads)
                strain = round(acute_load * monotony, 1) if monotony is not None else None
                training_status = _training_status(intensity_trend_pct)
                fatigue_flag = int(
                    (intensity_trend_pct is not None and intensity_trend_pct >= 150)
                    or tsb <= -15
                )

                connection.execute(
                    """
                    INSERT INTO load_daily (
                        athlete_id,
                        day,
                        ctl,
                        atl,
                        tsb,
                        acute_load,
                        chronic_load,
                        fatigue_flag,
                        day_training_load,
                        seven_day_avg_load,
                        forty_two_day_avg_load,
                        base_fitness,
                        load_impact,
                        intensity_trend_pct,
                        monotony,
                        strain,
                        training_status
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(athlete_id, day) DO UPDATE SET
                        ctl = excluded.ctl,
                        atl = excluded.atl,
                        tsb = excluded.tsb,
                        acute_load = excluded.acute_load,
                        chronic_load = excluded.chronic_load,
                        fatigue_flag = excluded.fatigue_flag,
                        day_training_load = excluded.day_training_load,
                        seven_day_avg_load = excluded.seven_day_avg_load,
                        forty_two_day_avg_load = excluded.forty_two_day_avg_load,
                        base_fitness = excluded.base_fitness,
                        load_impact = excluded.load_impact,
                        intensity_trend_pct = excluded.intensity_trend_pct,
                        monotony = excluded.monotony,
                        strain = excluded.strain,
                        training_status = excluded.training_status
                    """,
                    (
                        athlete_id,
                        current_day.isoformat(),
                        round(ctl, 1),
                        round(atl, 1),
                        round(tsb, 1),
                        acute_load,
                        chronic_load,
                        fatigue_flag,
                        round(day_training_load, 1),
                        round(seven_day_avg, 1),
                        round(forty_two_day_avg, 1),
                        base_fitness,
                        load_impact,
                        intensity_trend_pct,
                        monotony,
                        strain,
                        training_status,
                    ),
                )

                connection.execute(
                    """
                    INSERT INTO performance_snapshots (
                        athlete_id,
                        day,
                        sport,
                        base_fitness,
                        load_impact,
                        intensity_trend_pct,
                        training_status,
                        source
                    )
                    VALUES (?, ?, 'run', ?, ?, ?, ?, 'derived')
                    ON CONFLICT(athlete_id, day, sport, source) DO UPDATE SET
                        base_fitness = excluded.base_fitness,
                        load_impact = excluded.load_impact,
                        intensity_trend_pct = excluded.intensity_trend_pct,
                        training_status = excluded.training_status
                    """,
                    (
                        athlete_id,
                        current_day.isoformat(),
                        base_fitness,
                        load_impact,
                        intensity_trend_pct,
                        training_status,
                    ),
                )
                written_rows += 1

    return written_rows


def _build_daily_series(
    start_day: date,
    end_day: date,
    day_loads: dict[date, float],
) -> list[tuple[date, float]]:
    series: list[tuple[date, float]] = []
    current_day = start_day
    while current_day <= end_day:
        series.append((current_day, float(day_loads.get(current_day, 0.0))))
        current_day += timedelta(days=1)
    return series


def _mean(values: Iterable[float]) -> float:
    values_list = list(values)
    return sum(values_list) / len(values_list) if values_list else 0.0


def _monotony(values: list[float]) -> float | None:
    if not values:
        return None

    mean_value = _mean(values)
    if mean_value == 0:
        return 0.0

    variance = sum((value - mean_value) ** 2 for value in values) / len(values)
    std_dev = math.sqrt(variance)
    if std_dev == 0:
        return None
    return round(mean_value / std_dev, 2)


def _training_status(intensity_trend_pct: float | None) -> str:
    if intensity_trend_pct is None:
        return "no_baseline"
    if intensity_trend_pct >= 150:
        return "excessive"
    if intensity_trend_pct >= 100:
        return "optimized"
    if intensity_trend_pct >= 80:
        return "maintaining"
    if intensity_trend_pct >= 50:
        return "resuming"
    return "decreasing"


def main() -> None:
    parser = argparse.ArgumentParser(description="Recompute derived training load metrics.")
    parser.add_argument("--db-path", help="Override the AthleteHub database path.", default=None)
    args = parser.parse_args()

    rows = recompute_load_metrics(args.db_path)
    timestamp = datetime.now().isoformat(timespec="seconds")
    print(f"[{timestamp}] Recomputed derived load metrics for {rows} athlete-days")


if __name__ == "__main__":
    main()
