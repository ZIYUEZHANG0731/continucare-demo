"""Governed deterministic mappings from Questionnaire answers to Observations."""

from __future__ import annotations

from importlib.resources import files
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ObservationMapping(StrictModel):
    link_id: str
    kind: Literal[
        "boolean",
        "coded_choice",
        "count_per_day",
        "millilitres_per_24_hours",
        "kilograms",
    ]
    positive_only: bool = False
    effective_period_hours: int | None = Field(default=None, gt=0)
    accepted_quantity_unit_codes: list[str] = Field(default_factory=list)
    metric_id: str | None = None
    evidence_claim_ids: list[str] = Field(default_factory=list)


class ObservationMappingPolicy(StrictModel):
    pathway_code: str
    pathway_version: str
    questionnaire: str
    mappings: list[ObservationMapping] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_link_ids(self) -> "ObservationMappingPolicy":
        link_ids = [item.link_id for item in self.mappings]
        if len(link_ids) != len(set(link_ids)):
            raise ValueError("observation mapping contains duplicate linkIds")
        for item in self.mappings:
            if item.kind != "kilograms":
                continue
            expected_claim = (
                "goal-rule:continucare-followup-goals-v1.0.0:"
                "nhc-obesity-anthropometry-weight-bmi-2024-001"
            )
            if (
                item.link_id != "body-weight"
                or item.metric_id != "body_weight"
                or item.accepted_quantity_unit_codes != ["kg"]
                or item.evidence_claim_ids != [expected_claim]
            ):
                raise ValueError("body-weight mapping is not the governed release")
        return self


def load_observation_mapping(file_name: str) -> ObservationMappingPolicy:
    resource = files("continucare.pathways")
    for part in file_name.split("/"):
        resource = resource.joinpath(part)
    return ObservationMappingPolicy.model_validate_json(
        resource.read_text("utf-8")
    )


def load_glp1_observation_mapping() -> ObservationMappingPolicy:
    return load_observation_mapping(
        "mapping_artifacts/glp1_followup_observation_mapping_v1.json"
    )
