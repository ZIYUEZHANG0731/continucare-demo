from __future__ import annotations

from continucare.agents.contracts import CandidateSource
from continucare.care_agent.mimo_adapter import (
    MiMoSemanticAdapter,
    _official_doubao_base_url,
)
from continucare.care_agent.mimo_enhancements import _call_mimo
from continucare.care_agent.model_api import (
    SemanticModelConfig,
    build_model_adapter,
    provider_request_options,
)
from continucare.layer4.summary_agent import (
    MiMoControlledSummaryAdapter,
    build_summary_model_adapter,
)


def _config() -> SemanticModelConfig:
    return SemanticModelConfig(
        provider="volcengine_doubao",
        model_name="doubao-seed-2-0-lite-260215",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key_env="ARK_TEST_API_KEY",
        safety_llm_enabled=True,
        language_llm_enabled=True,
        summary_llm_enabled=True,
    )


def test_doubao_factory_uses_official_ark_configuration(monkeypatch):
    monkeypatch.setenv("ARK_TEST_API_KEY", "ark-test-not-a-real-secret")

    adapter = build_model_adapter(_config())

    assert isinstance(adapter, MiMoSemanticAdapter)
    assert adapter.configured is True
    assert adapter.mode == "model_api:volcengine_doubao"
    assert adapter.candidate_source == CandidateSource.DOUBAO
    assert adapter.VERSION == "volcengine-doubao-openai-v1"

    summary = build_summary_model_adapter(_config())
    assert isinstance(summary, MiMoControlledSummaryAdapter)
    assert summary.configured is True


def test_doubao_auxiliary_call_uses_chat_completions_json_mode(monkeypatch):
    monkeypatch.setenv("ARK_TEST_API_KEY", "ark-test-not-a-real-secret")
    captured = {}

    def transport(url, headers, payload, timeout):
        captured.update(
            {"url": url, "headers": headers, "payload": payload, "timeout": timeout}
        )
        return {"choices": [{"message": {"content": "{}"}}]}

    response = _call_mimo(
        _config(),
        transport,
        messages=[{"role": "user", "content": "synthetic test"}],
        max_completion_tokens=64,
    )

    assert response["choices"][0]["message"]["content"] == "{}"
    assert captured["url"] == (
        "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
    )
    assert captured["headers"]["Authorization"] == (
        "Bearer ark-test-not-a-real-secret"
    )
    assert captured["payload"]["model"] == "doubao-seed-2-0-lite-260215"
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert captured["payload"]["max_completion_tokens"] == 64
    assert captured["payload"]["thinking"] == {"type": "disabled"}


def test_doubao_disables_thinking_without_changing_mimo_requests():
    assert provider_request_options(_config()) == {
        "thinking": {"type": "disabled"}
    }
    mimo = SemanticModelConfig(provider="xiaomi_mimo")
    assert provider_request_options(mimo) == {}


def test_doubao_base_url_rejects_credentials_and_lookalike_hosts():
    assert _official_doubao_base_url(
        "https://ark.cn-beijing.volces.com/api/v3"
    )
    assert not _official_doubao_base_url(
        "https://ark.cn-beijing.volces.com.evil.example/api/v3"
    )
    assert not _official_doubao_base_url(
        "https://user:pass@ark.cn-beijing.volces.com/api/v3"
    )
    assert not _official_doubao_base_url(
        "https://ark.cn-beijing.volces.com/api/v3/other"
    )


def test_doubao_environment_defaults_are_provider_specific(monkeypatch):
    import continucare.config

    monkeypatch.setattr(continucare.config, "load_local_environment", lambda: None)
    monkeypatch.setenv("CONTINUCARE_LLM_PROVIDER", "volcengine_doubao")
    for name in (
        "CONTINUCARE_LLM_PROMPT_VERSION",
        "CONTINUCARE_SAFETY_PROMPT_VERSION",
        "CONTINUCARE_LANGUAGE_PROMPT_VERSION",
        "CONTINUCARE_SUMMARY_PROMPT_VERSION",
        "CONTINUCARE_LLM_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)

    config = SemanticModelConfig.from_environment()

    assert config.prompt_version == "doubao-semantic-extraction-v1"
    assert config.safety_prompt_version == "doubao-safety-critic-v1"
    assert config.language_prompt_version == "doubao-language-rewrite-v1"
    assert config.summary_prompt_version == "doubao-summary-outline-v1"
    assert config.timeout_seconds == 60
