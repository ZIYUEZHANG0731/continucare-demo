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


def load_local_environment(path: Path | str = ".env") -> None:
    """Load an ignored local env file without overwriting deployed secrets."""

    env_path = Path(path)
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value[:1] == value[-1:] and value.startswith(("'", '"')):
            value = value[1:-1]
        if key and key.replace("_", "").isalnum():
            os.environ.setdefault(key, value)


def get_settings() -> Settings:
    load_local_environment()
    return Settings(
        db_path=Path(os.getenv("CONTINUCARE_DB_PATH", "data/continucare.db")),
        mode=os.getenv("CONTINUCARE_MODE", "local_stable_demo"),
        feishu_enabled=os.getenv("FEISHU_ENABLED", "false").lower() == "true",
    )
