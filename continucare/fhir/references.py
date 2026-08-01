"""Cross-resource and Questionnaire semantic validation."""

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


def answer_value(answer: dict[str, Any]) -> tuple[str, Any]:
    populated = [(key, value) for key, value in answer.items() if key.startswith("value")]
    if len(populated) != 1:
        raise FHIRValidationError("QuestionnaireResponse answer must contain one value[x]")
    return populated[0]


def is_questionnaire_item_enabled(
    question: dict[str, Any], answers: dict[str, dict[str, Any]]
) -> bool:
    conditions = question.get("enableWhen", [])
    if not conditions:
        return True
    results = [_evaluate_enable_when(condition, answers) for condition in conditions]
    behavior = question.get("enableBehavior")
    if len(results) > 1 and behavior not in {"all", "any"}:
        raise FHIRValidationError(
            f"Questionnaire item {question['linkId']!r} requires enableBehavior"
        )
    return any(results) if behavior == "any" else all(results)


def validate_questionnaire_response_against_questionnaire(
    response: dict[str, Any], questionnaire: dict[str, Any]
) -> dict[str, Any]:
    """Validate canonical, nesting, types, choices, required and enableWhen."""

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
        item["linkId"]: item
        for item in _flatten_questionnaire_items(normalized_questionnaire.get("item", []))
    }
    response_items = _flatten_response_items(normalized_response.get("item", []))
    response_by_link_id: dict[str, dict[str, Any]] = {}
    for item in response_items:
        link_id = item["linkId"]
        if link_id in response_by_link_id:
            raise FHIRValidationError(
                f"QuestionnaireResponse contains duplicate linkId {link_id!r}"
            )
        response_by_link_id[link_id] = item
        question = questions.get(link_id)
        if question is None:
            raise FHIRValidationError(
                f"QuestionnaireResponse linkId {link_id!r} is not present in Questionnaire"
            )
        _validate_response_item(item, question)

    answer_lookup = {
        link_id: item["answer"][0]
        for link_id, item in response_by_link_id.items()
        if item.get("answer")
    }
    completed = normalized_response.get("status") in {"completed", "amended"}
    for link_id, question in questions.items():
        enabled = is_questionnaire_item_enabled(question, answer_lookup)
        answered = bool(response_by_link_id.get(link_id, {}).get("answer"))
        if answered and not enabled:
            raise FHIRValidationError(
                f"disabled Questionnaire item {link_id!r} cannot be answered"
            )
        if completed and enabled and question.get("required") and not answered:
            raise FHIRValidationError(
                f"required Questionnaire item {link_id!r} is missing an answer"
            )
    return normalized_response


def _validate_response_item(
    response_item: dict[str, Any], question: dict[str, Any]
) -> None:
    expected = _ANSWER_ELEMENT_BY_QUESTION_TYPE.get(question["type"])
    answers = response_item.get("answer", [])
    if expected is None:
        if answers:
            raise FHIRValidationError(
                f"Questionnaire item type {question['type']!r} cannot contain answers"
            )
        return
    if len(answers) > 1 and not question.get("repeats", False):
        raise FHIRValidationError(
            f"Questionnaire item {question['linkId']!r} does not allow repeated answers"
        )
    allowed = {expected} if isinstance(expected, str) else set(expected)
    for answer in answers:
        element, value = answer_value(answer)
        if element not in allowed:
            raise FHIRValidationError(
                f"QuestionnaireResponse linkId {question['linkId']!r} must use "
                f"{', '.join(sorted(allowed))}"
            )
        if element == "valueCoding":
            _validate_choice(value, question)


def _validate_choice(coding: dict[str, Any], question: dict[str, Any]) -> None:
    options = [
        option["valueCoding"]
        for option in question.get("answerOption", [])
        if "valueCoding" in option
    ]
    if not options:
        return
    identity = coding.get("system"), coding.get("code")
    if identity not in {(item.get("system"), item.get("code")) for item in options}:
        raise FHIRValidationError(
            f"QuestionnaireResponse linkId {question['linkId']!r} uses an "
            "answer outside the governed answerOption list"
        )


def _evaluate_enable_when(
    condition: dict[str, Any], answers: dict[str, dict[str, Any]]
) -> bool:
    source = answers.get(condition["question"])
    operator = condition["operator"]
    if operator == "exists":
        expected = condition.get("answerBoolean", True)
        return (source is not None) is expected
    if source is None:
        return False
    _, actual = answer_value(source)
    expected_values = [
        value for key, value in condition.items() if key.startswith("answer")
    ]
    if len(expected_values) != 1:
        raise FHIRValidationError("enableWhen must contain one answer[x]")
    expected = expected_values[0]
    actual = _comparable(actual)
    expected = _comparable(expected)
    operations = {
        "=": lambda: actual == expected,
        "!=": lambda: actual != expected,
        ">": lambda: actual > expected,
        "<": lambda: actual < expected,
        ">=": lambda: actual >= expected,
        "<=": lambda: actual <= expected,
    }
    if operator not in operations:
        raise FHIRValidationError(f"unsupported enableWhen operator {operator!r}")
    try:
        return operations[operator]()
    except TypeError as exc:
        raise FHIRValidationError("enableWhen compares incompatible answer types") from exc


def _comparable(value: Any) -> Any:
    if isinstance(value, dict) and "code" in value:
        return value.get("system"), value.get("code")
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    return value


def _flatten_questionnaire_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for item in items:
        flattened.append(item)
        flattened.extend(_flatten_questionnaire_items(item.get("item", [])))
    return flattened


def _flatten_response_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for item in items:
        flattened.append(item)
        flattened.extend(_flatten_response_items(item.get("item", [])))
        for answer in item.get("answer", []):
            flattened.extend(_flatten_response_items(answer.get("item", [])))
    return flattened
