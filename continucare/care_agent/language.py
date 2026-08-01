"""Deterministic patient-facing rendering, kept separate from semantics."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any


class PatientLanguageRenderer:
    def __init__(self, policy: dict[str, Any]):
        self.policy = policy
        self.version = policy["version"]

    @classmethod
    def load_builtin(cls) -> "PatientLanguageRenderer":
        path = files("continucare.care_agent.language_artifacts").joinpath(
            "patient_language_v1.json"
        )
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def render(self, template_id: str, **values: Any) -> str:
        try:
            template = self.policy["templates"][template_id]
        except KeyError as exc:
            raise ValueError(f"unknown patient language template {template_id!r}") from exc
        return template.format(**values)

    def option(self, option_id: str) -> str:
        try:
            return self.policy["clarification_options"][option_id]
        except KeyError as exc:
            raise ValueError(f"unknown clarification option {option_id!r}") from exc
