"""FHIR R4 QuestionnaireResponse construction for patient submissions."""

from __future__ import annotations

from typing import Any

from continucare.fhir.r4 import validate_r4_resource

GLP1_QUESTIONNAIRE_CANONICAL = "urn:uuid:7f28b4c6-49a1-4cef-b6c3-37d24660f7a4"
GLP1_QUESTIONNAIRE_VERSION = "1.0.0"


def build_free_text_questionnaire_response(
    *,
    response_id: str,
    patient_id: str,
    authored: str,
    text: str,
) -> dict[str, Any]:
    resource = {
        "resourceType": "QuestionnaireResponse",
        "id": response_id,
        "questionnaire": (
            f"{GLP1_QUESTIONNAIRE_CANONICAL}|{GLP1_QUESTIONNAIRE_VERSION}"
        ),
        "status": "completed",
        "subject": {"reference": f"Patient/{patient_id}"},
        "authored": authored,
        "author": {"reference": f"Patient/{patient_id}"},
        "source": {"reference": f"Patient/{patient_id}"},
        "item": [
            {
                "linkId": "free-text-report",
                "text": "请描述今天的身体状态",
                "answer": [{"valueString": text}],
            }
        ],
    }
    return validate_r4_resource(
        resource, expected_resource_type="QuestionnaireResponse"
    )
