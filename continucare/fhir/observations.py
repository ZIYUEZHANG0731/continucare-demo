"""Builders for patient-reported FHIR R4 Observation resources."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from continucare.fhir.r4 import validate_r4_resource
from continucare.fhir.terminology import (
    FHIR_OBSERVATION_CATEGORY,
    UCUM,
    CodingDefinition,
)


def build_patient_reported_observation(
    *,
    patient_id: str,
    questionnaire_response_id: str,
    effective_time: str,
    code: CodingDefinition,
    value_element: str,
    value: Any,
    observation_id: str | None = None,
    effective_period_hours: int | None = None,
) -> dict[str, Any]:
    """Build a base FHIR R4 Observation and fail closed on invalid output."""

    if not value_element.startswith("value"):
        raise ValueError("FHIR Observation value choice must use a value[x] JSON element")
    observation: dict[str, Any] = {
        "resourceType": "Observation",
        "id": observation_id or f"observation-{uuid4().hex}",
        "status": "final",
        "category": [
            {
                "coding": [
                    {
                        "system": FHIR_OBSERVATION_CATEGORY,
                        "code": "survey",
                        "display": "Survey",
                    }
                ]
            }
        ],
        "code": {"coding": [code.as_fhir()], "text": code.display},
        "subject": {"reference": f"Patient/{patient_id}"},
        "issued": effective_time,
        "performer": [{"reference": f"Patient/{patient_id}"}],
        value_element: value,
        "derivedFrom": [
            {"reference": f"QuestionnaireResponse/{questionnaire_response_id}"}
        ],
    }
    if effective_period_hours is None:
        observation["effectiveDateTime"] = effective_time
    else:
        end = datetime.fromisoformat(effective_time)
        start = end - timedelta(hours=effective_period_hours)
        observation["effectivePeriod"] = {
            "start": start.isoformat(),
            "end": end.isoformat(),
        }
    return validate_r4_resource(observation, expected_resource_type="Observation")


def per_day_quantity(value: int | float, *, unit: str) -> dict[str, Any]:
    return {"value": value, "unit": unit, "system": UCUM, "code": "/d"}


def millilitres_per_24_hours(value: int | float) -> dict[str, Any]:
    return {
        "value": value,
        "unit": "mL/24 hours",
        "system": UCUM,
        "code": "mL/(24.h)",
    }
