"""Validated business entities for the local ContinuCare workflow."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConfidenceTier(str, Enum):
    PATIENT_CONFIRMED = "patient_confirmed"
    VERBATIM_EXPLICIT = "verbatim_explicit"
    MODEL_INFERRED = "model_inferred"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


class AlertStatus(str, Enum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    ESCALATED = "escalated"
    RESOLVED = "resolved"


class Patient(StrictModel):
    patient_id: str
    display_name: str
    synthetic: bool
    pathway_code: str
    enrollment_date: str
    next_visit_date: str
    status: str
    created_at: str


class FollowUpMessage(StrictModel):
    message_id: str
    patient_id: str
    message_text: str = Field(min_length=1)
    submitted_at: str
    source: str = "patient_demo_web"
    processing_status: str


class Observation(StrictModel):
    observation_id: str
    patient_id: str
    message_id: str
    code: str
    value: Any
    unit: str | None = None
    effective_time: str
    source: str = "patient_reported"
    confidence_tier: ConfidenceTier
    evidence_text: str
    evidence_start: int = Field(ge=0)
    evidence_end: int = Field(ge=0)
    created_at: str

    @field_validator("evidence_end")
    @classmethod
    def evidence_end_must_follow_start(cls, value: int, info):
        start = info.data.get("evidence_start", 0)
        if value <= start:
            raise ValueError("evidence_end must be greater than evidence_start")
        return value


class Alert(StrictModel):
    alert_id: str
    patient_id: str
    severity: str
    title: str
    trigger_rule_id: str
    trigger_reason: str
    evidence_refs: list[str]
    owner_role: str
    status: AlertStatus = AlertStatus.OPEN
    sla_due_at: str | None = None
    created_at: str
    resolved_at: str | None = None
    resolution_reason: str | None = None


class AlertAction(StrictModel):
    action_id: str
    alert_id: str
    action_type: str
    actor_role: str
    note: str
    created_at: str


class SummaryItem(StrictModel):
    text: str
    evidence_refs: list[str] = Field(min_length=1)


class SummaryContent(StrictModel):
    overview: list[SummaryItem] = Field(default_factory=list)
    key_changes: list[SummaryItem] = Field(default_factory=list)
    alerts_and_actions: list[SummaryItem] = Field(default_factory=list)
    patient_questions: list[SummaryItem] = Field(default_factory=list)
    missing_data: list[SummaryItem] = Field(default_factory=list)
    doctor_to_confirm: list[SummaryItem] = Field(default_factory=list)


class Summary(StrictModel):
    summary_id: str
    patient_id: str
    period_start: str
    period_end: str
    status: str
    summary_json: SummaryContent
    created_at: str
    reviewed_at: str | None = None


class AuditEvent(StrictModel):
    event_id: str
    patient_id: str | None = None
    entity_type: str
    entity_id: str
    event_type: str
    actor_type: str
    details_json: dict[str, Any]
    created_at: str


class ExtractionResult(StrictModel):
    observations: list[Observation] = Field(default_factory=list)
    extractor_mode: str = "local_mock_rules"

    @property
    def has_current_emergency_signal(self) -> bool:
        return any(
            observation.code.startswith("emergency_") and observation.value is True
            for observation in self.observations
        )


class SummaryContext(StrictModel):
    patient: Patient
    messages: list[FollowUpMessage]
    observations: list[Observation]
    alerts: list[Alert]
    alert_actions: list[AlertAction]


class SummaryDraft(StrictModel):
    content: SummaryContent


class RiskDecision(StrictModel):
    severity: str
    create_alert: bool
    title: str | None = None
    trigger_rule_id: str | None = None
    trigger_reason: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    owner_role: str | None = None
    sla_hours: int | None = None


class DeliveryResult(StrictModel):
    delivered: bool
    channel: str
    delivery_id: str
    label: str
    detail: str
