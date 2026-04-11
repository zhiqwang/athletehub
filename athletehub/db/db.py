from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from athletehub.config import get_settings


def resolve_db_path(db_path: str | Path | None = None) -> Path:
    if db_path is None:
        return get_settings().database_path
    return Path(db_path).expanduser()


def get_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = resolve_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON;")
    return connection


def execute_script(script: str, db_path: str | Path | None = None) -> None:
    with get_connection(db_path) as connection:
        connection.executescript(script)


def execute(
    query: str,
    params: Iterable[object] = (),
    db_path: str | Path | None = None,
) -> int:
    with get_connection(db_path) as connection:
        cursor = connection.execute(query, tuple(params))
        return cursor.lastrowid


def fetch_all(
    query: str,
    params: Iterable[object] = (),
    db_path: str | Path | None = None,
) -> list[dict]:
    with get_connection(db_path) as connection:
        rows = connection.execute(query, tuple(params)).fetchall()
    return [dict(row) for row in rows]


def fetch_one(
    query: str,
    params: Iterable[object] = (),
    db_path: str | Path | None = None,
) -> dict | None:
    with get_connection(db_path) as connection:
        row = connection.execute(query, tuple(params)).fetchone()
    return dict(row) if row else None


def table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def get_table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    if not table_exists(connection, table_name):
        return set()

    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row["name"]) for row in rows}
