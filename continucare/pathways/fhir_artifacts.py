"""Load and validate versioned FHIR R4 artifacts shipped with a pathway."""

from __future__ import annotations

import json
from importlib.resources import files

from continucare.fhir.r4 import validate_r4_resource


def load_glp1_questionnaire() -> dict:
    return _load("glp1_followup_questionnaire_v1.json", "Questionnaire")


def load_glp1_plan_definition() -> dict:
    return _load("glp1_followup_plan_definition_v1.json", "PlanDefinition")


def load_fhir_artifact(file_name: str, resource_type: str) -> dict:
    """Load a governed built-in artifact by its manifest reference."""

    return _load(file_name, resource_type)


def _load(name: str, resource_type: str) -> dict:
    data_dir = files("continucare.pathways.data.fhir")
    resource = json.loads(data_dir.joinpath(name).read_text("utf-8"))
    return validate_r4_resource(resource, expected_resource_type=resource_type)
