from __future__ import annotations

from collections.abc import Iterable


def format_duration(total_seconds: float) -> str:
    seconds = int(round(max(total_seconds, 0)))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_pace(seconds_per_km: float) -> str:
    seconds = int(round(max(seconds_per_km, 0)))
    minutes, secs = divmod(seconds, 60)
    return f"{minutes}:{secs:02d}/km"


def safe_mean(values: Iterable[float | None]) -> float | None:
    filtered = [value for value in values if value is not None]
    if not filtered:
        return None
    return sum(filtered) / len(filtered)


def estimate_intensity_factor(
    average_hr_bpm: float | None,
    threshold_hr_bpm: float | None = None,
    max_hr_bpm: float | None = None,
) -> float | None:
    if average_hr_bpm is None:
        return None

    if threshold_hr_bpm:
        reference = threshold_hr_bpm
    elif max_hr_bpm:
        reference = max_hr_bpm * 0.88
    else:
        reference = 165.0

    if reference <= 0:
        return None

    return round(max(0.5, min(1.3, average_hr_bpm / reference)), 2)


def estimate_training_load(
    moving_time_s: int | float | None,
    average_hr_bpm: float | None,
    threshold_hr_bpm: float | None = None,
    max_hr_bpm: float | None = None,
) -> float:
    if not moving_time_s:
        return 0.0

    duration_minutes = max(float(moving_time_s) / 60.0, 0.0)
    intensity_factor = estimate_intensity_factor(
        average_hr_bpm=average_hr_bpm,
        threshold_hr_bpm=threshold_hr_bpm,
        max_hr_bpm=max_hr_bpm,
    )
    effective_intensity = intensity_factor if intensity_factor is not None else 0.75
    return round(duration_minutes * effective_intensity, 1)


def hr_zone_for_bpm(
    hr_bpm: float | None,
    max_hr_bpm: float | None = None,
    threshold_hr_bpm: float | None = None,
) -> int:
    """Return a heart-rate zone (1-5) for the given *hr_bpm*.

    Zone boundaries are derived from *threshold_hr_bpm* when available,
    otherwise from *max_hr_bpm*.  Falls back to a generic 165 bpm
    reference if neither is provided.
    """
    if hr_bpm is None:
        return 0

    if threshold_hr_bpm and threshold_hr_bpm > 0:
        reference = threshold_hr_bpm
    elif max_hr_bpm and max_hr_bpm > 0:
        reference = max_hr_bpm * 0.88
    else:
        reference = 165.0

    ratio = hr_bpm / reference
    if ratio < 0.72:
        return 1
    if ratio < 0.82:
        return 2
    if ratio < 0.92:
        return 3
    if ratio < 1.02:
        return 4
    return 5
