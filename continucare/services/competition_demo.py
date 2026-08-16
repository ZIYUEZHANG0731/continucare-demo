"""Persistent-fact orchestration for the synthetic competition demo.

This module does not own a second workflow state machine.  It projects the
existing Layer 3/4 facts and provides an atomic, explicitly invoked reset/start
boundary for the local synthetic database.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from continucare.adapters.sqlite_store import SQLiteStore
from continucare.agents.contracts import CandidateSource, SemanticResult, SemanticStatus
from continucare.care_agent import CareAgentService
from continucare.care_agent.mimo_adapter import MiMoSemanticAdapter
from continucare.care_agent.model_api import (
    MODEL_API_MODES,
    MODEL_API_PROVIDERS,
    MODEL_CANDIDATE_SOURCES,
    SemanticModelConfig,
    UnconfiguredModelAdapter,
)
from continucare.care_engine import CareEngine
from continucare.db import reset_demo, utc_now_iso
from continucare.demo_data import DEMO_PATIENT_ID, MANUAL_REVIEW_MESSAGE
from continucare.layer4.contracts import Layer4SummaryDraft
from continucare.layer4.manual_reviews import (
    PENDING_APPROVAL,
    READY_TO_SEND,
    communication_readiness,
    is_manual_review_communication,
    is_manual_review_task,
)
from continucare.services.demo_scenarios import (
    load_layer2_scenario,
    load_manual_review_scenario,
)
from continucare.services.patient_checkin import (
    CORE_LINK_IDS,
    project_patient_checkin,
    resolve_patient_chat_focus,
    validate_synthetic_chat_message,
)
from continucare.services.plan_collection import active_patient_link_ids
from continucare.terminology import load_supplemental_terminology_backend


class CompetitionDemoStage(StrEnum):
    NOT_STARTED = "not_started"
    PLAN_ACTIVATED = "plan_activated"
    CANDIDATE_READY = "candidate_ready"
    CANDIDATE_UNSURE = "candidate_unsure"
    CANDIDATE_REJECTED = "candidate_rejected"
    PATIENT_COLLECTING = "patient_collecting"
    PATIENT_REVIEW_READY = "patient_review_ready"
    PATIENT_CONFIRMED = "patient_confirmed"
    TASK_REQUESTED = "task_requested"
    NURSE_RECEIVED = "nurse_received"
    NURSE_IN_PROGRESS = "nurse_in_progress"
    TASK_REJECTED = "task_rejected"
    TASK_CANCELLED = "task_cancelled"
    TASK_FAILED = "task_failed"
    TASK_ENTERED_IN_ERROR = "task_entered_in_error"
    COMMUNICATION_PENDING = "communication_pending"
    DOCTOR_BRIEF_PENDING = "doctor_brief_pending"
    COMMUNICATION_READY = "communication_ready"
    DOCTOR_BRIEF_READY = "doctor_brief_ready"
    STORY_COMPLETE = "story_complete"


MILESTONE_ORDER = tuple(item.value for item in CompetitionDemoStage)
_EXPECTED_GENERATION_UNSET = object()
_PlanResult = TypeVar("_PlanResult")
SEMANTIC_HANDOFF_POLICY_VERSION = "patient-semantic-handoff-v1"
_SEMANTIC_HANDOFF_REASON_CODES = {
    "evidence_concept_mismatch",
    "answer_evidence_mismatch",
}


class CompetitionDemoProgress(BaseModel):
    """Read-only projection of the existing persisted workflow facts."""

    model_config = ConfigDict(frozen=True)

    stage: CompetitionDemoStage = CompetitionDemoStage.NOT_STARTED
    milestones: dict[str, bool] = Field(
        default_factory=lambda: {item: False for item in MILESTONE_ORDER}
    )
    generation: str | None = None
    plan_activated: bool = False
    plan_activated_at: str | None = None
    plan_actor: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    task_id: str | None = None
    communication_id: str | None = None
    summary_id: str | None = None
    summary_version: str | None = None
    session_status: str | None = None
    task_status: str | None = None
    communication_readiness: str | None = None
    candidate_count: int = 0
    candidate_decisions: dict[str, str] = Field(default_factory=dict)
    collection_resolutions: dict[str, str] = Field(default_factory=dict)
    questionnaire_response_count: int = 0
    observation_count: int = 0
    manual_task_count: int = 0
    communication_count: int = 0
    manual_brief_count: int = 0
    provenance_count: int = 0
    audit_count: int = 0
    alert_count: int = 0
    approved_clinical_rule_count: int = 0
    knowledge_available: bool = False
    knowledge_error: str | None = None
    is_terminal: bool = False
    terminal_reason: str | None = None
    next_page: str = "app.py"
    next_label: str = "开始完整比赛 Demo"
    next_help: str = "明确开始后，系统只准备未确认候选。"
    integrity_issue: str | None = None


class CompetitionDemoStartError(RuntimeError):
    """Stable, non-sensitive error surfaced by the explicit start action."""


class CompetitionDemoConflict(ValueError):
    """The visible story generation changed before a requested write."""


def _semantic_handoff_reason(result: SemanticResult) -> str | None:
    """Classify only narrow, non-structuring semantic outcomes for nurse review."""

    extraction = [
        item for item in result.stage_traces if item.stage == "care_extraction"
    ]
    if (
        result.mode not in MODEL_API_MODES
        or len(extraction) != 1
        or extraction[0].mode not in MODEL_API_MODES
        or result.status == SemanticStatus.BLOCKED
        or result.candidates
        or result.clarifications
    ):
        return None
    issue_codes = {
        code
        for issue in result.candidate_issues
        for code in issue.reason_codes
    }
    violation_codes = {
        value.rsplit(":", 1)[-1] for value in result.safety_violations
    }
    codes = issue_codes | violation_codes
    if not codes:
        return "no_structured_match"
    if codes <= _SEMANTIC_HANDOFF_REASON_CODES:
        return "insufficient_semantic_detail"
    return None


def _persist_semantic_handoff(
    staging: Path,
    *,
    session_id: str,
    patient_id: str,
    source_run_id: str,
    original_text: str,
    reason_code: str,
) -> str:
    """Persist one typed, unassessed raw-text handoff on the staging database."""

    report_id = "semantic-handoff-" + uuid5(
        NAMESPACE_URL, f"{session_id}|{source_run_id}|{SEMANTIC_HANDOFF_POLICY_VERSION}"
    ).hex
    audit_id = "audit-" + uuid5(
        NAMESPACE_URL, f"{report_id}|semantic_handoff_requested"
    ).hex
    now = utc_now_iso()
    details = json.dumps(
        {
            "session_id": session_id,
            "source_run_id": source_run_id,
            "report_kind": "semantic_handoff",
            "reason_code": reason_code,
            "handoff_policy_version": SEMANTIC_HANDOFF_POLICY_VERSION,
            "clinical_assessment": "not_assessed",
            "structured_write": "disabled",
            "external_send": "disabled",
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    with sqlite3.connect(staging) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            INSERT INTO patient_supplemental_reports (
                report_id, patient_id, session_id, anchor_session_id,
                source_run_id, original_text, structured_items_json,
                observation_ids_json, report_kind, handoff_reason_code,
                handoff_policy_version, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, '[]', '[]',
                      'semantic_handoff', ?, ?, 'requested', ?)
            """,
            (
                report_id,
                patient_id,
                session_id,
                session_id,
                source_run_id,
                original_text,
                reason_code,
                SEMANTIC_HANDOFF_POLICY_VERSION,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO audit_events (
                event_id, patient_id, entity_type, entity_id,
                event_type, actor_type, details_json, created_at
            ) VALUES (?, ?, 'SemanticHandoffReport', ?,
                      'patient_semantic_handoff_requested',
                      'synthetic_patient', ?, ?)
            """,
            (audit_id, patient_id, report_id, details, now),
        )
    return report_id


def _readonly_connection(db_path: Path) -> sqlite3.Connection:
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _json_rows(connection: sqlite3.Connection, query: str, params=()) -> list[dict]:
    return [json.loads(row[0]) for row in connection.execute(query, params).fetchall()]


def _summary_mentions(summary: Layer4SummaryDraft, resource: dict[str, Any]) -> bool:
    reference = f"{resource['resourceType']}/{resource['id']}"
    version = resource.get("meta", {}).get("versionId")
    return any(
        evidence.resource.reference == reference
        and evidence.resource.version_id == version
        for item in summary.items
        for evidence in item.evidence_refs
    )


def _knowledge_status() -> tuple[bool, str | None]:
    # The clinical orchestration layer deliberately does not import Knowledge.
    # Availability is rendered and verified inside the independent page.
    return False, None


def _activation_row(connection: sqlite3.Connection) -> sqlite3.Row | None:
    rows = connection.execute(
        """
        SELECT a.*, s.status AS session_status,
               s.pathway_code, s.pathway_version,
               s.questionnaire_canonical, s.questionnaire_version,
               s.knowledge_release_id
        FROM audit_events a
        JOIN care_sessions s
          ON a.entity_type = 'CareSession' AND a.entity_id = s.session_id
        WHERE a.patient_id = ?
          AND a.event_type = 'doctor_pathway_activated'
        ORDER BY a.created_at DESC, a.event_id DESC
        """,
        (DEMO_PATIENT_ID,),
    ).fetchall()
    if len(rows) > 1:
        raise ValueError("multiple doctor plan activations")
    return rows[0] if rows else None


def _validated_activation(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    details = json.loads(row["details_json"])
    expected = {
        "pathway_code": row["pathway_code"],
        "pathway_version": row["pathway_version"],
        "questionnaire": (
            f"{row['questionnaire_canonical']}|{row['questionnaire_version']}"
        ),
        "knowledge_release_id": row["knowledge_release_id"],
        "decision": "activated",
        "synthetic_only": True,
        "clinical_risk_assessment": "not_assessed",
        "external_send": "disabled",
    }
    if row["actor_type"] not in {"simulated_doctor", "doctor_portal_user"} or details != expected:
        raise ValueError("doctor plan activation boundary mismatch")
    return {
        "session_id": row["entity_id"],
        "activated_at": row["created_at"],
        "actor": row["actor_type"],
    }


def read_competition_demo(db_path: Path | str) -> CompetitionDemoProgress:
    """Derive current progress with a SQLite read-only connection.

    A missing database is a truthful ``not_started`` result and is never
    created by this function.
    """

    path = Path(db_path)
    knowledge_available, knowledge_error = _knowledge_status()
    empty = CompetitionDemoProgress(
        knowledge_available=knowledge_available,
        knowledge_error=knowledge_error,
    )
    if not path.is_file():
        return empty

    try:
        with _readonly_connection(path) as connection:
            activation = _validated_activation(_activation_row(connection))
            handoff_rows = []
            if activation is not None:
                current_session = connection.execute(
                    """
                    SELECT session_id
                    FROM care_sessions
                    WHERE patient_id = ? AND parent_session_id IS NULL
                    ORDER BY created_at DESC, updated_at DESC, session_id DESC
                    LIMIT 1
                    """,
                    (DEMO_PATIENT_ID,),
                ).fetchone()
                if current_session is None:
                    raise ValueError("activated plan has no follow-up session")
                current_session_id = current_session["session_id"]
                handoff_rows = connection.execute(
                    """
                    SELECT p.report_id, p.source_run_id, p.status, p.created_at,
                           p.reviewed_at, p.handoff_reason_code,
                           p.handoff_policy_version
                    FROM patient_supplemental_reports p
                    JOIN agent_runs r ON r.run_id = p.source_run_id
                    WHERE p.anchor_session_id = ?
                      AND p.report_kind = 'semantic_handoff'
                      AND r.session_id = ?
                    ORDER BY p.created_at, p.report_id
                    """,
                    (current_session_id, current_session_id),
                ).fetchall()
                run_row = connection.execute(
                    """
                    SELECT r.*, s.status AS session_status,
                           s.pathway_code, s.pathway_version,
                           s.questionnaire_response_id, s.updated_at AS session_updated_at,
                           s.answers_json
                    FROM agent_runs r
                    JOIN care_sessions s ON s.session_id = r.session_id
                    WHERE r.patient_id = ? AND r.session_id = ?
                      AND r.task_id NOT LIKE 'supplemental:%'
                      AND NOT EXISTS (
                          SELECT 1 FROM patient_supplemental_reports p
                          WHERE p.source_run_id = r.run_id
                            AND p.report_kind = 'semantic_handoff'
                      )
                    ORDER BY r.completed_at DESC, r.run_id DESC
                    LIMIT 1
                    """,
                    (DEMO_PATIENT_ID, current_session_id),
                ).fetchone()
            else:
                run_row = connection.execute(
                    """
                SELECT r.*, s.status AS session_status,
                       s.pathway_code, s.pathway_version,
                       s.questionnaire_response_id, s.updated_at AS session_updated_at,
                       s.answers_json
                FROM agent_runs r
                JOIN care_sessions s ON s.session_id = r.session_id
                WHERE r.patient_id = ? AND r.input_text = ?
                  AND r.task_id NOT LIKE 'supplemental:%'
                ORDER BY r.completed_at DESC, r.run_id DESC
                LIMIT 1
                """,
                (DEMO_PATIENT_ID, MANUAL_REVIEW_MESSAGE),
                ).fetchone()
            if run_row is None:
                if activation is None:
                    return empty
                if connection.execute(
                    "SELECT status FROM care_sessions WHERE session_id = ?",
                    (current_session_id,),
                ).fetchone()[0] != "in_progress":
                    raise ValueError("current follow-up session is not in progress")
                counts = {
                    "agent_runs": connection.execute(
                        """
                        SELECT COUNT(*) FROM agent_runs r
                        WHERE r.patient_id = ? AND r.session_id = ?
                          AND NOT EXISTS (
                              SELECT 1 FROM patient_supplemental_reports p
                              WHERE p.source_run_id = r.run_id
                                AND p.report_kind = 'semantic_handoff'
                          )
                        """,
                        (DEMO_PATIENT_ID, current_session_id),
                    ).fetchone()[0],
                    "questionnaire_responses": connection.execute(
                        "SELECT COUNT(*) FROM care_sessions "
                        "WHERE session_id = ? AND questionnaire_response_id IS NOT NULL",
                        (current_session_id,),
                    ).fetchone()[0],
                }
                if any(counts.values()):
                    raise ValueError("activated plan contains downstream facts")
                audit_count = connection.execute(
                    "SELECT COUNT(*) FROM audit_events WHERE patient_id = ?",
                    (DEMO_PATIENT_ID,),
                ).fetchone()[0]
                milestones = {item: False for item in MILESTONE_ORDER}
                milestones[CompetitionDemoStage.PLAN_ACTIVATED.value] = True
                handoff_revision = (
                    hashlib.sha256(
                        json.dumps(
                            [tuple(row) for row in handoff_rows],
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest()[:20]
                    if handoff_rows
                    else None
                )
                stage = (
                    CompetitionDemoStage.PATIENT_COLLECTING
                    if handoff_rows
                    else CompetitionDemoStage.PLAN_ACTIVATED
                )
                milestones[stage.value] = True
                return CompetitionDemoProgress(
                    stage=stage,
                    milestones=milestones,
                    generation=(
                        f"{current_session_id}:{handoff_revision}"
                        if handoff_revision
                        else f"{current_session_id}:pending"
                    ),
                    plan_activated=True,
                    plan_activated_at=activation["activated_at"],
                    plan_actor=activation["actor"],
                    session_id=current_session_id,
                    session_status="in_progress",
                    audit_count=audit_count,
                    knowledge_available=knowledge_available,
                    knowledge_error=knowledge_error,
                    next_page="pages/1_patient_followup.py",
                    next_label="前往患者端提交合成随访",
                    next_help=(
                        "患者原话已进入语义人工复核；当前 Pathway 问题仍可继续回答。"
                        if handoff_rows
                        else "患者明确点击后才会调用豆包；模型结果仍须患者确认。"
                    ),
                )

            result = SemanticResult.model_validate(json.loads(run_row["output_json"]))
            decisions = {
                row["action_id"]: row["decision"]
                for row in connection.execute(
                    """
                    SELECT action_id, decision
                    FROM conversation_action_resolutions
                    WHERE session_id = ?
                    """,
                    (run_row["session_id"],),
                ).fetchall()
            }
            draft_decisions = {
                row["action_id"]: row["decision"]
                for row in connection.execute(
                    "SELECT action_id, decision FROM patient_draft_action_resolutions "
                    "WHERE session_id=? AND status='active'",
                    (run_row["session_id"],),
                ).fetchall()
            }
            if set(decisions) & set(draft_decisions):
                raise ValueError("an action cannot be both provisional and confirmed")
            decisions.update(draft_decisions)
            has_collection_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='patient_collection_resolutions'"
            ).fetchone()
            collection_resolutions = (
                {
                    row["link_id"]: row["resolution"]
                    for row in connection.execute(
                        """
                        SELECT link_id, resolution
                        FROM patient_collection_resolutions
                        WHERE session_id = ? AND is_current = 1
                        """,
                        (run_row["session_id"],),
                    ).fetchall()
                }
                if has_collection_table
                else {}
            )
            response_id = run_row["questionnaire_response_id"]
            response = None
            if response_id:
                row = connection.execute(
                    """
                    SELECT resource_json FROM fhir_questionnaire_responses
                    WHERE resource_id = ? AND patient_id = ?
                    """,
                    (response_id, DEMO_PATIENT_ID),
                ).fetchone()
                response = json.loads(row[0]) if row else None
            observations = _json_rows(
                connection,
                """
                SELECT resource_json FROM fhir_observations
                WHERE patient_id = ? AND questionnaire_response_id = ?
                ORDER BY observation_id
                """,
                (DEMO_PATIENT_ID, response_id or ""),
            )

            all_tasks = _json_rows(
                connection,
                """
                SELECT resource_json FROM layer4_fhir_resources
                WHERE patient_id = ? AND resource_type = 'Task' AND is_current = 1
                """,
                (DEMO_PATIENT_ID,),
            )
            pathway_ref = (
                f"urn:continucare:pathway:{run_row['pathway_code']}"
                f"|{run_row['pathway_version']}"
            )
            manual_tasks = [
                item
                for item in all_tasks
                if is_manual_review_task(item)
                and pathway_ref
                in {ref.get("reference") for ref in item.get("basedOn", [])}
                and response_id is not None
                and item.get("reasonReference", {}).get("reference")
                == f"QuestionnaireResponse/{response_id}"
            ]
            manual_tasks.sort(
                key=lambda item: (
                    item.get("meta", {}).get("lastUpdated", ""), item["id"]
                ),
                reverse=True,
            )
            task = manual_tasks[0] if manual_tasks else None

            all_communications = _json_rows(
                connection,
                """
                SELECT resource_json FROM layer4_fhir_resources
                WHERE patient_id = ? AND resource_type = 'Communication'
                ORDER BY updated_at, resource_id, version_id
                """,
                (DEMO_PATIENT_ID,),
            )
            communications = [
                item
                for item in all_communications
                if is_manual_review_communication(item)
                and task is not None
                and any(
                    ref.get("reference", "").startswith(f"Task/{task['id']}/_history/")
                    for ref in item.get("basedOn", [])
                )
            ]
            current_communications = [
                item
                for item in communications
                if connection.execute(
                    """
                    SELECT is_current FROM layer4_fhir_resources
                    WHERE resource_type='Communication' AND resource_id=? AND version_id=?
                    """,
                    (item["id"], item["meta"]["versionId"]),
                ).fetchone()[0]
                == 1
            ]
            communication = current_communications[-1] if current_communications else None

            summary_rows = connection.execute(
                """
                SELECT record_json FROM layer4_contract_records
                WHERE patient_id = ? AND record_type='summary_draft'
                ORDER BY updated_at, record_id, record_version
                """,
                (DEMO_PATIENT_ID,),
            ).fetchall()
            summaries = []
            for row in summary_rows:
                candidate = Layer4SummaryDraft.model_validate(json.loads(row[0]))
                if (
                    candidate.summary_kind == "manual_review_brief"
                    and candidate.pathway_code == run_row["pathway_code"]
                    and candidate.pathway_version == run_row["pathway_version"]
                    and (task is None or _summary_mentions(candidate, task))
                ):
                    summaries.append(candidate)
            current_summary = max(
                summaries,
                key=lambda item: (item.created_at, item.summary_id, item.version),
                default=None,
            )

            alert_count = connection.execute(
                "SELECT COUNT(*) FROM alerts WHERE patient_id = ?",
                (DEMO_PATIENT_ID,),
            ).fetchone()[0]
            approved_rule_count = connection.execute(
                """
                SELECT COUNT(*) FROM layer4_contract_records
                WHERE record_type='clinical_rule' AND status IN ('approved', 'active')
                """
            ).fetchone()[0]
            provenance_count = connection.execute(
                """
                SELECT COUNT(*) FROM layer4_fhir_resources
                WHERE patient_id = ? AND resource_type='Provenance'
                """,
                (DEMO_PATIENT_ID,),
            ).fetchone()[0]
            audit_count = connection.execute(
                "SELECT COUNT(*) FROM audit_events WHERE patient_id = ?",
                (DEMO_PATIENT_ID,),
            ).fetchone()[0]
    except (sqlite3.Error, ValueError, KeyError, TypeError):
        return empty.model_copy(
            update={"integrity_issue": "本地合成 Demo 数据不可读取；请明确重新开始。"}
        )

    expected_source = f"QuestionnaireResponse/{response_id}" if response_id else None
    patient_confirmed = bool(
        run_row["session_status"] == "completed"
        and response
        and response.get("status") == "completed"
        and observations
        and all(
            item.get("status") == "final"
            and expected_source
            in {ref.get("reference") for ref in item.get("derivedFrom", [])}
            for item in observations
        )
    )
    readiness = communication_readiness(communication) if communication else None
    has_pending_communication = any(
        communication_readiness(item) == PENDING_APPROVAL for item in communications
    )
    current_pending_brief = bool(
        communication
        and readiness == PENDING_APPROVAL
        and current_summary
        and _summary_mentions(current_summary, communication)
    )
    current_ready_brief = bool(
        communication
        and readiness == READY_TO_SEND
        and current_summary
        and _summary_mentions(current_summary, communication)
    )
    task_status = task.get("status") if task else None
    candidate_ids = {item.candidate_id for item in result.candidates}
    clarification_ids = {
        item.clarification_id for item in result.clarifications
    }
    action_ids = candidate_ids | clarification_ids
    candidate_resolution_values = {
        candidate_id: decisions.get(candidate_id) for candidate_id in candidate_ids
    }
    action_resolution_values = {
        action_id: decisions.get(action_id) for action_id in action_ids
    }
    has_pending_candidate = any(
        decision is None for decision in candidate_resolution_values.values()
    )
    all_candidates_rejected = bool(candidate_ids) and all(
        decision == "rejected" for decision in candidate_resolution_values.values()
    )
    has_unsure_candidate = any(
        decision == "unsure" for decision in candidate_resolution_values.values()
    )
    candidate_stage = CompetitionDemoStage.CANDIDATE_READY
    if all_candidates_rejected:
        candidate_stage = (
            CompetitionDemoStage.PATIENT_COLLECTING
            if activation is not None and result.mode in MODEL_API_MODES
            else CompetitionDemoStage.CANDIDATE_REJECTED
        )
    elif has_unsure_candidate and not has_pending_candidate:
        candidate_stage = CompetitionDemoStage.CANDIDATE_UNSURE

    milestones = {item: False for item in MILESTONE_ORDER}
    milestones.update(
        {
            CompetitionDemoStage.PLAN_ACTIVATED.value: activation is not None,
            CompetitionDemoStage.CANDIDATE_READY.value: bool(result.candidates),
            CompetitionDemoStage.CANDIDATE_UNSURE.value: candidate_stage
            == CompetitionDemoStage.CANDIDATE_UNSURE,
            CompetitionDemoStage.CANDIDATE_REJECTED.value: candidate_stage
            == CompetitionDemoStage.CANDIDATE_REJECTED,
            CompetitionDemoStage.PATIENT_CONFIRMED.value: patient_confirmed,
            CompetitionDemoStage.TASK_REQUESTED.value: task is not None,
            CompetitionDemoStage.NURSE_RECEIVED.value: task_status
            in {"received", "accepted", "in-progress", "completed"},
            CompetitionDemoStage.NURSE_IN_PROGRESS.value: task_status
            in {"accepted", "in-progress", "completed"},
            CompetitionDemoStage.TASK_REJECTED.value: task_status == "rejected",
            CompetitionDemoStage.TASK_CANCELLED.value: task_status == "cancelled",
            CompetitionDemoStage.TASK_FAILED.value: task_status == "failed",
            CompetitionDemoStage.TASK_ENTERED_IN_ERROR.value: task_status
            == "entered-in-error",
            CompetitionDemoStage.COMMUNICATION_PENDING.value: has_pending_communication,
            CompetitionDemoStage.DOCTOR_BRIEF_PENDING.value: current_pending_brief
            or any(
                communication_readiness(item) == PENDING_APPROVAL
                and any(_summary_mentions(summary, item) for summary in summaries)
                for item in communications
            ),
            CompetitionDemoStage.COMMUNICATION_READY.value: readiness
            == READY_TO_SEND,
            CompetitionDemoStage.DOCTOR_BRIEF_READY.value: current_ready_brief,
            CompetitionDemoStage.STORY_COMPLETE.value: bool(
                task_status == "completed" and current_ready_brief
            ),
        }
    )

    stage = candidate_stage
    next_page = "pages/1_patient_followup.py"
    next_label = "前往患者端明确确认"
    next_help = "患者确认前不会创建 QR、Observation 或护士任务。"
    is_terminal = False
    terminal_reason = None
    terminal_tasks = {
        "rejected": (
            CompetitionDemoStage.TASK_REJECTED,
            "护士已明确拒绝人工复核任务；流程已终止，未创建 Communication 或医生简报。",
        ),
        "cancelled": (
            CompetitionDemoStage.TASK_CANCELLED,
            "护士已明确取消人工复核任务；流程已终止，未创建 Communication 或医生简报。",
        ),
        "failed": (
            CompetitionDemoStage.TASK_FAILED,
            "人工复核任务以 failed 异常终止；流程已 fail-closed，未继续生成业务资源。",
        ),
        "entered-in-error": (
            CompetitionDemoStage.TASK_ENTERED_IN_ERROR,
            "人工复核任务已标记 entered-in-error；流程已 fail-closed，未继续生成业务资源。",
        ),
    }
    if task_status in terminal_tasks:
        stage, terminal_reason = terminal_tasks[task_status]
        is_terminal = True
    elif task is not None and task_status == "requested":
        stage = CompetitionDemoStage.TASK_REQUESTED
        next_page = "pages/2_nurse_risk_center.py"
        next_label = "前往护士端确认收到"
        next_help = "任务优先级为 routine，临床评估仍为 not_assessed。"
    elif task_status == "received":
        stage = CompetitionDemoStage.NURSE_RECEIVED
        next_page = "pages/2_nurse_risk_center.py"
        next_label = "接受并开始人工复核"
        next_help = "接受与开始是同一次明确动作。"
    elif task_status in {"accepted", "in-progress"}:
        stage = CompetitionDemoStage.NURSE_IN_PROGRESS
        next_page = "pages/2_nurse_risk_center.py"
        next_label = "记录受控结果并生成草稿"
        next_help = "草稿生成后仍须人工批准，且不会发送。"
    elif task_status == "completed" and readiness == PENDING_APPROVAL:
        if current_pending_brief:
            stage = CompetitionDemoStage.DOCTOR_BRIEF_PENDING
            next_page = "pages/2_nurse_risk_center.py"
            next_label = "返回护士端人工批准草稿"
            next_help = "批准只改变 readiness；ready-to-send 不等于 sent。"
        else:
            stage = CompetitionDemoStage.COMMUNICATION_PENDING
            next_page = "pages/3_doctor_summary.py"
            next_label = "前往医生端生成 pending 简报"
            next_help = "简报只在明确点击后生成。"
    elif task_status == "completed" and readiness == READY_TO_SEND:
        if current_ready_brief:
            stage = CompetitionDemoStage.STORY_COMPLETE
            is_terminal = True
            terminal_reason = (
                "合成 happy path 的 9 项持久化事实已完成；"
                "这不代表临床结论，Communication 仍未发送。"
            )
        else:
            stage = CompetitionDemoStage.COMMUNICATION_READY
            next_page = "pages/3_doctor_summary.py"
            next_label = "前往医生端生成或刷新 ready 简报"
            next_help = "当前来源已变化，旧简报保持不可变并显示陈旧。"
    elif patient_confirmed:
        stage = CompetitionDemoStage.PATIENT_CONFIRMED

    if (
        activation is not None
        and run_row["session_status"] == "in_progress"
        and task is None
        and action_ids
        and not any(value is None for value in action_resolution_values.values())
        and candidate_stage
        not in {
            CompetitionDemoStage.CANDIDATE_UNSURE,
            CompetitionDemoStage.CANDIDATE_REJECTED,
        }
    ):
        try:
            store = SQLiteStore(path, initialize=False)
            session = store.get_care_session(run_row["session_id"])
            if session is None:
                raise ValueError("activated session missing")
            from continucare.services.patient_checkin import project_patient_checkin

            locked_questionnaire = CareEngine(store).questionnaire_for_session(session)

            checkin = project_patient_checkin(
                session,
                locked_questionnaire,
                explicit_unknown_link_ids={
                    link_id
                    for link_id, resolution in collection_resolutions.items()
                    if resolution == "explicit_unknown"
                },
                collection_link_ids=active_patient_link_ids(
                    path,
                    patient_id=session.patient_id,
                    pathway_code=session.pathway_code,
                    questionnaire=locked_questionnaire,
                ),
            )
            stage = (
                CompetitionDemoStage.PATIENT_REVIEW_READY
                if checkin.ready_to_submit
                else CompetitionDemoStage.PATIENT_COLLECTING
            )
        except (LookupError, ValueError, TypeError):
            return empty.model_copy(
                update={"integrity_issue": "患者采集状态不可验证；请刷新后重试。"}
            )

    if stage == CompetitionDemoStage.CANDIDATE_REJECTED:
        is_terminal = True
        terminal_reason = (
            "所有候选均已由患者明确拒绝；未创建临床资源或护士任务。"
        )
    elif stage == CompetitionDemoStage.CANDIDATE_UNSURE:
        next_label = "返回患者端明确接受或拒绝"
        next_help = "暂不确定不是终态；患者仍可明确接受或拒绝现有候选。"

    if is_terminal:
        next_page = "pages/4_audit_log.py"
        next_label = "查看终态审计"
        next_help = (
            f"{terminal_reason} 如需新故事，请返回首页勾选确认后明确重新开始；"
            "系统不会自动重启。"
        )

    revision_payload = json.dumps(
        {
            "session_updated_at": run_row["session_updated_at"],
            "run_id": run_row["run_id"],
            "decisions": sorted(action_resolution_values.items()),
            "collection_resolutions": sorted(collection_resolutions.items()),
            "session_status": run_row["session_status"],
            "semantic_handoffs": [tuple(row) for row in handoff_rows],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    revision = hashlib.sha256(revision_payload.encode("utf-8")).hexdigest()[:20]
    generation = f"{run_row['session_id']}:{revision}"
    return CompetitionDemoProgress(
        stage=stage,
        milestones=milestones,
        generation=generation,
        plan_activated=activation is not None,
        plan_activated_at=(activation or {}).get("activated_at"),
        plan_actor=(activation or {}).get("actor"),
        session_id=run_row["session_id"],
        run_id=run_row["run_id"],
        task_id=task["id"] if task else None,
        communication_id=communication["id"] if communication else None,
        summary_id=current_summary.summary_id if current_summary else None,
        summary_version=current_summary.version if current_summary else None,
        session_status=run_row["session_status"],
        task_status=task_status,
        communication_readiness=readiness,
        candidate_count=len(result.candidates),
        candidate_decisions={
            action_id: decisions[action_id]
            for action_id in action_ids
            if action_id in decisions
        },
        collection_resolutions=collection_resolutions,
        questionnaire_response_count=1 if response else 0,
        observation_count=len(observations),
        manual_task_count=len(manual_tasks),
        communication_count=len(communications),
        manual_brief_count=len(summaries),
        provenance_count=provenance_count,
        audit_count=audit_count,
        alert_count=alert_count,
        approved_clinical_rule_count=approved_rule_count,
        knowledge_available=knowledge_available,
        knowledge_error=knowledge_error,
        is_terminal=is_terminal,
        terminal_reason=terminal_reason,
        next_page=next_page,
        next_label=next_label,
        next_help=next_help,
    )


def _lock_path(db_path: Path) -> Path:
    return db_path.with_name(f".{db_path.name}.m5d.lock")


@contextmanager
def demo_write_guard(
    db_path: Path | str,
    *,
    expected_generation: str | None | object = _EXPECTED_GENERATION_UNSET,
) -> Iterator[None]:
    """Serialize local UI writes with reset/replace and reject stale stories."""

    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(_lock_path(path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        if expected_generation is not _EXPECTED_GENERATION_UNSET:
            current = read_competition_demo(path)
            if current.generation != expected_generation:
                raise CompetitionDemoConflict(
                    "比赛故事已在另一标签页重新开始，请刷新当前页面后继续。"
                )
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _sidecars(path: Path) -> tuple[Path, ...]:
    return tuple(Path(f"{path}{suffix}") for suffix in ("-journal", "-wal", "-shm"))


def _cleanup_exact_database_files(path: Path) -> None:
    for candidate in (path, *_sidecars(path)):
        if candidate.exists() and candidate.is_file():
            candidate.unlink()


def _atomic_stage_replace(
    db_path: Path,
    loader: Callable[[Path], Any],
    validator: Callable[[Path], None],
    *,
    expected_generation: str | None | object = _EXPECTED_GENERATION_UNSET,
    seed_from_existing: bool = False,
) -> Any:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with demo_write_guard(db_path, expected_generation=expected_generation):
        prefix = f".{db_path.name}.m5d-"
        for orphan in db_path.parent.glob(f"{prefix}*"):
            if orphan.is_file():
                orphan.unlink()
        descriptor, staging_name = tempfile.mkstemp(
            prefix=prefix, suffix=".sqlite", dir=db_path.parent
        )
        os.close(descriptor)
        staging = Path(staging_name)
        try:
            if seed_from_existing:
                if not db_path.is_file():
                    raise CompetitionDemoStartError("当前随访方案不存在，请先由医生启动。")
                with sqlite3.connect(db_path) as source, sqlite3.connect(staging) as target:
                    source.backup(target)
            result = loader(staging)
            os.chmod(staging, 0o600)
            if any(item.exists() for item in _sidecars(staging)):
                raise CompetitionDemoStartError("临时 Demo 数据未安全关闭，请重试。")
            validator(staging)
            staging_fd = os.open(staging, os.O_RDONLY)
            try:
                os.fsync(staging_fd)
            finally:
                os.close(staging_fd)

            # Explicit reset is the only operation allowed to remove exact
            # local SQLite sidecars.  The shared lock excludes all app writes.
            for sidecar in _sidecars(db_path):
                if sidecar.exists() and sidecar.is_file():
                    sidecar.unlink()
            os.replace(staging, db_path)
            directory_fd = os.open(db_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            if any(item.exists() for item in _sidecars(db_path)):
                raise CompetitionDemoStartError("Demo 数据替换未完成，请重试。")
            return result
        finally:
            _cleanup_exact_database_files(staging)


def _validate_candidate_start(staging: Path) -> None:
    progress = read_competition_demo(staging)
    if (
        progress.stage != CompetitionDemoStage.CANDIDATE_READY
        or progress.candidate_count < 1
        or progress.candidate_decisions
        or progress.questionnaire_response_count != 0
        or progress.observation_count != 0
        or progress.manual_task_count != 0
        or progress.communication_count != 0
        or progress.manual_brief_count != 0
        or progress.alert_count != 0
        or progress.approved_clinical_rule_count != 0
    ):
        raise CompetitionDemoStartError("完整比赛 Demo 起点校验失败，请重试。")


def _validate_mimo_candidate_start(staging: Path) -> None:
    """Require proof that the online story came from MiMo, never fallback."""

    _validate_candidate_start(staging)
    with _readonly_connection(staging) as connection:
        row = connection.execute(
            """
            SELECT output_json
            FROM agent_runs
            WHERE patient_id = ? AND input_text = ?
            ORDER BY completed_at DESC, run_id DESC
            LIMIT 1
            """,
            (DEMO_PATIENT_ID, MANUAL_REVIEW_MESSAGE),
        ).fetchone()
    if row is None:
        raise CompetitionDemoStartError(
            "豆包在线生成未通过安全契约，原来的演示记录没有被替换。"
        )
    result = SemanticResult.model_validate(json.loads(row["output_json"]))
    extraction_traces = [
        item for item in result.stage_traces if item.stage == "care_extraction"
    ]
    if (
        result.mode not in MODEL_API_MODES
        or len(extraction_traces) != 1
        or extraction_traces[0].mode not in MODEL_API_MODES
        or not result.candidates
        or any(item.source_mode not in MODEL_CANDIDATE_SOURCES for item in result.candidates)
    ):
        raise CompetitionDemoStartError(
            "豆包在线生成未通过安全契约，原来的演示记录没有被替换。"
        )


def _validate_plan_activation(staging: Path) -> None:
    progress = read_competition_demo(staging)
    if (
        progress.stage != CompetitionDemoStage.PLAN_ACTIVATED
        or not progress.plan_activated
        or not progress.session_id
        or progress.generation != f"{progress.session_id}:pending"
        or progress.plan_actor not in {"simulated_doctor", "doctor_portal_user"}
        or progress.candidate_count != 0
        or progress.questionnaire_response_count != 0
        or progress.observation_count != 0
        or progress.manual_task_count != 0
        or progress.communication_count != 0
        or progress.alert_count != 0
        or progress.approved_clinical_rule_count != 0
    ):
        raise CompetitionDemoStartError("医生随访方案启动校验失败；原记录未被替换。")


def _validate_plan_candidate(staging: Path, *, require_mimo: bool) -> None:
    _validate_candidate_start(staging)
    if require_mimo:
        _validate_mimo_candidate_start(staging)
    progress = read_competition_demo(staging)
    if (
        not progress.plan_activated
        or progress.plan_actor not in {"simulated_doctor", "doctor_portal_user"}
        or not progress.session_id
        or not progress.run_id
        or not progress.generation
    ):
        raise CompetitionDemoStartError(
            "患者提交没有保留医生启动的方案；原记录没有变化。"
        )


def submit_patient_chat_turn(
    db_path: Path | str,
    *,
    expected_generation: str,
    message_text: str,
    synthetic_confirmed: bool,
    target_link_id: str | None = None,
    selected_revision_link_id: str | None = None,
    model_adapter: MiMoSemanticAdapter | None = None,
    auto_stage_draft: bool = False,
) -> CompetitionDemoProgress:
    """Run one default-MiMo, candidate-only patient turn on a staging copy."""

    text = validate_synthetic_chat_message(
        message_text, synthetic_confirmed=synthetic_confirmed
    )
    path = Path(db_path)
    before = read_competition_demo(path)
    if (
        before.generation != expected_generation
        or not before.plan_activated
        or not before.session_id
        or before.stage
        not in {
            CompetitionDemoStage.PLAN_ACTIVATED,
            CompetitionDemoStage.PATIENT_COLLECTING,
            CompetitionDemoStage.PATIENT_REVIEW_READY,
        }
    ):
        raise CompetitionDemoConflict("患者随访状态已经变化，请刷新后继续。")
    adapter = model_adapter or _competition_mimo_adapter()
    if (
        not isinstance(adapter, MiMoSemanticAdapter)
        or not adapter.configured
        or adapter.config.safety_llm_enabled
        or adapter.config.language_llm_enabled
        or adapter.config.summary_llm_enabled
    ):
        raise CompetitionDemoStartError("豆包当前不可用；本次回答没有发送或保存。")

    before_store = SQLiteStore(path, initialize=False)
    before_session = before_store.get_care_session(before.session_id)
    if before_session is None or before_session.status.value != "in_progress":
        raise CompetitionDemoConflict("今天的随访已经结束，请刷新页面。")
    locked_questionnaire = CareEngine(before_store).questionnaire_for_session(
        before_session
    )
    before_runs = before_store.list_agent_runs(before.session_id)
    before_run_ids = {item.run_id for item in before_runs}
    before_active_contexts = sorted(
        (
            item.model_dump_json(exclude_none=False)
            for item in before_store.list_active_answer_contexts(before.session_id)
        )
    )
    before_provisional_contexts = sorted(
        item.model_dump_json(exclude_none=False)
        for item in before_store.list_active_provisional_answer_contexts(
            before.session_id
        )
    )
    before_provisional_reports = sorted(
        item.model_dump_json(exclude_none=False)
        for item in before_store.list_active_provisional_symptom_reports(
            before.session_id
        )
    )
    before_draft_decisions = before_store.provisional_action_decisions(
        before.session_id
    )
    with sqlite3.connect(path) as connection:
        before_report_ids = {
            row[0]
            for row in connection.execute(
                "SELECT report_id FROM patient_supplemental_reports "
                "WHERE anchor_session_id=?",
                (before.session_id,),
            ).fetchall()
        }
    focus = resolve_patient_chat_focus(
        before_session,
        message_text=text,
        default_link_id=target_link_id,
        selected_revision_link_id=selected_revision_link_id,
        active_contexts=(
            before_store.list_active_provisional_answer_contexts(before.session_id)
            if auto_stage_draft
            else before_store.list_active_answer_contexts(before.session_id)
        ),
        run_ids_newest_first=[item.run_id for item in before_runs],
        collection_resolutions=before_store.current_collection_resolutions(
            before.session_id
        ),
        collection_link_ids=active_patient_link_ids(
            path,
            patient_id=before_session.patient_id,
            pathway_code=before_session.pathway_code,
            questionnaire=locked_questionnaire,
        ),
    )
    focus_link_ids = list(focus.link_ids)

    def loader(staging: Path):
        store = SQLiteStore(staging, initialize=False)
        session = store.get_care_session(before.session_id or "")
        if session is None:
            raise CompetitionDemoStartError("医生启动的随访方案不可读取。")
        service = CareAgentService(
            store,
            care_engine=CareEngine(store),
            model_adapter=adapter,
            patient_timezone="Asia/Shanghai",
            terminology_backend=(
                load_supplemental_terminology_backend()
                if auto_stage_draft
                else None
            ),
        )
        interaction = (
            service.analyze_patient_checkin(
                session.session_id,
                text,
                focus_link_ids=focus_link_ids,
            )
            if auto_stage_draft
            else service.analyze(
                session.session_id,
                text,
                candidate_only=True,
                focus_link_ids=focus_link_ids,
            )
        )
        handoff_reason = _semantic_handoff_reason(interaction.result)
        if handoff_reason is not None:
            _persist_semantic_handoff(
                staging,
                session_id=session.session_id,
                patient_id=interaction.record.patient_id,
                source_run_id=interaction.record.run_id,
                original_text=text,
                reason_code=handoff_reason,
            )
        elif auto_stage_draft and interaction.result.candidates:
            include_original = "free-text-report" not in session.answers
            service.stage_candidates_for_final_review(
                interaction.record.run_id,
                [item.candidate_id for item in interaction.result.candidates],
                include_original_text=include_original,
                track_original_text_context=include_original,
            )
        return interaction

    def validator(staging: Path) -> None:
        store = SQLiteStore(staging, initialize=False)
        session = store.get_care_session(before.session_id or "")
        runs = store.list_agent_runs(before.session_id or "")
        new_runs = [item for item in runs if item.run_id not in before_run_ids]
        progress = read_competition_demo(staging)
        active_contexts = sorted(
            (
                item.model_dump_json(exclude_none=False)
                for item in store.list_active_answer_contexts(before.session_id or "")
            )
        )
        provisional_contexts = sorted(
            item.model_dump_json(exclude_none=False)
            for item in store.list_active_provisional_answer_contexts(
                before.session_id or ""
            )
        )
        provisional_reports = sorted(
            item.model_dump_json(exclude_none=False)
            for item in store.list_active_provisional_symptom_reports(
                before.session_id or ""
            )
        )
        if (
            session is None
            or active_contexts != before_active_contexts
            or len(new_runs) != 1
            or progress.session_id != before.session_id
            or progress.questionnaire_response_count != before.questionnaire_response_count
            or progress.observation_count != before.observation_count
            or progress.manual_task_count != before.manual_task_count
            or progress.communication_count != before.communication_count
            or progress.alert_count != 0
            or progress.approved_clinical_rule_count != 0
        ):
            raise CompetitionDemoStartError("豆包回答未通过患者采集边界校验。")
        record = new_runs[0]
        result = SemanticResult.model_validate(record.output_json)
        extraction = [
            item for item in result.stage_traces if item.stage == "care_extraction"
        ]
        clarification_targets = [
            (
                item.proposed_candidate.link_id
                if item.proposed_candidate is not None
                else item.target_link_id
            )
            for item in result.clarifications
        ]
        if (
            record.model_provider not in MODEL_API_PROVIDERS
            or record.model_name != adapter.config.model_name
            or record.prompt_version != adapter.config.prompt_version
            or result.mode not in MODEL_API_MODES
            or len(extraction) != 1
            or extraction[0].mode not in MODEL_API_MODES
        ):
            raise CompetitionDemoStartError("豆包运行来源未通过患者采集边界校验。")
        with sqlite3.connect(staging) as connection:
            connection.row_factory = sqlite3.Row
            report_rows = connection.execute(
                "SELECT * FROM patient_supplemental_reports "
                "WHERE anchor_session_id=? ORDER BY created_at, report_id",
                (before.session_id,),
            ).fetchall()
            handoff_audits = connection.execute(
                "SELECT entity_id, details_json FROM audit_events "
                "WHERE event_type='patient_semantic_handoff_requested' "
                "AND patient_id=?",
                (DEMO_PATIENT_ID,),
            ).fetchall()
        after_report_ids = {row["report_id"] for row in report_rows}
        handoff_reason = _semantic_handoff_reason(result)
        if handoff_reason is not None:
            new_report_ids = after_report_ids - before_report_ids
            expected_handoff_stage = (
                CompetitionDemoStage.PATIENT_REVIEW_READY
                if before.stage == CompetitionDemoStage.PATIENT_REVIEW_READY
                else CompetitionDemoStage.PATIENT_COLLECTING
            )
            if (
                session != before_session
                or provisional_contexts != before_provisional_contexts
                or provisional_reports != before_provisional_reports
                or store.provisional_action_decisions(before.session_id or "")
                != before_draft_decisions
                or
                progress.stage != expected_handoff_stage
                or progress.run_id != before.run_id
                or len(new_report_ids) != 1
                or len(after_report_ids) != len(before_report_ids) + 1
            ):
                raise CompetitionDemoStartError("语义人工复核没有与主采集状态隔离。")
            report_id = next(iter(new_report_ids))
            report = next(row for row in report_rows if row["report_id"] == report_id)
            audit_rows = [row for row in handoff_audits if row["entity_id"] == report_id]
            if (
                report["source_run_id"] != record.run_id
                or report["session_id"] != before.session_id
                or report["report_kind"] != "semantic_handoff"
                or report["handoff_reason_code"] != handoff_reason
                or report["handoff_policy_version"]
                != SEMANTIC_HANDOFF_POLICY_VERSION
                or report["structured_items_json"] != "[]"
                or report["questionnaire_response_id"] is not None
                or json.loads(report["observation_ids_json"] or "[]")
                or report["provenance_id"] is not None
                or report["status"] != "requested"
                or len(audit_rows) != 1
            ):
                raise CompetitionDemoStartError("语义人工复核记录边界校验失败。")
            return
        if auto_stage_draft and result.candidates:
            draft_decisions = store.provisional_action_decisions(
                before.session_id or ""
            )
            new_candidate_ids = {item.candidate_id for item in result.candidates}
            structured = [
                item
                for item in result.candidates
                if not item.link_id.startswith("patient-reported-symptom::")
            ]
            dynamic = [
                item
                for item in result.candidates
                if item.link_id.startswith("patient-reported-symptom::")
            ]
            current_contexts = store.list_active_provisional_answer_contexts(
                before.session_id or ""
            )
            current_reports = store.list_active_provisional_symptom_reports(
                before.session_id or ""
            )
            context_by_link = {item.link_id: item for item in current_contexts}
            staged_audits = [
                event
                for event in store.list_audit_events(DEMO_PATIENT_ID)
                if event.entity_type == "AgentRun"
                and event.entity_id == record.run_id
                and event.event_type == "semantic_candidate_staged_to_draft"
            ]
            patient_confirmation_audits = [
                event
                for event in store.list_audit_events(DEMO_PATIENT_ID)
                if event.entity_type == "AgentRun"
                and event.entity_id == record.run_id
                and event.event_type == "semantic_candidate_patient_decision"
            ]
            locked_questionnaire = CareEngine(store).questionnaire_for_session(
                session
            )
            expected_stage = (
                CompetitionDemoStage.CANDIDATE_READY
                if result.clarifications
                else (
                    CompetitionDemoStage.PATIENT_REVIEW_READY
                    if project_patient_checkin(
                        session,
                        locked_questionnaire,
                        explicit_unknown_link_ids={
                            link_id
                            for link_id, resolution in progress.collection_resolutions.items()
                            if resolution == "explicit_unknown"
                        },
                        collection_link_ids=active_patient_link_ids(
                            staging,
                            patient_id=session.patient_id,
                            pathway_code=session.pathway_code,
                            questionnaire=locked_questionnaire,
                        ),
                    ).ready_to_submit
                    else CompetitionDemoStage.PATIENT_COLLECTING
                )
            )
            if (
                session.status.value != "in_progress"
                or any(
                    draft_decisions.get(candidate_id) != "drafted"
                    for candidate_id in new_candidate_ids
                )
                or set(draft_decisions) != set(before_draft_decisions) | new_candidate_ids
                or any(
                    context_by_link.get(item.link_id) is None
                    or context_by_link[item.link_id].answer != item.answer
                    or context_by_link[item.link_id].source_run_id != record.run_id
                    for item in structured
                )
                or any(
                    not any(
                        report.source_run_id == record.run_id
                        and report.concept_id == item.terminology_match.concept_id
                        for report in current_reports
                    )
                    for item in dynamic
                )
                or len(staged_audits) != 1
                or patient_confirmation_audits
                or progress.stage != expected_stage
                or after_report_ids != before_report_ids
            ):
                raise CompetitionDemoStartError(
                    "豆包草稿没有保持单次最终确认边界。"
                )
            return
        if (
            session != before_session
            or provisional_contexts != before_provisional_contexts
            or provisional_reports != before_provisional_reports
            or store.provisional_action_decisions(before.session_id or "")
            != before_draft_decisions
        ):
            raise CompetitionDemoStartError("未确认候选意外改变了患者草稿。")
        if (
            progress.stage != CompetitionDemoStage.CANDIDATE_READY
            or after_report_ids != before_report_ids
            or not (result.candidates or result.clarifications)
            or any(
                item.source_mode
                not in {*MODEL_CANDIDATE_SOURCES, CandidateSource.PATIENT_SELECTION}
                for item in result.candidates
            )
            or any(item.link_id not in focus_link_ids for item in result.candidates)
            or any(target not in focus_link_ids for target in clarification_targets)
            or any(
                item.proposed_candidate is not None
                and item.proposed_candidate.source_mode
                not in {*MODEL_CANDIDATE_SOURCES, CandidateSource.PATIENT_SELECTION}
                for item in result.clarifications
            )
        ):
            raise CompetitionDemoStartError("豆包没有生成可安全确认的目标指标。")

    try:
        _atomic_stage_replace(
            path,
            loader,
            validator,
            expected_generation=expected_generation,
            seed_from_existing=True,
        )
    except CompetitionDemoConflict:
        raise
    except Exception as exc:
        raise CompetitionDemoStartError(
            "豆包本轮整理未通过安全校验；原来的随访内容没有变化。"
        ) from exc
    return read_competition_demo(path)


def activate_competition_plan(
    db_path: Path | str,
    *,
    expected_generation: str | None,
) -> CompetitionDemoProgress:
    """Make the simulated doctor's plan activation the first workflow fact."""

    path = Path(db_path)

    def loader(staging: Path):
        reset_demo(staging)
        store = SQLiteStore(staging)
        return CareEngine(store).activate_followup_plan(DEMO_PATIENT_ID)

    try:
        _atomic_stage_replace(
            path,
            loader,
            _validate_plan_activation,
            expected_generation=expected_generation,
        )
        return read_competition_demo(path)
    except (CompetitionDemoConflict, CompetitionDemoStartError):
        raise
    except Exception as exc:
        raise CompetitionDemoStartError(
            "医生随访方案暂时无法启动；原来的本地记录没有变化。"
        ) from exc


def start_next_competition_checkin(
    db_path: Path | str,
    *,
    expected_generation: str | None,
    now: str | None = None,
) -> CompetitionDemoProgress:
    """Open the next due daily check-in under the already confirmed plan.

    This is the deterministic boundary a scheduler can invoke.  It never
    creates a second check-in on the same patient-local calendar day and it
    does not create another doctor-plan activation.
    """

    path = Path(db_path)
    previous = read_competition_demo(path)
    if not previous.plan_activated or not previous.session_id:
        raise CompetitionDemoStartError("医生尚未确认随访方案。")
    if previous.session_status != "completed":
        raise CompetitionDemoStartError("今天的随访尚未结束。")

    anchor = now or utc_now_iso()

    def validate_schedule(staging: Path) -> None:
        try:
            instant = datetime.fromisoformat(anchor)
        except ValueError as exc:
            raise CompetitionDemoStartError("下一天的随访时间无效。") from exc
        if instant.tzinfo is None:
            raise CompetitionDemoStartError("下一天的随访时间必须包含时区。")
        local_date = instant.astimezone(ZoneInfo("Asia/Shanghai")).date()

        with _readonly_connection(staging) as connection:
            plan = connection.execute(
                "SELECT pathway_code, pathway_version, period_start, period_end, "
                "knowledge_release_id, plan_json FROM doctor_followup_plans "
                "WHERE patient_id=? AND status='confirmed' AND is_current=1",
                (DEMO_PATIENT_ID,),
            ).fetchone()
            prior = connection.execute(
                "SELECT * FROM care_sessions WHERE session_id=?",
                (previous.session_id,),
            ).fetchone()
            if plan is None or prior is None:
                raise CompetitionDemoStartError("医生确认的随访方案不可读取。")
            plan_json = json.loads(plan["plan_json"])
            activation_id = str(plan_json.get("activationSessionId") or "")
            activation = connection.execute(
                "SELECT * FROM care_sessions WHERE session_id=?",
                (activation_id,),
            ).fetchone()
            if activation is None:
                raise CompetitionDemoStartError("医生确认的随访方案缺少启动会话。")

        try:
            start = date.fromisoformat(plan["period_start"])
            end = date.fromisoformat(plan["period_end"])
            completed = datetime.fromisoformat(
                prior["completed_at"] or prior["updated_at"]
            )
        except (TypeError, ValueError) as exc:
            raise CompetitionDemoStartError("医生确认的随访日期无效。") from exc
        if completed.tzinfo is None:
            raise CompetitionDemoStartError("上一天的随访完成时间缺少时区。")
        expected_date = (
            completed.astimezone(ZoneInfo("Asia/Shanghai")).date()
            + timedelta(days=1)
        )
        if local_date != expected_date:
            raise CompetitionDemoStartError("只能开始患者当地日历的下一天随访。")
        if not start <= local_date <= end:
            raise CompetitionDemoStartError("下一天已超出医生确认的随访周期。")

        locked_fields = (
            "patient_id",
            "pathway_code",
            "pathway_version",
            "questionnaire_canonical",
            "questionnaire_version",
            "knowledge_release_id",
        )
        if any(prior[field] != activation[field] for field in locked_fields):
            raise CompetitionDemoStartError("每日随访与医生启动的锁定版本不一致。")
        if (
            prior["pathway_code"] != plan["pathway_code"]
            or prior["pathway_version"] != plan["pathway_version"]
            or prior["knowledge_release_id"] != plan["knowledge_release_id"]
        ):
            raise CompetitionDemoStartError("每日随访与当前医生方案版本不一致。")

    def loader(staging: Path):
        validate_schedule(staging)
        store = SQLiteStore(staging, initialize=False)
        return CareEngine(store).start_next_locked_checkin(
            previous.session_id,
            now=anchor,
        )

    def validator(staging: Path) -> None:
        progress = read_competition_demo(staging)
        if (
            progress.integrity_issue
            or not progress.plan_activated
            or progress.session_id == previous.session_id
            or progress.session_status != "in_progress"
            or progress.stage != CompetitionDemoStage.PLAN_ACTIVATED
        ):
            raise CompetitionDemoStartError("下一天的随访会话未能安全建立。")

    try:
        _atomic_stage_replace(
            path,
            loader,
            validator,
            expected_generation=expected_generation,
            seed_from_existing=True,
        )
        return read_competition_demo(path)
    except (CompetitionDemoConflict, CompetitionDemoStartError):
        raise
    except Exception as exc:
        raise CompetitionDemoStartError(
            "下一天的随访暂时无法启动；原记录没有变化。"
        ) from exc


def persist_doctor_plan_with_activation(
    db_path: Path | str,
    *,
    expected_generation: str | None,
    persist_plan: Callable[[Path, CompetitionDemoProgress], _PlanResult],
) -> tuple[_PlanResult, CompetitionDemoProgress]:
    """Atomically join a doctor-confirmed plan to the shared demo workflow.

    The callback writes only to the staging database.  Readers therefore see
    either the previous state or both the confirmed plan and its activation;
    they can never observe a saved plan that the patient surface cannot use.
    Existing active sessions are preserved when a doctor versions the plan.
    """

    path = Path(db_path)

    def loader(staging: Path) -> _PlanResult:
        progress = read_competition_demo(staging)
        if not progress.plan_activated:
            store = SQLiteStore(staging, initialize=False)
            CareEngine(store).activate_followup_plan(
                DEMO_PATIENT_ID,
                actor_type="doctor_portal_user",
            )
            progress = read_competition_demo(staging)
        return persist_plan(staging, progress)

    def validator(staging: Path) -> None:
        progress = read_competition_demo(staging)
        if (
            progress.integrity_issue
            or not progress.plan_activated
            or not progress.session_id
            or progress.plan_actor not in {"simulated_doctor", "doctor_portal_user"}
            or progress.alert_count
            or progress.approved_clinical_rule_count
        ):
            raise CompetitionDemoStartError(
                "医生方案与共享随访链路的联合校验失败；原记录未被替换。"
            )

    try:
        result = _atomic_stage_replace(
            path,
            loader,
            validator,
            expected_generation=expected_generation,
            seed_from_existing=True,
        )
        return result, read_competition_demo(path)
    except (CompetitionDemoConflict, CompetitionDemoStartError):
        raise
    except Exception as exc:
        raise CompetitionDemoStartError(
            "医生方案与患者随访未能一起启用；原来的共享记录没有变化。"
        ) from exc


def reset_competition_demo(
    db_path: Path | str,
    *,
    expected_generation: str | None,
) -> CompetitionDemoProgress:
    """Explicitly clear the synthetic workflow back to the doctor-start state."""

    path = Path(db_path)

    def loader(staging: Path) -> None:
        reset_demo(staging)

    def validator(staging: Path) -> None:
        progress = read_competition_demo(staging)
        if progress.stage != CompetitionDemoStage.NOT_STARTED or progress.generation:
            raise CompetitionDemoStartError("合成演示重置校验失败；原记录未被替换。")

    try:
        _atomic_stage_replace(
            path,
            loader,
            validator,
            expected_generation=expected_generation,
        )
        return read_competition_demo(path)
    except (CompetitionDemoConflict, CompetitionDemoStartError):
        raise
    except Exception as exc:
        raise CompetitionDemoStartError("合成演示未能重置；原记录没有变化。") from exc


def submit_activated_plan_feedback(
    db_path: Path | str,
    *,
    expected_generation: str,
    use_mimo: bool,
    model_adapter: MiMoSemanticAdapter | None = None,
) -> CompetitionDemoProgress:
    """Create a candidate from the doctor-activated plan without touching live DB on failure."""

    path = Path(db_path)
    before = read_competition_demo(path)
    if (
        before.stage != CompetitionDemoStage.PLAN_ACTIVATED
        or not before.plan_activated
        or before.generation != expected_generation
        or not before.session_id
    ):
        raise CompetitionDemoConflict("随访方案状态已经变化，请刷新患者页面后继续。")

    if use_mimo:
        adapter = model_adapter or _competition_mimo_adapter()
        if (
            not isinstance(adapter, MiMoSemanticAdapter)
            or not adapter.configured
            or adapter.config.safety_llm_enabled
            or adapter.config.language_llm_enabled
            or adapter.config.summary_llm_enabled
        ):
            raise CompetitionDemoStartError(
                "豆包当前未正确配置；医生已启动的方案保持不变。"
            )
    else:
        adapter = UnconfiguredModelAdapter(SemanticModelConfig())

    def loader(staging: Path):
        store = SQLiteStore(staging, initialize=False)
        session = store.get_care_session(before.session_id)
        if session is None:
            raise CompetitionDemoStartError("医生已启动的随访方案不可读取。")
        engine = CareEngine(store)
        agent = CareAgentService(
            store,
            care_engine=engine,
            model_adapter=adapter,
            patient_timezone="Asia/Shanghai",
        )
        return agent.analyze(session.session_id, MANUAL_REVIEW_MESSAGE)

    def validator(staging: Path) -> None:
        _validate_plan_candidate(staging, require_mimo=use_mimo)

    try:
        _atomic_stage_replace(
            path,
            loader,
            validator,
            expected_generation=expected_generation,
            seed_from_existing=True,
        )
        return read_competition_demo(path)
    except CompetitionDemoConflict:
        raise
    except CompetitionDemoStartError as exc:
        message = (
            "豆包在线整理未通过安全校验；医生已启动的方案保持不变。"
            if use_mimo
            else "离线整理未通过校验；医生已启动的方案保持不变。"
        )
        raise CompetitionDemoStartError(message) from exc
    except Exception as exc:
        message = (
            "豆包在线整理未通过安全校验；医生已启动的方案保持不变。"
            if use_mimo
            else "离线整理未通过校验；医生已启动的方案保持不变。"
        )
        raise CompetitionDemoStartError(message) from exc


def competition_mimo_configured() -> bool:
    """Report configuration readiness without contacting MiMo or exposing a key."""

    try:
        return _competition_mimo_adapter().configured
    except (CompetitionDemoStartError, TypeError, ValueError):
        return False


def _competition_mimo_adapter() -> MiMoSemanticAdapter:
    config = replace(
        SemanticModelConfig.from_environment(),
        safety_llm_enabled=False,
        language_llm_enabled=False,
        summary_llm_enabled=False,
    )
    adapter = MiMoSemanticAdapter(config)
    if not adapter.configured:
        raise CompetitionDemoStartError(
            "豆包在线演示尚未正确配置；原来的演示记录没有被替换。"
        )
    return adapter


def start_competition_demo(
    db_path: Path | str,
    *,
    expected_generation: str | None | object = _EXPECTED_GENERATION_UNSET,
) -> CompetitionDemoProgress:
    """Explicitly reset and prepare exactly one unreleased candidate story."""

    path = Path(db_path)
    try:
        _atomic_stage_replace(
            path,
            load_manual_review_scenario,
            _validate_candidate_start,
            expected_generation=expected_generation,
        )
        progress = read_competition_demo(path)
        if progress.stage != CompetitionDemoStage.CANDIDATE_READY:
            raise CompetitionDemoStartError("完整比赛 Demo 起点未能恢复，请重试。")
        return progress
    except CompetitionDemoStartError:
        raise
    except Exception as exc:
        raise CompetitionDemoStartError(
            "完整比赛 Demo 暂时无法开始；旧故事未被替换，请明确重试。"
        ) from exc


def start_competition_demo_with_mimo(
    db_path: Path | str,
    *,
    expected_generation: str | None,
    model_adapter: MiMoSemanticAdapter | None = None,
) -> CompetitionDemoProgress:
    """Atomically prepare the fixed synthetic story using one real MiMo call."""

    adapter = model_adapter or _competition_mimo_adapter()
    if (
        not isinstance(adapter, MiMoSemanticAdapter)
        or not adapter.configured
        or adapter.config.safety_llm_enabled
        or adapter.config.language_llm_enabled
        or adapter.config.summary_llm_enabled
    ):
        raise CompetitionDemoStartError(
            "豆包在线演示尚未正确配置；原来的演示记录没有被替换。"
        )
    path = Path(db_path)

    def loader(staging: Path):
        return load_manual_review_scenario(staging, model_adapter=adapter)

    try:
        _atomic_stage_replace(
            path,
            loader,
            _validate_mimo_candidate_start,
            expected_generation=expected_generation,
        )
        progress = read_competition_demo(path)
        if progress.stage != CompetitionDemoStage.CANDIDATE_READY:
            raise CompetitionDemoStartError(
                "豆包在线演示起点未能恢复；原来的演示记录没有被替换。"
            )
        return progress
    except CompetitionDemoStartError:
        raise
    except Exception as exc:
        raise CompetitionDemoStartError(
            "豆包在线生成未通过安全契约，原来的演示记录没有被替换。"
        ) from exc


def load_technical_demo_atomically(
    db_path: Path | str, scenario_label: str
) -> Any:
    """Keep legacy technical fixtures behind the same atomic reset boundary."""

    path = Path(db_path)

    def loader(staging: Path):
        return load_layer2_scenario(staging, scenario_label)

    try:
        return _atomic_stage_replace(path, loader, lambda candidate: _readonly_connection(candidate).close())
    except Exception as exc:
        raise CompetitionDemoStartError(
            "技术演示暂时无法载入；当前故事未被替换，请明确重试。"
        ) from exc
