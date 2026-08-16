"""Read-only product projections for the role-separated synthetic MVP.

The product shell does not own a second workflow state machine.  It turns the
same persisted competition facts into role identity and operations views.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from continucare.adapters.sqlite_store import SQLiteStore
from continucare.demo_data import DEMO_PATIENT_ID
from continucare.models import Patient
from continucare.services.competition_demo import (
    CompetitionDemoProgress,
    CompetitionDemoStage,
)


class ProductRole(StrEnum):
    PATIENT = "patient"
    NURSE = "nurse"
    DOCTOR = "doctor"
    OPERATIONS = "operations"
    AUDIT = "audit"


ROLE_LABELS = {
    ProductRole.PATIENT: "患者端",
    ProductRole.NURSE: "护士端 · 人工安全复核",
    ProductRole.DOCTOR: "医生端 · 复诊工作台",
    ProductRole.OPERATIONS: "医院端 · 运营与治理",
    ProductRole.AUDIT: "治理端 · 记录追溯",
}


@dataclass(frozen=True, slots=True)
class ProductContext:
    role: ProductRole
    role_label: str
    patient: Patient | None
    synthetic_only: bool = True
    role_simulation: bool = True


@dataclass(frozen=True, slots=True)
class OperationsSnapshot:
    stage: str
    stage_label: str
    patient_count: int
    patients: tuple[Patient, ...]
    active_story_count: int
    patient_confirmed_count: int
    pending_manual_review_count: int
    pending_draft_approval_count: int
    doctor_brief_count: int
    communication_ready_count: int
    observation_count: int
    audit_count: int
    alert_count: int
    approved_clinical_rule_count: int
    model_source: str
    model_name: str | None
    knowledge_release_id: str | None
    integrity_ok: bool
    integrity_message: str
    next_page: str
    next_label: str

    def evidence_payload(self) -> dict[str, Any]:
        """Return a secret-free, machine-readable acceptance snapshot."""

        if not self.integrity_ok:
            raise ValueError("untrusted operations snapshot cannot be exported")

        return {
            "classification": "synthetic_only",
            "role_access": "simulated_not_authenticated",
            "stage": self.stage,
            "counts": {
                "patients": self.patient_count,
                "active_stories": self.active_story_count,
                "patient_confirmed": self.patient_confirmed_count,
                "pending_manual_review": self.pending_manual_review_count,
                "pending_draft_approval": self.pending_draft_approval_count,
                "doctor_briefs": self.doctor_brief_count,
                "communication_ready_to_send": self.communication_ready_count,
                "observations": self.observation_count,
                "audit_events": self.audit_count,
                "alerts": self.alert_count,
                "approved_clinical_rules": self.approved_clinical_rule_count,
            },
            "model": {
                "source": self.model_source,
                "name": self.model_name,
            },
            "knowledge_release_id": self.knowledge_release_id,
            "integrity": {
                "ok": self.integrity_ok,
                "message": self.integrity_message,
            },
            "external_send": "disabled",
            "emr_write": "disabled",
            "clinical_risk_assessment": "not_assessed",
        }


_STAGE_LABELS = {
    CompetitionDemoStage.NOT_STARTED: "尚未开始",
    CompetitionDemoStage.PLAN_ACTIVATED: "医生已启动，等待患者提交",
    CompetitionDemoStage.CANDIDATE_READY: "等待患者确认",
    CompetitionDemoStage.CANDIDATE_UNSURE: "患者暂不确定",
    CompetitionDemoStage.CANDIDATE_REJECTED: "本轮已由患者拒绝",
    CompetitionDemoStage.PATIENT_COLLECTING: "患者正在完成今日随访",
    CompetitionDemoStage.PATIENT_REVIEW_READY: "等待患者最终提交",
    CompetitionDemoStage.PATIENT_CONFIRMED: "患者已确认",
    CompetitionDemoStage.TASK_REQUESTED: "等待护士接手",
    CompetitionDemoStage.NURSE_RECEIVED: "护士已接手",
    CompetitionDemoStage.NURSE_IN_PROGRESS: "护士核对中",
    CompetitionDemoStage.TASK_REJECTED: "护士已拒绝任务",
    CompetitionDemoStage.TASK_CANCELLED: "任务已取消",
    CompetitionDemoStage.TASK_FAILED: "任务未完成",
    CompetitionDemoStage.TASK_ENTERED_IN_ERROR: "任务记录错误",
    CompetitionDemoStage.COMMUNICATION_PENDING: "沟通草稿待批准",
    CompetitionDemoStage.DOCTOR_BRIEF_PENDING: "医生速览待生成",
    CompetitionDemoStage.COMMUNICATION_READY: "沟通草稿已批准",
    CompetitionDemoStage.DOCTOR_BRIEF_READY: "医生速览已生成",
    CompetitionDemoStage.STORY_COMPLETE: "本轮流程已完成",
}


_PENDING_MANUAL_TASK_STATUSES = {"requested", "received", "accepted", "in-progress"}


def build_product_context(
    store: SQLiteStore | None,
    role: ProductRole,
    *,
    patient_id: str = DEMO_PATIENT_ID,
) -> ProductContext:
    """Resolve one explicitly scoped role/patient view, fail-closed if absent."""

    patient = store.get_patient(patient_id) if store is not None else None
    if patient is not None and not patient.synthetic:
        raise ValueError("体验型 MVP 只允许合成患者数据")
    return ProductContext(
        role=role,
        role_label=ROLE_LABELS[role],
        patient=patient,
    )


def build_operations_snapshot(
    store: SQLiteStore | None,
    progress: CompetitionDemoProgress,
) -> OperationsSnapshot:
    """Project truthful operational metrics from the shared persisted facts."""

    listed_patients = store.list_patients() if store is not None else []
    non_synthetic_present = any(not patient.synthetic for patient in listed_patients)
    record = (
        store.get_agent_run(progress.run_id)
        if store is not None and progress.run_id
        else None
    )
    session = (
        store.get_care_session(progress.session_id)
        if store is not None and progress.session_id
        else None
    )
    model_source = "尚未调用"
    model_name = None
    if record is not None:
        model_source = {
            "model_api:xiaomi_mimo": "小米 MiMo API",
            "model_api:volcengine_doubao": "火山方舟豆包 API",
        }.get(record.mode, "确定性离线引擎")
        model_name = record.model_name

    boundary_violations = []
    if progress.integrity_issue is not None:
        boundary_violations.append("故事完整性检查未通过")
    if non_synthetic_present:
        boundary_violations.append("患者登记包含非合成数据")
    if progress.alert_count:
        boundary_violations.append("冻结范围内出现了 Alert")
    if progress.approved_clinical_rule_count:
        boundary_violations.append("冻结范围内出现了获批临床规则")
    integrity_ok = not boundary_violations
    trusted_patients = tuple(listed_patients) if not non_synthetic_present else ()
    return OperationsSnapshot(
        stage=progress.stage.value,
        stage_label=_STAGE_LABELS[progress.stage],
        patient_count=len(trusted_patients),
        patients=trusted_patients,
        active_story_count=int(progress.generation is not None and not progress.is_terminal),
        patient_confirmed_count=int(progress.milestones.get("patient_confirmed", False)),
        pending_manual_review_count=int(
            progress.task_status in _PENDING_MANUAL_TASK_STATUSES
        ),
        pending_draft_approval_count=int(
            progress.communication_readiness == "pending-approval"
        ),
        doctor_brief_count=progress.manual_brief_count,
        communication_ready_count=int(
            progress.communication_readiness == "ready-to-send"
        ),
        observation_count=progress.observation_count,
        audit_count=progress.audit_count,
        alert_count=progress.alert_count,
        approved_clinical_rule_count=progress.approved_clinical_rule_count,
        model_source=model_source,
        model_name=model_name,
        knowledge_release_id=(session.knowledge_release_id if session else None),
        integrity_ok=integrity_ok,
        integrity_message=(
            "当前故事和审计链可读取"
            if integrity_ok
            else "；".join(boundary_violations)
        ),
        next_page=progress.next_page,
        next_label=progress.next_label,
    )
