from __future__ import annotations


def climbing_density(distance_km: float, elevation_gain_m: float) -> float:
    if distance_km <= 0:
        return 0.0
    return elevation_gain_m / distance_km


def grade_percent(distance_m: float, elevation_gain_m: float) -> float:
    if distance_m <= 0:
        return 0.0
    return (elevation_gain_m / distance_m) * 100.0


def compute_vam(elevation_gain_m: float, time_s: float) -> float:
    """Return vertical ascent speed in metres per hour (VAM)."""
    if time_s <= 0:
        return 0.0
    return elevation_gain_m / time_s * 3600.0
