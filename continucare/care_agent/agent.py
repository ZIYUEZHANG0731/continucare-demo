"""Care Agent extraction with a deterministic local fallback."""

from __future__ import annotations

from continucare.agents.contracts import SemanticTask
from continucare.care_agent.mock_semantics import DeterministicSemanticMock
from continucare.care_agent.model_api import (
    SemanticModelAdapter,
    build_model_adapter,
)
from continucare.care_agent.safety import instruction_like_text


class CareSemanticAgent:
    VERSION = "care-agent-v2"

    def __init__(
        self,
        *,
        model_adapter: SemanticModelAdapter | None = None,
        fallback: DeterministicSemanticMock | None = None,
    ):
        self.model_adapter = model_adapter or build_model_adapter()
        self.fallback = fallback or DeterministicSemanticMock()

    def analyze(self, task: SemanticTask):
        # Never send recognized instruction-injection text to an external model.
        if instruction_like_text(task.message_text):
            draft = self.fallback.extract(task)
            draft.ignored_reasons.append("safety_preflight_blocked_external_call")
            return draft
        if self.model_adapter.configured:
            try:
                draft = self.model_adapter.extract(task)
                draft = draft.model_copy(update={"care_agent_version": self.VERSION})
            except Exception as exc:  # provider adapters must fail closed to local mock
                draft = self.fallback.extract(task)
                draft.ignored_reasons.append(
                    f"model_adapter_error_fallback:{type(exc).__name__}"
                )
        else:
            draft = self.fallback.extract(task)
            draft.ignored_reasons.append("model_adapter_not_configured_fallback")
        return draft

    def extract_focused(self, task: SemanticTask, link_ids: list[str]):
        """Retry only Safety-Critic identified omissions; never invent locally."""

        if not self.model_adapter.configured or not hasattr(
            self.model_adapter, "extract_focused"
        ):
            return None
        return self.model_adapter.extract_focused(task, link_ids)
