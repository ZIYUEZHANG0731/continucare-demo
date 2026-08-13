from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

import pytest
from pydantic import ValidationError

from continucare.db import connect
from continucare.fhir.observations import build_patient_reported_observation
from continucare.fhir.questionnaires import build_free_text_questionnaire_response
from continucare.fhir.terminology import BODY_WEIGHT
from continucare.layer4 import (
    ClinicalMemoryService,
    ClinicalStateService,
    ComponentReadStatus,
    DoctorReviewService,
    DoctorWorkbenchService,
    EvidenceArtifactType,
    EvidenceSummaryService,
    Layer4InputSnapshot,
    Layer4SQLiteStore,
    StateMetricDefinition,
    TaskWorkflowService,
    WorkbenchAccessContext,
    WorkbenchPurpose,
    WorkbenchRole,
    build_workflow_task,
    build_patient_confirmed_review_task,
)
from continucare.layer4.contracts import DoctorReviewDecision, SummaryDraftStatus


PATIENT_ID = "P-DEMO-001"
PATHWAY_CODE = "GLP1-14D"
PATHWAY_VERSION = "1.0.0"
UCUM = "http://unitsofmeasure.org"


class MutableInputReader:
    def __init__(self, snapshot: Layer4InputSnapshot):
        self.snapshot = snapshot

    def read(self, patient_id: str) -> Layer4InputSnapshot:
        assert patient_id == self.snapshot.patient_id
        return self.snapshot


class FailingInputReader:
    def read(self, patient_id: str) -> Layer4InputSnapshot:
        raise RuntimeError("synthetic input outage")


class SelectiveFailureRepository:
    def __init__(self, delegate, *, contract_types=(), fhir_types=()):
        self.delegate = delegate
        self.contract_types = set(contract_types)
        self.fhir_types = set(fhir_types)

    def list_contracts(self, record_type: str, **kwargs):
        if record_type in self.contract_types:
            raise RuntimeError("synthetic contract outage")
        return self.delegate.list_contracts(record_type, **kwargs)

    def list_fhir_resources(self, **kwargs):
        if kwargs.get("resource_type") in self.fhir_types:
            raise RuntimeError("synthetic FHIR outage")
        return self.delegate.list_fhir_resources(**kwargs)

    def __getattr__(self, name):
        return getattr(self.delegate, name)


def _access(
    *,
    role: WorkbenchRole = WorkbenchRole.DOCTOR,
    verified: bool = True,
    patients: list[str] | None = None,
) -> WorkbenchAccessContext:
    purpose = {
        WorkbenchRole.DOCTOR: WorkbenchPurpose.TREATMENT,
        WorkbenchRole.CLINICAL_AUDITOR: WorkbenchPurpose.AUDIT,
        WorkbenchRole.NURSE: WorkbenchPurpose.OPERATIONS,
    }[role]
    return WorkbenchAccessContext(
        actor_reference="Practitioner/doctor-workbench",
        role=role,
        purpose=purpose,
        permitted_patient_ids=patients or [PATIENT_ID],
        identity_verified=verified,
    )


def _response() -> dict:
    return build_free_text_questionnaire_response(
        response_id="response-workbench",
        patient_id=PATIENT_ID,
        authored="2026-08-02T10:00:00+00:00",
        text="合成工作台回放测试。",
    )


def _weight(
    observation_id: str,
    *,
    effective_time: str,
    issued_time: str | None = None,
    value: int,
) -> dict:
    return build_patient_reported_observation(
        observation_id=observation_id,
        patient_id=PATIENT_ID,
        questionnaire_response_id="response-workbench",
        effective_time=effective_time,
        issued_time=issued_time,
        code=BODY_WEIGHT,
        value_element="valueQuantity",
        value={
            "value": value,
            "unit": "kg",
            "system": UCUM,
            "code": "kg",
        },
    )


def _snapshot(observations: list[dict]) -> Layer4InputSnapshot:
    return Layer4InputSnapshot(
        patient_id=PATIENT_ID,
        questionnaire_responses=[_response()],
        observations=observations,
        assembled_at="2026-08-02T12:00:00+00:00",
    )


def _definition() -> StateMetricDefinition:
    return StateMetricDefinition(
        metric_id="body-weight",
        version="1.0.0",
        pathway_code=PATHWAY_CODE,
        pathway_version=PATHWAY_VERSION,
        code_system=BODY_WEIGHT.system,
        code=BODY_WEIGHT.code,
        display="Body weight",
        unit="kg",
        unit_system=UCUM,
        lookback_hours=72,
        stale_after_hours=24,
        trend_window_hours=72,
    )


@dataclass
class Scenario:
    repository: Layer4SQLiteStore
    reader: MutableInputReader
    workbench: DoctorWorkbenchService
    task_id: str
    summary_id: str
    state_id: str


def _scenario(tmp_path) -> Scenario:
    recent = _weight(
        "weight-workbench-recent",
        effective_time="2026-08-02T10:00:00+00:00",
        value=72,
    )
    reader = MutableInputReader(_snapshot([recent]))
    repository = Layer4SQLiteStore(tmp_path / "workbench.db")
    memory = ClinicalMemoryService(
        reader,
        repository,
        pathway_code=PATHWAY_CODE,
        pathway_version=PATHWAY_VERSION,
    )
    task = build_workflow_task(
        patient_id=PATIENT_ID,
        rule_id="synthetic-workbench-rule",
        rule_version="1.0.0",
        task_code_system="urn:continucare:task-code",
        task_code="review",
        task_code_display="Synthetic review",
        description="Synthetic task for read-only replay.",
        requester_reference="Organization/continucare",
        owner_reference="PractitionerRole/nurse",
        authored_on="2026-08-02T10:30:00+00:00",
        trigger_reference="Observation/weight-workbench-recent/_history/1",
        due_at="2026-08-02T14:30:00+00:00",
        task_id="task-workbench",
        based_on_references=[
            "urn:continucare:clinical-rule:synthetic-workbench-rule|1.0.0",
            f"urn:continucare:pathway:{PATHWAY_CODE}|{PATHWAY_VERSION}",
        ],
        evidence_references=[
            "Observation/weight-workbench-recent/_history/1"
        ],
    )
    repository.save_fhir_resource(task, patient_id=PATIENT_ID)
    memory.rebuild(PATIENT_ID)
    TaskWorkflowService(repository).transition(
        patient_id=PATIENT_ID,
        task_id=task["id"],
        to_status="received",
        actor_reference="PractitionerRole/nurse",
        note="Synthetic task received.",
        transitioned_at="2026-08-02T10:35:00+00:00",
    )
    memory.rebuild(PATIENT_ID)

    state_service = ClinicalStateService(
        reader,
        repository,
        pathway_code=PATHWAY_CODE,
        pathway_version=PATHWAY_VERSION,
    )
    state_1 = state_service.build(
        patient_id=PATIENT_ID,
        definitions=[_definition()],
        as_of="2026-08-02T12:00:00+00:00",
    )
    summaries = EvidenceSummaryService(memory, repository)
    summary_1 = summaries.generate(
        patient_id=PATIENT_ID,
        period_start="2026-08-01T00:00:00+00:00",
        period_end="2026-08-02T12:00:00+00:00",
        generated_at="2026-08-02T12:01:00+00:00",
    )
    DoctorReviewService(repository).review(
        summary_id=summary_1.summary_id,
        summary_version=summary_1.version,
        reviewer_reference="Practitioner/doctor-workbench",
        decision=DoctorReviewDecision.ACCEPT,
        reviewed_at="2026-08-02T12:05:00+00:00",
    )

    late = _weight(
        "weight-workbench-late",
        effective_time="2026-08-01T10:00:00+00:00",
        issued_time="2026-08-02T11:30:00+00:00",
        value=70,
    )
    reader.snapshot = _snapshot([recent, late])
    state_service.build(
        patient_id=PATIENT_ID,
        definitions=[_definition()],
        as_of="2026-08-02T12:00:00+00:00",
        generated_at="2026-08-02T12:10:00+00:00",
    )
    return Scenario(
        repository=repository,
        reader=reader,
        workbench=DoctorWorkbenchService(
            reader,
            repository,
            pathway_code=PATHWAY_CODE,
            pathway_version=PATHWAY_VERSION,
        ),
        task_id=task["id"],
        summary_id=summary_1.summary_id,
        state_id=state_1.snapshot_id,
    )


def test_workbench_composes_current_read_only_view_with_explicit_components(tmp_path):
    scenario = _scenario(tmp_path)
    before_contracts = len(
        scenario.repository.list_contracts(
            "state_snapshot", patient_id=PATIENT_ID, current_only=False
        )
    )
    before_fhir = len(
        scenario.repository.list_fhir_resources(
            patient_id=PATIENT_ID, current_only=False
        )
    )

    view = scenario.workbench.query(
        patient_id=PATIENT_ID,
        access=_access(),
        as_of="2026-08-02T12:06:00+00:00",
        generated_at="2026-08-02T12:06:00+00:00",
    )

    assert view.degraded is False
    assert all(item.status == ComponentReadStatus.AVAILABLE for item in view.components)
    assert view.state_snapshot.version == "1"
    assert view.summary.status == SummaryDraftStatus.DOCTOR_REVIEWED
    assert view.summary.version == "2"
    assert view.tasks[0]["meta"]["versionId"] == "2"
    assert view.tasks[0]["status"] == "received"
    assert any(item.state.value == "conflict" for item in view.timeline) is False
    assert len(view.evidence_roots) == len(set(view.evidence_roots))
    assert len(
        scenario.repository.list_contracts(
            "state_snapshot", patient_id=PATIENT_ID, current_only=False
        )
    ) == before_contracts
    assert len(
        scenario.repository.list_fhir_resources(
            patient_id=PATIENT_ID, current_only=False
        )
    ) == before_fhir


def test_historical_replay_uses_revision_and_task_state_known_at_that_time(tmp_path):
    scenario = _scenario(tmp_path)

    before_transition = scenario.workbench.query(
        patient_id=PATIENT_ID,
        access=_access(),
        as_of="2026-08-02T10:32:00+00:00",
        generated_at="2026-08-02T12:20:00+00:00",
    )
    after_transition = scenario.workbench.query(
        patient_id=PATIENT_ID,
        access=_access(),
        as_of="2026-08-02T10:36:00+00:00",
        generated_at="2026-08-02T12:20:00+00:00",
    )

    assert before_transition.tasks[0]["meta"]["versionId"] == "1"
    assert before_transition.tasks[0]["status"] == "requested"
    assert after_transition.tasks[0]["meta"]["versionId"] == "2"
    assert after_transition.tasks[0]["status"] == "received"
    before_task_events = [
        item for item in before_transition.timeline if item.source.reference.startswith("Task/")
    ]
    after_task_events = [
        item for item in after_transition.timeline if item.source.reference.startswith("Task/")
    ]
    assert [item.source.version_id for item in before_task_events] == ["1"]
    assert [item.source.version_id for item in after_task_events] == ["2"]
    assert before_transition.state_snapshot is None
    assert before_transition.summary is None


def test_historical_replay_selects_snapshot_version_available_at_cutoff(tmp_path):
    scenario = _scenario(tmp_path)

    before_late_version = scenario.workbench.query(
        patient_id=PATIENT_ID,
        access=_access(),
        as_of="2026-08-02T12:06:00+00:00",
        generated_at="2026-08-02T12:20:00+00:00",
    )
    after_late_version = scenario.workbench.query(
        patient_id=PATIENT_ID,
        access=_access(),
        as_of="2026-08-02T12:11:00+00:00",
        generated_at="2026-08-02T12:20:00+00:00",
    )

    assert before_late_version.state_snapshot.version == "1"
    assert before_late_version.state_snapshot.trends[0].status.value == "insufficient_data"
    assert after_late_version.state_snapshot.version == "2"
    assert after_late_version.state_snapshot.trends[0].status.value == "calculated"


def test_access_requires_verified_doctor_or_auditor_and_exact_patient_scope(tmp_path):
    scenario = _scenario(tmp_path)

    with pytest.raises(PermissionError, match="verified identity"):
        scenario.workbench.query(
            patient_id=PATIENT_ID,
            access=_access(verified=False),
            as_of="2026-08-02T12:06:00+00:00",
        )
    with pytest.raises(PermissionError, match="role is not permitted"):
        scenario.workbench.query(
            patient_id=PATIENT_ID,
            access=_access(role=WorkbenchRole.NURSE),
            as_of="2026-08-02T12:06:00+00:00",
        )
    with pytest.raises(PermissionError, match="not permitted.*patient"):
        scenario.workbench.query(
            patient_id=PATIENT_ID,
            access=_access(patients=["P-OTHER"]),
            as_of="2026-08-02T12:06:00+00:00",
        )

    auditor_view = scenario.workbench.query(
        patient_id=PATIENT_ID,
        access=_access(role=WorkbenchRole.CLINICAL_AUDITOR),
        as_of="2026-08-02T12:06:00+00:00",
        generated_at="2026-08-02T12:06:00+00:00",
    )
    assert auditor_view.role == WorkbenchRole.CLINICAL_AUDITOR


def test_access_contract_rejects_role_purpose_confusion():
    with pytest.raises(ValidationError, match="role and purpose"):
        WorkbenchAccessContext(
            actor_reference="Practitioner/doctor",
            role=WorkbenchRole.DOCTOR,
            purpose=WorkbenchPurpose.AUDIT,
            permitted_patient_ids=[PATIENT_ID],
            identity_verified=True,
        )


def test_component_failure_degrades_independently_without_fabricating_data(tmp_path):
    scenario = _scenario(tmp_path)
    failing = SelectiveFailureRepository(
        scenario.repository,
        contract_types={"state_snapshot"},
        fhir_types={"Task"},
    )
    service = DoctorWorkbenchService(
        scenario.reader,
        failing,
        pathway_code=PATHWAY_CODE,
        pathway_version=PATHWAY_VERSION,
    )

    view = service.query(
        patient_id=PATIENT_ID,
        access=_access(),
        as_of="2026-08-02T12:06:00+00:00",
        generated_at="2026-08-02T12:06:00+00:00",
    )

    statuses = {item.component.value: item for item in view.components}
    assert view.degraded is True
    assert statuses["timeline"].status == ComponentReadStatus.AVAILABLE
    assert statuses["summary"].status == ComponentReadStatus.AVAILABLE
    assert statuses["state"].status == ComponentReadStatus.DEGRADED
    assert statuses["tasks"].status == ComponentReadStatus.DEGRADED
    assert view.state_snapshot is None
    assert view.tasks == []
    assert all("synthetic" not in (item.message or "") for item in view.components)


def test_workbench_excludes_other_pathway_summary_and_task(tmp_path):
    scenario = _scenario(tmp_path)
    current_summary = scenario.repository.get_contract(
        "summary_draft", scenario.summary_id
    )
    unrelated_summary = current_summary.model_copy(
        update={
            "summary_id": "summary-other-pathway",
            "version": "1",
            "pathway_code": "OTHER-PATHWAY",
            "pathway_version": "9.0.0",
            "created_at": "2026-08-02T12:06:00+00:00",
        }
    )
    scenario.repository.save_contract(unrelated_summary)
    unrelated_task = build_workflow_task(
        patient_id=PATIENT_ID,
        rule_id="other-pathway-rule",
        rule_version="1.0.0",
        task_code_system="urn:continucare:task-code",
        task_code="other",
        task_code_display="Other pathway task",
        description="Must not appear in this pathway view.",
        requester_reference="Organization/continucare",
        owner_reference="PractitionerRole/nurse",
        authored_on="2026-08-02T12:06:00+00:00",
        trigger_reference="Observation/weight-workbench-recent/_history/1",
        due_at="2026-08-02T15:00:00+00:00",
        task_id="task-other-pathway",
        based_on_references=["urn:continucare:pathway:OTHER-PATHWAY|9.0.0"],
    )
    scenario.repository.save_fhir_resource(unrelated_task, patient_id=PATIENT_ID)

    view = scenario.workbench.query(
        patient_id=PATIENT_ID,
        access=_access(),
        as_of="2026-08-02T12:07:00+00:00",
        generated_at="2026-08-02T12:07:00+00:00",
    )

    assert view.summary.summary_id == scenario.summary_id
    assert {item["id"] for item in view.tasks} == {scenario.task_id}


def test_workbench_excludes_same_pathway_manual_review_task(tmp_path):
    scenario = _scenario(tmp_path)
    manual = build_patient_confirmed_review_task(
        patient_id=PATIENT_ID,
        receipt_digest="b" * 64,
        questionnaire_response_reference="QuestionnaireResponse/response-workbench",
        observation_references=["Observation/weight-workbench-recent"],
        pathway_reference=f"urn:continucare:pathway:{PATHWAY_CODE}|{PATHWAY_VERSION}",
        authored_on="2026-08-02T12:06:00+00:00",
        task_id="task-manual-review-workbench",
    )
    scenario.repository.save_fhir_resource(manual, patient_id=PATIENT_ID)
    completed_manual = deepcopy(manual)
    completed_manual["status"] = "completed"
    completed_manual["meta"] = {
        **completed_manual["meta"],
        "versionId": "2",
        "lastUpdated": "2026-08-02T12:06:30+00:00",
    }
    completed_manual["executionPeriod"] = {
        "start": "2026-08-02T12:06:10+00:00",
        "end": "2026-08-02T12:06:30+00:00",
    }
    scenario.repository.save_fhir_resource(completed_manual, patient_id=PATIENT_ID)

    view = scenario.workbench.query(
        patient_id=PATIENT_ID,
        access=_access(),
        as_of="2026-08-02T12:07:00+00:00",
        generated_at="2026-08-02T12:07:00+00:00",
    )

    assert {item["id"] for item in view.tasks} == {scenario.task_id}


def test_evidence_resolution_does_not_cross_requested_patient_scope(tmp_path):
    scenario = _scenario(tmp_path)
    with connect(scenario.repository.db_path) as connection:
        connection.execute(
            """
            INSERT INTO patients (
                patient_id, display_name, synthetic, pathway_code,
                enrollment_date, next_visit_date, status, created_at
            ) VALUES (?, ?, 1, ?, ?, ?, 'active', ?)
            """,
            (
                "P-OTHER",
                "Other synthetic patient",
                PATHWAY_CODE,
                "2026-08-01",
                "2026-08-14",
                "2026-08-01T00:00:00+00:00",
            ),
        )
    other_task = build_workflow_task(
        patient_id="P-OTHER",
        rule_id="other-patient-rule",
        rule_version="1.0.0",
        task_code_system="urn:continucare:task-code",
        task_code="other-patient",
        task_code_display="Other patient task",
        description="Must remain isolated.",
        requester_reference="Organization/continucare",
        owner_reference="PractitionerRole/nurse",
        authored_on="2026-08-02T11:00:00+00:00",
        trigger_reference="Observation/other-patient-observation/_history/1",
        due_at="2026-08-02T15:00:00+00:00",
        task_id="task-other-patient",
        based_on_references=[
            f"urn:continucare:pathway:{PATHWAY_CODE}|{PATHWAY_VERSION}"
        ],
    )
    scenario.repository.save_fhir_resource(other_task, patient_id="P-OTHER")

    trace = scenario.workbench.trace_evidence(
        patient_id=PATIENT_ID,
        access=_access(patients=[PATIENT_ID, "P-OTHER"]),
        root_reference="Task/task-other-patient/_history/1",
        as_of="2026-08-02T12:11:00+00:00",
    )

    assert trace.artifacts == []
    assert trace.unresolved_references == ["Task/task-other-patient/_history/1"]


@pytest.mark.parametrize("root_kind", ["state", "summary", "task"])
def test_snapshot_summary_and_task_roots_resolve_to_versioned_evidence(
    tmp_path, root_kind
):
    scenario = _scenario(tmp_path)
    roots = {
        "state": (
            f"urn:continucare:state-snapshot:{scenario.state_id}:version:2"
        ),
        "summary": (
            f"urn:continucare:summary:{scenario.summary_id}:version:2"
        ),
        "task": f"Task/{scenario.task_id}/_history/2",
    }

    trace = scenario.workbench.trace_evidence(
        patient_id=PATIENT_ID,
        access=_access(),
        root_reference=roots[root_kind],
        as_of="2026-08-02T12:11:00+00:00",
    )

    assert trace.artifacts[0].reference == roots[root_kind]
    assert trace.degraded is False
    assert any(
        item.artifact_type == EvidenceArtifactType.FHIR_RESOURCE
        and item.resource_type == "Observation"
        for item in trace.artifacts
    )
    assert any(
        item.artifact_type == EvidenceArtifactType.FHIR_RESOURCE
        and item.resource_type == "Provenance"
        for item in trace.artifacts
    )
    if root_kind == "state":
        assert any(
            item.artifact_type == EvidenceArtifactType.METRIC_DEFINITION
            for item in trace.artifacts
        )


def test_evidence_trace_marks_missing_source_and_input_outage_without_invention(tmp_path):
    scenario = _scenario(tmp_path)
    missing = scenario.workbench.trace_evidence(
        patient_id=PATIENT_ID,
        access=_access(),
        root_reference="Observation/not-present/_history/1",
        as_of="2026-08-02T12:11:00+00:00",
    )
    assert missing.artifacts == []
    assert missing.unresolved_references == ["Observation/not-present/_history/1"]
    assert missing.degraded is False

    unavailable = DoctorWorkbenchService(
        FailingInputReader(),
        scenario.repository,
        pathway_code=PATHWAY_CODE,
        pathway_version=PATHWAY_VERSION,
    ).trace_evidence(
        patient_id=PATIENT_ID,
        access=_access(),
        root_reference="Observation/weight-workbench-recent/_history/1",
        as_of="2026-08-02T12:11:00+00:00",
    )
    assert unavailable.artifacts == []
    assert unavailable.degraded is True
    assert unavailable.reason_codes == ["evidence_source_unavailable"]


def test_trace_depth_limit_is_explicit_not_silent(tmp_path):
    scenario = _scenario(tmp_path)
    trace = scenario.workbench.trace_evidence(
        patient_id=PATIENT_ID,
        access=_access(),
        root_reference=(
            f"urn:continucare:summary:{scenario.summary_id}:version:2"
        ),
        as_of="2026-08-02T12:11:00+00:00",
        max_depth=1,
    )

    assert trace.truncated is True
    assert trace.artifacts
