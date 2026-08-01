from __future__ import annotations

import pytest
from pydantic import ValidationError

from continucare.pathways import (
    ApprovalStatus,
    PathwayDefinition,
    PathwayStatus,
    load_builtin_pathways,
    load_glp1_observation_mapping,
    load_glp1_plan_definition,
    load_glp1_questionnaire,
)


def test_builtin_glp1_pathway_is_fhir_native_and_fail_closed():
    pathway = load_builtin_pathways().get("GLP1-14D")

    assert pathway.version == "1.0.0"
    assert pathway.fhir_version == "4.0.1"
    assert {item.resource_type for item in pathway.artifacts} == {
        "Questionnaire",
        "PlanDefinition",
    }
    assert pathway.clinical_rules == []
    assert pathway.evidence_document.endswith("glp1_14d_observation_evidence.md")
    assert pathway.observation_mapping_file.endswith(
        "glp1_followup_observation_mapping_v1.json"
    )


def test_builtin_pathway_is_explicitly_synthetic_and_not_approved():
    pathway = load_builtin_pathways().get("GLP1-14D")

    assert pathway.synthetic_only is True
    assert pathway.status == PathwayStatus.DRAFT
    assert pathway.approval.status == ApprovalStatus.NOT_REVIEWED


def test_questionnaire_has_standard_codes_answers_and_free_text():
    questionnaire = load_glp1_questionnaire()
    items = {item["linkId"]: item for item in questionnaire["item"]}

    assert items["vomiting-count-24h"]["code"][0]["code"] == "94070-0"
    assert items["fluid-intake-24h-estimated"]["code"][0]["code"] == "75301-2"
    assert {option["valueCoding"]["code"] for option in items["nausea-severity"]["answerOption"]} == {
        "LA6752-5",
        "LA6751-7",
        "LA6750-9",
    }
    assert items["free-text-report"]["type"] == "text"


def test_observation_mapping_is_versioned_and_only_targets_questionnaire_items():
    questionnaire = load_glp1_questionnaire()
    mapping = load_glp1_observation_mapping()

    assert mapping.questionnaire == f"{questionnaire['url']}|{questionnaire['version']}"
    assert {item.link_id for item in mapping.mappings} <= {
        item["linkId"] for item in questionnaire["item"]
    }


def test_plan_definition_only_collects_questionnaire():
    plan = load_glp1_plan_definition()

    assert plan["status"] == "draft"
    assert len(plan["action"]) == 1
    assert "definitionCanonical" in plan["action"][0]
    assert "condition" not in plan["action"][0]


def test_unknown_clinical_rule_evidence_source_is_rejected():
    pathway = load_builtin_pathways().get("GLP1-14D")
    data = pathway.model_dump(mode="json")
    data["clinical_rules"].append(
        {
            "rule_id": "UNAPPROVED-TEST",
            "title": "test",
            "description": "test only",
            "evidence_source_ids": ["missing-source"],
        }
    )

    with pytest.raises(ValidationError, match="unknown evidence sources"):
        PathwayDefinition.model_validate(data)


def test_active_pathway_requires_approvals():
    pathway = load_builtin_pathways().get("GLP1-14D")
    data = pathway.model_dump(mode="json")
    data["status"] = "active"

    with pytest.raises(ValidationError, match="cannot remain synthetic_only"):
        PathwayDefinition.model_validate(data)
