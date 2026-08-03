from __future__ import annotations

import sqlite3
from copy import deepcopy

import pytest

from continucare.layer4.contracts import (
    ClinicalRuleDefinition,
    DoctorReview,
    DoctorReviewDecision,
    Layer4SummaryDraft,
    MemoryEvent,
    MemoryEventKind,
    ResourceReference,
    RevisionLink,
    RevisionRelationship,
    TimelineEvent,
)
from continucare.layer4.fhir import (
    build_communication,
    build_provenance,
    build_workflow_task,
)
from continucare.layer4.storage import Layer4SQLiteStore
from continucare.db import connect


PATIENT_ID = "P-DEMO-001"
NOW = "2026-08-02T10:00:00+00:00"


def resources():
    communication = build_communication(
        patient_id=PATIENT_ID,
        content_text="合成随访确认。",
        sender_reference=f"Patient/{PATIENT_ID}",
        recipient_references=["PractitionerRole/nurse"],
        sent_at=NOW,
        communication_id="communication-layer4-1",
    )
    task = build_workflow_task(
        patient_id=PATIENT_ID,
        rule_id="synthetic-review-rule",
        rule_version="1.0.0",
        task_code_system="urn:continucare:task-code",
        task_code="human-review",
        task_code_display="人工复核",
        description="合成数据人工复核；不构成临床建议。",
        requester_reference="Organization/continucare",
        owner_reference="PractitionerRole/nurse",
        authored_on=NOW,
        trigger_reference="Observation/observation-1",
        due_at="2026-08-02T14:00:00+00:00",
        task_id="task-layer4-1",
        based_on_references=["PlanDefinition/glp1-followup-plan-v1"],
    )
    provenance = build_provenance(
        target_references=["Task/task-layer4-1"],
        recorded_at=NOW,
        agent_reference="Device/continucare-rule-engine",
        agent_role_code="author",
        agent_role_display="Author",
        provenance_id="provenance-layer4-1",
        activity_code="CREATE",
        activity_display="create",
        entity_source_references=["Observation/observation-1"],
    )
    return communication, task, provenance


def test_layer4_fhir_builders_produce_strict_resources():
    communication, task, provenance = resources()

    assert communication["resourceType"] == "Communication"
    assert communication["subject"]["reference"] == f"Patient/{PATIENT_ID}"
    assert task["resourceType"] == "Task"
    assert task["identifier"][0]["value"] == "synthetic-review-rule|1.0.0"
    assert task["reasonReference"]["reference"] == "Observation/observation-1"
    assert provenance["resourceType"] == "Provenance"
    assert provenance["target"][0]["reference"] == "Task/task-layer4-1"


def test_layer4_fhir_storage_is_versioned_idempotent_and_exact(tmp_path):
    db_path = tmp_path / "layer4-fhir.db"
    store = Layer4SQLiteStore(db_path)
    communication, task, provenance = resources()
    for resource in (communication, task, provenance):
        store.save_fhir_resource(resource, patient_id=PATIENT_ID)
        store.save_fhir_resource(resource, patient_id=PATIENT_ID)

    reopened = Layer4SQLiteStore(db_path)
    assert reopened.get_fhir_resource("Communication", communication["id"]) == communication
    assert reopened.get_fhir_resource("Task", task["id"]) == task
    assert reopened.get_fhir_resource("Provenance", provenance["id"]) == provenance
    assert len(reopened.list_fhir_resources(patient_id=PATIENT_ID)) == 3

    conflicting = deepcopy(communication)
    conflicting["payload"][0]["contentString"] = "同版本被篡改。"
    with pytest.raises(ValueError, match="version is immutable"):
        reopened.save_fhir_resource(conflicting, patient_id=PATIENT_ID)


def test_layer4_fhir_storage_keeps_history_and_marks_latest_version(tmp_path):
    store = Layer4SQLiteStore(tmp_path / "layer4-history.db")
    version_1, _, _ = resources()
    version_2 = build_communication(
        patient_id=PATIENT_ID,
        content_text="患者更正后的合成随访确认。",
        sender_reference=f"Patient/{PATIENT_ID}",
        recipient_references=["PractitionerRole/nurse"],
        sent_at="2026-08-02T11:00:00+00:00",
        communication_id=version_1["id"],
        version_id="2",
    )
    store.save_fhir_resource(version_1, patient_id=PATIENT_ID)
    store.save_fhir_resource(version_2, patient_id=PATIENT_ID)

    assert store.get_fhir_resource("Communication", version_1["id"]) == version_2
    assert (
        store.get_fhir_resource(
            "Communication", version_1["id"], version_id="1"
        )
        == version_1
    )
    assert len(store.list_fhir_resources(patient_id=PATIENT_ID)) == 1
    assert len(
        store.list_fhir_resources(patient_id=PATIENT_ID, current_only=False)
    ) == 2


def test_layer4_fhir_storage_rejects_patient_mismatch(tmp_path):
    store = Layer4SQLiteStore(tmp_path / "layer4-patient.db")
    communication, _, _ = resources()

    with pytest.raises(ValueError, match="does not match patient_id"):
        store.save_fhir_resource(communication, patient_id="P-WRONG")


def test_layer4_contract_storage_round_trips_exact_json(tmp_path):
    db_path = tmp_path / "layer4-contract.db"
    store = Layer4SQLiteStore(db_path)
    event = MemoryEvent(
        event_id="memory-event-1",
        patient_id=PATIENT_ID,
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
        evidence_refs=[
            {
                "evidence_id": "evidence-1",
                "resource": {
                    "reference": "QuestionnaireResponse/response-1",
                    "version_id": "1",
                },
                "role": "source",
                "effective_start": "2026-08-02T08:00:00+00:00",
                "effective_end": NOW,
            }
        ],
    )
    store.save_contract(event)
    store.save_contract(event)

    reopened = Layer4SQLiteStore(db_path)
    persisted = reopened.get_contract("memory_event", event.event_id)
    assert persisted == event
    assert reopened.list_contracts("memory_event", patient_id=PATIENT_ID) == [event]

    changed = event.model_copy(update={"source_status": "amended"})
    with pytest.raises(ValueError, match="contract version is immutable"):
        reopened.save_contract(changed)


def test_all_layer4_contract_record_types_round_trip(tmp_path):
    store = Layer4SQLiteStore(tmp_path / "layer4-all-contracts.db")
    evidence = {
        "evidence_id": "evidence-1",
        "resource": {
            "reference": "Observation/observation-1",
            "version_id": "1",
        },
        "role": "supporting",
        "effective_start": "2026-08-02T08:00:00+00:00",
        "effective_end": NOW,
    }
    memory = MemoryEvent(
        event_id="memory-all-1",
        patient_id=PATIENT_ID,
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
        deduplication_key="all-contracts-memory",
        evidence_refs=[evidence],
    )
    timeline = TimelineEvent(
        timeline_event_id="timeline-all-1",
        patient_id=PATIENT_ID,
        memory_event_id=memory.event_id,
        pathway_code="GLP1-14D",
        pathway_version="1.0.0",
        kind=MemoryEventKind.OBSERVATION,
        title="合成事件",
        summary="合成数据时间线事件。",
        effective_start=memory.effective_start,
        effective_end=memory.effective_end,
        recorded_at=NOW,
        source=memory.source,
        evidence_refs=[evidence],
    )
    revision = RevisionLink(
        link_id="revision-all-1",
        patient_id=PATIENT_ID,
        predecessor=ResourceReference(
            reference="Observation/observation-1", version_id="1"
        ),
        successor=ResourceReference(
            reference="Observation/observation-1", version_id="2"
        ),
        relationship=RevisionRelationship.AMENDS,
        reason="合成修订。",
        actor_reference=f"Patient/{PATIENT_ID}",
        provenance_reference="Provenance/provenance-1",
        created_at=NOW,
    )
    summary = Layer4SummaryDraft(
        summary_id="summary-all-1",
        patient_id=PATIENT_ID,
        period_start="2026-07-20T00:00:00+00:00",
        period_end=NOW,
        items=[
            {
                "item_id": "summary-all-item-1",
                "section": "overview",
                "text": "合成数据摘要。",
                "evidence_refs": [evidence],
            }
        ],
        created_at=NOW,
    )
    review = DoctorReview(
        review_id="review-all-1",
        summary_id=summary.summary_id,
        summary_version=summary.version,
        patient_id=PATIENT_ID,
        reviewer_reference="Practitioner/doctor",
        decision=DoctorReviewDecision.ACCEPT,
        reviewed_at=NOW,
    )
    rule = ClinicalRuleDefinition.model_validate(
        {
            "rule_id": "rule-all-1",
            "version": "1.0.0",
            "title": "合成规则合同",
            "description": "仅持久化，不执行。",
            "applicability": {
                "pathway_code": "GLP1-14D",
                "pathway_version": "1.0.0",
                "synthetic_only": True,
                "population": "合成比赛患者",
                "region": "DE-demo",
            },
            "evidence_refs": [evidence],
            "inputs": [
                {
                    "input_id": "vomiting",
                    "code_system": "http://loinc.org",
                    "code": "94070-0",
                    "unit": "/d",
                    "lookback_hours": 24,
                }
            ],
            "conditions": [
                {
                    "input_id": "vomiting",
                    "operator": "gte",
                    "expected_value": 2,
                    "unit": "/d",
                }
            ],
            "action": {
                "task_code_system": "urn:continucare:task-code",
                "task_code": "human-review",
                "task_code_display": "人工复核",
                "title": "合成人工复核",
                "description": "只用于合同测试。",
                "owner_role": "nurse",
                "sla_hours": 4,
                "deduplication_window_hours": 24,
            },
            "test_case_ids": ["contract-storage-1"],
            "rollback_plan": "保持 not_assessed。",
            "created_at": NOW,
        }
    )
    records = [rule, memory, timeline, revision, summary, review]

    for record in records:
        store.save_contract(record)

    assert store.get_contract("clinical_rule", rule.rule_id) == rule
    assert store.get_contract("memory_event", memory.event_id) == memory
    assert store.get_contract("timeline_event", timeline.timeline_event_id) == timeline
    assert store.get_contract("revision_link", revision.link_id) == revision
    assert store.get_contract("summary_draft", summary.summary_id) == summary
    assert store.get_contract("doctor_review", review.review_id) == review


def test_contract_record_type_migration_preserves_existing_immutable_rows(tmp_path):
    db_path = tmp_path / "layer4-contract-migration.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE layer4_contract_records (
                record_type TEXT NOT NULL CHECK (
                    record_type IN (
                        'clinical_rule', 'memory_event', 'timeline_event',
                        'revision_link', 'summary_draft', 'doctor_review'
                    )
                ),
                record_id TEXT NOT NULL,
                record_version TEXT NOT NULL,
                patient_id TEXT,
                pathway_code TEXT,
                status TEXT NOT NULL,
                effective_time TEXT NOT NULL,
                record_json TEXT NOT NULL,
                is_current INTEGER NOT NULL CHECK (is_current IN (0, 1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (record_type, record_id, record_version)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO layer4_contract_records (
                record_type, record_id, record_version, patient_id,
                pathway_code, status, effective_time, record_json,
                is_current, created_at, updated_at
            ) VALUES ('summary_draft', 'legacy-summary', '1', NULL, NULL,
                      'draft', ?, '{"legacy":true}', 1, ?, ?)
            """,
            (NOW, NOW, NOW),
        )

    Layer4SQLiteStore(db_path)

    with connect(db_path) as connection:
        row = connection.execute(
            "SELECT record_json FROM layer4_contract_records "
            "WHERE record_id = 'legacy-summary'"
        ).fetchone()
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'layer4_contract_records'"
        ).fetchone()["sql"]
        indexes = {
            item["name"]
            for item in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'index' AND tbl_name = 'layer4_contract_records'"
            )
        }

    assert row["record_json"] == '{"legacy":true}'
    assert "state_snapshot" in table_sql
    assert "idx_layer4_contract_current" in indexes
