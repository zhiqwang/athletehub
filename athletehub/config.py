from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent
DATA_ROOT = PROJECT_ROOT / "data"


@dataclass(frozen=True)
class Settings:
    database_path: Path
    raw_data_dir: Path
    athlete_name: str


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    database_path = Path(
        os.getenv("ATHLETEHUB_DB_PATH", str(DATA_ROOT / "athletehub.db"))
    ).expanduser()
    raw_data_dir = Path(os.getenv("ATHLETEHUB_RAW_DIR", str(DATA_ROOT / "raw"))).expanduser()

    return Settings(
        database_path=database_path,
        raw_data_dir=raw_data_dir,
        athlete_name=os.getenv("ATHLETEHUB_ATHLETE_NAME", "default-athlete"),
    )
