from __future__ import annotations

from json import loads
from importlib.resources import files

import pytest

from continucare.record_points import (
    group_answer_rows,
    project_record_points,
    record_point_metadata,
    validate_questionnaire_contract,
    validate_record_point_selection,
)


def test_symptoms_have_record_point_specific_detail_rules():
    nausea = record_point_metadata("nausea_present_now")
    vomiting = record_point_metadata("vomiting_count_24h")
    abdominal = record_point_metadata("abdominal_pain_present_now")
    weight = record_point_metadata("body_weight")

    assert nausea["metricIds"] == ["nausea_present_now", "nausea_severity_current"]
    assert nausea["fields"][1]["role"] == "severity"
    assert nausea["fields"][1]["enableWhen"]["answer"] is True
    assert vomiting["fields"][0]["role"] == "frequency"
    assert abdominal["fields"][0]["role"] == "presence"
    assert weight["patientWebTask"] is True


def test_partial_nausea_selection_is_rejected_as_split_record_point():
    with pytest.raises(ValueError, match="完整记录要点"):
        validate_record_point_selection(["nausea_present_now"])


def test_legacy_partial_nausea_projection_is_normalized_to_atomic_record_point():
    projected = project_record_points(
        [{"metricId": "nausea_severity_current", "frequency": "daily"}]
    )

    assert projected[0]["metricIds"] == [
        "nausea_present_now",
        "nausea_severity_current",
    ]
    assert projected[0]["linkIds"] == ["nausea-present", "nausea-severity"]


def test_registry_conditions_match_the_governed_questionnaire():
    questionnaire = loads(
        files("continucare.pathways.data.fhir")
        .joinpath("glp1_followup_questionnaire_v1.json")
        .read_text(encoding="utf-8")
    )

    validate_questionnaire_contract(questionnaire)


def test_patient_review_groups_nausea_presence_and_severity_together():
    groups = group_answer_rows(
        [
            {"linkId": "nausea-present", "label": "恶心", "value": "有"},
            {"linkId": "nausea-severity", "label": "恶心程度", "value": "中度"},
            {"linkId": "abdominal-pain-present", "label": "腹痛", "value": "无"},
        ]
    )

    assert [item["recordPointId"] for item in groups] == ["nausea", "abdominal-pain"]
    assert groups[0]["summary"] == "有 · 中度"
