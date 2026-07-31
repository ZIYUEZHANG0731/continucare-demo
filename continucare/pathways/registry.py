"""Loading and lookup for built-in Pathway definitions."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files

from continucare.pathways.models import PathwayDefinition


class PathwayNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class PathwayRegistry:
    pathways: tuple[PathwayDefinition, ...]

    def __post_init__(self) -> None:
        keys = [(item.code, item.version) for item in self.pathways]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate pathway code and version")

    def get(self, code: str, version: str | None = None) -> PathwayDefinition:
        candidates = [item for item in self.pathways if item.code == code]
        if version is not None:
            candidates = [item for item in candidates if item.version == version]
        if not candidates:
            suffix = f" version {version}" if version else ""
            raise PathwayNotFoundError(f"pathway {code}{suffix} was not found")
        return max(candidates, key=lambda item: _version_key(item.version))

    def list(self) -> tuple[PathwayDefinition, ...]:
        return tuple(sorted(self.pathways, key=lambda item: (item.code, _version_key(item.version))))


def load_builtin_pathways() -> PathwayRegistry:
    data_dir = files("continucare.pathways.data")
    pathways = []
    for resource in sorted(data_dir.iterdir(), key=lambda item: item.name):
        if resource.name.endswith(".json"):
            pathways.append(PathwayDefinition.model_validate_json(resource.read_text("utf-8")))
    return PathwayRegistry(tuple(pathways))


def _version_key(version: str) -> tuple[int | str, ...]:
    parts: list[int | str] = []
    for part in version.split("."):
        parts.append(int(part) if part.isdigit() else part)
    return tuple(parts)
