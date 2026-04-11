from __future__ import annotations


def heat_adjustment_seconds_per_km(temperature_c: float | None) -> float:
    if temperature_c is None or temperature_c <= 12:
        return 0.0
    return min((temperature_c - 12.0) * 1.5, 25.0)
