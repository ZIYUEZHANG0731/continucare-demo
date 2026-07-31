from __future__ import annotations

from continucare.adapters.mock_extractor import MockExtractor
from continucare.models import FollowUpMessage


def make_message(text: str) -> FollowUpMessage:
    return FollowUpMessage(
        message_id="message-test",
        patient_id="P-DEMO-001",
        message_text=text,
        submitted_at="2026-07-17T10:00:00+00:00",
        processing_status="received",
    )


def values_by_code(text: str):
    result = MockExtractor().extract(make_message(text))
    return {item.code: item for item in result.observations}


def test_quantified_message_uses_loinc_ucum_and_exact_evidence():
    text = "今天吐了一次，估计过去24小时喝水800毫升。"

    observations = values_by_code(text)

    vomiting = observations["94070-0"]
    fluid = observations["75301-2"]
    assert vomiting.code_system == "http://loinc.org"
    assert vomiting.value == 1
    assert vomiting.unit == "/d"
    assert vomiting.evidence_text == "吐了一次"
    assert text[vomiting.evidence_start : vomiting.evidence_end] == "吐了一次"
    assert fluid.code_system == "http://loinc.org"
    assert fluid.value == 800
    assert fluid.unit == "mL/(24.h)"
    assert fluid.evidence_text == "喝水800毫升"
    assert text[fluid.evidence_start : fluid.evidence_end] == "喝水800毫升"
    assert vomiting.resource["derivedFrom"] == [
        {"reference": "QuestionnaireResponse/message-test"}
    ]
    assert "effectivePeriod" in vomiting.resource


def test_negated_vomiting_and_unquantified_fluid_are_not_structured():
    observations = values_by_code("今天有点恶心，但是能正常喝水，没有吐。")

    assert "422587007" in observations
    assert "75301-2" not in observations
    assert "94070-0" not in observations


def test_unapproved_emergency_phrases_are_not_structured_or_classified():
    observations = values_by_code("我现在胸口很痛，还有点喘不过气。")

    assert observations == {}


def test_historical_abdominal_pain_is_not_current():
    observations = values_by_code("上个月腹痛过，现在没有。")

    assert "21522001" not in observations


def test_current_abdominal_pain_uses_snomed_ct():
    observations = values_by_code("今天肚子疼。")

    abdominal_pain = observations["21522001"]
    assert abdominal_pain.code_system == "http://snomed.info/sct"
    assert abdominal_pain.value is True
    assert abdominal_pain.resource["valueBoolean"] is True
