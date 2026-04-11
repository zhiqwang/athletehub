from __future__ import annotations

from pathlib import Path
import sqlite3

from athletehub.config import get_settings
from athletehub.db.db import get_connection, get_table_columns, resolve_db_path, table_exists

SCHEMA_VERSION = "2"
DATA_SOURCES = (
    {
        "source": "coros",
        "sync_method": "training_hub_export",
        "base_url": "https://training.coros.com/",
        "notes": "Import official COROS Training Hub export archives or TCX files.",
    },
)

ADDITIONAL_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "data_sources": [
        ("sync_method", "TEXT NOT NULL DEFAULT 'manual_import'"),
        ("base_url", "TEXT"),
        ("notes", "TEXT"),
    ],
    "activities": [
        ("source_file_id", "INTEGER REFERENCES source_files(id)"),
        ("sport_subtype", "TEXT"),
        ("workout_type", "TEXT"),
        ("terrain_type", "TEXT"),
        ("lap_count", "INTEGER"),
        ("start_lat", "REAL"),
        ("start_lon", "REAL"),
        ("end_lat", "REAL"),
        ("end_lon", "REAL"),
        ("device_name", "TEXT"),
        ("route_id", "INTEGER REFERENCES course_routes(id)"),
        ("load_source", "TEXT"),
        ("checksum", "TEXT"),
    ],
    "health_daily": [
        ("respiratory_rate_bpm", "REAL"),
        ("spo2_pct", "REAL"),
        ("skin_temperature_c", "REAL"),
        ("fatigue_score", "REAL"),
        ("soreness_score", "REAL"),
        ("mood_score", "REAL"),
        ("energy_score", "REAL"),
    ],
    "load_daily": [
        ("day_training_load", "REAL"),
        ("seven_day_avg_load", "REAL"),
        ("forty_two_day_avg_load", "REAL"),
        ("base_fitness", "REAL"),
        ("load_impact", "REAL"),
        ("intensity_trend_pct", "REAL"),
        ("monotony", "REAL"),
        ("strain", "REAL"),
        ("training_status", "TEXT"),
    ],
    "race_events": [
        ("route_id", "INTEGER REFERENCES course_routes(id)"),
        ("priority", "TEXT"),
        ("strategy_notes", "TEXT"),
    ],
    "sync_runs": [
        ("files_discovered", "INTEGER NOT NULL DEFAULT 0"),
        ("activities_skipped", "INTEGER NOT NULL DEFAULT 0"),
        ("errors_count", "INTEGER NOT NULL DEFAULT 0"),
    ],
}


def load_schema() -> str:
    return Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")


def ensure_schema_metadata(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def ensure_columns(connection: sqlite3.Connection) -> None:
    for table_name, columns in ADDITIONAL_COLUMNS.items():
        if not table_exists(connection, table_name):
            continue

        existing_columns = get_table_columns(connection, table_name)
        for column_name, column_definition in columns:
            if column_name in existing_columns:
                continue
            connection.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
            )


def seed_defaults(connection: sqlite3.Connection) -> None:
    settings = get_settings()
    connection.execute(
        "INSERT OR IGNORE INTO athletes(name) VALUES (?)",
        (settings.athlete_name,),
    )

    athlete_id_row = connection.execute(
        "SELECT id FROM athletes WHERE name = ?",
        (settings.athlete_name,),
    ).fetchone()
    if athlete_id_row is None:
        raise RuntimeError("Failed to create default athlete")

    athlete_id = int(athlete_id_row["id"])
    connection.execute(
        """
        INSERT OR IGNORE INTO athlete_profiles(athlete_id, timezone)
        VALUES (?, ?)
        """,
        (athlete_id, "UTC"),
    )

    for source in DATA_SOURCES:
        connection.execute(
            """
            INSERT INTO data_sources(source, sync_method, base_url, notes)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET
                sync_method = excluded.sync_method,
                base_url = excluded.base_url,
                notes = excluded.notes
            """,
            (
                source["source"],
                source["sync_method"],
                source["base_url"],
                source["notes"],
            ),
        )

    connection.execute(
        """
        INSERT INTO schema_metadata(key, value)
        VALUES ('schema_version', ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = CURRENT_TIMESTAMP
        """,
        (SCHEMA_VERSION,),
    )

    connection.execute(
        """
        DELETE FROM data_sources
        WHERE source <> 'coros'
          AND id NOT IN (SELECT DISTINCT COALESCE(source_id, -1) FROM activities WHERE source_id IS NOT NULL)
          AND id NOT IN (SELECT DISTINCT COALESCE(source_id, -1) FROM sync_runs WHERE source_id IS NOT NULL)
          AND id NOT IN (SELECT DISTINCT COALESCE(source_id, -1) FROM source_files WHERE source_id IS NOT NULL)
          AND id NOT IN (SELECT DISTINCT COALESCE(source_id, -1) FROM health_daily WHERE source_id IS NOT NULL)
          AND id NOT IN (SELECT DISTINCT COALESCE(source_id, -1) FROM body_metrics WHERE source_id IS NOT NULL)
          AND id NOT IN (SELECT DISTINCT COALESCE(source_id, -1) FROM sleep_sessions WHERE source_id IS NOT NULL)
        """
    )


def migrate(db_path: str | Path | None = None) -> Path:
    target = resolve_db_path(db_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    with get_connection(target) as connection:
        ensure_schema_metadata(connection)
        connection.executescript(load_schema())
        ensure_columns(connection)
        seed_defaults(connection)

    return target


def main() -> None:
    path = migrate()
    print(f"Initialized AthleteHub database at {path}")


if __name__ == "__main__":
    main()
