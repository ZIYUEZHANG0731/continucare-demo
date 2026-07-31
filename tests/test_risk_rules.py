from __future__ import annotations

import pytest

from continucare.adapters.mock_extractor import MockExtractor
from continucare.models import FollowUpMessage
from continucare.services.risk_rules import evaluate_risk


def observations(text: str):
    message = FollowUpMessage(
        message_id="message-test",
        patient_id="P-DEMO-001",
        message_text=text,
        submitted_at="2026-07-17T10:00:00+00:00",
        processing_status="received",
    )
    return MockExtractor().extract(message).observations


@pytest.mark.parametrize(
    "text",
    [
        "今天有点恶心，但是能正常喝水，没有吐。",
        "今天吐了一次，估计过去24小时喝水800毫升。",
        "我现在胸口很痛，还有点喘不过气。",
        "今天肚子疼。",
    ],
)
def test_no_text_triggers_unapproved_clinical_classification(text):
    decision = evaluate_risk(observations(text))

    assert decision.severity == "not_assessed"
    assert decision.create_alert is False
    assert decision.trigger_rule_id is None
