from __future__ import annotations

from continucare.adapters.mock_extractor import MockExtractor
from continucare.models import FollowUpMessage


def make_message(text: str) -> FollowUpMessage:
    return FollowUpMessage(
        message_id="message_test",
        patient_id="P-DEMO-001",
        message_text=text,
        submitted_at="2026-07-17T10:00:00+00:00",
        processing_status="received",
    )


def values_by_code(text: str):
    result = MockExtractor().extract(make_message(text))
    return {item.code: item for item in result.observations}


def test_l2_message_extracts_values_and_exact_evidence():
    text = "今天吐了一次，喝水也不太想喝。"

    observations = values_by_code(text)

    vomiting = observations["vomiting_count"]
    reduced = observations["fluid_intake_reduced"]
    assert vomiting.value == 1
    assert vomiting.evidence_text == "吐了一次"
    assert text[vomiting.evidence_start : vomiting.evidence_end] == "吐了一次"
    assert reduced.value is True
    assert reduced.evidence_text == "喝水也不太想喝"
    assert text[reduced.evidence_start : reduced.evidence_end] == "喝水也不太想喝"


def test_negated_vomiting_is_not_positive():
    observations = values_by_code("今天有点恶心，但是能正常喝水，没有吐。")

    assert "nausea" in observations
    assert "fluid_intake_normal" in observations
    assert "vomiting_count" not in observations


def test_historical_chest_pain_is_not_current_l4():
    observations = values_by_code("上个月胸痛过，现在没有。")

    assert "emergency_chest_pain" not in observations


def test_current_emergency_phrases_are_extracted_with_evidence():
    text = "我现在胸口很痛，还有点喘不过气。"

    observations = values_by_code(text)

    assert observations["emergency_chest_pain"].evidence_text == "胸口很痛"
    assert (
        observations["emergency_breathing_difficulty"].evidence_text
        == "喘不过气"
    )
