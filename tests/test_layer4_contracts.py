from __future__ import annotations

import pytest
from pydantic import ValidationError

from continucare.layer4.contracts import (
    ApprovalDecision,
    ClinicalRuleDefinition,
    DoctorReview,
    DoctorReviewDecision,
    EvidenceReference,
    EvidenceRole,
    Layer4SummaryDraft,
    MemoryEvent,
    MemoryEventKind,
    ResourceReference,
    RevisionLink,
    RevisionRelationship,
    RuleApplicability,
    RuleApproval,
    RuleCondition,
    RuleLifecycle,
    RuleObservationInput,
    RuleOperator,
    RuleTaskAction,
    SummaryEvidenceItem,
    TimelineEvent,
)


NOW = "2026-08-02T10:00:00+00:00"


def evidence() -> EvidenceReference:
    return EvidenceReference(
        evidence_id="evidence-1",
        resource=ResourceReference(
            reference="Observation/observation-1", version_id="1"
        ),
        role=EvidenceRole.SUPPORTING,
        effective_start="2026-08-02T08:00:00+00:00",
        effective_end=NOW,
        evidence_text="合成数据：过去24小时呕吐2次。",
    )


def draft_rule(**updates) -> ClinicalRuleDefinition:
    data = {
        "rule_id": "synthetic-review-rule",
        "version": "1.0.0",
        "title": "合成人工复核规则",
        "description": "仅用于测试规则治理和 Task 合同。",
        "lifecycle": RuleLifecycle.DRAFT,
        "applicability": RuleApplicability(
            pathway_code="GLP1-14D",
            pathway_version="1.0.0",
            synthetic_only=True,
            population="合成比赛患者",
            region="DE-demo",
        ),
        "evidence_refs": [evidence()],
        "inputs": [
            RuleObservationInput(
                input_id="vomiting",
                code_system="http://loinc.org",
                code="94070-0",
                unit="/d",
                lookback_hours=24,
            )
        ],
        "conditions": [
            RuleCondition(
                input_id="vomiting",
                operator=RuleOperator.GTE,
                expected_value=2,
                unit="/d",
            )
        ],
        "action": RuleTaskAction(
            task_code_system="urn:continucare:task-code",
            task_code="human-review",
            task_code_display="人工复核",
            title="合成数据人工复核",
            description="请查看触发证据；不构成临床建议。",
            owner_role="nurse",
            sla_hours=4,
            deduplication_window_hours=24,
        ),
        "test_case_ids": ["synthetic-review-001"],
        "rollback_plan": "停用规则并保持 not_assessed。",
        "created_at": NOW,
    }
    data.update(updates)
    return ClinicalRuleDefinition.model_validate(data)


def test_draft_rule_contract_is_valid_but_active_requires_dual_approval():
    assert draft_rule().lifecycle == RuleLifecycle.DRAFT

    with pytest.raises(ValidationError, match="clinical and terminology approvals"):
        draft_rule(lifecycle=RuleLifecycle.ACTIVE)

    active = draft_rule(
        lifecycle=RuleLifecycle.ACTIVE,
        approval=RuleApproval(
            clinical_status=ApprovalDecision.APPROVED,
            terminology_status=ApprovalDecision.APPROVED,
            clinical_approver="Practitioner/clinical-reviewer",
            terminology_approver="Practitioner/terminology-reviewer",
            clinical_approved_at=NOW,
            terminology_approved_at=NOW,
        ),
    )
    assert active.approval.fully_approved() is True


def test_rule_conditions_cannot_reference_unknown_inputs():
    with pytest.raises(ValidationError, match="unknown inputs"):
        draft_rule(
            conditions=[
                RuleCondition(
                    input_id="invented",
                    operator=RuleOperator.EQ,
                    expected_value=True,
                )
            ]
        )


def test_rule_condition_unit_cannot_conflict_with_declared_input_unit():
    with pytest.raises(ValidationError, match="condition unit.*conflicts"):
        draft_rule(
            conditions=[
                RuleCondition(
                    input_id="vomiting",
                    operator=RuleOperator.GTE,
                    expected_value=2,
                    unit="mg",
                )
            ]
        )


def test_memory_timeline_revision_and_summary_contracts_are_evidence_bound():
    memory = MemoryEvent(
        event_id="memory-1",
        patient_id="P-DEMO-001",
        pathway_code="GLP1-14D",
        pathway_version="1.0.0",
        kind=MemoryEventKind.OBSERVATION,
        source=ResourceReference(
            reference="Observation/observation-1", version_id="1"
        ),
        source_status="final",
        effective_start="2026-08-02T08:00:00+00:00",
        effective_end=NOW,
        recorded_at=NOW,
        deduplication_key="P-DEMO-001|Observation/observation-1|1",
        evidence_refs=[evidence()],
    )
    timeline = TimelineEvent(
        timeline_event_id="timeline-1",
        patient_id=memory.patient_id,
        memory_event_id=memory.event_id,
        pathway_code=memory.pathway_code,
        pathway_version=memory.pathway_version,
        kind=memory.kind,
        title="患者报告",
        summary="合成患者报告过去24小时呕吐2次。",
        effective_start=memory.effective_start,
        effective_end=memory.effective_end,
        recorded_at=memory.recorded_at,
        source=memory.source,
        evidence_refs=memory.evidence_refs,
    )
    revision = RevisionLink(
        link_id="revision-1",
        patient_id=memory.patient_id,
        predecessor=ResourceReference(
            reference="Observation/observation-1", version_id="1"
        ),
        successor=ResourceReference(
            reference="Observation/observation-1", version_id="2"
        ),
        relationship=RevisionRelationship.CORRECTS,
        reason="患者更正。",
        actor_reference="Patient/P-DEMO-001",
        provenance_reference="Provenance/provenance-1",
        created_at=NOW,
    )
    summary = Layer4SummaryDraft(
        summary_id="summary-layer4-1",
        patient_id=memory.patient_id,
        period_start="2026-07-20T00:00:00+00:00",
        period_end=NOW,
        items=[
            SummaryEvidenceItem(
                item_id="summary-item-1",
                section="overview",
                text=timeline.summary,
                evidence_refs=[evidence()],
            )
        ],
        created_at=NOW,
    )

    assert timeline.evidence_refs[0].resource.reference == memory.source.reference
    assert revision.predecessor.version_id == "1"
    assert summary.items[0].evidence_refs


def test_doctor_modify_and_reject_decisions_require_explanation():
    with pytest.raises(ValidationError, match="modify review requires"):
        DoctorReview(
            review_id="review-1",
            summary_id="summary-1",
            summary_version="1",
            patient_id="P-DEMO-001",
            reviewer_reference="Practitioner/doctor",
            decision=DoctorReviewDecision.MODIFY,
            reviewed_at=NOW,
        )

    with pytest.raises(ValidationError, match="reject review requires"):
        DoctorReview(
            review_id="review-2",
            summary_id="summary-1",
            summary_version="1",
            patient_id="P-DEMO-001",
            reviewer_reference="Practitioner/doctor",
            decision=DoctorReviewDecision.REJECT,
            reviewed_at=NOW,
        )


def test_summary_generation_mode_requires_consistent_model_metadata():
    with pytest.raises(ValidationError, match="deterministic summary cannot"):
        Layer4SummaryDraft(
            summary_id="summary-mode-1",
            patient_id="P-DEMO-001",
            period_start="2026-08-01T00:00:00+00:00",
            period_end=NOW,
            model_name="invented-model",
            created_at=NOW,
        )

    with pytest.raises(ValidationError, match="llm_assisted summary requires"):
        Layer4SummaryDraft(
            summary_id="summary-mode-2",
            patient_id="P-DEMO-001",
            period_start="2026-08-01T00:00:00+00:00",
            period_end=NOW,
            generation_mode="llm_assisted",
            model_name="test-model",
            created_at=NOW,
        )
