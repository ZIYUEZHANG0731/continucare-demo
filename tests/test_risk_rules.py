from __future__ import annotations

from continucare.adapters.mock_extractor import MockExtractor
from continucare.models import FollowUpMessage
from continucare.services.risk_rules import evaluate_risk


def observations(text: str):
    message = FollowUpMessage(
        message_id="message_test",
        patient_id="P-DEMO-001",
        message_text=text,
        submitted_at="2026-07-17T10:00:00+00:00",
        processing_status="received",
    )
    return MockExtractor().extract(message).observations


def test_normal_message_does_not_trigger_l2():
    decision = evaluate_risk(
        observations("今天有点恶心，但是能正常喝水，没有吐。")
    )

    assert decision.severity == "L0"
    assert decision.create_alert is False


def test_l2_rule_requires_both_conditions():
    vomiting_only = evaluate_risk(observations("今天吐了一次。"))
    fluid_only = evaluate_risk(observations("今天不想喝水。"))
    both = evaluate_risk(observations("今天吐了一次，喝水也不太想喝。"))

    assert vomiting_only.severity == "L0"
    assert fluid_only.severity == "L0"
    assert both.severity == "L2"
    assert both.trigger_rule_id == "GLP1-002"


def test_current_emergency_phrase_triggers_l4():
    decision = evaluate_risk(observations("我现在胸口很痛，还有点喘不过气。"))

    assert decision.severity == "L4"
    assert decision.trigger_rule_id == "EMERGENCY-001"
    assert decision.owner_role == "on_call_clinician"


def test_historical_emergency_phrase_does_not_trigger_l4():
    decision = evaluate_risk(observations("上个月胸痛过，现在没有。"))

    assert decision.severity == "L0"

