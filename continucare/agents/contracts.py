"""Strict, JSON-serializable contracts used between Layer-3 agents."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SemanticStatus(str, Enum):
    NEEDS_CONFIRMATION = "needs_confirmation"
    NEEDS_CLARIFICATION = "needs_clarification"
    NO_MATCH = "no_match"
    BLOCKED = "blocked"


class Temporality(str, Enum):
    CURRENT = "current"
    EXPLICIT_24H = "explicit_24h"
    UNSPECIFIED = "unspecified"
    HISTORICAL = "historical"


class SubjectType(str, Enum):
    PATIENT = "patient"
    OTHER_PERSON = "other_person"
    UNKNOWN = "unknown"


class ClarificationKind(str, Enum):
    CONFIRM_TIME_WINDOW = "confirm_time_window"
    CONFIRM_CURRENT = "confirm_current"
    CLARIFY_COUNT = "clarify_count"
    CLARIFY_QUANTITY = "clarify_quantity"
    RESOLVE_CONFLICT = "resolve_conflict"


class CandidateIssueAction(str, Enum):
    REJECTED = "rejected"
    CLARIFICATION_REQUIRED = "clarification_required"


class CodingContract(StrictModel):
    system: str
    code: str
    display: str | None = None
    version: str | None = None


class AnswerOptionContract(StrictModel):
    code: str
    system: str
    display: str | None = None


class QuestionnaireItemContract(StrictModel):
    link_id: str
    item_type: str
    text: str
    codes: list[CodingContract] = Field(default_factory=list)
    answer_options: list[AnswerOptionContract] = Field(default_factory=list)


class SemanticTask(StrictModel):
    task_id: str
    patient_id: str
    session_id: str
    pathway_code: str
    pathway_version: str
    questionnaire_canonical: str
    questionnaire_version: str
    message_text: str = Field(min_length=1, max_length=4000)
    existing_answers: dict[str, Any] = Field(default_factory=dict)
    allowed_items: list[QuestionnaireItemContract] = Field(min_length=1)
    created_at: str

    @field_validator("message_text")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message_text cannot be blank")
        return value


class SemanticCandidate(StrictModel):
    candidate_id: str
    link_id: str
    answer: Any
    questionnaire_code: CodingContract | None = None
    evidence_text: str = Field(min_length=1)
    evidence_start: int = Field(ge=0)
    evidence_end: int = Field(gt=0)
    subject: SubjectType = SubjectType.PATIENT
    temporality: Temporality
    negated: bool = False
    requires_patient_confirmation: bool = True
    patient_message: str = Field(min_length=1)
    template_id: str

    @model_validator(mode="after")
    def validate_span(self) -> "SemanticCandidate":
        if self.evidence_end <= self.evidence_start:
            raise ValueError("evidence_end must be greater than evidence_start")
        return self


class ClarificationOption(StrictModel):
    option_id: str
    label: str
    accepts_candidate: bool = False


class ClarificationRequest(StrictModel):
    clarification_id: str
    kind: ClarificationKind
    prompt: str = Field(min_length=1)
    options: list[ClarificationOption] = Field(min_length=2)
    proposed_candidate: SemanticCandidate | None = None


class CandidateIssue(StrictModel):
    """Patient-readable disposition for a model proposal that was not accepted."""

    issue_id: str
    candidate_id: str
    link_id: str
    field_label: str
    proposed_answer: Any | None = None
    evidence_text: str = Field(min_length=1)
    action: CandidateIssueAction
    reason_codes: list[str] = Field(min_length=1)
    explanation: str = Field(min_length=1)


class SemanticResult(StrictModel):
    run_id: str
    task_id: str
    status: SemanticStatus
    mode: str
    care_agent_version: str
    safety_agent_version: str
    language_policy_version: str
    candidates: list[SemanticCandidate] = Field(default_factory=list)
    clarifications: list[ClarificationRequest] = Field(default_factory=list)
    candidate_issues: list[CandidateIssue] = Field(default_factory=list)
    ignored_reasons: list[str] = Field(default_factory=list)
    safety_violations: list[str] = Field(default_factory=list)
    model_usage: dict[str, int] | None = None
    provider_request_id: str | None = None
    completed_at: str


class AgentRunRecord(StrictModel):
    run_id: str
    task_id: str
    patient_id: str
    session_id: str
    agent_name: str
    agent_version: str
    mode: str
    input_text: str
    input_hash: str
    output_json: dict[str, Any]
    status: str
    model_provider: str | None = None
    model_name: str | None = None
    prompt_version: str | None = None
    started_at: str
    completed_at: str
    error_code: str | None = None


class AgentRuntimeOutcome(StrictModel):
    result: SemanticResult
    started_at: str
    completed_at: str
    agent_name: str
    agent_version: str
    idempotent_replay: bool = False
