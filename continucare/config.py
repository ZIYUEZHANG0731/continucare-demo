"""Runtime configuration with safe, key-free defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    db_path: Path
    mode: str
    feishu_enabled: bool


def get_settings() -> Settings:
    return Settings(
        db_path=Path(os.getenv("CONTINUCARE_DB_PATH", "data/continucare.db")),
        mode=os.getenv("CONTINUCARE_MODE", "local_stable_demo"),
        feishu_enabled=os.getenv("FEISHU_ENABLED", "false").lower() == "true",
    )

