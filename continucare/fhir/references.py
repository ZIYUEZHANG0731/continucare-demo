"""Cross-resource checks not covered by base FHIR JSON parsing."""

from __future__ import annotations

from typing import Any

from continucare.fhir.r4 import FHIRValidationError, validate_r4_resource


_ANSWER_ELEMENT_BY_QUESTION_TYPE = {
    "boolean": "valueBoolean",
    "decimal": "valueDecimal",
    "integer": "valueInteger",
    "date": "valueDate",
    "dateTime": "valueDateTime",
    "time": "valueTime",
    "string": "valueString",
    "text": "valueString",
    "url": "valueUri",
    "choice": "valueCoding",
    "open-choice": ("valueCoding", "valueString"),
    "attachment": "valueAttachment",
    "reference": "valueReference",
    "quantity": "valueQuantity",
}


def validate_questionnaire_response_against_questionnaire(
    response: dict[str, Any], questionnaire: dict[str, Any]
) -> dict[str, Any]:
    """Validate canonical, linkIds and answer choice types for a flat response.

    Base FHIR validation deliberately does not validate a response against the
    content of its referenced Questionnaire. This project currently publishes a
    flat Questionnaire; nested/group/repeating items must receive dedicated
    traversal tests before they are enabled.
    """

    normalized_response = validate_r4_resource(
        response, expected_resource_type="QuestionnaireResponse"
    )
    normalized_questionnaire = validate_r4_resource(
        questionnaire, expected_resource_type="Questionnaire"
    )
    expected_canonical = normalized_questionnaire["url"]
    if normalized_questionnaire.get("version"):
        expected_canonical += f"|{normalized_questionnaire['version']}"
    if normalized_response.get("questionnaire") != expected_canonical:
        raise FHIRValidationError(
            "QuestionnaireResponse.questionnaire does not match the governed "
            "Questionnaire canonical and version"
        )

    questions = {
        item["linkId"]: item for item in normalized_questionnaire.get("item", [])
    }
    seen_link_ids: set[str] = set()
    for item in normalized_response.get("item", []):
        link_id = item["linkId"]
        if link_id in seen_link_ids:
            raise FHIRValidationError(
                f"QuestionnaireResponse contains duplicate linkId {link_id!r}"
            )
        seen_link_ids.add(link_id)
        question = questions.get(link_id)
        if question is None:
            raise FHIRValidationError(
                f"QuestionnaireResponse linkId {link_id!r} is not present in Questionnaire"
            )
        expected = _ANSWER_ELEMENT_BY_QUESTION_TYPE.get(question["type"])
        if expected is None:
            if item.get("answer"):
                raise FHIRValidationError(
                    f"Questionnaire item type {question['type']!r} cannot contain answers"
                )
            continue
        allowed = {expected} if isinstance(expected, str) else set(expected)
        for answer in item.get("answer", []):
            populated = {key for key in answer if key.startswith("value")}
            if len(populated) != 1 or not populated <= allowed:
                raise FHIRValidationError(
                    f"QuestionnaireResponse linkId {link_id!r} must use "
                    f"{', '.join(sorted(allowed))}"
                )
    return normalized_response
