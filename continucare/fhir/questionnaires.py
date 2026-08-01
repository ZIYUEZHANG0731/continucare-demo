"""FHIR R4 QuestionnaireResponse construction for Layer-2 patient sessions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from continucare.fhir.r4 import FHIRValidationError, validate_r4_resource
from continucare.fhir.references import (
    answer_value,
    is_questionnaire_item_enabled,
    validate_questionnaire_response_against_questionnaire,
)

GLP1_QUESTIONNAIRE_CANONICAL = "urn:uuid:7f28b4c6-49a1-4cef-b6c3-37d24660f7a4"
GLP1_QUESTIONNAIRE_VERSION = "1.0.0"


def questionnaire_canonical(questionnaire: dict[str, Any]) -> str:
    canonical = questionnaire["url"]
    if questionnaire.get("version"):
        canonical += f"|{questionnaire['version']}"
    return canonical


def flatten_questionnaire_items(
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for item in items:
        flattened.append(item)
        flattened.extend(flatten_questionnaire_items(item.get("item", [])))
    return flattened


def visible_questionnaire_items(
    questionnaire: dict[str, Any], answers: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Return enabled answerable items in publication order."""

    all_items = flatten_questionnaire_items(questionnaire.get("item", []))
    item_by_link_id = {item["linkId"]: item for item in all_items}
    normalized_answers = {
        key: _coerce_answer(item_by_link_id[key], value)
        for key, value in answers.items()
        if key in item_by_link_id and not _is_empty(value)
    }
    return [
        item
        for item in all_items
        if item.get("type") not in {"display", "group"}
        and is_questionnaire_item_enabled(item, normalized_answers)
    ]


def build_questionnaire_response(
    *,
    questionnaire: dict[str, Any],
    response_id: str,
    patient_id: str,
    authored: str,
    answers: Mapping[str, Any],
    status: str = "completed",
) -> dict[str, Any]:
    """Build a response from linkId-keyed values using Questionnaire semantics.

    Input values are application-friendly: booleans, numbers, strings, a Coding
    dict (or an answerOption code), and a FHIR Quantity dict. The Questionnaire,
    not the caller, determines which FHIR ``value[x]`` element is emitted.
    """

    questionnaire = validate_r4_resource(
        questionnaire, expected_resource_type="Questionnaire"
    )
    known_items = {
        item["linkId"]: item
        for item in flatten_questionnaire_items(questionnaire.get("item", []))
    }
    unknown = set(answers) - set(known_items)
    if unknown:
        raise FHIRValidationError(
            "answers contain unknown Questionnaire linkIds: "
            + ", ".join(sorted(unknown))
        )

    normalized_answers: dict[str, dict[str, Any]] = {}
    for link_id, value in answers.items():
        if _is_empty(value):
            continue
        normalized_answers[link_id] = _coerce_answer(known_items[link_id], value)

    response_items = _build_response_items(
        questionnaire.get("item", []), normalized_answers
    )
    resource: dict[str, Any] = {
        "resourceType": "QuestionnaireResponse",
        "id": response_id,
        "questionnaire": questionnaire_canonical(questionnaire),
        "status": status,
        "subject": {"reference": f"Patient/{patient_id}"},
        "authored": authored,
        "author": {"reference": f"Patient/{patient_id}"},
        "source": {"reference": f"Patient/{patient_id}"},
    }
    if response_items:
        resource["item"] = response_items
    return validate_questionnaire_response_against_questionnaire(
        resource, questionnaire
    )


def build_free_text_questionnaire_response(
    *,
    response_id: str,
    patient_id: str,
    authored: str,
    text: str,
) -> dict[str, Any]:
    """Compatibility wrapper for the original free-text demo workflow."""

    from continucare.pathways.fhir_artifacts import load_glp1_questionnaire

    return build_questionnaire_response(
        questionnaire=load_glp1_questionnaire(),
        response_id=response_id,
        patient_id=patient_id,
        authored=authored,
        answers={"free-text-report": text},
    )


def questionnaire_response_answers(response: dict[str, Any]) -> dict[str, Any]:
    """Flatten a response to application-friendly values for rendering."""

    result: dict[str, Any] = {}
    for item in _flatten_response_items(response.get("item", [])):
        answers = item.get("answer", [])
        if not answers:
            continue
        values = [answer_value(answer)[1] for answer in answers]
        result[item["linkId"]] = values if len(values) > 1 else values[0]
    return result


def questionnaire_response_summary(
    response: dict[str, Any], questionnaire: dict[str, Any]
) -> tuple[str, dict[str, str]]:
    """Create a readable compatibility message and evidence fragments."""

    question_by_link_id = {
        item["linkId"]: item
        for item in flatten_questionnaire_items(questionnaire.get("item", []))
    }
    fragments: dict[str, str] = {}
    for link_id, value in questionnaire_response_answers(response).items():
        question = question_by_link_id[link_id]
        values = value if isinstance(value, list) else [value]
        display = "、".join(_display_answer(item) for item in values)
        fragments[link_id] = f"{question.get('text', link_id)}：{display}"
    return "\n".join(fragments.values()), fragments


def _build_response_items(
    questions: list[dict[str, Any]], answers: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    response_items: list[dict[str, Any]] = []
    for question in questions:
        if not is_questionnaire_item_enabled(question, answers):
            if question["linkId"] in answers:
                raise FHIRValidationError(
                    f"disabled Questionnaire item {question['linkId']!r} cannot be answered"
                )
            continue
        item: dict[str, Any] = {
            "linkId": question["linkId"],
            "text": question.get("text", question["linkId"]),
        }
        child_items = _build_response_items(question.get("item", []), answers)
        if child_items:
            item["item"] = child_items
        if question["linkId"] in answers:
            item["answer"] = [answers[question["linkId"]]]
        if item.get("answer") or item.get("item"):
            response_items.append(item)
    return response_items


def _coerce_answer(question: dict[str, Any], value: Any) -> dict[str, Any]:
    item_type = question["type"]
    if isinstance(value, list):
        if not question.get("repeats", False):
            raise FHIRValidationError(
                f"Questionnaire item {question['linkId']!r} does not allow repeated answers"
            )
        raise FHIRValidationError(
            "repeating answers are not enabled in the current Layer-2 renderer"
        )
    if item_type == "boolean":
        if type(value) is not bool:
            raise FHIRValidationError(f"{question['linkId']!r} requires a boolean")
        return {"valueBoolean": value}
    if item_type == "integer":
        if type(value) is not int:
            raise FHIRValidationError(f"{question['linkId']!r} requires an integer")
        return {"valueInteger": value}
    if item_type == "decimal":
        if type(value) not in {int, float}:
            raise FHIRValidationError(f"{question['linkId']!r} requires a decimal")
        return {"valueDecimal": float(value)}
    if item_type in {"string", "text"}:
        if not isinstance(value, str) or not value.strip():
            raise FHIRValidationError(f"{question['linkId']!r} requires text")
        return {"valueString": value.strip()}
    if item_type == "choice":
        coding = _choice_coding(question, value)
        return {"valueCoding": coding}
    if item_type == "quantity":
        if not isinstance(value, dict):
            raise FHIRValidationError(
                f"{question['linkId']!r} requires a FHIR Quantity"
            )
        if type(value.get("value")) not in {int, float}:
            raise FHIRValidationError(
                f"{question['linkId']!r} quantity requires a numeric value"
            )
        if not value.get("unit") or not value.get("code") or not value.get("system"):
            raise FHIRValidationError(
                f"{question['linkId']!r} quantity requires unit, system and code"
            )
        return {"valueQuantity": value}
    raise FHIRValidationError(
        f"Questionnaire item type {item_type!r} is not supported by the Layer-2 builder"
    )


def _choice_coding(question: dict[str, Any], value: Any) -> dict[str, Any]:
    options = [
        option["valueCoding"]
        for option in question.get("answerOption", [])
        if "valueCoding" in option
    ]
    if isinstance(value, str):
        matches = [option for option in options if option.get("code") == value]
        if len(matches) != 1:
            raise FHIRValidationError(
                f"{value!r} is not an allowed option for {question['linkId']!r}"
            )
        return matches[0]
    if isinstance(value, dict):
        for option in options:
            if _coding_identity(option) == _coding_identity(value):
                return option
    raise FHIRValidationError(
        f"{question['linkId']!r} requires one of its governed answerOption codings"
    )


def _flatten_response_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for item in items:
        flattened.append(item)
        flattened.extend(_flatten_response_items(item.get("item", [])))
        for answer in item.get("answer", []):
            flattened.extend(_flatten_response_items(answer.get("item", [])))
    return flattened


def _display_answer(value: Any) -> str:
    if type(value) is bool:
        return "是" if value else "否"
    if isinstance(value, dict) and "value" in value:
        return f"{value['value']} {value.get('unit', value.get('code', ''))}".strip()
    if isinstance(value, dict) and "code" in value:
        return str(value.get("display") or value["code"])
    return str(value)


def _coding_identity(coding: dict[str, Any]) -> tuple[Any, Any]:
    return coding.get("system"), coding.get("code")


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())
