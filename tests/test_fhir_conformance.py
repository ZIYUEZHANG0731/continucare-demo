from __future__ import annotations

import os

import pytest

from continucare.adapters.mock_extractor import MockExtractor
from continucare.adapters.sqlite_store import SQLiteStore
from continucare.fhir.r4 import (
    FHIRValidationError,
    validate_official_json_schema,
    validate_r4_resource,
)
from continucare.fhir.questionnaires import build_free_text_questionnaire_response
from continucare.fhir.references import (
    validate_questionnaire_response_against_questionnaire,
)
from continucare.models import FollowUpMessage
from continucare.pathways import load_glp1_plan_definition, load_glp1_questionnaire


def example_resources():
    response = build_free_text_questionnaire_response(
        response_id="message-fhir-test",
        patient_id="P-DEMO-001",
        authored="2026-07-17T10:00:00+00:00",
        text="今天吐了一次，估计过去24小时喝水800毫升。",
    )
    message = FollowUpMessage(
        message_id=response["id"],
        patient_id="P-DEMO-001",
        message_text=response["item"][0]["answer"][0]["valueString"],
        submitted_at=response["authored"],
        processing_status="received",
    )
    observations = [item.resource for item in MockExtractor().extract(message).observations]
    return [
        load_glp1_questionnaire(),
        load_glp1_plan_definition(),
        response,
        *observations,
    ]


def test_all_clinical_resources_pass_strict_r4_models():
    for resource in example_resources():
        normalized = validate_r4_resource(resource)
        assert normalized["resourceType"] == resource["resourceType"]


def test_all_clinical_resources_pass_official_hl7_json_schema():
    schema_path = os.environ.get("FHIR_R4_SCHEMA_ZIP")
    if not schema_path:
        pytest.skip("set FHIR_R4_SCHEMA_ZIP to the official HL7 R4 schema archive")
    for resource in example_resources():
        validate_official_json_schema(resource, schema_path)


def test_unknown_fhir_element_is_rejected():
    resource = example_resources()[2]
    resource["inventedClinicalField"] = "not allowed"

    with pytest.raises(FHIRValidationError):
        validate_r4_resource(resource)


def test_missing_observation_status_is_rejected():
    resource = example_resources()[3]
    resource.pop("status")

    with pytest.raises(FHIRValidationError):
        validate_r4_resource(resource)


def test_questionnaire_response_must_match_questionnaire():
    questionnaire = load_glp1_questionnaire()
    response = example_resources()[2]
    response["item"][0]["linkId"] = "invented-question"

    with pytest.raises(FHIRValidationError, match="not present in Questionnaire"):
        validate_questionnaire_response_against_questionnaire(
            response, questionnaire
        )


def test_persistence_keeps_exact_fhir_resource(tmp_path):
    store = SQLiteStore(tmp_path / "fhir-round-trip.db")
    resource = example_resources()[2]

    store.save_message(
        FollowUpMessage(
            message_id=resource["id"],
            patient_id="P-DEMO-001",
            message_text=resource["item"][0]["answer"][0]["valueString"],
            submitted_at=resource["authored"],
            processing_status="received",
        )
    )
    store.save_questionnaire_response(resource)

    assert store.get_questionnaire_response(resource["id"]) == resource


def test_persistence_revalidates_mutated_observation(tmp_path):
    store = SQLiteStore(tmp_path / "fhir-write-boundary.db")
    resource = example_resources()[2]
    message = FollowUpMessage(
        message_id=resource["id"],
        patient_id="P-DEMO-001",
        message_text=resource["item"][0]["answer"][0]["valueString"],
        submitted_at=resource["authored"],
        processing_status="received",
    )
    store.save_message(message)
    store.save_questionnaire_response(resource)
    observation = MockExtractor().extract(message).observations[0]
    observation.resource["inventedClinicalField"] = True

    with pytest.raises(FHIRValidationError):
        store.save_observations([observation])
