"""Provider-neutral model API seam and safe environment-driven factory."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

from continucare.agents.contracts import SemanticResult, SemanticTask
from continucare.agents.errors import ModelNotConfiguredError


@dataclass(frozen=True)
class SemanticModelConfig:
    """Model metadata; the secret value is resolved only at request time."""

    provider: str = "unconfigured"
    model_name: str | None = None
    base_url: str | None = None
    api_key_env: str = "CONTINUCARE_LLM_API_KEY"
    prompt_version: str = "semantic-extraction-v1"
    timeout_seconds: float = 8.0

    @classmethod
    def from_environment(cls) -> "SemanticModelConfig":
        from continucare.config import load_local_environment

        load_local_environment()
        return cls(
            provider=os.getenv("CONTINUCARE_LLM_PROVIDER", "unconfigured"),
            model_name=os.getenv("CONTINUCARE_LLM_MODEL") or None,
            base_url=os.getenv("CONTINUCARE_LLM_BASE_URL") or None,
            api_key_env=os.getenv(
                "CONTINUCARE_LLM_API_KEY_ENV", "CONTINUCARE_LLM_API_KEY"
            ),
            prompt_version=os.getenv(
                "CONTINUCARE_LLM_PROMPT_VERSION", "semantic-extraction-v1"
            ),
            timeout_seconds=float(os.getenv("CONTINUCARE_LLM_TIMEOUT_SECONDS", "8")),
        )

    @property
    def configured(self) -> bool:
        return bool(
            self.provider != "unconfigured"
            and self.model_name
            and self.base_url
            and os.getenv(self.api_key_env)
        )

    def api_key(self) -> str | None:
        return os.getenv(self.api_key_env) or None


class SemanticModelAdapter(Protocol):
    """Implement this protocol later for the chosen provider/model."""

    config: SemanticModelConfig

    @property
    def configured(self) -> bool: ...

    def extract(self, task: SemanticTask) -> SemanticResult: ...


class UnconfiguredModelAdapter:
    """Safe default that makes missing model configuration explicit."""

    def __init__(self, config: SemanticModelConfig | None = None):
        self.config = config or SemanticModelConfig.from_environment()

    @property
    def configured(self) -> bool:
        return False

    def extract(self, task: SemanticTask) -> SemanticResult:
        raise ModelNotConfiguredError(
            "semantic model adapter is not configured; local mock fallback is required"
        )


def build_model_adapter(
    config: SemanticModelConfig | None = None,
) -> SemanticModelAdapter:
    """Select only explicitly supported provider adapters."""

    config = config or SemanticModelConfig.from_environment()
    if config.provider in {"xiaomi_mimo", "mimo"}:
        from continucare.care_agent.mimo_adapter import MiMoSemanticAdapter

        return MiMoSemanticAdapter(config)
    return UnconfiguredModelAdapter(config)
