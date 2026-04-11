from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

from athletehub.utils.metrics import safe_mean


@dataclass(slots=True)
class TcxLap:
    lap_index: int
    started_at: str | None
    total_time_s: int | None
    distance_m: float | None
    calories_kcal: float | None
    average_hr_bpm: float | None
    max_hr_bpm: float | None
    cadence_spm: float | None


@dataclass(slots=True)
class TcxRecord:
    sample_index: int
    recorded_at: str | None
    elapsed_time_s: int | None
    distance_m: float | None
    latitude: float | None
    longitude: float | None
    altitude_m: float | None
    heart_rate_bpm: float | None
    cadence_spm: float | None
    speed_mps: float | None
    power_w: float | None
    temperature_c: float | None


@dataclass(slots=True)
class ParsedTcxActivity:
    source_activity_id: str
    sport: str
    title: str
    started_at: str
    ended_at: str | None
    timezone: str | None
    distance_m: float | None
    moving_time_s: int | None
    elapsed_time_s: int | None
    elevation_gain_m: float | None
    elevation_loss_m: float | None
    average_speed_mps: float | None
    average_hr_bpm: float | None
    max_hr_bpm: float | None
    average_power_w: float | None
    average_cadence_spm: float | None
    calories_kcal: float | None
    lap_count: int
    start_lat: float | None
    start_lon: float | None
    end_lat: float | None
    end_lon: float | None
    device_name: str | None
    laps: list[TcxLap]
    records: list[TcxRecord]


def parse_tcx_file(path: str | Path) -> ParsedTcxActivity:
    file_path = Path(path).expanduser()
    return parse_tcx_bytes(file_path.read_bytes(), file_path.name)


def parse_tcx_bytes(payload: bytes, file_name: str) -> ParsedTcxActivity:
    root = ET.fromstring(payload)
    activity = root.find(".//{*}Activity")
    if activity is None:
        raise ValueError(f"No Activity node found in {file_name}")

    sport = _map_sport(activity.attrib.get("Sport"), file_name)
    title = Path(file_name).stem.replace("_", " ").replace("-", " ").strip() or "COROS Activity"
    activity_id = _find_text(activity, "./{*}Id") or title
    timezone_name = _timezone_name(activity_id)

    laps: list[TcxLap] = []
    records: list[TcxRecord] = []
    lap_times: list[int] = []
    lap_distances: list[float] = []
    lap_calories: list[float] = []
    lap_avg_hrs: list[float] = []
    lap_max_hrs: list[float] = []
    lap_cadences: list[float] = []

    first_time: datetime | None = None
    last_time: datetime | None = None
    prev_altitude: float | None = None
    elevation_gain = 0.0
    elevation_loss = 0.0

    sample_index = 0

    for lap_index, lap_node in enumerate(activity.findall("./{*}Lap"), start=1):
        lap_started_at = lap_node.attrib.get("StartTime")
        lap_total_time = _to_int(_find_text(lap_node, "./{*}TotalTimeSeconds"))
        lap_distance = _to_float(_find_text(lap_node, "./{*}DistanceMeters"))
        lap_calories_value = _to_float(_find_text(lap_node, "./{*}Calories"))
        lap_avg_hr = _to_float(_find_text(lap_node, "./{*}AverageHeartRateBpm/{*}Value"))
        lap_max_hr = _to_float(_find_text(lap_node, "./{*}MaximumHeartRateBpm/{*}Value"))
        lap_cadence = _to_float(_find_text(lap_node, "./{*}Cadence"))

        if lap_total_time is not None:
            lap_times.append(lap_total_time)
        if lap_distance is not None:
            lap_distances.append(lap_distance)
        if lap_calories_value is not None:
            lap_calories.append(lap_calories_value)
        if lap_avg_hr is not None:
            lap_avg_hrs.append(lap_avg_hr)
        if lap_max_hr is not None:
            lap_max_hrs.append(lap_max_hr)
        if lap_cadence is not None:
            lap_cadences.append(lap_cadence)

        laps.append(
            TcxLap(
                lap_index=lap_index,
                started_at=_normalize_timestamp(lap_started_at) if lap_started_at else None,
                total_time_s=lap_total_time,
                distance_m=lap_distance,
                calories_kcal=lap_calories_value,
                average_hr_bpm=lap_avg_hr,
                max_hr_bpm=lap_max_hr,
                cadence_spm=lap_cadence,
            )
        )

        for point in lap_node.findall(".//{*}Trackpoint"):
            recorded_at_raw = _find_text(point, "./{*}Time")
            recorded_at = _normalize_timestamp(recorded_at_raw) if recorded_at_raw else None
            recorded_dt = _parse_timestamp(recorded_at_raw)

            if recorded_dt is not None and first_time is None:
                first_time = recorded_dt
            if recorded_dt is not None:
                last_time = recorded_dt

            altitude = _to_float(_find_text(point, "./{*}AltitudeMeters"))
            if altitude is not None and prev_altitude is not None:
                if altitude > prev_altitude:
                    elevation_gain += altitude - prev_altitude
                elif altitude < prev_altitude:
                    elevation_loss += prev_altitude - altitude
            if altitude is not None:
                prev_altitude = altitude

            latitude = _to_float(_find_text(point, "./{*}Position/{*}LatitudeDegrees"))
            longitude = _to_float(_find_text(point, "./{*}Position/{*}LongitudeDegrees"))
            heart_rate = _to_float(_find_text(point, "./{*}HeartRateBpm/{*}Value"))
            cadence = _to_float(_find_text(point, "./{*}Cadence"))
            distance = _to_float(_find_text(point, "./{*}DistanceMeters"))
            speed = _extension_value(point, {"Speed"})
            power = _extension_value(point, {"Watts", "RunWatts"})
            temperature = _extension_value(point, {"Temp", "Temperature"})
            elapsed_time_s = (
                int((recorded_dt - first_time).total_seconds())
                if recorded_dt is not None and first_time is not None
                else None
            )

            records.append(
                TcxRecord(
                    sample_index=sample_index,
                    recorded_at=recorded_at,
                    elapsed_time_s=elapsed_time_s,
                    distance_m=distance,
                    latitude=latitude,
                    longitude=longitude,
                    altitude_m=altitude,
                    heart_rate_bpm=heart_rate,
                    cadence_spm=cadence,
                    speed_mps=speed,
                    power_w=power,
                    temperature_c=temperature,
                )
            )
            sample_index += 1

    total_distance = max(
        lap_distances[-1] if lap_distances else 0.0,
        max((record.distance_m or 0.0) for record in records) if records else 0.0,
    )
    moving_time_s = sum(lap_times) if lap_times else None
    elapsed_time_s = (
        int((last_time - first_time).total_seconds())
        if first_time is not None and last_time is not None
        else moving_time_s
    )
    average_hr = safe_mean(record.heart_rate_bpm for record in records) or safe_mean(lap_avg_hrs)
    max_hr = max((record.heart_rate_bpm or 0.0) for record in records) if records else None
    if not max_hr and lap_max_hrs:
        max_hr = max(lap_max_hrs)
    average_power = safe_mean(record.power_w for record in records)
    average_cadence = safe_mean(record.cadence_spm for record in records) or safe_mean(lap_cadences)
    average_speed = total_distance / moving_time_s if moving_time_s and total_distance else None

    start_lat = records[0].latitude if records else None
    start_lon = records[0].longitude if records else None
    end_lat = records[-1].latitude if records else None
    end_lon = records[-1].longitude if records else None
    device_name = _find_text(root, ".//{*}Creator/{*}Name")

    started_at = (
        _normalize_timestamp(activity_id)
        if _parse_timestamp(activity_id) is not None
        else (
            _normalize_timestamp(records[0].recorded_at)
            if records and records[0].recorded_at
            else None
        )
    )
    if started_at is None:
        raise ValueError(f"Unable to determine start time for {file_name}")

    return ParsedTcxActivity(
        source_activity_id=activity_id,
        sport=sport,
        title=title,
        started_at=started_at,
        ended_at=_normalize_timestamp(last_time.isoformat()) if last_time is not None else None,
        timezone=timezone_name,
        distance_m=round(total_distance, 1) if total_distance else None,
        moving_time_s=moving_time_s,
        elapsed_time_s=elapsed_time_s,
        elevation_gain_m=round(elevation_gain, 1) if elevation_gain else None,
        elevation_loss_m=round(elevation_loss, 1) if elevation_loss else None,
        average_speed_mps=round(average_speed, 3) if average_speed is not None else None,
        average_hr_bpm=round(average_hr, 1) if average_hr is not None else None,
        max_hr_bpm=round(max_hr, 1) if max_hr is not None else None,
        average_power_w=round(average_power, 1) if average_power is not None else None,
        average_cadence_spm=round(average_cadence, 1) if average_cadence is not None else None,
        calories_kcal=round(sum(lap_calories), 1) if lap_calories else None,
        lap_count=len(laps),
        start_lat=start_lat,
        start_lon=start_lon,
        end_lat=end_lat,
        end_lon=end_lon,
        device_name=device_name,
        laps=laps,
        records=records,
    )


def _map_sport(tcx_sport: str | None, file_name: str) -> str:
    sport_value = (tcx_sport or "Other").lower()
    name_hint = file_name.lower()
    if "trail" in name_hint:
        return "trail_run"
    if "hike" in name_hint:
        return "hike"
    if "walk" in name_hint:
        return "walk"
    if sport_value == "running":
        return "run"
    if sport_value == "biking":
        return "ride"
    return "other"


def _find_text(node: ET.Element, path: str) -> str | None:
    match = node.find(path)
    if match is None or match.text is None:
        return None
    value = match.text.strip()
    return value or None


def _extension_value(node: ET.Element, candidate_names: set[str]) -> float | None:
    for descendant in node.findall(".//{*}Extensions//*"):
        local_name = descendant.tag.split("}", 1)[-1]
        if local_name in candidate_names and descendant.text:
            try:
                return float(descendant.text)
            except ValueError:
                continue
    return None


def _to_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _to_int(value: str | None) -> int | None:
    float_value = _to_float(value)
    return int(round(float_value)) if float_value is not None else None


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _normalize_timestamp(value: str | None) -> str | None:
    dt = _parse_timestamp(value)
    if dt is None:
        return value
    if dt.tzinfo is None:
        return dt.isoformat(timespec="seconds")
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _timezone_name(value: str | None) -> str | None:
    dt = _parse_timestamp(value)
    if dt is None or dt.tzinfo is None:
        return None
    return "UTC" if dt.utcoffset() == timezone.utc.utcoffset(dt) else str(dt.tzinfo)
