"""Provider-neutral model API seam and safe environment-driven factory."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

from continucare.agents.contracts import CandidateSource, SemanticResult, SemanticTask
from continucare.agents.errors import ModelNotConfiguredError


MODEL_API_PROVIDERS = frozenset(
    {"xiaomi_mimo", "mimo", "volcengine_doubao", "doubao"}
)
MODEL_API_MODES = frozenset(
    {"model_api:xiaomi_mimo", "model_api:volcengine_doubao"}
)
MODEL_CANDIDATE_SOURCES = frozenset(
    {CandidateSource.MIMO, CandidateSource.DOUBAO}
)


@dataclass(frozen=True)
class SemanticModelConfig:
    """Model metadata; the secret value is resolved only at request time."""

    provider: str = "unconfigured"
    model_name: str | None = None
    base_url: str | None = None
    api_key_env: str = "CONTINUCARE_LLM_API_KEY"
    prompt_version: str = "mimo-semantic-extraction-v4"
    safety_llm_enabled: bool = False
    language_llm_enabled: bool = False
    summary_llm_enabled: bool = False
    safety_prompt_version: str = "mimo-safety-critic-v2"
    language_prompt_version: str = "mimo-language-rewrite-v1"
    summary_prompt_version: str = "mimo-summary-outline-v1"
    timeout_seconds: float = 8.0

    @classmethod
    def from_environment(cls) -> "SemanticModelConfig":
        from continucare.config import load_local_environment

        load_local_environment()
        provider = os.getenv("CONTINUCARE_LLM_PROVIDER", "unconfigured")
        doubao = provider in {"volcengine_doubao", "doubao"}
        return cls(
            provider=provider,
            model_name=os.getenv("CONTINUCARE_LLM_MODEL") or None,
            base_url=os.getenv("CONTINUCARE_LLM_BASE_URL") or None,
            api_key_env=os.getenv(
                "CONTINUCARE_LLM_API_KEY_ENV", "CONTINUCARE_LLM_API_KEY"
            ),
            prompt_version=os.getenv(
                "CONTINUCARE_LLM_PROMPT_VERSION",
                "doubao-semantic-extraction-v1"
                if doubao
                else "mimo-semantic-extraction-v4",
            ),
            safety_llm_enabled=_env_bool("CONTINUCARE_USE_SAFETY_LLM"),
            language_llm_enabled=_env_bool("CONTINUCARE_USE_LANGUAGE_LLM"),
            summary_llm_enabled=_env_bool("CONTINUCARE_USE_SUMMARY_LLM"),
            safety_prompt_version=os.getenv(
                "CONTINUCARE_SAFETY_PROMPT_VERSION",
                "doubao-safety-critic-v1" if doubao else "mimo-safety-critic-v2",
            ),
            language_prompt_version=os.getenv(
                "CONTINUCARE_LANGUAGE_PROMPT_VERSION",
                "doubao-language-rewrite-v1"
                if doubao
                else "mimo-language-rewrite-v1",
            ),
            summary_prompt_version=os.getenv(
                "CONTINUCARE_SUMMARY_PROMPT_VERSION",
                "doubao-summary-outline-v1" if doubao else "mimo-summary-outline-v1",
            ),
            timeout_seconds=float(
                os.getenv("CONTINUCARE_LLM_TIMEOUT_SECONDS", "60" if doubao else "8")
            ),
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


def provider_request_options(config: SemanticModelConfig) -> dict[str, object]:
    """Return provider-specific request controls that preserve JSON output budget."""

    if config.provider in {"volcengine_doubao", "doubao"}:
        return {"thinking": {"type": "disabled"}}
    return {}


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
    if config.provider in MODEL_API_PROVIDERS:
        from continucare.care_agent.mimo_adapter import MiMoSemanticAdapter

        return MiMoSemanticAdapter(config)
    if config.provider == "feishu_aily":
        from continucare.adapters.factory import AdapterFactory

        factory = AdapterFactory()
        if factory.statuses()["aily"].external_calls_allowed:
            return factory.build_aily()
    return UnconfiguredModelAdapter(config)


def _env_bool(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}
