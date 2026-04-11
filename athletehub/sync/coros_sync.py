from __future__ import annotations

import argparse
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from zipfile import ZipFile

from athletehub.config import get_settings
from athletehub.db.analytics import recompute_load_metrics
from athletehub.db.db import get_connection
from athletehub.db.migrate import migrate
from athletehub.sync import SyncResult
from athletehub.utils.metrics import estimate_intensity_factor, estimate_training_load
from athletehub.utils.tcx import ParsedTcxActivity, TcxRecord, parse_tcx_bytes


@dataclass(slots=True)
class ImportArtifact:
    display_path: str
    file_type: str
    payload: bytes
    original_path: str


def sync_coros(
    export_path: str,
    account_label: str | None = None,
    persist_raw: bool = True,
    db_path: str | Path | None = None,
    raw_dir: str | Path | None = None,
) -> SyncResult:
    source = "coros"
    target_path = Path(export_path).expanduser()
    if not target_path.exists():
        return SyncResult(
            source=source,
            status="missing_path",
            message=f"COROS export path does not exist: {target_path}",
        )

    migrate(db_path)

    discovered = 0
    imported = 0
    skipped = 0
    errors = 0
    details: list[str] = []

    with get_connection(db_path) as connection:
        source_id = _get_source_id(connection, account_label)
        athlete_id = _get_default_athlete_id(connection)
        sync_run_id = connection.execute(
            "INSERT INTO sync_runs(source_id, status) VALUES (?, 'running')",
            (source_id,),
        ).lastrowid

        try:
            for artifact in _discover_artifacts(target_path):
                discovered += 1
                try:
                    if _source_file_exists(connection, artifact.payload):
                        skipped += 1
                        details.append(
                            f"Skipped already imported artifact: {artifact.display_path}"
                        )
                        continue

                    parsed_activity = _parse_artifact(artifact)
                    imported_activity_id = _upsert_parsed_activity(
                        connection=connection,
                        athlete_id=athlete_id,
                        source_id=source_id,
                        artifact=artifact,
                        activity=parsed_activity,
                        persist_raw=persist_raw,
                        raw_dir=raw_dir,
                    )
                    imported += 1
                    details.append(
                        f"Imported {parsed_activity.sport} activity #{imported_activity_id}: "
                        f"{parsed_activity.title}"
                    )
                except Exception as exc:
                    errors += 1
                    details.append(f"Failed to import {artifact.display_path}: {exc}")

            if imported > 0:
                connection.commit()
                recompute_load_metrics(db_path)

            connection.execute(
                """
                UPDATE data_sources
                SET last_synced_at = CURRENT_TIMESTAMP,
                    account_label = COALESCE(?, account_label)
                WHERE id = ?
                """,
                (account_label, source_id),
            )
            connection.execute(
                """
                UPDATE sync_runs
                SET finished_at = CURRENT_TIMESTAMP,
                    status = ?,
                    files_discovered = ?,
                    activities_imported = ?,
                    activities_skipped = ?,
                    errors_count = ?,
                    message = ?
                WHERE id = ?
                """,
                (
                    _final_status(imported, errors),
                    discovered,
                    imported,
                    skipped,
                    errors,
                    " | ".join(details[-5:]) if details else None,
                    sync_run_id,
                ),
            )
        except Exception as exc:
            connection.execute(
                """
                UPDATE sync_runs
                SET finished_at = CURRENT_TIMESTAMP,
                    status = 'failed',
                    files_discovered = ?,
                    activities_imported = ?,
                    activities_skipped = ?,
                    errors_count = ?,
                    message = ?
                WHERE id = ?
                """,
                (discovered, imported, skipped, errors + 1, str(exc), sync_run_id),
            )
            raise

    message = (
        f"Processed {discovered} COROS artifact(s): imported {imported}, "
        f"skipped {skipped}, errors {errors}."
    )
    return SyncResult(
        source=source,
        status=_final_status(imported, errors),
        imported=imported,
        discovered=discovered,
        skipped=skipped,
        errors=errors,
        message=message,
        details=details,
    )


def _discover_artifacts(path: Path) -> list[ImportArtifact]:
    if path.is_dir():
        artifacts: list[ImportArtifact] = []
        for file_path in sorted(path.rglob("*")):
            if file_path.is_dir():
                continue
            if file_path.suffix.lower() == ".zip":
                artifacts.extend(_artifacts_from_zip(file_path))
            elif file_path.suffix.lower() in {".tcx", ".fit"}:
                artifacts.append(
                    ImportArtifact(
                        display_path=str(file_path),
                        file_type=file_path.suffix.lower().lstrip("."),
                        payload=file_path.read_bytes(),
                        original_path=str(file_path),
                    )
                )
        return artifacts

    if path.suffix.lower() == ".zip":
        return _artifacts_from_zip(path)

    if path.suffix.lower() in {".tcx", ".fit"}:
        return [
            ImportArtifact(
                display_path=str(path),
                file_type=path.suffix.lower().lstrip("."),
                payload=path.read_bytes(),
                original_path=str(path),
            )
        ]

    raise ValueError(f"Unsupported COROS export path: {path}")


def _artifacts_from_zip(path: Path) -> list[ImportArtifact]:
    artifacts: list[ImportArtifact] = []
    with ZipFile(path) as archive:
        for member in sorted(archive.namelist()):
            member_path = Path(member)
            suffix = member_path.suffix.lower()
            if suffix not in {".tcx", ".fit"}:
                continue
            artifacts.append(
                ImportArtifact(
                    display_path=f"{path}::{member}",
                    file_type=suffix.lstrip("."),
                    payload=archive.read(member),
                    original_path=str(path),
                )
            )
    return artifacts


def _parse_artifact(artifact: ImportArtifact) -> ParsedTcxActivity:
    if artifact.file_type == "tcx":
        file_name = artifact.display_path.split("::", 1)[-1]
        return parse_tcx_bytes(artifact.payload, Path(file_name).name)

    if artifact.file_type == "fit":
        raise ValueError(
            "FIT parsing is not enabled in this build yet. Export COROS activities as TCX."
        )

    raise ValueError(f"Unsupported artifact type: {artifact.file_type}")


def _source_file_exists(connection: sqlite3.Connection, payload: bytes) -> bool:
    checksum = _checksum(payload)
    row = connection.execute(
        "SELECT id FROM source_files WHERE checksum = ?",
        (checksum,),
    ).fetchone()
    return row is not None


def _upsert_parsed_activity(
    connection: sqlite3.Connection,
    athlete_id: int,
    source_id: int,
    artifact: ImportArtifact,
    activity: ParsedTcxActivity,
    persist_raw: bool,
    raw_dir: str | Path | None,
) -> int:
    athlete_profile = _get_athlete_profile(connection, athlete_id)
    threshold_hr = athlete_profile.get("threshold_hr_bpm")
    max_hr = athlete_profile.get("max_hr_bpm")

    intensity_factor = estimate_intensity_factor(
        activity.average_hr_bpm,
        threshold_hr,
        max_hr,
    )
    training_load = estimate_training_load(
        activity.moving_time_s,
        activity.average_hr_bpm,
        threshold_hr,
        max_hr,
    )
    checksum = _checksum(artifact.payload)
    stored_path = (
        _store_raw_artifact(artifact, checksum, raw_dir)
        if persist_raw
        else Path(artifact.original_path)
    )

    source_file_id = connection.execute(
        """
        INSERT INTO source_files(source_id, path, original_path, file_type, checksum, byte_size, status)
        VALUES (?, ?, ?, ?, ?, ?, 'imported')
        ON CONFLICT(checksum) DO UPDATE SET
            path = excluded.path,
            original_path = excluded.original_path,
            file_type = excluded.file_type,
            byte_size = excluded.byte_size,
            status = excluded.status,
            imported_at = CURRENT_TIMESTAMP
        RETURNING id
        """,
        (
            source_id,
            str(stored_path),
            artifact.display_path,
            artifact.file_type,
            checksum,
            len(artifact.payload),
        ),
    ).fetchone()["id"]

    device_name = activity.device_name
    if device_name:
        brand, model = _split_device_name(device_name)
        connection.execute(
            """
            INSERT OR IGNORE INTO athlete_devices(athlete_id, brand, model)
            VALUES (?, ?, ?)
            """,
            (athlete_id, brand, model),
        )

    payload_summary = json.dumps(
        {
            "import_path": artifact.display_path,
            "stored_path": str(stored_path),
            "lap_count": activity.lap_count,
            "record_count": len(activity.records),
            "parser": "tcx",
        },
        ensure_ascii=True,
    )

    existing = connection.execute(
        """
        SELECT id
        FROM activities
        WHERE source_id = ? AND source_activity_id = ?
        """,
        (source_id, activity.source_activity_id),
    ).fetchone()

    if existing:
        activity_id = int(existing["id"])
        connection.execute(
            """
            UPDATE activities
            SET source_file_id = ?,
                sport = ?,
                sport_subtype = ?,
                workout_type = ?,
                title = ?,
                started_at = ?,
                ended_at = ?,
                timezone = ?,
                distance_m = ?,
                moving_time_s = ?,
                elapsed_time_s = ?,
                elevation_gain_m = ?,
                elevation_loss_m = ?,
                average_speed_mps = ?,
                average_hr_bpm = ?,
                max_hr_bpm = ?,
                average_power_w = ?,
                average_cadence_spm = ?,
                calories_kcal = ?,
                training_load = ?,
                intensity_factor = ?,
                lap_count = ?,
                start_lat = ?,
                start_lon = ?,
                end_lat = ?,
                end_lon = ?,
                device_name = ?,
                load_source = 'derived',
                checksum = ?,
                raw_payload = ?
            WHERE id = ?
            """,
            (
                source_file_id,
                activity.sport,
                _sport_subtype(activity.sport),
                "imported",
                activity.title,
                activity.started_at,
                activity.ended_at,
                activity.timezone,
                activity.distance_m,
                activity.moving_time_s,
                activity.elapsed_time_s,
                activity.elevation_gain_m,
                activity.elevation_loss_m,
                activity.average_speed_mps,
                activity.average_hr_bpm,
                activity.max_hr_bpm,
                activity.average_power_w,
                activity.average_cadence_spm,
                activity.calories_kcal,
                training_load,
                intensity_factor,
                activity.lap_count,
                activity.start_lat,
                activity.start_lon,
                activity.end_lat,
                activity.end_lon,
                device_name,
                checksum,
                payload_summary,
                activity_id,
            ),
        )
        _clear_activity_children(connection, activity_id)
    else:
        activity_id = int(
            connection.execute(
                """
                INSERT INTO activities(
                    athlete_id,
                    source_id,
                    source_file_id,
                    source_activity_id,
                    sport,
                    sport_subtype,
                    workout_type,
                    title,
                    started_at,
                    ended_at,
                    timezone,
                    distance_m,
                    moving_time_s,
                    elapsed_time_s,
                    elevation_gain_m,
                    elevation_loss_m,
                    average_speed_mps,
                    average_hr_bpm,
                    max_hr_bpm,
                    average_power_w,
                    average_cadence_spm,
                    calories_kcal,
                    training_load,
                    intensity_factor,
                    lap_count,
                    start_lat,
                    start_lon,
                    end_lat,
                    end_lon,
                    device_name,
                    load_source,
                    checksum,
                    raw_payload
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'derived', ?, ?)
                """,
                (
                    athlete_id,
                    source_id,
                    source_file_id,
                    activity.source_activity_id,
                    activity.sport,
                    _sport_subtype(activity.sport),
                    "imported",
                    activity.title,
                    activity.started_at,
                    activity.ended_at,
                    activity.timezone,
                    activity.distance_m,
                    activity.moving_time_s,
                    activity.elapsed_time_s,
                    activity.elevation_gain_m,
                    activity.elevation_loss_m,
                    activity.average_speed_mps,
                    activity.average_hr_bpm,
                    activity.max_hr_bpm,
                    activity.average_power_w,
                    activity.average_cadence_spm,
                    activity.calories_kcal,
                    training_load,
                    intensity_factor,
                    activity.lap_count,
                    activity.start_lat,
                    activity.start_lon,
                    activity.end_lat,
                    activity.end_lon,
                    device_name,
                    checksum,
                    payload_summary,
                ),
            ).lastrowid
        )

    connection.execute(
        """
        INSERT INTO activity_files(activity_id, file_type, path, checksum)
        VALUES (?, ?, ?, ?)
        """,
        (activity_id, artifact.file_type, str(stored_path), checksum),
    )

    for lap in activity.laps:
        connection.execute(
            """
            INSERT INTO activity_laps(
                activity_id,
                lap_index,
                started_at,
                total_time_s,
                distance_m,
                calories_kcal,
                average_hr_bpm,
                max_hr_bpm,
                average_cadence_spm
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                activity_id,
                lap.lap_index,
                lap.started_at,
                lap.total_time_s,
                lap.distance_m,
                lap.calories_kcal,
                lap.average_hr_bpm,
                lap.max_hr_bpm,
                lap.cadence_spm,
            ),
        )

        avg_pace = (
            lap.total_time_s / (lap.distance_m / 1000.0)
            if lap.total_time_s and lap.distance_m and lap.distance_m > 0
            else None
        )
        connection.execute(
            """
            INSERT INTO activity_splits(
                activity_id,
                split_index,
                distance_m,
                elapsed_time_s,
                moving_time_s,
                avg_pace_s_per_km,
                avg_hr_bpm
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                activity_id,
                lap.lap_index,
                lap.distance_m,
                lap.total_time_s,
                lap.total_time_s,
                avg_pace,
                lap.average_hr_bpm,
            ),
        )

    for record in activity.records:
        connection.execute(
            """
            INSERT INTO activity_records(
                activity_id,
                sample_index,
                recorded_at,
                elapsed_time_s,
                distance_m,
                latitude,
                longitude,
                altitude_m,
                heart_rate_bpm,
                cadence_spm,
                speed_mps,
                power_w,
                temperature_c,
                grade_pct
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                activity_id,
                record.sample_index,
                record.recorded_at,
                record.elapsed_time_s,
                record.distance_m,
                record.latitude,
                record.longitude,
                record.altitude_m,
                record.heart_rate_bpm,
                record.cadence_spm,
                record.speed_mps,
                record.power_w,
                record.temperature_c,
                _grade_pct(record, activity.records),
            ),
        )

    for best_effort in _compute_best_efforts(activity.records):
        connection.execute(
            """
            INSERT INTO activity_best_efforts(
                activity_id,
                metric_type,
                distance_m,
                duration_s,
                value,
                unit
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                activity_id,
                best_effort["metric_type"],
                best_effort["distance_m"],
                best_effort["duration_s"],
                best_effort["value"],
                best_effort["unit"],
            ),
        )

    return activity_id


def _get_source_id(connection: sqlite3.Connection, account_label: str | None) -> int:
    connection.execute(
        """
        INSERT INTO data_sources(source, account_label, sync_method, base_url, notes)
        VALUES ('coros', ?, 'training_hub_export', 'https://training.coros.com/', ?)
        ON CONFLICT(source) DO UPDATE SET
            account_label = COALESCE(excluded.account_label, data_sources.account_label),
            sync_method = excluded.sync_method,
            base_url = excluded.base_url,
            notes = excluded.notes
        """,
        (account_label, "Official COROS Training Hub TCX import."),
    )
    row = connection.execute(
        "SELECT id FROM data_sources WHERE source = 'coros'"
    ).fetchone()
    if row is None:
        raise RuntimeError("COROS data source is not initialized")
    return int(row["id"])


def _get_default_athlete_id(connection: sqlite3.Connection) -> int:
    athlete_name = get_settings().athlete_name
    row = connection.execute(
        "SELECT id FROM athletes WHERE name = ?",
        (athlete_name,),
    ).fetchone()
    if row is None:
        raise RuntimeError(
            "Default athlete record is missing. Run `python3 -m athletehub.db.migrate` first."
        )
    return int(row["id"])


def _get_athlete_profile(connection: sqlite3.Connection, athlete_id: int) -> dict:
    row = connection.execute(
        """
        SELECT threshold_hr_bpm, max_hr_bpm
        FROM athlete_profiles
        WHERE athlete_id = ?
        """,
        (athlete_id,),
    ).fetchone()
    return dict(row) if row else {}


def _store_raw_artifact(
    artifact: ImportArtifact,
    checksum: str,
    raw_dir: str | Path | None,
) -> Path:
    base_raw_dir = Path(raw_dir).expanduser() if raw_dir else get_settings().raw_data_dir
    coros_raw_dir = base_raw_dir / "coros"
    coros_raw_dir.mkdir(parents=True, exist_ok=True)
    suffix = f".{artifact.file_type}"
    destination = coros_raw_dir / f"{checksum}{suffix}"
    if not destination.exists():
        destination.write_bytes(artifact.payload)
    return destination


def _checksum(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _clear_activity_children(connection: sqlite3.Connection, activity_id: int) -> None:
    for table_name in (
        "activity_files",
        "activity_laps",
        "activity_splits",
        "activity_records",
        "activity_best_efforts",
        "activity_training_effects",
        "activity_zone_times",
        "activity_fueling",
        "activity_weather",
    ):
        connection.execute(f"DELETE FROM {table_name} WHERE activity_id = ?", (activity_id,))


def _sport_subtype(sport: str) -> str | None:
    if sport == "trail_run":
        return "trail"
    if sport == "run":
        return "road"
    return None


def _split_device_name(device_name: str) -> tuple[str, str]:
    parts = device_name.split(maxsplit=1)
    if len(parts) == 1:
        return "COROS", parts[0]
    return parts[0], parts[1]


def _grade_pct(record: TcxRecord, all_records: list[TcxRecord]) -> float | None:
    if record.sample_index == 0:
        return None

    prev = all_records[record.sample_index - 1]
    if (
        prev.distance_m is None
        or record.distance_m is None
        or prev.altitude_m is None
        or record.altitude_m is None
    ):
        return None

    horizontal_m = record.distance_m - prev.distance_m
    if horizontal_m <= 0:
        return None

    vertical_m = record.altitude_m - prev.altitude_m
    return round((vertical_m / horizontal_m) * 100.0, 2)


def _compute_best_efforts(records: list[TcxRecord]) -> list[dict]:
    targets = (1000, 5000, 10000)
    valid = [
        record
        for record in records
        if record.distance_m is not None and record.elapsed_time_s is not None
    ]
    if len(valid) < 2:
        return []

    efforts: list[dict] = []
    for target in targets:
        if (valid[-1].distance_m or 0.0) < target:
            continue

        best_duration: int | None = None
        end_index = 1
        for start_index, start in enumerate(valid):
            start_distance = start.distance_m or 0.0
            start_time = start.elapsed_time_s or 0
            end_index = max(end_index, start_index + 1)
            while end_index < len(valid) and (valid[end_index].distance_m or 0.0) - start_distance < target:
                end_index += 1
            if end_index >= len(valid):
                break

            duration_s = (valid[end_index].elapsed_time_s or 0) - start_time
            if duration_s <= 0:
                continue
            if best_duration is None or duration_s < best_duration:
                best_duration = duration_s

        if best_duration is None:
            continue

        efforts.append(
            {
                "metric_type": "best_pace",
                "distance_m": float(target),
                "duration_s": best_duration,
                "value": round(best_duration / (target / 1000.0), 1),
                "unit": "s_per_km",
            }
        )

    return efforts


def _final_status(imported: int, errors: int) -> str:
    if errors and imported:
        return "partial"
    if errors:
        return "failed"
    return "completed"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import official COROS Training Hub TCX exports into AthleteHub."
    )
    parser.add_argument("path", help="A .tcx file, .zip export, or directory containing exports.")
    parser.add_argument("--account-label", default=None, help="Optional label for this COROS account.")
    parser.add_argument(
        "--no-persist-raw",
        action="store_true",
        help="Do not store a local raw copy under data/raw/coros.",
    )
    parser.add_argument("--db-path", default=None, help="Optional database path override.")
    parser.add_argument("--raw-dir", default=None, help="Optional raw data directory override.")
    args = parser.parse_args()

    result = sync_coros(
        export_path=args.path,
        account_label=args.account_label,
        persist_raw=not args.no_persist_raw,
        db_path=args.db_path,
        raw_dir=args.raw_dir,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
