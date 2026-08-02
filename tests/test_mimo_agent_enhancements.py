from __future__ import annotations

import json

from continucare.adapters.sqlite_store import SQLiteStore
from continucare.agents.contracts import SemanticStatus
from continucare.care_agent import CareAgentService
from continucare.care_agent.mimo_adapter import MiMoSemanticAdapter
from continucare.care_agent.mimo_enhancements import (
    MiMoLanguageRewriter,
    MiMoSafetyCritic,
)
from continucare.care_agent.model_api import SemanticModelConfig
from continucare.care_engine import CareEngine
from continucare.demo_data import DEMO_PATIENT_ID


def _response(content, *, request_id="mimo-test", total_tokens=120):
    return {
        "id": request_id,
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": json.dumps(content, ensure_ascii=False),
                }
            }
        ],
        "usage": {
            "prompt_tokens": total_tokens - 20,
            "completion_tokens": 20,
            "total_tokens": total_tokens,
        },
    }


def _config(monkeypatch, *, safety=True, language=True):
    monkeypatch.setenv("MIMO_ENHANCEMENT_TEST_KEY", "sk-test-not-a-secret")
    return SemanticModelConfig(
        provider="xiaomi_mimo",
        model_name="mimo-v2.5",
        base_url="https://api.xiaomimimo.com/v1",
        api_key_env="MIMO_ENHANCEMENT_TEST_KEY",
        prompt_version="mimo-semantic-extraction-v2",
        safety_llm_enabled=safety,
        language_llm_enabled=language,
        timeout_seconds=2,
    )


def _service(tmp_path, adapter, *, critic=None, rewriter=None):
    store = SQLiteStore(tmp_path / "mimo-enhancements.db")
    engine = CareEngine(store)
    session = engine.start_or_resume(DEMO_PATIENT_ID)
    service = CareAgentService(
        store,
        care_engine=engine,
        model_adapter=adapter,
        safety_critic=critic,
        language_rewriter=rewriter,
    )
    return store, session, service


def test_safety_critic_recovers_supported_missing_dependent_value(
    monkeypatch, tmp_path
):
    config = _config(monkeypatch)
    extraction_responses = iter(
        [
            _response(
                {
                    "blocked": False,
                    "items": [
                        {
                            "link_id": "vomiting-count-24h",
                            "answer": 2,
                            "evidence_text": "过去24小时我吐了2次",
                            "subject": "patient",
                            "temporality": "explicit_24h",
                            "negated": False,
                        },
                        {
                            "link_id": "nausea-present",
                            "answer": True,
                            "evidence_text": "现在有点恶心",
                            "subject": "patient",
                            "temporality": "current",
                            "negated": False,
                        },
                    ],
                },
                request_id="extract-initial",
            ),
            _response(
                {
                    "blocked": False,
                    "items": [
                        {
                            "link_id": "nausea-severity",
                            "answer": "LA6752-5",
                            "evidence_text": "现在有点恶心",
                            "subject": "patient",
                            "temporality": "current",
                            "negated": False,
                        }
                    ],
                },
                request_id="extract-focused",
            ),
        ]
    )
    extraction_payloads = []

    def extraction_transport(url, headers, payload, timeout):
        extraction_payloads.append(payload)
        return next(extraction_responses)

    safety_payloads = []

    def safety_transport(url, headers, payload, timeout):
        safety_payloads.append(payload)
        review_input = json.loads(
            payload["messages"][1]["content"].split("review_input:\n", 1)[1]
        )
        reviews = [
            {
                "candidate_id": candidate["candidate_id"],
                "verdict": "pass",
                "evidence_status": "supported",
                "reason_codes": [],
                "explanation": "候选与患者原话一致。",
            }
            for candidate in review_input["surviving_candidates"]
        ]
        return _response(
            {
                "overall_verdict": "revise",
                "candidate_reviews": reviews,
                "missing_items": [
                    {
                        "link_id": "nausea-severity",
                        "status": "supported",
                        "evidence_text": "现在有点恶心",
                        "reason_codes": ["explicit_severity_word"],
                        "explanation": "“有点”明确表达了轻度。",
                    }
                ],
            },
            request_id="safety-critic",
            total_tokens=100,
        )

    language_payloads = []

    def language_transport(url, headers, payload, timeout):
        language_payloads.append(payload)
        requests = json.loads(
            payload["messages"][1]["content"].split("rewrite_input:\n", 1)[1]
        )
        return _response(
            {
                "items": [
                    {
                        "message_id": item["message_id"],
                        "rewritten_text": "辛苦您确认一下，" + item["canonical_text"],
                    }
                    for item in requests
                ]
            },
            request_id="language-rewriter",
            total_tokens=70,
        )

    adapter = MiMoSemanticAdapter(config, transport=extraction_transport)
    critic = MiMoSafetyCritic(config, transport=safety_transport)
    rewriter = MiMoLanguageRewriter(config, transport=language_transport)
    _, session, service = _service(
        tmp_path, adapter, critic=critic, rewriter=rewriter
    )

    interaction = service.analyze(
        session.session_id, "过去24小时我吐了2次，现在有点恶心。"
    )

    assert interaction.result.status == SemanticStatus.NEEDS_CONFIRMATION
    assert {item.link_id for item in interaction.result.candidates} == {
        "vomiting-count-24h",
        "nausea-present",
        "nausea-severity",
    }
    assert len(extraction_payloads) == 2
    focused_prompt = extraction_payloads[1]["messages"][0]["content"]
    assert "focused completeness retry" in focused_prompt
    assert '"link_id":"nausea-severity"' in focused_prompt
    assert '"link_id":"vomiting-count-24h"' not in focused_prompt
    assert len(safety_payloads) == 1
    assert "independent semantic Safety Critic" in (
        safety_payloads[0]["messages"][0]["content"]
    )
    assert len(language_payloads) == 1
    assert all(
        item.patient_message.startswith("辛苦您确认一下，")
        for item in interaction.result.candidates
    )
    stages = {trace.stage: trace for trace in interaction.result.stage_traces}
    assert stages["safety_hard_rules"].mode == "deterministic_rules"
    assert stages["safety_critic"].mode == "model_api:xiaomi_mimo"
    assert stages["focused_reextract"].status == "resolved"
    assert stages["language_rewrite"].details["rewritten_count"] == 3
    assert interaction.result.model_usage["total_tokens"] == 410


def test_safety_critic_never_sees_or_restores_hard_rejected_candidate(
    monkeypatch, tmp_path
):
    config = _config(monkeypatch, language=False)
    extraction = _response(
        {
            "blocked": False,
            "items": [
                {
                    "link_id": "nausea-present",
                    "answer": True,
                    "evidence_text": "吐了五次",
                    "subject": "patient",
                    "temporality": "current",
                    "negated": False,
                }
            ],
        }
    )
    seen_candidates = []

    def safety_transport(url, headers, payload, timeout):
        review_input = json.loads(
            payload["messages"][1]["content"].split("review_input:\n", 1)[1]
        )
        seen_candidates.extend(review_input["surviving_candidates"])
        return _response(
            {
                "overall_verdict": "block",
                "candidate_reviews": [],
                "missing_items": [
                    {
                        "link_id": "nausea-present",
                        "status": "supported",
                        "evidence_text": "吐了五次",
                        "reason_codes": ["possible_nausea"],
                        "explanation": "错误地把呕吐当作恶心。",
                    }
                ],
            }
        )

    adapter = MiMoSemanticAdapter(config, transport=lambda *args: extraction)
    critic = MiMoSafetyCritic(config, transport=safety_transport)
    _, session, service = _service(tmp_path, adapter, critic=critic)

    interaction = service.analyze(session.session_id, "我今天吐了五次。")

    assert seen_candidates == []
    assert interaction.result.candidates == []
    assert interaction.result.status == SemanticStatus.NO_MATCH
    assert interaction.result.missing_items == []
    assert interaction.result.clarifications == []
    assert interaction.result.candidate_issues[0].reason_codes == [
        "evidence_concept_mismatch"
    ]


def test_safety_critic_cannot_turn_other_person_text_into_missing_patient_fact(
    monkeypatch, tmp_path
):
    config = _config(monkeypatch, language=False)
    extraction_calls = []

    def extraction_transport(*_args):
        extraction_calls.append(True)
        return _response({"blocked": False, "items": [], "symptom_mentions": []})

    def safety_transport(url, headers, payload, timeout):
        return _response(
            {
                "overall_verdict": "revise",
                "candidate_reviews": [],
                "missing_items": [
                    {
                        "link_id": "vomiting-count-24h",
                        "status": "supported",
                        "evidence_text": "过去24小时吐了2次",
                        "reason_codes": ["explicit_count"],
                        "explanation": "文字中包含呕吐次数。",
                    }
                ],
            }
        )

    adapter = MiMoSemanticAdapter(config, transport=extraction_transport)
    critic = MiMoSafetyCritic(config, transport=safety_transport)
    _, session, service = _service(tmp_path, adapter, critic=critic)

    interaction = service.analyze(
        session.session_id, "我妈妈过去24小时吐了2次。"
    )

    assert interaction.result.status == SemanticStatus.NO_MATCH
    assert interaction.result.missing_items == []
    assert interaction.result.clarifications == []
    assert len(extraction_calls) == 1


def test_safety_critic_retries_one_invalid_json_contract(monkeypatch, tmp_path):
    config = _config(monkeypatch, language=False)
    extraction = _response(
        {
            "blocked": False,
            "items": [
                {
                    "link_id": "vomiting-count-24h",
                    "answer": 2,
                    "evidence_text": "过去24小时我吐了2次",
                    "subject": "patient",
                    "temporality": "explicit_24h",
                    "negated": False,
                }
            ],
        }
    )
    calls = []

    def safety_transport(url, headers, payload, timeout):
        calls.append(payload)
        if len(calls) == 1:
            return _response({"unexpected": True})
        review_input = json.loads(
            payload["messages"][1]["content"].split("review_input:\n", 1)[1]
        )
        candidate_id = review_input["surviving_candidates"][0]["candidate_id"]
        return _response(
            {
                "overall_verdict": "pass",
                "candidate_reviews": [
                    {
                        "candidate_id": candidate_id,
                        "verdict": "pass",
                        "evidence_status": "supported",
                        "evidence_text": "过去24小时我吐了2次",
                        "reason_codes": [],
                        "explanation": "候选有原文支持。",
                    }
                ],
                "missing_items": [],
            }
        )

    adapter = MiMoSemanticAdapter(config, transport=lambda *args: extraction)
    critic = MiMoSafetyCritic(config, transport=safety_transport)
    _, session, service = _service(tmp_path, adapter, critic=critic)

    interaction = service.analyze(
        session.session_id, "过去24小时我吐了2次。"
    )

    assert len(calls) == 2
    assert "STRICT RETRY" in calls[1]["messages"][0]["content"]
    trace = next(
        item
        for item in interaction.result.stage_traces
        if item.stage == "safety_critic"
    )
    assert trace.mode == "model_api:xiaomi_mimo"
    assert trace.details["attempt_count"] == 2
    assert trace.model_usage["total_tokens"] == 240


def test_safety_critic_failure_keeps_hard_rule_result(monkeypatch, tmp_path):
    config = _config(monkeypatch, language=False)
    extraction = _response(
        {
            "blocked": False,
            "items": [
                {
                    "link_id": "vomiting-count-24h",
                    "answer": 2,
                    "evidence_text": "过去24小时我吐了2次",
                    "subject": "patient",
                    "temporality": "explicit_24h",
                    "negated": False,
                }
            ],
        }
    )
    adapter = MiMoSemanticAdapter(config, transport=lambda *args: extraction)
    critic = MiMoSafetyCritic(
        config,
        transport=lambda *args: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    _, session, service = _service(tmp_path, adapter, critic=critic)

    interaction = service.analyze(
        session.session_id, "过去24小时我吐了2次。"
    )

    assert [item.link_id for item in interaction.result.candidates] == [
        "vomiting-count-24h"
    ]
    critic_trace = next(
        item
        for item in interaction.result.stage_traces
        if item.stage == "safety_critic"
    )
    assert critic_trace.status == "failed"
    assert critic_trace.mode == "deterministic_fallback"
    assert "safety_critic_fallback:RuntimeError" in (
        interaction.result.ignored_reasons
    )


def test_language_rewriter_rejection_falls_back_to_canonical_template(
    monkeypatch, tmp_path
):
    config = _config(monkeypatch, safety=False, language=True)
    extraction = _response(
        {
            "blocked": False,
            "items": [
                {
                    "link_id": "vomiting-count-24h",
                    "answer": 2,
                    "evidence_text": "过去24小时我吐了2次",
                    "subject": "patient",
                    "temporality": "explicit_24h",
                    "negated": False,
                }
            ],
        }
    )

    def bad_language_transport(url, headers, payload, timeout):
        requests = json.loads(
            payload["messages"][1]["content"].split("rewrite_input:\n", 1)[1]
        )
        return _response(
            {
                "items": [
                    {
                        "message_id": requests[0]["message_id"],
                        "rewritten_text": "不用担心，确认呕吐2次，对吗？",
                    }
                ]
            }
        )

    adapter = MiMoSemanticAdapter(config, transport=lambda *args: extraction)
    rewriter = MiMoLanguageRewriter(config, transport=bad_language_transport)
    _, session, service = _service(tmp_path, adapter, rewriter=rewriter)

    interaction = service.analyze(
        session.session_id, "过去24小时我吐了2次。"
    )

    candidate = interaction.result.candidates[0]
    assert candidate.patient_message == (
        "为了确保记录准确：过去24小时您一共呕吐了2次，对吗？"
    )
    trace = next(
        item
        for item in interaction.result.stage_traces
        if item.stage == "language_rewrite"
    )
    assert trace.details["rewritten_count"] == 0
    assert trace.details["template_fallback_count"] == 1
    assert set(trace.details["fallback_reasons"][candidate.candidate_id]) == {
        "immutable_fact_missing",
        "numeric_fact_changed",
        "controlled_fact_changed",
        "forbidden_patient_claim",
    }
