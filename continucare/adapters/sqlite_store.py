"""Persistent local DataStore backed by the standard sqlite3 module."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from continucare.agents.contracts import AgentRunRecord, CandidateSource, SemanticResult
from continucare.care_agent.model_api import (
    MODEL_API_MODES,
    MODEL_API_PROVIDERS,
    MODEL_CANDIDATE_SOURCES,
)
from continucare.db import connect, initialize_database
from continucare.errors import ConcurrentWriteConflict, is_sqlite_busy
from continucare.fhir.r4 import FHIRValidationError, validate_r4_resource
from continucare.fhir.references import (
    validate_questionnaire_response_against_questionnaire,
)
from continucare.layer4.fhir import validate_layer4_fhir_resource
from continucare.layer4.manual_reviews import is_manual_review_task
from continucare.models import (
    Alert,
    AlertAction,
    AuditEvent,
    CareSession,
    CareSessionStatus,
    ConfirmedAnswerContext,
    ConfirmedSymptomReport,
    FollowUpMessage,
    Observation,
    Patient,
    ProvisionalAnswerContext,
    ProvisionalSymptomReport,
    Summary,
)
from continucare.repositories import (
    row_to_alert,
    row_to_alert_action,
    row_to_agent_run,
    row_to_audit_event,
    row_to_care_session,
    row_to_message,
    row_to_observation,
    row_to_patient,
    row_to_summary,
)
from continucare.pathways.fhir_artifacts import (
    load_glp1_questionnaire,
    load_questionnaire_for_reference,
)
from continucare.services.patient_checkin import (
    exact_questionnaire_direct_answer,
    is_single_focused_patient_checkin_task,
)


def _agent_run_values(record: AgentRunRecord) -> tuple:
    return (
        record.run_id,
        record.task_id,
        record.patient_id,
        record.session_id,
        record.agent_name,
        record.agent_version,
        record.mode,
        record.input_text,
        record.input_hash,
        json.dumps(record.output_json, ensure_ascii=False),
        record.knowledge_release_id,
        record.terminology_catalog_id,
        record.terminology_catalog_version,
        record.terminology_catalog_sha256,
        record.status,
        record.model_provider,
        record.model_name,
        record.prompt_version,
        record.started_at,
        record.completed_at,
        record.error_code,
    )


def _insert_agent_run_row(
    connection: sqlite3.Connection, record: AgentRunRecord
) -> None:
    connection.execute(
        """
        INSERT INTO agent_runs (
            run_id, task_id, patient_id, session_id, agent_name,
            agent_version, mode, input_text, input_hash, output_json,
            knowledge_release_id, terminology_catalog_id,
            terminology_catalog_version, terminology_catalog_sha256,
            status, model_provider, model_name, prompt_version,
            started_at, completed_at, error_code
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        _agent_run_values(record),
    )


def _agent_run_matches(row, record: AgentRunRecord) -> bool:
    return bool(row is not None and row_to_agent_run(row) == record)


def _audit_row_matches(row, event: AuditEvent) -> bool:
    return bool(
        row is not None
        and row["patient_id"] == event.patient_id
        and row["entity_type"] == event.entity_type
        and row["entity_id"] == event.entity_id
        and row["event_type"] == event.event_type
        and row["actor_type"] == event.actor_type
        and json.loads(row["details_json"]) == event.details_json
        and row["created_at"] == event.created_at
    )


def _insert_audit_row(connection: sqlite3.Connection, event: AuditEvent) -> None:
    connection.execute(
        """
        INSERT INTO audit_events (
            event_id, patient_id, entity_type, entity_id, event_type,
            actor_type, details_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.event_id,
            event.patient_id,
            event.entity_type,
            event.entity_id,
            event.event_type,
            event.actor_type,
            json.dumps(
                event.details_json,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
            event.created_at,
        ),
    )


def _provisional_material_sha256(item) -> str:
    encoded = json.dumps(
        item.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class SQLiteStore:
    def __init__(self, db_path: Path | str, *, initialize: bool = True):
        self.db_path = Path(db_path)
        if initialize:
            initialize_database(self.db_path)

    def get_patient(self, patient_id: str) -> Patient | None:
        with connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT * FROM patients WHERE patient_id = ?", (patient_id,)
            ).fetchone()
        return row_to_patient(row) if row else None

    def list_patients(self) -> list[Patient]:
        """Return the local registry without inventing population semantics.

        The MVP uses this read-only query for role worklists and operational
        counts.  Clinical priority and risk ordering deliberately remain out
        of scope until governed rules exist.
        """

        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM patients
                ORDER BY status = 'active' DESC, next_visit_date, patient_id
                """
            ).fetchall()
        return [row_to_patient(row) for row in rows]

    def save_care_session(self, session: CareSession) -> None:
        with connect(self.db_path) as connection:
            self._insert_care_session(connection, session)

    @staticmethod
    def _insert_care_session(
        connection: sqlite3.Connection, session: CareSession
    ) -> None:
        connection.execute(
                """
                INSERT INTO care_sessions (
                    session_id, patient_id, pathway_code, pathway_version,
                    questionnaire_canonical, questionnaire_version,
                    knowledge_release_id, parent_session_id, status,
                    answers_json, questionnaire_response_id, created_at,
                    updated_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.session_id,
                    session.patient_id,
                    session.pathway_code,
                    session.pathway_version,
                    session.questionnaire_canonical,
                    session.questionnaire_version,
                    session.knowledge_release_id,
                    session.parent_session_id,
                    session.status.value,
                    json.dumps(session.answers, ensure_ascii=False),
                    session.questionnaire_response_id,
                    session.created_at,
                    session.updated_at,
                    session.completed_at,
                ),
            )

    def create_care_session_bundle(
        self,
        session: CareSession,
        audit_events: list[AuditEvent],
    ) -> None:
        """Persist a new session and its required audit facts atomically."""

        if not audit_events:
            raise ValueError("care session creation requires audit events")
        if any(event.patient_id != session.patient_id for event in audit_events):
            raise ValueError("care session audit patient mismatch")
        if any(
            event.entity_type != "CareSession"
            or event.entity_id != session.session_id
            for event in audit_events
        ):
            raise ValueError("care session audit entity mismatch")
        if len({event.event_id for event in audit_events}) != len(audit_events):
            raise ValueError("duplicate care session audit id")

        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._insert_care_session(connection, session)
            for event in audit_events:
                _insert_audit_row(connection, event)

    def get_care_session(self, session_id: str) -> CareSession | None:
        with connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT * FROM care_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return row_to_care_session(row) if row else None

    def get_active_care_session(
        self, patient_id: str, pathway_code: str
    ) -> CareSession | None:
        with connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT * FROM care_sessions
                WHERE patient_id = ? AND pathway_code = ? AND status = 'in_progress'
                  AND parent_session_id IS NULL
                ORDER BY updated_at DESC LIMIT 1
                """,
                (patient_id, pathway_code),
            ).fetchone()
        return row_to_care_session(row) if row else None

    def update_care_session(
        self,
        session_id: str,
        *,
        answers: dict,
        status: CareSessionStatus,
        updated_at: str,
        questionnaire_response_id: str | None = None,
        completed_at: str | None = None,
    ) -> None:
        with connect(self.db_path) as connection:
            cursor = connection.execute(
                """
                UPDATE care_sessions
                SET answers_json = ?, status = ?, updated_at = ?,
                    questionnaire_response_id = COALESCE(?, questionnaire_response_id),
                    completed_at = COALESCE(?, completed_at)
                WHERE session_id = ?
                """,
                (
                    json.dumps(answers, ensure_ascii=False),
                    status.value,
                    updated_at,
                    questionnaire_response_id,
                    completed_at,
                    session_id,
                ),
            )
            if cursor.rowcount != 1:
                raise LookupError(f"care session {session_id!r} was not found")

    def list_care_sessions(self, patient_id: str) -> list[CareSession]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM care_sessions
                WHERE patient_id = ? ORDER BY updated_at DESC
                """,
                (patient_id,),
            ).fetchall()
        return [row_to_care_session(row) for row in rows]

    def save_agent_run(self, record: AgentRunRecord) -> None:
        with connect(self.db_path) as connection:
            _insert_agent_run_row(connection, record)

    def persist_agent_run_bundle(
        self,
        *,
        record: AgentRunRecord,
        audit_events: list[AuditEvent],
    ) -> bool:
        """Persist one analysis result and its required audit facts atomically."""

        if not audit_events:
            raise ValueError("AgentRun bundle requires audit evidence")
        if len({event.event_id for event in audit_events}) != len(audit_events):
            raise ValueError("AgentRun bundle audit ids must be unique")
        if any(event.patient_id != record.patient_id for event in audit_events):
            raise ValueError("AgentRun bundle audit patient mismatch")
        if any(
            event.entity_type != "AgentRun" or event.entity_id != record.run_id
            for event in audit_events
        ):
            raise ValueError("AgentRun bundle audit identity mismatch")
        try:
            with connect(self.db_path) as connection:
                connection.execute("PRAGMA busy_timeout=0")
                connection.execute("BEGIN IMMEDIATE")
                run_row = connection.execute(
                    "SELECT * FROM agent_runs WHERE run_id=?", (record.run_id,)
                ).fetchone()
                audit_rows = {
                    event.event_id: connection.execute(
                        "SELECT * FROM audit_events WHERE event_id=?",
                        (event.event_id,),
                    ).fetchone()
                    for event in audit_events
                }
                if run_row is not None or any(
                    row is not None for row in audit_rows.values()
                ):
                    if _agent_run_matches(run_row, record) and all(
                        _audit_row_matches(audit_rows[event.event_id], event)
                        for event in audit_events
                    ):
                        return False
                    raise ConcurrentWriteConflict(
                        "AgentRun bundle has a conflicting or partial replay"
                    )
                _insert_agent_run_row(connection, record)
                self._agent_run_bundle_fault("after_run")
                for event in audit_events:
                    _insert_audit_row(connection, event)
                    self._agent_run_bundle_fault(f"after_audit:{event.event_type}")
                self._agent_run_bundle_fault("before_commit")
            self._agent_run_bundle_fault("after_commit")
        except sqlite3.OperationalError as exc:
            if is_sqlite_busy(exc):
                raise ConcurrentWriteConflict(
                    "AgentRun database is busy; retry the same request"
                ) from exc
            raise
        return True

    def _agent_run_bundle_fault(self, stage: str) -> None:
        """Test seam for AgentRun rollback and post-commit replay."""

        return None

    def get_agent_run(self, run_id: str) -> AgentRunRecord | None:
        with connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return row_to_agent_run(row) if row else None

    def get_agent_run_by_task(self, task_id: str) -> AgentRunRecord | None:
        with connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT * FROM agent_runs WHERE task_id = ?", (task_id,)
            ).fetchone()
        return row_to_agent_run(row) if row else None

    def list_agent_runs(self, session_id: str) -> list[AgentRunRecord]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM agent_runs WHERE session_id = ?
                ORDER BY completed_at DESC
                """,
                (session_id,),
            ).fetchall()
        return [row_to_agent_run(row) for row in rows]

    def resolve_conversation_action(
        self,
        *,
        action_id: str,
        source_run_id: str,
        session_id: str,
        decision: str,
        resolved_at: str,
        option_id: str | None = None,
        response_run_id: str | None = None,
        response_text: str | None = None,
    ) -> None:
        """Close an action once, allowing an unsure decision to be finalized."""

        with connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO conversation_action_resolutions (
                    action_id, source_run_id, session_id, response_run_id,
                    decision, option_id, response_text, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(action_id) DO UPDATE SET
                    response_run_id = excluded.response_run_id,
                    decision = excluded.decision,
                    option_id = excluded.option_id,
                    response_text = excluded.response_text,
                    resolved_at = excluded.resolved_at
                WHERE conversation_action_resolutions.decision = 'unsure'
                  AND excluded.decision IN ('accepted', 'rejected')
                """,
                (
                    action_id,
                    source_run_id,
                    session_id,
                    response_run_id,
                    decision,
                    option_id,
                    response_text,
                    resolved_at,
                ),
            )

    def persist_conversation_decision_bundle(
        self,
        *,
        expected_session: CareSession,
        answers: dict | None,
        answer_contexts: list[ConfirmedAnswerContext],
        symptom_reports: list[ConfirmedSymptomReport],
        supersede_link_ids: list[str],
        action_ids: list[str],
        source_run_id: str,
        decision: str,
        resolution_decision: str,
        option_id: str | None,
        response_run_id: str | None,
        response_text: str | None,
        resolved_at: str,
        audit_events: list[AuditEvent],
        response_record: AgentRunRecord | None = None,
    ) -> bool:
        """CAS one ordinary M2/M3 patient decision as an atomic command."""

        if len(action_ids) != len(set(action_ids)) or any(
            not item.strip() for item in action_ids
        ):
            raise ValueError("conversation action ids must be non-blank and unique")
        if len(supersede_link_ids) != len(set(supersede_link_ids)) or any(
            not item.strip() for item in supersede_link_ids
        ):
            raise ValueError("superseded draft link ids must be non-blank and unique")
        if not action_ids and decision != "verbatim-only":
            raise ValueError("conversation decision requires at least one action")
        if decision not in {"accepted", "rejected", "unsure", "verbatim-only"}:
            raise ValueError("conversation decision identity is invalid")
        if resolution_decision not in {"accepted", "rejected", "unsure"}:
            raise ValueError("conversation resolution decision is invalid")
        if not audit_events:
            raise ValueError("conversation decision requires audit evidence")
        if len({event.event_id for event in audit_events}) != len(audit_events):
            raise ValueError("conversation decision audit ids must be unique")
        if any(
            event.patient_id != expected_session.patient_id for event in audit_events
        ):
            raise ValueError("conversation decision audit patient mismatch")
        if any(
            item.session_id != expected_session.session_id
            or item.source_run_id != source_run_id
            for item in [*answer_contexts, *symptom_reports]
        ):
            raise ValueError("conversation material does not match source run/session")
        if answers is None and (answer_contexts or symptom_reports):
            raise ValueError("conversation material requires a draft answer update")
        replacement_links = {item.link_id for item in answer_contexts}
        if replacement_links & set(supersede_link_ids):
            raise ValueError("a draft link cannot be replaced and invalidated together")
        if supersede_link_ids and (
            answers is None
            or any(link_id not in expected_session.answers for link_id in supersede_link_ids)
            or any(link_id in answers for link_id in supersede_link_ids)
        ):
            raise ValueError("invalidated draft links must be removed from the target answers")
        if response_record is not None and (
            response_run_id != response_record.run_id
            or response_record.patient_id != expected_session.patient_id
            or response_record.session_id != expected_session.session_id
        ):
            raise ValueError("conversation decision response AgentRun mismatch")

        def payload(value) -> str:
            return json.dumps(
                value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            )

        expected_answers_payload = payload(expected_session.answers)
        target_answers_payload = payload(answers) if answers is not None else None

        def replay_audit_matches(row, event: AuditEvent) -> bool:
            # Patient button retries rebuild the command timestamp. The stable
            # event identity and semantic payload, not that retry timestamp,
            # determine whether this is the same already-committed decision.
            return bool(
                row is not None
                and row["patient_id"] == event.patient_id
                and row["entity_type"] == event.entity_type
                and row["entity_id"] == event.entity_id
                and row["event_type"] == event.event_type
                and row["actor_type"] == event.actor_type
                and json.loads(row["details_json"]) == event.details_json
            )

        try:
            with connect(self.db_path) as connection:
                connection.execute("PRAGMA busy_timeout=0")
                connection.execute("BEGIN IMMEDIATE")
                session_row = connection.execute(
                    "SELECT * FROM care_sessions WHERE session_id=?",
                    (expected_session.session_id,),
                ).fetchone()
                if session_row is None:
                    raise LookupError(
                        f"care session {expected_session.session_id!r} was not found"
                    )
                run_row = connection.execute(
                    "SELECT * FROM agent_runs WHERE run_id=?",
                    (source_run_id,),
                ).fetchone()
                if (
                    run_row is None
                    or run_row["patient_id"] != expected_session.patient_id
                    or run_row["session_id"] != expected_session.session_id
                ):
                    raise ValueError("conversation decision source AgentRun mismatch")
                response_row = None
                if response_run_id is not None:
                    response_row = connection.execute(
                        "SELECT * FROM agent_runs WHERE run_id=?",
                        (response_run_id,),
                    ).fetchone()
                    if response_record is None:
                        if (
                            response_row is None
                            or response_row["patient_id"]
                            != expected_session.patient_id
                            or response_row["session_id"]
                            != expected_session.session_id
                        ):
                            raise ValueError(
                                "conversation decision response AgentRun mismatch"
                            )
                    elif response_row is not None and not _agent_run_matches(
                        response_row, response_record
                    ):
                        raise ConcurrentWriteConflict(
                            "conversation response AgentRun conflicts with replay"
                        )
                result = json.loads(run_row["output_json"])
                available_action_ids = {
                    item["candidate_id"] for item in result.get("candidates", [])
                } | {
                    item["clarification_id"]
                    for item in result.get("clarifications", [])
                }
                unknown = set(action_ids) - available_action_ids
                if unknown:
                    raise ValueError(
                        "conversation decision contains an unknown or cross-run action"
                    )

                resolution_rows = {
                    row["action_id"]: row
                    for row in connection.execute(
                        "SELECT * FROM conversation_action_resolutions "
                        f"WHERE action_id IN ({','.join('?' for _ in action_ids)})",
                        tuple(action_ids),
                    ).fetchall()
                } if action_ids else {}
                all_resolutions_absent = not resolution_rows
                all_resolutions_exact = all(
                    (row := resolution_rows.get(action_id)) is not None
                    and row["source_run_id"] == source_run_id
                    and row["session_id"] == expected_session.session_id
                    and row["decision"] == resolution_decision
                    and row["option_id"] == option_id
                    and row["response_run_id"] == response_run_id
                    and row["response_text"] == response_text
                    for action_id in action_ids
                )
                all_unsure_transition = bool(action_ids) and all(
                    (row := resolution_rows.get(action_id)) is not None
                    and row["source_run_id"] == source_run_id
                    and row["session_id"] == expected_session.session_id
                    and row["decision"] == "unsure"
                    for action_id in action_ids
                ) and resolution_decision in {"accepted", "rejected"}

                audit_rows = {
                    event.event_id: connection.execute(
                        "SELECT * FROM audit_events WHERE event_id=?",
                        (event.event_id,),
                    ).fetchone()
                    for event in audit_events
                }
                any_audit = any(row is not None for row in audit_rows.values())
                all_audits_exact = all(
                    replay_audit_matches(audit_rows[event.event_id], event)
                    for event in audit_events
                )

                context_rows = {
                    context.answer_context_id: connection.execute(
                        "SELECT * FROM confirmed_answer_contexts "
                        "WHERE answer_context_id=?",
                        (context.answer_context_id,),
                    ).fetchone()
                    for context in answer_contexts
                }
                report_rows = {
                    report.report_id: connection.execute(
                        "SELECT * FROM confirmed_symptom_reports WHERE report_id=?",
                        (report.report_id,),
                    ).fetchone()
                    for report in symptom_reports
                }
                supersede_exact = all(
                    connection.execute(
                        "SELECT 1 FROM confirmed_answer_contexts "
                        "WHERE session_id=? AND link_id=? AND status='active' LIMIT 1",
                        (expected_session.session_id, link_id),
                    ).fetchone()
                    is None
                    and connection.execute(
                        "SELECT 1 FROM confirmed_answer_contexts "
                        "WHERE session_id=? AND link_id=? AND status='superseded' "
                        "AND answer_json=? LIMIT 1",
                        (
                            expected_session.session_id,
                            link_id,
                            payload(expected_session.answers[link_id]),
                        ),
                    ).fetchone()
                    is not None
                    for link_id in supersede_link_ids
                )
                domain_rows_exist = any(
                    row is not None for row in [*context_rows.values(), *report_rows.values()]
                )
                replay_domain_exact = (
                    (answers is None or payload(json.loads(session_row["answers_json"])) == target_answers_payload)
                    and supersede_exact
                    and all(
                        row is not None
                        and row["session_id"] == context.session_id
                        and row["link_id"] == context.link_id
                        and payload(json.loads(row["answer_json"]))
                        == payload(context.answer)
                        and row["source_run_id"] == context.source_run_id
                        and row["followup_occurrence_id"]
                        == context.followup_occurrence_id
                        and row["patient_timezone"] == context.patient_timezone
                        and row["reported_at"] == context.reported_at
                        and row["effective_start"] == context.effective_start
                        and row["effective_end"] == context.effective_end
                        and row["temporal_kind"] == context.temporal_kind
                        and row["resolution_basis"] == context.resolution_basis
                        and row["raw_text"] == context.raw_text
                        and (
                            payload(json.loads(row["terminology_match_json"]))
                            if row["terminology_match_json"]
                            else None
                        )
                        == (
                            payload(context.terminology_match)
                            if context.terminology_match is not None
                            else None
                        )
                        and row["status"] == "active"
                        for context in answer_contexts
                        for row in [context_rows[context.answer_context_id]]
                    )
                    and all(
                        row is not None
                        and row["session_id"] == report.session_id
                        and row["concept_id"] == report.concept_id
                        and row["source_run_id"] == report.source_run_id
                        and row["report_id"] == report.report_id
                        and row["status"] == "active"
                        for report in symptom_reports
                        for row in [report_rows[report.report_id]]
                    )
                )
                response_exact = response_record is None or _agent_run_matches(
                    response_row, response_record
                )
                if (
                    response_exact
                    and all_resolutions_exact
                    and all_audits_exact
                    and replay_domain_exact
                ):
                    return False
                if (
                    (response_record is not None and response_row is not None)
                    or any_audit
                    or (bool(action_ids) and all_resolutions_exact)
                    or domain_rows_exist
                    or not (
                        all_resolutions_absent
                        or all_unsure_transition
                        or (not action_ids and not resolution_rows)
                    )
                ):
                    raise ConcurrentWriteConflict(
                        "conversation decision has a conflicting or partial replay"
                    )

                if (
                    session_row["patient_id"] != expected_session.patient_id
                    or session_row["status"] != CareSessionStatus.IN_PROGRESS.value
                    or session_row["updated_at"] != expected_session.updated_at
                    or payload(json.loads(session_row["answers_json"]))
                    != expected_answers_payload
                ):
                    raise ConcurrentWriteConflict(
                        "care session changed; refresh and retry"
                    )

                if response_record is not None:
                    _insert_agent_run_row(connection, response_record)
                    self._conversation_decision_fault("after_response_run")
                self._conversation_decision_fault("before_material")
                for link_id in supersede_link_ids:
                    cursor = connection.execute(
                        """
                        UPDATE confirmed_answer_contexts SET status='superseded'
                        WHERE session_id=? AND link_id=? AND status='active'
                        """,
                        (expected_session.session_id, link_id),
                    )
                    if cursor.rowcount != 1:
                        raise ConcurrentWriteConflict(
                            "draft dependency context changed; refresh and retry"
                        )
                    connection.execute(
                        """
                        UPDATE patient_collection_resolutions SET is_current=0
                        WHERE session_id=? AND link_id=? AND is_current=1
                        """,
                        (expected_session.session_id, link_id),
                    )
                    self._conversation_decision_fault(
                        f"after_dependency_invalidation:{link_id}"
                    )
                for context in answer_contexts:
                    connection.execute(
                        """
                        UPDATE confirmed_answer_contexts SET status='superseded'
                        WHERE session_id=? AND link_id=? AND status='active'
                        """,
                        (context.session_id, context.link_id),
                    )
                    connection.execute(
                        """
                        INSERT INTO confirmed_answer_contexts (
                            answer_context_id, session_id, link_id, answer_json,
                            source_run_id, followup_occurrence_id, patient_timezone,
                            reported_at, effective_start, effective_end, temporal_kind,
                            resolution_basis, raw_text, terminology_match_json,
                            status, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            context.answer_context_id,
                            context.session_id,
                            context.link_id,
                            payload(context.answer),
                            context.source_run_id,
                            context.followup_occurrence_id,
                            context.patient_timezone,
                            context.reported_at,
                            context.effective_start,
                            context.effective_end,
                            context.temporal_kind,
                            context.resolution_basis,
                            context.raw_text,
                            (
                                payload(context.terminology_match)
                                if context.terminology_match is not None
                                else None
                            ),
                            context.status,
                            context.created_at,
                        ),
                    )
                    connection.execute(
                        """
                        UPDATE patient_collection_resolutions SET is_current = 0
                        WHERE session_id = ? AND link_id = ? AND is_current = 1
                        """,
                        (context.session_id, context.link_id),
                    )
                    self._conversation_decision_fault("after_answer_context")
                for report in symptom_reports:
                    connection.execute(
                        """
                        INSERT INTO confirmed_symptom_reports (
                            report_id, session_id, concept_id, preferred_zh, coding_json,
                            terminology_match_json, source_kind, source_run_id,
                            evidence_text, evidence_start, evidence_end,
                            followup_occurrence_id, patient_timezone, reported_at,
                            effective_start, effective_end, temporal_kind, status,
                            created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(session_id, concept_id) DO UPDATE SET
                            report_id=excluded.report_id,
                            preferred_zh=excluded.preferred_zh,
                            coding_json=excluded.coding_json,
                            terminology_match_json=excluded.terminology_match_json,
                            source_kind=excluded.source_kind,
                            source_run_id=excluded.source_run_id,
                            evidence_text=excluded.evidence_text,
                            evidence_start=excluded.evidence_start,
                            evidence_end=excluded.evidence_end,
                            followup_occurrence_id=excluded.followup_occurrence_id,
                            patient_timezone=excluded.patient_timezone,
                            reported_at=excluded.reported_at,
                            effective_start=excluded.effective_start,
                            effective_end=excluded.effective_end,
                            temporal_kind=excluded.temporal_kind,
                            status='active', created_at=excluded.created_at
                        """,
                        (
                            report.report_id,
                            report.session_id,
                            report.concept_id,
                            report.preferred_zh,
                            payload(report.coding),
                            payload(report.terminology_match),
                            report.source_kind,
                            report.source_run_id,
                            report.evidence_text,
                            report.evidence_start,
                            report.evidence_end,
                            report.followup_occurrence_id,
                            report.patient_timezone,
                            report.reported_at,
                            report.effective_start,
                            report.effective_end,
                            report.temporal_kind,
                            report.status,
                            report.created_at,
                        ),
                    )
                    self._conversation_decision_fault("after_symptom_report")
                if answers is not None:
                    cursor = connection.execute(
                        """
                        UPDATE care_sessions SET answers_json=?, updated_at=?
                        WHERE session_id=? AND status='in_progress'
                          AND updated_at=? AND answers_json=?
                        """,
                        (
                            target_answers_payload,
                            resolved_at,
                            expected_session.session_id,
                            expected_session.updated_at,
                            session_row["answers_json"],
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise ConcurrentWriteConflict(
                            "care session changed; refresh and retry"
                        )
                    self._conversation_decision_fault("after_session")
                for index, action_id in enumerate(action_ids):
                    if all_unsure_transition:
                        cursor = connection.execute(
                            """
                            UPDATE conversation_action_resolutions
                            SET decision=?, option_id=?, response_run_id=?,
                                response_text=?, resolved_at=?
                            WHERE action_id=? AND source_run_id=? AND session_id=?
                              AND decision='unsure'
                            """,
                            (
                                resolution_decision,
                                option_id,
                                response_run_id,
                                response_text,
                                resolved_at,
                                action_id,
                                source_run_id,
                                expected_session.session_id,
                            ),
                        )
                    else:
                        cursor = connection.execute(
                            """
                            INSERT INTO conversation_action_resolutions (
                                action_id, source_run_id, session_id, decision,
                                option_id, response_run_id, response_text, resolved_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                action_id,
                                source_run_id,
                                expected_session.session_id,
                                resolution_decision,
                                option_id,
                                response_run_id,
                                response_text,
                                resolved_at,
                            ),
                        )
                    if cursor.rowcount != 1:
                        raise ConcurrentWriteConflict(
                            "conversation action changed; refresh and retry"
                        )
                    self._conversation_decision_fault(f"after_resolution:{index}")
                for event in audit_events:
                    _insert_audit_row(connection, event)
                    self._conversation_decision_fault(
                        f"after_audit:{event.event_type}"
                    )
                self._conversation_decision_fault("before_commit")
            self._conversation_decision_fault("after_commit")
        except sqlite3.OperationalError as exc:
            if is_sqlite_busy(exc):
                raise ConcurrentWriteConflict(
                    "conversation decision database is busy; retry the same request"
                ) from exc
            raise
        return True

    def persist_provisional_draft_bundle(
        self,
        *,
        expected_session: CareSession,
        answers: dict | None,
        answer_contexts: list[ProvisionalAnswerContext],
        symptom_reports: list[ProvisionalSymptomReport],
        supersede_link_ids: list[str],
        action_ids: list[str],
        action_type: str,
        source_run_id: str,
        decision: str,
        option_id: str | None,
        resolved_at: str,
        audit_events: list[AuditEvent],
    ) -> None:
        """CAS model output into an explicitly unconfirmed patient draft."""

        if (
            not action_ids
            or len(action_ids) != len(set(action_ids))
            or any(not item.strip() for item in action_ids)
        ):
            raise ValueError("provisional draft actions must be non-empty and unique")
        if action_type not in {"candidate", "clarification"}:
            raise ValueError("provisional draft action type is invalid")
        if decision not in {"drafted", "rejected", "unsure"}:
            raise ValueError("provisional draft decision is invalid")
        if decision == "drafted" and answers is None:
            raise ValueError("a drafted action must update the provisional material")
        if answers is None and (answer_contexts or symptom_reports):
            raise ValueError("provisional material requires a draft answer update")
        if len(supersede_link_ids) != len(set(supersede_link_ids)):
            raise ValueError("provisional invalidation links must be unique")
        if any(
            item.session_id != expected_session.session_id
            or item.source_run_id != source_run_id
            for item in [*answer_contexts, *symptom_reports]
        ):
            raise ValueError("provisional material does not match its source run")
        if not audit_events or any(
            event.patient_id != expected_session.patient_id for event in audit_events
        ):
            raise ValueError("provisional draft requires matching audit evidence")

        def payload(value) -> str:
            return json.dumps(
                value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            )

        try:
            with connect(self.db_path) as connection:
                connection.execute("PRAGMA busy_timeout=0")
                connection.execute("BEGIN IMMEDIATE")
                session_row = connection.execute(
                    "SELECT * FROM care_sessions WHERE session_id=?",
                    (expected_session.session_id,),
                ).fetchone()
                if session_row is None:
                    raise LookupError("care session was not found")
                if (
                    session_row["patient_id"] != expected_session.patient_id
                    or session_row["status"] != CareSessionStatus.IN_PROGRESS.value
                    or session_row["updated_at"] != expected_session.updated_at
                    or payload(json.loads(session_row["answers_json"]))
                    != payload(expected_session.answers)
                ):
                    raise ConcurrentWriteConflict(
                        "care session changed; refresh and retry"
                    )
                source = connection.execute(
                    "SELECT patient_id, session_id, output_json FROM agent_runs "
                    "WHERE run_id=?",
                    (source_run_id,),
                ).fetchone()
                if (
                    source is None
                    or source["patient_id"] != expected_session.patient_id
                    or source["session_id"] != expected_session.session_id
                ):
                    raise ValueError("provisional draft source AgentRun mismatch")
                result = SemanticResult.model_validate(json.loads(source["output_json"]))
                available = (
                    {item.candidate_id for item in result.candidates}
                    if action_type == "candidate"
                    else {item.clarification_id for item in result.clarifications}
                )
                if set(action_ids) - available:
                    raise ValueError("provisional draft contains a cross-run action")
                placeholders = ",".join("?" for _ in action_ids)
                if connection.execute(
                    "SELECT 1 FROM patient_draft_action_resolutions "
                    f"WHERE action_id IN ({placeholders}) LIMIT 1",
                    tuple(action_ids),
                ).fetchone() or connection.execute(
                    "SELECT 1 FROM conversation_action_resolutions "
                    f"WHERE action_id IN ({placeholders}) LIMIT 1",
                    tuple(action_ids),
                ).fetchone():
                    raise ConcurrentWriteConflict(
                        "provisional draft action changed; refresh and retry"
                    )
                if connection.execute(
                    "SELECT 1 FROM confirmed_answer_contexts "
                    "WHERE session_id=? LIMIT 1",
                    (expected_session.session_id,),
                ).fetchone() or connection.execute(
                    "SELECT 1 FROM confirmed_symptom_reports "
                    "WHERE session_id=? LIMIT 1",
                    (expected_session.session_id,),
                ).fetchone():
                    raise ValueError(
                        "legacy per-turn confirmations cannot be mixed into a final-only draft"
                    )

                for link_id in supersede_link_ids:
                    cursor = connection.execute(
                        "UPDATE provisional_answer_contexts SET status='superseded' "
                        "WHERE session_id=? AND link_id=? AND status='active'",
                        (expected_session.session_id, link_id),
                    )
                    if cursor.rowcount != 1:
                        raise ConcurrentWriteConflict(
                            "provisional dependency changed; refresh and retry"
                        )
                    connection.execute(
                        "UPDATE patient_collection_resolutions SET is_current=0 "
                        "WHERE session_id=? AND link_id=? AND is_current=1",
                        (expected_session.session_id, link_id),
                    )
                for context in answer_contexts:
                    connection.execute(
                        "UPDATE provisional_answer_contexts SET status='superseded' "
                        "WHERE session_id=? AND link_id=? AND status='active'",
                        (context.session_id, context.link_id),
                    )
                    connection.execute(
                        """
                        INSERT INTO provisional_answer_contexts (
                            draft_context_id, session_id, link_id, answer_json,
                            source_run_id, followup_occurrence_id, patient_timezone,
                            reported_at, effective_start, effective_end, temporal_kind,
                            resolution_basis, raw_text, terminology_match_json,
                            status, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            context.draft_context_id,
                            context.session_id,
                            context.link_id,
                            payload(context.answer),
                            context.source_run_id,
                            context.followup_occurrence_id,
                            context.patient_timezone,
                            context.reported_at,
                            context.effective_start,
                            context.effective_end,
                            context.temporal_kind,
                            context.resolution_basis,
                            context.raw_text,
                            (
                                payload(context.terminology_match)
                                if context.terminology_match is not None
                                else None
                            ),
                            context.status,
                            context.created_at,
                        ),
                    )
                    connection.execute(
                        "UPDATE patient_collection_resolutions SET is_current=0 "
                        "WHERE session_id=? AND link_id=? AND is_current=1",
                        (context.session_id, context.link_id),
                    )
                for report in symptom_reports:
                    connection.execute(
                        "UPDATE provisional_symptom_reports SET status='superseded' "
                        "WHERE session_id=? AND concept_id=? AND status='active'",
                        (report.session_id, report.concept_id),
                    )
                    connection.execute(
                        """
                        INSERT INTO provisional_symptom_reports (
                            draft_report_id, session_id, concept_id, preferred_zh,
                            coding_json, terminology_match_json, source_kind,
                            source_run_id, evidence_text, evidence_start, evidence_end,
                            followup_occurrence_id, patient_timezone, reported_at,
                            effective_start, effective_end, temporal_kind, status,
                            created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            report.draft_report_id,
                            report.session_id,
                            report.concept_id,
                            report.preferred_zh,
                            payload(report.coding),
                            payload(report.terminology_match),
                            report.source_kind,
                            report.source_run_id,
                            report.evidence_text,
                            report.evidence_start,
                            report.evidence_end,
                            report.followup_occurrence_id,
                            report.patient_timezone,
                            report.reported_at,
                            report.effective_start,
                            report.effective_end,
                            report.temporal_kind,
                            report.status,
                            report.created_at,
                        ),
                    )
                if answers is not None:
                    cursor = connection.execute(
                        "UPDATE care_sessions SET answers_json=?, updated_at=? "
                        "WHERE session_id=? AND status='in_progress' "
                        "AND updated_at=? AND answers_json=?",
                        (
                            payload(answers),
                            resolved_at,
                            expected_session.session_id,
                            expected_session.updated_at,
                            session_row["answers_json"],
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise ConcurrentWriteConflict(
                            "care session changed; refresh and retry"
                        )
                for action_id in action_ids:
                    connection.execute(
                        "INSERT INTO patient_draft_action_resolutions ("
                        "action_id, source_run_id, session_id, action_type, decision, "
                        "option_id, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'active', ?)",
                        (
                            action_id,
                            source_run_id,
                            expected_session.session_id,
                            action_type,
                            decision,
                            option_id,
                            resolved_at,
                        ),
                    )
                for event in audit_events:
                    _insert_audit_row(connection, event)
        except sqlite3.OperationalError as exc:
            if is_sqlite_busy(exc):
                raise ConcurrentWriteConflict(
                    "provisional draft database is busy; retry after refresh"
                ) from exc
            raise

    def _conversation_decision_fault(self, stage: str) -> None:
        """Test seam for proving ordinary decision bundle rollback."""

        return None

    def agent_run_has_audit(
        self, record: AgentRunRecord, event_type: str
    ) -> bool:
        """Verify the minimum durable audit evidence required for a replay."""

        with connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT 1 FROM audit_events
                WHERE patient_id=? AND entity_type='AgentRun'
                  AND entity_id=? AND event_type=?
                LIMIT 1
                """,
                (record.patient_id, record.run_id, event_type),
            ).fetchone()
        return row is not None

    def contextual_response_is_complete(
        self,
        *,
        record: AgentRunRecord,
        source_run_id: str,
        action_ids: list[str],
        decision: str,
        applied_link_ids: list[str],
        require_context_audit: bool,
    ) -> bool:
        """Fail-closed read check for a response and its decision effects."""

        if (
            not action_ids
            or len(action_ids) != len(set(action_ids))
            or decision not in {"accepted", "rejected", "unsure"}
        ):
            return False
        placeholders = ",".join("?" for _ in action_ids)
        with connect(self.db_path) as connection:
            resolution_rows = connection.execute(
                "SELECT * FROM conversation_action_resolutions "
                f"WHERE action_id IN ({placeholders})",
                tuple(action_ids),
            ).fetchall()
            if len(resolution_rows) != len(action_ids) or any(
                row["source_run_id"] != source_run_id
                or row["session_id"] != record.session_id
                or row["response_run_id"] != record.run_id
                or row["response_text"] != record.input_text
                or row["decision"] != decision
                for row in resolution_rows
            ):
                return False

            decision_rows = connection.execute(
                """
                SELECT details_json FROM audit_events
                WHERE patient_id=? AND entity_type='AgentRun'
                  AND entity_id=?
                  AND event_type='semantic_candidate_patient_decision'
                  AND created_at=?
                """,
                (
                    record.patient_id,
                    source_run_id,
                    record.completed_at,
                ),
            ).fetchall()
            if not decision_rows:
                return False

            if require_context_audit:
                context_rows = connection.execute(
                    """
                    SELECT details_json FROM audit_events
                    WHERE patient_id=? AND entity_type='AgentRun'
                      AND entity_id=?
                      AND event_type='conversation_context_resolved'
                    """,
                    (record.patient_id, record.run_id),
                ).fetchall()
                expected_actions = sorted(action_ids)
                expected_links = sorted(applied_link_ids)
                if not any(
                    (details := json.loads(row["details_json"]))
                    .get("session_id")
                    == record.session_id
                    and details.get("source_run_id") == source_run_id
                    and sorted(details.get("action_ids", [])) == expected_actions
                    and details.get("decision") == decision
                    and sorted(details.get("applied_link_ids", []))
                    == expected_links
                    for row in context_rows
                ):
                    return False

            if applied_link_ids:
                session_row = connection.execute(
                    "SELECT answers_json FROM care_sessions WHERE session_id=?",
                    (record.session_id,),
                ).fetchone()
                if session_row is None:
                    return False
                answers = json.loads(session_row["answers_json"])
                for link_id in applied_link_ids:
                    if link_id.startswith("patient-reported-symptom::"):
                        concept_id = link_id.removeprefix(
                            "patient-reported-symptom::"
                        )
                        material = connection.execute(
                            """
                            SELECT 1 FROM confirmed_symptom_reports
                            WHERE session_id=? AND concept_id=?
                              AND source_run_id=?
                            LIMIT 1
                            """,
                            (record.session_id, concept_id, source_run_id),
                        ).fetchone()
                    else:
                        if link_id not in answers:
                            return False
                        material = connection.execute(
                            """
                            SELECT 1 FROM confirmed_answer_contexts
                            WHERE session_id=? AND link_id=?
                              AND source_run_id=?
                            LIMIT 1
                            """,
                            (record.session_id, link_id, source_run_id),
                        ).fetchone()
                    if material is None:
                        return False
        return True

    def conversation_action_decisions(self, session_id: str) -> dict[str, str]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT action_id, decision FROM conversation_action_resolutions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchall()
        return {row["action_id"]: row["decision"] for row in rows}

    def current_collection_resolutions(self, session_id: str) -> dict[str, str]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT link_id, resolution FROM patient_collection_resolutions
                WHERE session_id = ? AND is_current = 1
                """,
                (session_id,),
            ).fetchall()
        return {row["link_id"]: row["resolution"] for row in rows}

    def record_explicit_unknown(
        self,
        *,
        expected_session: CareSession,
        link_id: str,
        policy_version: str,
        resolution_id: str,
        resolved_at: str,
        audit_event: AuditEvent,
    ) -> CareSession:
        """Persist a non-clinical answered/unknown fact with session CAS."""

        if audit_event.patient_id != expected_session.patient_id:
            raise ValueError("collection resolution audit patient mismatch")
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM care_sessions WHERE session_id = ?",
                (expected_session.session_id,),
            ).fetchone()
            if (
                row is None
                or row["status"] != CareSessionStatus.IN_PROGRESS.value
                or row["updated_at"] != expected_session.updated_at
                or json.loads(row["answers_json"]) != expected_session.answers
            ):
                raise ConcurrentWriteConflict("care session changed; refresh and retry")
            if link_id in expected_session.answers:
                raise ValueError("已有确认答案的指标不能标记为不确定")
            connection.execute(
                """
                UPDATE patient_collection_resolutions SET is_current = 0
                WHERE session_id = ? AND link_id = ? AND is_current = 1
                """,
                (expected_session.session_id, link_id),
            )
            connection.execute(
                """
                INSERT INTO patient_collection_resolutions (
                    resolution_id, session_id, link_id, resolution,
                    policy_version, actor_type, is_current, created_at
                ) VALUES (?, ?, ?, 'explicit_unknown', ?, 'synthetic_patient', 1, ?)
                """,
                (
                    resolution_id,
                    expected_session.session_id,
                    link_id,
                    policy_version,
                    resolved_at,
                ),
            )
            cursor = connection.execute(
                """
                UPDATE care_sessions SET updated_at = ?
                WHERE session_id = ? AND status = 'in_progress' AND updated_at = ?
                """,
                (resolved_at, expected_session.session_id, expected_session.updated_at),
            )
            if cursor.rowcount != 1:
                raise ConcurrentWriteConflict("care session changed; refresh and retry")
            _insert_audit_row(connection, audit_event)
        current = self.get_care_session(expected_session.session_id)
        if current is None:
            raise RuntimeError("care session disappeared after collection resolution")
        return current

    def resolved_conversation_action_ids(self, session_id: str) -> set[str]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT action_id FROM conversation_action_resolutions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchall()
        return {row["action_id"] for row in rows}

    def save_confirmed_answer_context(self, context: ConfirmedAnswerContext) -> None:
        """Append a revision and keep exactly one active context per answer."""

        with connect(self.db_path) as connection:
            connection.execute(
                """
                UPDATE confirmed_answer_contexts SET status = 'superseded'
                WHERE session_id = ? AND link_id = ? AND status = 'active'
                """,
                (context.session_id, context.link_id),
            )
            connection.execute(
                """
                INSERT INTO confirmed_answer_contexts (
                    answer_context_id, session_id, link_id, answer_json,
                    source_run_id, followup_occurrence_id, patient_timezone,
                    reported_at, effective_start, effective_end, temporal_kind,
                    resolution_basis, raw_text, terminology_match_json,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(answer_context_id) DO UPDATE SET status = 'active'
                """,
                (
                    context.answer_context_id,
                    context.session_id,
                    context.link_id,
                    json.dumps(context.answer, ensure_ascii=False),
                    context.source_run_id,
                    context.followup_occurrence_id,
                    context.patient_timezone,
                    context.reported_at,
                    context.effective_start,
                    context.effective_end,
                    context.temporal_kind,
                    context.resolution_basis,
                    context.raw_text,
                    (
                        json.dumps(context.terminology_match, ensure_ascii=False)
                        if context.terminology_match is not None
                        else None
                    ),
                    context.status,
                    context.created_at,
                ),
            )

    def list_active_answer_contexts(
        self, session_id: str
    ) -> list[ConfirmedAnswerContext]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM confirmed_answer_contexts
                WHERE session_id = ? AND status = 'active'
                ORDER BY created_at
                """,
                (session_id,),
            ).fetchall()
        return [
            ConfirmedAnswerContext(
                answer_context_id=row["answer_context_id"],
                session_id=row["session_id"],
                link_id=row["link_id"],
                answer=json.loads(row["answer_json"]),
                source_run_id=row["source_run_id"],
                followup_occurrence_id=row["followup_occurrence_id"],
                patient_timezone=row["patient_timezone"],
                reported_at=row["reported_at"],
                effective_start=row["effective_start"],
                effective_end=row["effective_end"],
                temporal_kind=row["temporal_kind"],
                resolution_basis=row["resolution_basis"],
                raw_text=row["raw_text"],
                terminology_match=(
                    json.loads(row["terminology_match_json"])
                    if row["terminology_match_json"]
                    else None
                ),
                status=row["status"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def list_answer_context_history(
        self, session_id: str
    ) -> list[ConfirmedAnswerContext]:
        """Return active and superseded draft contexts for lineage validation."""

        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM confirmed_answer_contexts
                WHERE session_id = ?
                ORDER BY created_at, answer_context_id
                """,
                (session_id,),
            ).fetchall()
        return [
            ConfirmedAnswerContext(
                answer_context_id=row["answer_context_id"],
                session_id=row["session_id"],
                link_id=row["link_id"],
                answer=json.loads(row["answer_json"]),
                source_run_id=row["source_run_id"],
                followup_occurrence_id=row["followup_occurrence_id"],
                patient_timezone=row["patient_timezone"],
                reported_at=row["reported_at"],
                effective_start=row["effective_start"],
                effective_end=row["effective_end"],
                temporal_kind=row["temporal_kind"],
                resolution_basis=row["resolution_basis"],
                raw_text=row["raw_text"],
                terminology_match=(
                    json.loads(row["terminology_match_json"])
                    if row["terminology_match_json"]
                    else None
                ),
                status=row["status"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def list_active_provisional_answer_contexts(
        self, session_id: str
    ) -> list[ProvisionalAnswerContext]:
        return [
            self._provisional_answer_context(row)
            for row in self._provisional_answer_context_rows(
                session_id, active_only=True
            )
        ]

    def list_provisional_answer_context_history(
        self, session_id: str
    ) -> list[ProvisionalAnswerContext]:
        return [
            self._provisional_answer_context(row)
            for row in self._provisional_answer_context_rows(
                session_id, active_only=False
            )
        ]

    def _provisional_answer_context_rows(
        self, session_id: str, *, active_only: bool
    ):
        predicate = " AND status='active'" if active_only else ""
        with connect(self.db_path) as connection:
            return connection.execute(
                "SELECT * FROM provisional_answer_contexts "
                f"WHERE session_id=?{predicate} ORDER BY created_at, draft_context_id",
                (session_id,),
            ).fetchall()

    @staticmethod
    def _provisional_answer_context(row) -> ProvisionalAnswerContext:
        return ProvisionalAnswerContext(
            draft_context_id=row["draft_context_id"],
            session_id=row["session_id"],
            link_id=row["link_id"],
            answer=json.loads(row["answer_json"]),
            source_run_id=row["source_run_id"],
            followup_occurrence_id=row["followup_occurrence_id"],
            patient_timezone=row["patient_timezone"],
            reported_at=row["reported_at"],
            effective_start=row["effective_start"],
            effective_end=row["effective_end"],
            temporal_kind=row["temporal_kind"],
            resolution_basis=row["resolution_basis"],
            raw_text=row["raw_text"],
            terminology_match=(
                json.loads(row["terminology_match_json"])
                if row["terminology_match_json"]
                else None
            ),
            status=row["status"],
            created_at=row["created_at"],
        )

    def save_confirmed_symptom_report(
        self, report: ConfirmedSymptomReport
    ) -> None:
        with connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO confirmed_symptom_reports (
                    report_id, session_id, concept_id, preferred_zh, coding_json,
                    terminology_match_json, source_kind, source_run_id, evidence_text,
                    evidence_start, evidence_end, followup_occurrence_id,
                    patient_timezone, reported_at, effective_start, effective_end,
                    temporal_kind, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, concept_id) DO UPDATE SET
                    report_id = excluded.report_id,
                    preferred_zh = excluded.preferred_zh,
                    coding_json = excluded.coding_json,
                    terminology_match_json = excluded.terminology_match_json,
                    source_kind = excluded.source_kind,
                    source_run_id = excluded.source_run_id,
                    evidence_text = excluded.evidence_text,
                    evidence_start = excluded.evidence_start,
                    evidence_end = excluded.evidence_end,
                    followup_occurrence_id = excluded.followup_occurrence_id,
                    patient_timezone = excluded.patient_timezone,
                    reported_at = excluded.reported_at,
                    effective_start = excluded.effective_start,
                    effective_end = excluded.effective_end,
                    temporal_kind = excluded.temporal_kind,
                    status = 'active',
                    created_at = excluded.created_at
                """,
                (
                    report.report_id,
                    report.session_id,
                    report.concept_id,
                    report.preferred_zh,
                    json.dumps(report.coding, ensure_ascii=False),
                    json.dumps(report.terminology_match, ensure_ascii=False),
                    report.source_kind,
                    report.source_run_id,
                    report.evidence_text,
                    report.evidence_start,
                    report.evidence_end,
                    report.followup_occurrence_id,
                    report.patient_timezone,
                    report.reported_at,
                    report.effective_start,
                    report.effective_end,
                    report.temporal_kind,
                    report.status,
                    report.created_at,
                ),
            )

    def list_active_symptom_reports(
        self, session_id: str
    ) -> list[ConfirmedSymptomReport]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM confirmed_symptom_reports
                WHERE session_id = ? AND status = 'active'
                ORDER BY created_at
                """,
                (session_id,),
            ).fetchall()
        return [
            ConfirmedSymptomReport(
                report_id=row["report_id"],
                session_id=row["session_id"],
                concept_id=row["concept_id"],
                preferred_zh=row["preferred_zh"],
                coding=json.loads(row["coding_json"]),
                terminology_match=json.loads(row["terminology_match_json"]),
                source_kind=row["source_kind"],
                source_run_id=row["source_run_id"],
                evidence_text=row["evidence_text"],
                evidence_start=row["evidence_start"],
                evidence_end=row["evidence_end"],
                followup_occurrence_id=row["followup_occurrence_id"],
                patient_timezone=row["patient_timezone"],
                reported_at=row["reported_at"],
                effective_start=row["effective_start"],
                effective_end=row["effective_end"],
                temporal_kind=row["temporal_kind"],
                status=row["status"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def list_active_provisional_symptom_reports(
        self, session_id: str
    ) -> list[ProvisionalSymptomReport]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                "SELECT * FROM provisional_symptom_reports "
                "WHERE session_id=? AND status='active' "
                "ORDER BY created_at, draft_report_id",
                (session_id,),
            ).fetchall()
        return [self._provisional_symptom_report(row) for row in rows]

    @staticmethod
    def _provisional_symptom_report(row) -> ProvisionalSymptomReport:
        return ProvisionalSymptomReport(
            draft_report_id=row["draft_report_id"],
            session_id=row["session_id"],
            concept_id=row["concept_id"],
            preferred_zh=row["preferred_zh"],
            coding=json.loads(row["coding_json"]),
            terminology_match=json.loads(row["terminology_match_json"]),
            source_kind=row["source_kind"],
            source_run_id=row["source_run_id"],
            evidence_text=row["evidence_text"],
            evidence_start=row["evidence_start"],
            evidence_end=row["evidence_end"],
            followup_occurrence_id=row["followup_occurrence_id"],
            patient_timezone=row["patient_timezone"],
            reported_at=row["reported_at"],
            effective_start=row["effective_start"],
            effective_end=row["effective_end"],
            temporal_kind=row["temporal_kind"],
            status=row["status"],
            created_at=row["created_at"],
        )

    def provisional_action_decisions(self, session_id: str) -> dict[str, str]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                "SELECT action_id, decision FROM patient_draft_action_resolutions "
                "WHERE session_id=? AND status='active'",
                (session_id,),
            ).fetchall()
        return {row["action_id"]: row["decision"] for row in rows}

    def list_provisional_actions(self, session_id: str) -> list[dict]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                "SELECT * FROM patient_draft_action_resolutions "
                "WHERE session_id=? AND status='active' ORDER BY created_at, action_id",
                (session_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def remove_provisional_symptom_report(
        self,
        *,
        expected_session: CareSession,
        draft_report_id: str,
        removed_at: str,
        audit_event: AuditEvent,
    ) -> None:
        """CAS-remove one unsubmitted dynamic symptom without creating clinical facts."""

        if (
            not draft_report_id.strip()
            or audit_event.patient_id != expected_session.patient_id
            or audit_event.entity_type != "CareSession"
            or audit_event.entity_id != expected_session.session_id
            or audit_event.event_type != "patient_draft_symptom_removed"
            or audit_event.actor_type != "synthetic_patient"
        ):
            raise ValueError("provisional symptom removal boundary is invalid")
        try:
            with connect(self.db_path) as connection:
                connection.execute("PRAGMA busy_timeout=0")
                connection.execute("BEGIN IMMEDIATE")
                session_row = connection.execute(
                    "SELECT * FROM care_sessions WHERE session_id=?",
                    (expected_session.session_id,),
                ).fetchone()
                if (
                    session_row is None
                    or session_row["patient_id"] != expected_session.patient_id
                    or session_row["status"] != CareSessionStatus.IN_PROGRESS.value
                    or session_row["updated_at"] != expected_session.updated_at
                    or json.loads(session_row["answers_json"])
                    != expected_session.answers
                ):
                    raise ConcurrentWriteConflict(
                        "care session changed; refresh and retry"
                    )
                report = connection.execute(
                    "SELECT * FROM provisional_symptom_reports "
                    "WHERE draft_report_id=? AND session_id=? AND status='active'",
                    (draft_report_id, expected_session.session_id),
                ).fetchone()
                details = audit_event.details_json
                if (
                    report is None
                    or details.get("session_id") != expected_session.session_id
                    or details.get("draft_report_id") != draft_report_id
                    or details.get("source_run_id") != report["source_run_id"]
                    or details.get("concept_id") != report["concept_id"]
                    or details.get("confirmation_status")
                    != "removed_before_final_confirmation"
                ):
                    raise ValueError("provisional symptom removal target changed")
                if any(
                    (
                        connection.execute(
                            "SELECT COUNT(*) FROM fhir_questionnaire_responses "
                            "WHERE patient_id=?",
                            (expected_session.patient_id,),
                        ).fetchone()[0],
                        connection.execute(
                            "SELECT COUNT(*) FROM fhir_observations WHERE patient_id=?",
                            (expected_session.patient_id,),
                        ).fetchone()[0],
                        connection.execute(
                            "SELECT COUNT(*) FROM layer4_fhir_resources "
                            "WHERE patient_id=? AND resource_type IN ('Task','Communication')",
                            (expected_session.patient_id,),
                        ).fetchone()[0],
                        connection.execute(
                            "SELECT COUNT(*) FROM alerts WHERE patient_id=?",
                            (expected_session.patient_id,),
                        ).fetchone()[0],
                    )
                ):
                    raise ValueError("clinical resources already exist for this draft")
                cursor = connection.execute(
                    "UPDATE provisional_symptom_reports SET status='superseded' "
                    "WHERE draft_report_id=? AND session_id=? AND status='active'",
                    (draft_report_id, expected_session.session_id),
                )
                if cursor.rowcount != 1:
                    raise ConcurrentWriteConflict(
                        "provisional symptom changed; refresh and retry"
                    )
                cursor = connection.execute(
                    "UPDATE care_sessions SET updated_at=? "
                    "WHERE session_id=? AND status='in_progress' AND updated_at=?",
                    (
                        removed_at,
                        expected_session.session_id,
                        expected_session.updated_at,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ConcurrentWriteConflict(
                        "care session changed; refresh and retry"
                    )
                _insert_audit_row(connection, audit_event)
        except sqlite3.OperationalError as exc:
            if is_sqlite_busy(exc):
                raise ConcurrentWriteConflict(
                    "provisional symptom database is busy; refresh and retry"
                ) from exc
            raise

    def complete_care_session_submission(
        self,
        *,
        session: CareSession,
        message: FollowUpMessage,
        questionnaire_response: dict,
        questionnaire: dict,
        observations: list[Observation],
        completed_at: str,
        expected_session: CareSession | None = None,
        audit_event: AuditEvent | None = None,
    ) -> bool:
        """Atomically persist completion resources, session CAS, and audit."""

        requires_atomic_audit = expected_session is not None
        if requires_atomic_audit and audit_event is None:
            raise ValueError("atomic care completion requires its audit event")
        resource = validate_questionnaire_response_against_questionnaire(
            questionnaire_response, questionnaire
        )
        expected_session = expected_session or session
        patient_id = resource["subject"]["reference"].removeprefix("Patient/")
        if (
            patient_id != session.patient_id
            or message.patient_id != session.patient_id
            or expected_session.patient_id != session.patient_id
            or expected_session.session_id != session.session_id
        ):
            raise FHIRValidationError(
                "care session, message and QuestionnaireResponse patient must match"
            )
        if message.message_id != resource["id"]:
            raise FHIRValidationError(
                "follow-up message id must match QuestionnaireResponse.id"
            )

        validated_observations: list[Observation] = []
        for item in observations:
            normalized = validate_r4_resource(
                item.as_fhir(), expected_resource_type="Observation"
            )
            validated = Observation(resource=normalized, evidence=item.evidence)
            if validated.patient_id != session.patient_id:
                raise FHIRValidationError("Observation patient must match care session")
            if validated.message_id != resource["id"]:
                raise FHIRValidationError(
                    "Observation must derive from the completed QuestionnaireResponse"
                )
            validated_observations.append(validated)
        observation_ids = [item.observation_id for item in validated_observations]
        if len(observation_ids) != len(set(observation_ids)):
            raise ValueError("completion Observation ids must be unique")
        if audit_event is not None and (
            audit_event.patient_id != patient_id
            or audit_event.entity_type != "QuestionnaireResponse"
            or audit_event.entity_id != resource["id"]
            or audit_event.event_type != "questionnaire_response_completed"
        ):
            raise ValueError("completion audit identity is invalid")

        def payload(value) -> str:
            return json.dumps(
                value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            )

        target_answers_payload = payload(session.answers)
        expected_answers_payload = payload(expected_session.answers)
        resource_payload = payload(resource)
        audit_payload = payload(audit_event.details_json) if audit_event else None

        def audit_matches(row) -> bool:
            if audit_event is None:
                return row is None
            return bool(
                row is not None
                and row["patient_id"] == audit_event.patient_id
                and row["entity_type"] == audit_event.entity_type
                and row["entity_id"] == audit_event.entity_id
                and row["event_type"] == audit_event.event_type
                and row["actor_type"] == audit_event.actor_type
                and payload(json.loads(row["details_json"])) == audit_payload
                and row["created_at"] == audit_event.created_at
            )

        try:
            with connect(self.db_path) as connection:
                connection.execute("PRAGMA busy_timeout=0")
                connection.execute("BEGIN IMMEDIATE")
                current = connection.execute(
                    "SELECT * FROM care_sessions WHERE session_id=?",
                    (session.session_id,),
                ).fetchone()
                if current is None:
                    raise LookupError(
                        f"care session {session.session_id!r} was not found"
                    )
                message_row = connection.execute(
                    "SELECT * FROM followup_messages WHERE message_id=?",
                    (message.message_id,),
                ).fetchone()
                response_row = connection.execute(
                    "SELECT * FROM fhir_questionnaire_responses WHERE resource_id=?",
                    (resource["id"],),
                ).fetchone()
                observation_rows = connection.execute(
                    """
                    SELECT o.*, e.confidence_tier, e.evidence_text,
                           e.evidence_start, e.evidence_end, e.recorded_at,
                           e.source_kind, e.terminology_match_json, e.metric_id,
                           e.evidence_claim_ids_json, e.knowledge_release_id,
                           e.observation_mapping_sha256
                    FROM fhir_observations o
                    JOIN observation_evidence e USING (observation_id)
                    WHERE o.questionnaire_response_id=?
                    """,
                    (resource["id"],),
                ).fetchall()
                audit_row = (
                    connection.execute(
                        "SELECT * FROM audit_events WHERE event_id=?",
                        (audit_event.event_id,),
                    ).fetchone()
                    if audit_event is not None
                    else None
                )

                if current["status"] == CareSessionStatus.COMPLETED.value:
                    stored_observations = {
                        item.observation_id: item
                        for item in (row_to_observation(row) for row in observation_rows)
                    }
                    exact = (
                        current["patient_id"] == patient_id
                        and payload(json.loads(current["answers_json"]))
                        == target_answers_payload
                        and current["questionnaire_response_id"] == resource["id"]
                        and current["completed_at"] == completed_at
                        and message_row is not None
                        and all(
                            message_row[field] == value
                            for field, value in message.model_dump(mode="json").items()
                        )
                        and response_row is not None
                        and response_row["patient_id"] == patient_id
                        and response_row["message_id"] == message.message_id
                        and payload(json.loads(response_row["resource_json"]))
                        == resource_payload
                        and set(stored_observations) == set(observation_ids)
                        and all(
                            stored_observations[item.observation_id] == item
                            for item in validated_observations
                        )
                        and (audit_event is None or audit_matches(audit_row))
                    )
                    if exact:
                        return False
                    raise ConcurrentWriteConflict(
                        "completed care session has incomplete or conflicting evidence"
                    )
                if current["status"] != CareSessionStatus.IN_PROGRESS.value:
                    raise ConcurrentWriteConflict(
                        "care session status changed; refresh and retry"
                    )
                if (
                    current["patient_id"] != expected_session.patient_id
                    or expected_session.status != CareSessionStatus.IN_PROGRESS
                    or current["updated_at"] != expected_session.updated_at
                    or payload(json.loads(current["answers_json"]))
                    != expected_answers_payload
                ):
                    raise ConcurrentWriteConflict(
                        "care session changed; refresh and retry"
                    )
                if (
                    message_row is not None
                    or response_row is not None
                    or observation_rows
                    or audit_row is not None
                ):
                    raise ConcurrentWriteConflict(
                        "care completion has a conflicting partial replay"
                    )

                self._completion_bundle_fault("before_message")
                connection.execute(
                    """
                    INSERT INTO followup_messages (
                        message_id, patient_id, message_text, submitted_at,
                        source, processing_status
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    tuple(message.model_dump(mode="json").values()),
                )
                self._completion_bundle_fault("after_message")
                connection.execute(
                    """
                    INSERT INTO fhir_questionnaire_responses (
                        resource_id, patient_id, message_id, resource_json, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        resource["id"],
                        patient_id,
                        message.message_id,
                        resource_payload,
                        resource["authored"],
                    ),
                )
                self._completion_bundle_fault("after_questionnaire_response")
                for index, item in enumerate(validated_observations):
                    connection.execute(
                        """
                        INSERT INTO fhir_observations (
                            observation_id, patient_id, questionnaire_response_id,
                            effective_time, resource_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            item.observation_id,
                            item.patient_id,
                            item.message_id,
                            item.effective_time,
                            payload(item.as_fhir()),
                            item.created_at,
                        ),
                    )
                    self._completion_bundle_fault(f"after_observation:{index}")
                    connection.execute(
                        """
                        INSERT INTO observation_evidence (
                            observation_id, confidence_tier, evidence_text,
                            evidence_start, evidence_end, recorded_at, source_kind,
                            terminology_match_json, metric_id,
                            evidence_claim_ids_json, knowledge_release_id,
                            observation_mapping_sha256
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            item.observation_id,
                            item.confidence_tier.value,
                            item.evidence_text,
                            item.evidence_start,
                            item.evidence_end,
                            item.created_at,
                            item.evidence.source_kind,
                            (
                                payload(item.evidence.terminology_match)
                                if item.evidence.terminology_match is not None
                                else None
                            ),
                            item.evidence.metric_id,
                            payload(item.evidence.evidence_claim_ids),
                            item.evidence.knowledge_release_id,
                            item.evidence.observation_mapping_sha256,
                        ),
                    )
                    self._completion_bundle_fault(f"after_evidence:{index}")
                cursor = connection.execute(
                    """
                    UPDATE care_sessions
                    SET answers_json=?, status='completed', updated_at=?,
                        questionnaire_response_id=?, completed_at=?
                    WHERE session_id=? AND status='in_progress'
                      AND updated_at=? AND answers_json=?
                    """,
                    (
                        target_answers_payload,
                        completed_at,
                        resource["id"],
                        completed_at,
                        session.session_id,
                        expected_session.updated_at,
                        current["answers_json"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise ConcurrentWriteConflict(
                        "care session changed; refresh and retry"
                    )
                self._completion_bundle_fault("after_session")
                if audit_event is not None:
                    connection.execute(
                        """
                        INSERT INTO audit_events (
                            event_id, patient_id, entity_type, entity_id, event_type,
                            actor_type, details_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            audit_event.event_id,
                            audit_event.patient_id,
                            audit_event.entity_type,
                            audit_event.entity_id,
                            audit_event.event_type,
                            audit_event.actor_type,
                            audit_payload,
                            audit_event.created_at,
                        ),
                    )
                    self._completion_bundle_fault("after_audit")
                self._completion_bundle_fault("before_commit")
        except sqlite3.OperationalError as exc:
            if is_sqlite_busy(exc):
                raise ConcurrentWriteConflict(
                    "care completion database is busy; retry the same request"
                ) from exc
            raise
        return True

    def _completion_bundle_fault(self, stage: str) -> None:
        """Test seam for proving completion rollback at every write point."""

        return None

    def persist_confirmed_review_bundle(
        self,
        *,
        session: CareSession,
        message: FollowUpMessage,
        questionnaire_response: dict,
        questionnaire: dict,
        observations: list[Observation],
        answer_contexts: list[ConfirmedAnswerContext],
        symptom_reports: list[ConfirmedSymptomReport],
        action_ids: list[str],
        source_run_id: str,
        resolved_at: str,
        audit_events: list[AuditEvent],
        layer4_resources: list[dict],
        expected_session: CareSession | None = None,
        finalize_existing_draft: bool = False,
        collection_resolutions: dict[str, str] | None = None,
        collection_policy_version: str | None = None,
        revision_lineage_event_ids: list[str] | None = None,
        promote_provisional_draft: bool = False,
        provisional_action_ids: list[str] | None = None,
        accepted_provisional_action_ids: list[str] | None = None,
        provisional_draft_event_ids: list[str] | None = None,
        provisional_boundary: dict | None = None,
    ) -> bool:
        """Atomically release patient-confirmed evidence and one manual Task."""

        resource = validate_questionnaire_response_against_questionnaire(
            questionnaire_response, questionnaire
        )
        patient_id = resource["subject"]["reference"].removeprefix("Patient/")
        if patient_id != session.patient_id or message.patient_id != patient_id:
            raise FHIRValidationError("confirmed review bundle patient mismatch")
        if message.message_id != resource["id"]:
            raise FHIRValidationError("message id must match QuestionnaireResponse.id")
        validated_observations: list[Observation] = []
        for item in observations:
            normalized = validate_r4_resource(
                item.as_fhir(), expected_resource_type="Observation"
            )
            validated = Observation(resource=normalized, evidence=item.evidence)
            if validated.patient_id != patient_id:
                raise FHIRValidationError("Observation patient must match care session")
            validated_observations.append(validated)
        validated_layer4 = [
            validate_layer4_fhir_resource(item) for item in layer4_resources
        ]
        tasks = [item for item in validated_layer4 if item["resourceType"] == "Task"]
        provenances = [
            item for item in validated_layer4 if item["resourceType"] == "Provenance"
        ]
        if len(tasks) != 1 or len(provenances) != 1:
            raise ValueError("confirmed review bundle requires one Task and one Provenance")
        task = tasks[0]
        if task.get("for", {}).get("reference") != f"Patient/{patient_id}":
            raise FHIRValidationError("manual review Task patient mismatch")
        if not is_manual_review_task(task):
            raise FHIRValidationError("confirmed review bundle requires a manual-review Task")
        expected_session = expected_session or session
        if finalize_existing_draft:
            if action_ids:
                raise ValueError("draft finalization must not re-resolve candidate actions")
            if promote_provisional_draft and (
                not provisional_action_ids
                or len(provisional_action_ids) != len(set(provisional_action_ids))
                or not answer_contexts
            ):
                raise ValueError("provisional finalization requires its complete draft")
        elif not action_ids or len(set(action_ids)) != len(action_ids):
            raise ValueError("confirmed review action ids must be non-empty and unique")
        if any(item.session_id != session.session_id for item in [*answer_contexts, *symptom_reports]) or (
            not (finalize_existing_draft and promote_provisional_draft)
            and any(
                item.source_run_id != source_run_id
                for item in [*answer_contexts, *symptom_reports]
            )
        ):
            raise ValueError("confirmed Layer-3 material does not match source run/session")
        if any(event.patient_id != patient_id for event in audit_events):
            raise ValueError("confirmed review audit patient mismatch")
        task_reference = f"Task/{task['id']}"
        if task_reference not in {
            item.get("reference") for item in provenances[0].get("target", [])
        }:
            raise FHIRValidationError("confirmation Provenance must target the Task")
        revision_lineage_event_ids = revision_lineage_event_ids or []
        provisional_action_ids = provisional_action_ids or []
        accepted_provisional_action_ids = accepted_provisional_action_ids or []
        provisional_draft_event_ids = provisional_draft_event_ids or []
        if promote_provisional_draft:
            required_boundary_keys = {
                "model_provider",
                "model_name",
                "prompt_version",
                "knowledge_release_id",
                "terminology_catalog_id",
                "terminology_catalog_version",
                "terminology_catalog_sha256",
                "fixed_source",
                "dynamic_source",
            }
            if (
                not isinstance(provisional_boundary, dict)
                or set(provisional_boundary) != required_boundary_keys
                or provisional_boundary.get("model_provider")
                not in MODEL_API_PROVIDERS
            ):
                raise ValueError("provisional finalization boundary is incomplete")
        if (
            len(accepted_provisional_action_ids)
            != len(set(accepted_provisional_action_ids))
            or set(accepted_provisional_action_ids) - set(provisional_action_ids)
        ):
            raise ValueError("accepted provisional action ids must be a unique subset")
        if len(revision_lineage_event_ids) != len(set(revision_lineage_event_ids)):
            raise ValueError("revision lineage audit ids must be unique")
        provenance_entities = {
            item.get("what", {}).get("reference")
            for item in provenances[0].get("entity", [])
        }
        if any(
            f"urn:continucare:audit-event:{event_id}" not in provenance_entities
            for event_id in [
                *revision_lineage_event_ids,
                *provisional_draft_event_ids,
            ]
        ):
            raise FHIRValidationError(
                "confirmation Provenance is missing revision lineage audit evidence"
            )

        def payload(value):
            return json.dumps(
                value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            )
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT * FROM care_sessions WHERE session_id = ?",
                (session.session_id,),
            ).fetchone()
            if current is None:
                raise LookupError(f"care session {session.session_id!r} was not found")
            if current["status"] != CareSessionStatus.IN_PROGRESS.value:
                existing = connection.execute(
                    """
                    SELECT 1 FROM layer4_fhir_resources
                    WHERE resource_type = 'Task' AND resource_id = ? AND version_id = '1'
                    """,
                    (task["id"],),
                ).fetchone()
                if (
                    current["status"] == CareSessionStatus.COMPLETED.value
                    and existing is not None
                ):
                    return False
                raise ValueError("随访状态已变化，请刷新后重试")
            source = connection.execute(
                """
                SELECT patient_id, session_id FROM agent_runs WHERE run_id = ?
                """,
                (source_run_id,),
            ).fetchone()
            if (
                source is None
                or source["patient_id"] != patient_id
                or source["session_id"] != session.session_id
            ):
                raise ValueError("confirmed review source AgentRun mismatch")

            current_answers = json.loads(current["answers_json"])
            if (
                current["updated_at"] != expected_session.updated_at
                or current_answers != expected_session.answers
            ):
                raise ConcurrentWriteConflict("care session changed; refresh and retry")
            if finalize_existing_draft:
                if current_answers != session.answers:
                    raise ValueError("confirmed draft does not match current care session")
                context_table = (
                    "provisional_answer_contexts"
                    if promote_provisional_draft
                    else "confirmed_answer_contexts"
                )
                active_rows = connection.execute(
                    f"""
                    SELECT c.*, r.session_id AS run_session_id
                    FROM {context_table} c
                    JOIN agent_runs r ON r.run_id = c.source_run_id
                    WHERE c.session_id = ? AND c.status = 'active'
                      AND r.session_id = c.session_id
                    """,
                    (session.session_id,),
                ).fetchall()
                active = {row["link_id"]: row for row in active_rows}
                for link_id, answer in current_answers.items():
                    row = active.get(link_id)
                    if row is None or json.loads(row["answer_json"]) != answer:
                        raise ValueError("confirmed draft answer provenance is incomplete")
                if promote_provisional_draft:
                    expected_context_ids = {
                        item.answer_context_id.removeprefix("answer-context-final-")
                        for item in answer_contexts
                    }
                    actual_context_ids = {
                        hashlib.sha256(row["draft_context_id"].encode("utf-8")).hexdigest()[:24]
                        for row in active_rows
                    }
                    if expected_context_ids != actual_context_ids:
                        raise ValueError("provisional answer contexts changed before finalization")
                    rows_by_digest = {
                        hashlib.sha256(row["draft_context_id"].encode("utf-8")).hexdigest()[:24]: row
                        for row in active_rows
                    }
                    for item in answer_contexts:
                        digest = item.answer_context_id.removeprefix(
                            "answer-context-final-"
                        )
                        row = rows_by_digest[digest]
                        if (
                            row["session_id"] != item.session_id
                            or row["link_id"] != item.link_id
                            or json.loads(row["answer_json"]) != item.answer
                            or row["source_run_id"] != item.source_run_id
                            or row["followup_occurrence_id"]
                            != item.followup_occurrence_id
                            or row["patient_timezone"] != item.patient_timezone
                            or row["reported_at"] != item.reported_at
                            or row["effective_start"] != item.effective_start
                            or row["effective_end"] != item.effective_end
                            or row["temporal_kind"] != item.temporal_kind
                            or row["resolution_basis"] != item.resolution_basis
                            or row["raw_text"] != item.raw_text
                            or (
                                json.loads(row["terminology_match_json"])
                                if row["terminology_match_json"]
                                else None
                            )
                            != item.terminology_match
                        ):
                            raise ValueError(
                                "provisional answer evidence changed before finalization"
                            )
                    active_report_rows = connection.execute(
                        "SELECT * FROM provisional_symptom_reports "
                        "WHERE session_id=? AND status='active'",
                        (session.session_id,),
                    ).fetchall()
                    expected_report_ids = {
                        item.report_id.removeprefix("symptom-report-final-")
                        for item in symptom_reports
                    }
                    actual_report_ids = {
                        hashlib.sha256(row["draft_report_id"].encode("utf-8")).hexdigest()[:24]
                        for row in active_report_rows
                    }
                    if expected_report_ids != actual_report_ids:
                        raise ValueError("provisional symptom reports changed before finalization")
                    report_rows_by_digest = {
                        hashlib.sha256(row["draft_report_id"].encode("utf-8")).hexdigest()[:24]: row
                        for row in active_report_rows
                    }
                    for item in symptom_reports:
                        digest = item.report_id.removeprefix(
                            "symptom-report-final-"
                        )
                        row = report_rows_by_digest[digest]
                        if (
                            row["session_id"] != item.session_id
                            or row["concept_id"] != item.concept_id
                            or row["preferred_zh"] != item.preferred_zh
                            or json.loads(row["coding_json"]) != item.coding
                            or json.loads(row["terminology_match_json"])
                            != item.terminology_match
                            or row["source_kind"] != item.source_kind
                            or row["source_run_id"] != item.source_run_id
                            or row["evidence_text"] != item.evidence_text
                            or row["evidence_start"] != item.evidence_start
                            or row["evidence_end"] != item.evidence_end
                            or row["followup_occurrence_id"]
                            != item.followup_occurrence_id
                            or row["patient_timezone"] != item.patient_timezone
                            or row["reported_at"] != item.reported_at
                            or row["effective_start"] != item.effective_start
                            or row["effective_end"] != item.effective_end
                            or row["temporal_kind"] != item.temporal_kind
                        ):
                            raise ValueError(
                                "provisional symptom evidence changed before finalization"
                            )
                    material_audits = connection.execute(
                        "SELECT * FROM audit_events "
                        "WHERE patient_id=? AND entity_type='CareSession' "
                        "AND entity_id=? "
                        "AND event_type='care_session_provisional_draft_saved'",
                        (patient_id, session.session_id),
                    ).fetchall()
                    context_digest_rows: dict[str, list[tuple[str, str]]] = {}
                    report_digest_rows: dict[str, list[tuple[str, str]]] = {}
                    for audit_row in material_audits:
                        details = json.loads(audit_row["details_json"])
                        context_map = details.get("draft_context_sha256")
                        report_map = details.get("draft_report_sha256")
                        if (
                            audit_row["actor_type"] != "deterministic_workflow"
                            or details.get("session_id") != session.session_id
                            or details.get("confirmation_status")
                            != "pending_final_review"
                            or not isinstance(context_map, dict)
                            or not isinstance(report_map, dict)
                            or sorted(context_map)
                            != sorted(details.get("draft_context_ids", []))
                            or sorted(report_map)
                            != sorted(details.get("draft_report_ids", []))
                        ):
                            raise ValueError(
                                "provisional material digest audit changed"
                            )
                        for material_id, material_digest in context_map.items():
                            context_digest_rows.setdefault(material_id, []).append(
                                (material_digest, details.get("source_run_id"))
                            )
                        for material_id, material_digest in report_map.items():
                            report_digest_rows.setdefault(material_id, []).append(
                                (material_digest, details.get("source_run_id"))
                            )
                    for row in active_rows:
                        matches = context_digest_rows.get(
                            row["draft_context_id"], []
                        )
                        if (
                            len(matches) != 1
                            or matches[0][0]
                            != _provisional_material_sha256(
                                self._provisional_answer_context(row)
                            )
                            or matches[0][1] != row["source_run_id"]
                        ):
                            raise ValueError(
                                "provisional answer material changed after staging"
                            )
                    for row in active_report_rows:
                        matches = report_digest_rows.get(
                            row["draft_report_id"], []
                        )
                        if (
                            len(matches) != 1
                            or matches[0][0]
                            != _provisional_material_sha256(
                                self._provisional_symptom_report(row)
                            )
                            or matches[0][1] != row["source_run_id"]
                        ):
                            raise ValueError(
                                "provisional symptom material changed after staging"
                            )
                    if connection.execute(
                        "SELECT 1 FROM confirmed_answer_contexts WHERE session_id=? LIMIT 1",
                        (session.session_id,),
                    ).fetchone() or connection.execute(
                        "SELECT 1 FROM confirmed_symptom_reports WHERE session_id=? LIMIT 1",
                        (session.session_id,),
                    ).fetchone():
                        raise ValueError("legacy confirmed material cannot mix with provisional draft")
                    draft_action_rows = connection.execute(
                        "SELECT * FROM patient_draft_action_resolutions "
                        "WHERE session_id=? AND status='active'",
                        (session.session_id,),
                    ).fetchall()
                    if {row["action_id"] for row in draft_action_rows} != set(
                        provisional_action_ids
                    ):
                        raise ValueError("provisional actions changed before finalization")
                    for action_row in draft_action_rows:
                        run_row = connection.execute(
                            "SELECT * FROM agent_runs WHERE run_id=?",
                            (action_row["source_run_id"],),
                        ).fetchone()
                        if (
                            run_row is None
                            or run_row["patient_id"] != patient_id
                            or run_row["session_id"] != session.session_id
                            or run_row["model_provider"]
                            != provisional_boundary["model_provider"]
                            or run_row["model_name"]
                            != provisional_boundary["model_name"]
                            or run_row["prompt_version"]
                            != provisional_boundary["prompt_version"]
                            or run_row["knowledge_release_id"]
                            != provisional_boundary["knowledge_release_id"]
                            or run_row["knowledge_release_id"]
                            != current["knowledge_release_id"]
                            or run_row["terminology_catalog_id"]
                            != provisional_boundary["terminology_catalog_id"]
                            or run_row["terminology_catalog_version"]
                            != provisional_boundary["terminology_catalog_version"]
                            or run_row["terminology_catalog_sha256"]
                            != provisional_boundary["terminology_catalog_sha256"]
                        ):
                            raise ValueError(
                                "provisional action model/release boundary changed"
                            )
                        run_result = SemanticResult.model_validate(
                            json.loads(run_row["output_json"])
                        )
                        extraction = [
                            item
                            for item in run_result.stage_traces
                            if item.stage == "care_extraction"
                        ]
                        available_actions = {
                            item.candidate_id for item in run_result.candidates
                        } | {
                            item.clarification_id
                            for item in run_result.clarifications
                        }
                        if (
                            run_result.mode not in MODEL_API_MODES
                            or len(extraction) != 1
                            or extraction[0].mode not in MODEL_API_MODES
                            or action_row["action_id"] not in available_actions
                        ):
                            raise ValueError(
                                "provisional action no longer matches its MiMo run"
                            )
                        if action_row["action_type"] == "candidate":
                            candidate = next(
                                (
                                    item
                                    for item in run_result.candidates
                                    if item.candidate_id == action_row["action_id"]
                                ),
                                None,
                            )
                            dynamic_catalog_candidate = bool(
                                candidate is not None
                                and candidate.link_id.startswith(
                                    "patient-reported-symptom::"
                                )
                                and candidate.source_mode
                                == CandidateSource.DETERMINISTIC_CATALOG
                                and candidate.terminology_match is not None
                                and candidate.terminology_match.source_catalog_id
                                and candidate.terminology_match.source_catalog_version
                                and candidate.terminology_match.source_catalog_sha256
                            )
                            patient_selection_candidate = bool(
                                candidate is not None
                                and candidate.source_mode
                                == CandidateSource.PATIENT_SELECTION
                                and not candidate.link_id.startswith(
                                    "patient-reported-symptom::"
                                )
                                and candidate.evidence_text == run_row["input_text"]
                                and candidate.evidence_start == 0
                                and candidate.evidence_end
                                == len(run_row["input_text"])
                                and run_result.task_id == run_row["task_id"]
                                and is_single_focused_patient_checkin_task(
                                    run_row["task_id"], candidate.link_id
                                )
                                and candidate.answer
                                == exact_questionnaire_direct_answer(
                                    load_glp1_questionnaire(),
                                    candidate.link_id,
                                    run_row["input_text"],
                                )
                                and run_result.ignored_reasons.count(
                                    "focused_governed_patient_selection_used"
                                )
                                == 1
                            )
                            if candidate is None or (
                                candidate.source_mode not in MODEL_CANDIDATE_SOURCES
                                and not dynamic_catalog_candidate
                                and not patient_selection_candidate
                            ):
                                raise ValueError(
                                    "provisional candidate source boundary changed"
                                )
                            match = candidate.terminology_match
                            source_boundary = provisional_boundary[
                                "dynamic_source"
                                if candidate.link_id.startswith(
                                    "patient-reported-symptom::"
                                )
                                else "fixed_source"
                            ]
                            if (
                                match is None
                                or match.catalog_id
                                != provisional_boundary["terminology_catalog_id"]
                                or match.catalog_version
                                != provisional_boundary[
                                    "terminology_catalog_version"
                                ]
                                or match.source_catalog_id
                                != source_boundary["catalog_id"]
                                or match.source_catalog_version
                                != source_boundary["version"]
                                or match.source_catalog_sha256
                                != source_boundary["sha256"]
                                or match.source_catalog_status
                                != source_boundary["status"]
                            ):
                                raise ValueError(
                                    "provisional terminology boundary changed"
                                )
                    if any(
                        connection.execute(
                            "SELECT 1 FROM conversation_action_resolutions WHERE action_id=?",
                            (action_id,),
                        ).fetchone()
                        for action_id in provisional_action_ids
                    ):
                        raise ValueError("provisional action was confirmed before final submit")
                unresolved = []
                for run_row in connection.execute(
                    "SELECT run_id, output_json FROM agent_runs WHERE session_id = ?",
                    (session.session_id,),
                ).fetchall():
                    run_result = SemanticResult.model_validate(
                        json.loads(run_row["output_json"])
                    )
                    for candidate in run_result.candidates:
                        decision = connection.execute(
                            "SELECT decision FROM conversation_action_resolutions WHERE action_id = ?",
                            (candidate.candidate_id,),
                        ).fetchone() or connection.execute(
                            "SELECT decision FROM patient_draft_action_resolutions "
                            "WHERE action_id=? AND status='active'",
                            (candidate.candidate_id,),
                        ).fetchone()
                        if decision is None:
                            unresolved.append(candidate.candidate_id)
                    for clarification in run_result.clarifications:
                        decision = connection.execute(
                            "SELECT decision FROM conversation_action_resolutions WHERE action_id = ?",
                            (clarification.clarification_id,),
                        ).fetchone() or connection.execute(
                            "SELECT decision FROM patient_draft_action_resolutions "
                            "WHERE action_id=? AND status='active'",
                            (clarification.clarification_id,),
                        ).fetchone()
                        if decision is None:
                            unresolved.append(clarification.clarification_id)
                if unresolved:
                    raise ValueError("patient check-in still has unresolved candidates")
                lineage_rows = {
                    row["event_id"]: row
                    for row in connection.execute(
                        "SELECT * FROM audit_events WHERE entity_type='CareSession' "
                        "AND entity_id=? AND event_type IN "
                        "('patient_answer_corrected', "
                        "'patient_answers_dependency_invalidated', "
                        "'patient_draft_answer_revised', "
                        "'patient_draft_dependencies_invalidated', "
                        "'patient_answer_selected', "
                        "'patient_draft_symptom_removed')",
                        (session.session_id,),
                    ).fetchall()
                }
                if set(lineage_rows) != set(revision_lineage_event_ids):
                    raise ValueError("patient revision lineage changed before finalization")
                if promote_provisional_draft:
                    all_draft_event_rows = connection.execute(
                        "SELECT event_id, entity_id, event_type, actor_type, details_json "
                        "FROM audit_events WHERE "
                        "(event_type='semantic_candidate_staged_to_draft' "
                        "AND entity_type='AgentRun' AND entity_id IN ("
                        "SELECT run_id FROM agent_runs WHERE session_id=?)) OR "
                        "(event_type='care_session_provisional_draft_saved' "
                        "AND entity_type='CareSession' AND entity_id=?)",
                        (session.session_id, session.session_id),
                    ).fetchall()
                    if {row["event_id"] for row in all_draft_event_rows} != set(
                        provisional_draft_event_ids
                    ):
                        raise ValueError("provisional draft audit changed before finalization")
                    draft_event_rows = [
                        row
                        for row in all_draft_event_rows
                        if row["event_type"]
                        == "semantic_candidate_staged_to_draft"
                    ]
                    events_by_action: dict[str, list[sqlite3.Row]] = {}
                    for row in draft_event_rows:
                        details = json.loads(row["details_json"])
                        for action_id in details.get("action_ids", []):
                            events_by_action.setdefault(action_id, []).append(row)
                    draft_actions_by_id = {
                        row["action_id"]: row for row in draft_action_rows
                    }
                    for action_id, action_row in draft_actions_by_id.items():
                        matching_events = events_by_action.get(action_id, [])
                        if len(matching_events) != 1:
                            raise ValueError(
                                "provisional action is missing unique staging audit"
                            )
                        event_row = matching_events[0]
                        details = json.loads(event_row["details_json"])
                        if (
                            event_row["entity_id"] != action_row["source_run_id"]
                            or event_row["actor_type"] != "deterministic_workflow"
                            or details.get("session_id") != session.session_id
                            or details.get("action_type")
                            != action_row["action_type"]
                            or details.get("decision") != action_row["decision"]
                            or details.get("option_id") != action_row["option_id"]
                            or details.get("confirmation_status")
                            != "pending_final_review"
                        ):
                            raise ValueError(
                                "provisional action staging audit payload changed"
                            )
                resolution_rows = connection.execute(
                    """
                    SELECT link_id, resolution, policy_version
                    FROM patient_collection_resolutions
                    WHERE session_id = ? AND is_current = 1
                    """,
                    (session.session_id,),
                ).fetchall()
                stored_resolutions = {
                    row["link_id"]: row["resolution"] for row in resolution_rows
                }
                if (
                    collection_policy_version is None
                    or stored_resolutions != (collection_resolutions or {})
                    or any(
                        row["policy_version"] != collection_policy_version
                        for row in resolution_rows
                    )
                ):
                    raise ValueError("patient collection resolutions are incomplete")
                alert_count = connection.execute(
                    "SELECT COUNT(*) FROM alerts WHERE patient_id = ?",
                    (patient_id,),
                ).fetchone()[0]
                approved_rule_count = connection.execute(
                    """
                    SELECT COUNT(*) FROM layer4_contract_records
                    WHERE record_type='clinical_rule'
                      AND status IN ('approved', 'active')
                    """
                ).fetchone()[0]
                downstream_count = connection.execute(
                    """
                    SELECT COUNT(*) FROM layer4_fhir_resources
                    WHERE patient_id = ? AND resource_type IN ('Task', 'Communication')
                      AND instr(resource_json, ?) > 0
                    """,
                    (patient_id, f"QuestionnaireResponse/{resource['id']}"),
                ).fetchone()[0]
                response_count = connection.execute(
                    "SELECT COUNT(*) FROM fhir_questionnaire_responses "
                    "WHERE patient_id = ? AND resource_id = ?",
                    (patient_id, resource["id"]),
                ).fetchone()[0]
                observation_count = connection.execute(
                    "SELECT COUNT(*) FROM fhir_observations "
                    "WHERE patient_id = ? AND questionnaire_response_id = ?",
                    (patient_id, resource["id"]),
                ).fetchone()[0]
                if any(
                    (
                        alert_count,
                        approved_rule_count,
                        downstream_count,
                        response_count,
                        observation_count,
                    )
                ):
                    raise ValueError("frozen patient check-in boundary was violated")

            for context in answer_contexts:
                connection.execute(
                    """
                    UPDATE confirmed_answer_contexts SET status = 'superseded'
                    WHERE session_id = ? AND link_id = ? AND status = 'active'
                    """,
                    (context.session_id, context.link_id),
                )
                connection.execute(
                    """
                    INSERT INTO confirmed_answer_contexts (
                        answer_context_id, session_id, link_id, answer_json,
                        source_run_id, followup_occurrence_id, patient_timezone,
                        reported_at, effective_start, effective_end, temporal_kind,
                        resolution_basis, raw_text, terminology_match_json,
                        status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        context.answer_context_id,
                        context.session_id,
                        context.link_id,
                        json.dumps(context.answer, ensure_ascii=False),
                        context.source_run_id,
                        context.followup_occurrence_id,
                        context.patient_timezone,
                        context.reported_at,
                        context.effective_start,
                        context.effective_end,
                        context.temporal_kind,
                        context.resolution_basis,
                        context.raw_text,
                        (
                            json.dumps(context.terminology_match, ensure_ascii=False)
                            if context.terminology_match is not None
                            else None
                        ),
                        context.status,
                        context.created_at,
                    ),
                )
            for report in symptom_reports:
                connection.execute(
                    """
                    INSERT INTO confirmed_symptom_reports (
                        report_id, session_id, concept_id, preferred_zh, coding_json,
                        terminology_match_json, source_kind, source_run_id,
                        evidence_text, evidence_start, evidence_end,
                        followup_occurrence_id, patient_timezone, reported_at,
                        effective_start, effective_end, temporal_kind, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        report.report_id,
                        report.session_id,
                        report.concept_id,
                        report.preferred_zh,
                        json.dumps(report.coding, ensure_ascii=False),
                        json.dumps(report.terminology_match, ensure_ascii=False),
                        report.source_kind,
                        report.source_run_id,
                        report.evidence_text,
                        report.evidence_start,
                        report.evidence_end,
                        report.followup_occurrence_id,
                        report.patient_timezone,
                        report.reported_at,
                        report.effective_start,
                        report.effective_end,
                        report.temporal_kind,
                        report.status,
                        report.created_at,
                    ),
                )

            if promote_provisional_draft:
                promoted_contexts = connection.execute(
                    "UPDATE provisional_answer_contexts SET status='promoted' "
                    "WHERE session_id=? AND status='active'",
                    (session.session_id,),
                ).rowcount
                promoted_reports = connection.execute(
                    "UPDATE provisional_symptom_reports SET status='promoted' "
                    "WHERE session_id=? AND status='active'",
                    (session.session_id,),
                ).rowcount
                if promoted_contexts != len(answer_contexts) or promoted_reports != len(
                    symptom_reports
                ):
                    raise ConcurrentWriteConflict(
                        "provisional material changed before promotion"
                    )

            connection.execute(
                """
                INSERT INTO followup_messages (
                    message_id, patient_id, message_text, submitted_at,
                    source, processing_status
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                tuple(message.model_dump().values()),
            )
            connection.execute(
                """
                INSERT INTO fhir_questionnaire_responses (
                    resource_id, patient_id, message_id, resource_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    resource["id"],
                    patient_id,
                    message.message_id,
                    payload(resource),
                    resource["authored"],
                ),
            )
            for item in validated_observations:
                connection.execute(
                    """
                    INSERT INTO fhir_observations (
                        observation_id, patient_id, questionnaire_response_id,
                        effective_time, resource_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.observation_id,
                        item.patient_id,
                        item.message_id,
                        item.effective_time,
                        payload(item.as_fhir()),
                        item.created_at,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO observation_evidence (
                        observation_id, confidence_tier, evidence_text,
                        evidence_start, evidence_end, recorded_at, source_kind,
                        terminology_match_json, metric_id,
                        evidence_claim_ids_json, knowledge_release_id,
                        observation_mapping_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.observation_id,
                        item.confidence_tier.value,
                        item.evidence_text,
                        item.evidence_start,
                        item.evidence_end,
                        item.created_at,
                        item.evidence.source_kind,
                        (
                            json.dumps(item.evidence.terminology_match, ensure_ascii=False)
                            if item.evidence.terminology_match is not None
                            else None
                        ),
                        item.evidence.metric_id,
                        json.dumps(item.evidence.evidence_claim_ids, ensure_ascii=False),
                        item.evidence.knowledge_release_id,
                        item.evidence.observation_mapping_sha256,
                    ),
                )
            cursor = connection.execute(
                """
                UPDATE care_sessions
                SET answers_json = ?, status = 'completed', updated_at = ?,
                    questionnaire_response_id = ?, completed_at = ?
                WHERE session_id = ? AND status = 'in_progress'
                  AND updated_at = ?
                """,
                (
                    json.dumps(session.answers, ensure_ascii=False),
                    resolved_at,
                    resource["id"],
                    resolved_at,
                    session.session_id,
                    expected_session.updated_at,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("随访状态已变化，请刷新后重试")
            for action_id in action_ids:
                cursor = connection.execute(
                    """
                    INSERT INTO conversation_action_resolutions (
                        action_id, source_run_id, session_id, decision, resolved_at
                    ) VALUES (?, ?, ?, 'accepted', ?)
                    ON CONFLICT(action_id) DO UPDATE SET
                        decision = 'accepted', resolved_at = excluded.resolved_at
                    WHERE conversation_action_resolutions.decision = 'unsure'
                    """,
                    (action_id, source_run_id, session.session_id, resolved_at),
                )
                if cursor.rowcount != 1:
                    raise ValueError("候选决策已完成，不能重复发布")
            if promote_provisional_draft:
                draft_rows = connection.execute(
                    "SELECT * FROM patient_draft_action_resolutions "
                    "WHERE session_id=? AND status='active'",
                    (session.session_id,),
                ).fetchall()
                if {row["action_id"] for row in draft_rows} != set(
                    provisional_action_ids
                ):
                    raise ConcurrentWriteConflict(
                        "provisional actions changed before final promotion"
                    )
                for row in draft_rows:
                    final_decision = row["decision"]
                    if row["decision"] == "drafted":
                        final_decision = (
                            "accepted"
                            if row["action_id"] in accepted_provisional_action_ids
                            else "rejected"
                        )
                    connection.execute(
                        "INSERT INTO conversation_action_resolutions ("
                        "action_id, source_run_id, session_id, decision, option_id, resolved_at"
                        ") VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            row["action_id"],
                            row["source_run_id"],
                            row["session_id"],
                            final_decision,
                            row["option_id"],
                            resolved_at,
                        ),
                    )
                finalized = connection.execute(
                    "UPDATE patient_draft_action_resolutions "
                    "SET status='finalized', finalized_at=? "
                    "WHERE session_id=? AND status='active'",
                    (resolved_at, session.session_id),
                ).rowcount
                if finalized != len(provisional_action_ids):
                    raise ConcurrentWriteConflict(
                        "provisional actions changed during final promotion"
                    )
            for event in audit_events:
                connection.execute(
                    """
                    INSERT INTO audit_events (
                        event_id, patient_id, entity_type, entity_id, event_type,
                        actor_type, details_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        event.patient_id,
                        event.entity_type,
                        event.entity_id,
                        event.event_type,
                        event.actor_type,
                        json.dumps(event.details_json, ensure_ascii=False),
                        event.created_at,
                    ),
                )
            for item in validated_layer4:
                resource_type = item["resourceType"]
                meta = item["meta"]
                clinical_time = (
                    item.get("authoredOn")
                    if resource_type == "Task"
                    else item.get("recorded")
                ) or meta["lastUpdated"]
                connection.execute(
                    """
                    INSERT INTO layer4_fhir_resources (
                        resource_type, resource_id, version_id, patient_id, status,
                        clinical_time, resource_json, is_current, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        resource_type,
                        item["id"],
                        meta["versionId"],
                        patient_id,
                        item.get("status"),
                        clinical_time,
                        payload(item),
                        meta["lastUpdated"],
                        meta["lastUpdated"],
                    ),
                )
            self._confirmed_review_fault("after_layer4_insert")
        return True

    def _confirmed_review_fault(self, stage: str) -> None:
        """Test seam for proving rollback after the final domain write."""

        return None

    def save_message(self, message: FollowUpMessage) -> None:
        with connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO followup_messages (
                    message_id, patient_id, message_text, submitted_at,
                    source, processing_status
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                tuple(message.model_dump().values()),
            )

    def update_message_status(self, message_id: str, status: str) -> None:
        with connect(self.db_path) as connection:
            connection.execute(
                "UPDATE followup_messages SET processing_status = ? WHERE message_id = ?",
                (status, message_id),
            )

    def get_message(self, message_id: str) -> FollowUpMessage | None:
        with connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT * FROM followup_messages WHERE message_id = ?", (message_id,)
            ).fetchone()
        return row_to_message(row) if row else None

    def save_questionnaire_response(
        self, resource: dict, questionnaire: dict | None = None
    ) -> None:
        resource = validate_questionnaire_response_against_questionnaire(
            resource,
            questionnaire
            or load_questionnaire_for_reference(str(resource.get("questionnaire") or "")),
        )
        patient_reference = resource["subject"]["reference"]
        patient_id = patient_reference.removeprefix("Patient/")
        with connect(self.db_path) as connection:
            message_row = connection.execute(
                "SELECT patient_id FROM followup_messages WHERE message_id = ?",
                (resource["id"],),
            ).fetchone()
            if message_row is None:
                raise FHIRValidationError(
                    "QuestionnaireResponse.id must match a stored follow-up message"
                )
            if message_row["patient_id"] != patient_id:
                raise FHIRValidationError(
                    "QuestionnaireResponse.subject must match the source message patient"
                )
            connection.execute(
                """
                INSERT INTO fhir_questionnaire_responses (
                    resource_id, patient_id, message_id, resource_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    resource["id"],
                    patient_id,
                    resource["id"],
                    json.dumps(resource, ensure_ascii=False, separators=(",", ":")),
                    resource["authored"],
                ),
            )

    def get_questionnaire_response(
        self, resource_id: str, questionnaire: dict | None = None
    ) -> dict | None:
        with connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT resource_json FROM fhir_questionnaire_responses
                WHERE resource_id = ?
                """,
                (resource_id,),
            ).fetchone()
        if row is None:
            return None
        return validate_questionnaire_response_against_questionnaire(
            (resource := json.loads(row["resource_json"])),
            questionnaire
            or load_questionnaire_for_reference(str(resource.get("questionnaire") or "")),
        )

    def list_completed_questionnaire_responses(
        self,
        patient_id: str,
        *,
        pathway_code: str,
        pathway_version: str,
    ) -> list[dict]:
        """Return completed responses proven to belong to one exact Pathway.

        This is the Layer-4 read boundary. Compatibility free-text responses
        and in-progress session state are intentionally excluded.
        """

        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT q.resource_json
                FROM fhir_questionnaire_responses q
                JOIN care_sessions c
                  ON c.questionnaire_response_id = q.resource_id
                WHERE q.patient_id = ?
                  AND c.status = 'completed'
                  AND c.pathway_code = ?
                  AND c.pathway_version = ?
                ORDER BY q.created_at DESC
                """,
                (patient_id, pathway_code, pathway_version),
            ).fetchall()
        validated = []
        for row in rows:
            resource = json.loads(row["resource_json"])
            questionnaire = load_questionnaire_for_reference(
                str(resource.get("questionnaire") or "")
            )
            validated.append(
                validate_questionnaire_response_against_questionnaire(
                    resource, questionnaire
                )
            )
        return validated

    def list_messages(self, patient_id: str) -> list[FollowUpMessage]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM followup_messages
                WHERE patient_id = ? ORDER BY submitted_at DESC
                """,
                (patient_id,),
            ).fetchall()
        return [row_to_message(row) for row in rows]

    def save_observations(self, observations: list[Observation]) -> None:
        if not observations:
            return
        with connect(self.db_path) as connection:
            for item in observations:
                resource = validate_r4_resource(
                    item.as_fhir(), expected_resource_type="Observation"
                )
                item = Observation(resource=resource, evidence=item.evidence)
                connection.execute(
                    """
                    INSERT INTO fhir_observations (
                        observation_id, patient_id, questionnaire_response_id,
                        effective_time, resource_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.observation_id,
                        item.patient_id,
                        item.message_id,
                        item.effective_time,
                        json.dumps(
                            item.as_fhir(), ensure_ascii=False, separators=(",", ":")
                        ),
                        item.created_at,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO observation_evidence (
                        observation_id, confidence_tier, evidence_text,
                        evidence_start, evidence_end, recorded_at, source_kind,
                        terminology_match_json, metric_id,
                        evidence_claim_ids_json, knowledge_release_id,
                        observation_mapping_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.observation_id,
                        item.confidence_tier.value,
                        item.evidence_text,
                        item.evidence_start,
                        item.evidence_end,
                        item.created_at,
                        item.evidence.source_kind,
                        (
                            json.dumps(
                                item.evidence.terminology_match, ensure_ascii=False
                            )
                            if item.evidence.terminology_match is not None
                            else None
                        ),
                        item.evidence.metric_id,
                        json.dumps(item.evidence.evidence_claim_ids, ensure_ascii=False),
                        item.evidence.knowledge_release_id,
                        item.evidence.observation_mapping_sha256,
                    ),
                )

    def list_observations(self, patient_id: str) -> list[Observation]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT o.*, e.confidence_tier, e.evidence_text,
                       e.evidence_start, e.evidence_end, e.recorded_at,
                       e.source_kind, e.terminology_match_json, e.metric_id,
                       e.evidence_claim_ids_json, e.knowledge_release_id,
                       e.observation_mapping_sha256
                FROM fhir_observations o
                JOIN observation_evidence e USING (observation_id)
                WHERE o.patient_id = ? ORDER BY o.effective_time DESC
                """,
                (patient_id,),
            ).fetchall()
        return [row_to_observation(row) for row in rows]

    def list_final_observations(
        self,
        patient_id: str,
        *,
        pathway_code: str,
        pathway_version: str,
    ) -> list[Observation]:
        """Return exact-Pathway candidates plus unowned rows for fail-closed review.

        Observations uniquely owned by another completed Pathway are excluded.
        Rows with no completed-session owner are deliberately returned so the
        Layer-4 admission predicate rejects the whole snapshot instead of
        silently shrinking its evidence set.
        """

        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT o.*, e.confidence_tier, e.evidence_text,
                       e.evidence_start, e.evidence_end, e.recorded_at,
                       e.source_kind, e.terminology_match_json, e.metric_id,
                       e.evidence_claim_ids_json, e.knowledge_release_id,
                       e.observation_mapping_sha256
                FROM fhir_observations o
                JOIN observation_evidence e USING (observation_id)
                LEFT JOIN care_sessions c
                  ON c.questionnaire_response_id = o.questionnaire_response_id
                 AND c.status = 'completed'
                WHERE o.patient_id = ?
                  AND (
                    c.session_id IS NULL
                    OR (
                      c.pathway_code = ?
                      AND c.pathway_version = ?
                    )
                  )
                ORDER BY o.effective_time DESC
                """,
                (patient_id, pathway_code, pathway_version),
            ).fetchall()
        return [row_to_observation(row) for row in rows]

    def list_pathway_audit_events(
        self,
        patient_id: str,
        *,
        pathway_code: str,
        pathway_version: str,
    ) -> list[AuditEvent]:
        """Return only audit rows whose durable entities prove exact Pathway scope."""

        with connect(self.db_path) as connection:
            session_rows = connection.execute(
                """
                SELECT session_id, questionnaire_response_id
                FROM care_sessions
                WHERE patient_id = ? AND pathway_code = ? AND pathway_version = ?
                """,
                (patient_id, pathway_code, pathway_version),
            ).fetchall()
            session_ids = {row["session_id"] for row in session_rows}
            response_ids = {
                row["questionnaire_response_id"]
                for row in session_rows
                if row["questionnaire_response_id"]
            }
            run_ids = {
                row["run_id"]
                for row in connection.execute(
                    "SELECT run_id FROM agent_runs WHERE session_id IN "
                    "(SELECT session_id FROM care_sessions WHERE patient_id = ? "
                    "AND pathway_code = ? AND pathway_version = ?)",
                    (patient_id, pathway_code, pathway_version),
                ).fetchall()
            }
            observation_ids = {
                row["observation_id"]
                for row in connection.execute(
                    """
                    SELECT o.observation_id
                    FROM fhir_observations o
                    JOIN care_sessions c
                      ON c.questionnaire_response_id = o.questionnaire_response_id
                    WHERE o.patient_id = ? AND c.status = 'completed'
                      AND c.pathway_code = ? AND c.pathway_version = ?
                    """,
                    (patient_id, pathway_code, pathway_version),
                ).fetchall()
            }
            supplemental_report_ids = {
                row["report_id"]
                for row in connection.execute(
                    """
                    SELECT report_id
                    FROM patient_supplemental_reports
                    WHERE patient_id = ? AND session_id IN (
                        SELECT session_id FROM care_sessions
                        WHERE patient_id = ? AND pathway_code = ? AND pathway_version = ?
                    )
                    """,
                    (patient_id, patient_id, pathway_code, pathway_version),
                ).fetchall()
            }
            fhir_rows = connection.execute(
                """
                SELECT resource_type, resource_id, version_id, resource_json
                FROM layer4_fhir_resources
                WHERE patient_id = ? AND resource_type IN ('Task', 'Communication')
                """,
                (patient_id,),
            ).fetchall()
            contract_rows = connection.execute(
                """
                SELECT record_type, record_id, record_json
                FROM layer4_contract_records
                WHERE patient_id = ? AND pathway_code = ?
                """,
                (patient_id, pathway_code),
            ).fetchall()
            audit_rows = connection.execute(
                "SELECT * FROM audit_events WHERE patient_id = ? ORDER BY created_at DESC",
                (patient_id,),
            ).fetchall()

        task_ids: set[str] = set()
        task_references: set[str] = set()
        communication_candidates: list[tuple[str, dict]] = []
        for row in fhir_rows:
            resource = json.loads(row["resource_json"])
            if row["resource_type"] == "Task":
                pathway_references = [
                    item.get("reference")
                    for item in resource.get("basedOn", [])
                    if isinstance(item.get("reference"), str)
                    and item["reference"].startswith("urn:continucare:pathway:")
                ]
                if pathway_references == [
                    f"urn:continucare:pathway:{pathway_code}|{pathway_version}"
                ]:
                    task_ids.add(row["resource_id"])
                    task_references.add(
                        f"Task/{row['resource_id']}/_history/{row['version_id']}"
                    )
            else:
                communication_candidates.append((row["resource_id"], resource))
        communication_ids = {
            resource_id
            for resource_id, resource in communication_candidates
            if any(
                item.get("reference") in task_references
                for item in resource.get("basedOn", [])
            )
        }
        contract_ids = {
            row["record_id"]
            for row in contract_rows
            if json.loads(row["record_json"]).get("pathway_version")
            == pathway_version
        }
        entity_ids = {
            "CareSession": session_ids,
            "AgentRun": run_ids,
            "QuestionnaireResponse": response_ids,
            "Observation": observation_ids,
            "SupplementalReport": supplemental_report_ids,
            "Task": task_ids,
            "Communication": communication_ids,
            "Layer4SummaryDraft": contract_ids,
        }
        return [
            row_to_audit_event(row)
            for row in audit_rows
            if row["entity_id"] in entity_ids.get(row["entity_type"], set())
        ]

    def list_observations_for_message(self, message_id: str) -> list[Observation]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT o.*, e.confidence_tier, e.evidence_text,
                       e.evidence_start, e.evidence_end, e.recorded_at,
                       e.source_kind, e.terminology_match_json, e.metric_id,
                       e.evidence_claim_ids_json, e.knowledge_release_id,
                       e.observation_mapping_sha256
                FROM fhir_observations o
                JOIN observation_evidence e USING (observation_id)
                WHERE o.questionnaire_response_id = ? ORDER BY e.evidence_start
                """,
                (message_id,),
            ).fetchall()
        return [row_to_observation(row) for row in rows]

    def get_observation(self, observation_id: str) -> Observation | None:
        with connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT o.*, e.confidence_tier, e.evidence_text,
                       e.evidence_start, e.evidence_end, e.recorded_at,
                       e.source_kind, e.terminology_match_json, e.metric_id,
                       e.evidence_claim_ids_json, e.knowledge_release_id,
                       e.observation_mapping_sha256
                FROM fhir_observations o
                JOIN observation_evidence e USING (observation_id)
                WHERE o.observation_id = ?
                """,
                (observation_id,),
            ).fetchone()
        return row_to_observation(row) if row else None

    def save_alert(self, alert: Alert) -> None:
        with connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO alerts (
                    alert_id, patient_id, severity, title, trigger_rule_id,
                    trigger_reason, evidence_refs_json, owner_role, status,
                    sla_due_at, created_at, resolved_at, resolution_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    alert.alert_id,
                    alert.patient_id,
                    alert.severity,
                    alert.title,
                    alert.trigger_rule_id,
                    alert.trigger_reason,
                    json.dumps(alert.evidence_refs, ensure_ascii=False),
                    alert.owner_role,
                    alert.status.value,
                    alert.sla_due_at,
                    alert.created_at,
                    alert.resolved_at,
                    alert.resolution_reason,
                ),
            )

    def update_alert(self, alert: Alert) -> None:
        with connect(self.db_path) as connection:
            connection.execute(
                """
                UPDATE alerts SET status = ?, owner_role = ?, resolved_at = ?,
                    resolution_reason = ? WHERE alert_id = ?
                """,
                (
                    alert.status.value,
                    alert.owner_role,
                    alert.resolved_at,
                    alert.resolution_reason,
                    alert.alert_id,
                ),
            )

    def get_alert(self, alert_id: str) -> Alert | None:
        with connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT * FROM alerts WHERE alert_id = ?", (alert_id,)
            ).fetchone()
        return row_to_alert(row) if row else None

    def list_alerts(self, patient_id: str | None = None) -> list[Alert]:
        query = "SELECT * FROM alerts"
        params: tuple[str, ...] = ()
        if patient_id:
            query += " WHERE patient_id = ?"
            params = (patient_id,)
        query += " ORDER BY created_at DESC"
        with connect(self.db_path) as connection:
            rows = connection.execute(query, params).fetchall()
        return [row_to_alert(row) for row in rows]

    def save_alert_action(self, action: AlertAction) -> None:
        with connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO alert_actions (
                    action_id, alert_id, action_type, actor_role, note, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                tuple(action.model_dump().values()),
            )

    def list_alert_actions(self, alert_id: str) -> list[AlertAction]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM alert_actions
                WHERE alert_id = ? ORDER BY created_at
                """,
                (alert_id,),
            ).fetchall()
        return [row_to_alert_action(row) for row in rows]

    def get_alert_action(self, action_id: str) -> AlertAction | None:
        with connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT * FROM alert_actions WHERE action_id = ?", (action_id,)
            ).fetchone()
        return row_to_alert_action(row) if row else None

    def save_summary(self, summary: Summary) -> None:
        with connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO summaries (
                    summary_id, patient_id, period_start, period_end, status,
                    summary_json, created_at, reviewed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    summary.summary_id,
                    summary.patient_id,
                    summary.period_start,
                    summary.period_end,
                    summary.status,
                    summary.summary_json.model_dump_json(),
                    summary.created_at,
                    summary.reviewed_at,
                ),
            )

    def update_summary_review(self, summary_id: str, reviewed_at: str) -> None:
        with connect(self.db_path) as connection:
            connection.execute(
                """
                UPDATE summaries SET status = 'reviewed', reviewed_at = ?
                WHERE summary_id = ?
                """,
                (reviewed_at, summary_id),
            )

    def get_latest_summary(self, patient_id: str) -> Summary | None:
        with connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT * FROM summaries WHERE patient_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (patient_id,),
            ).fetchone()
        return row_to_summary(row) if row else None

    def get_summary(self, summary_id: str) -> Summary | None:
        with connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT * FROM summaries WHERE summary_id = ?", (summary_id,)
            ).fetchone()
        return row_to_summary(row) if row else None

    def append_audit_event(self, event: AuditEvent) -> None:
        with connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO audit_events (
                    event_id, patient_id, entity_type, entity_id, event_type,
                    actor_type, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.patient_id,
                    event.entity_type,
                    event.entity_id,
                    event.event_type,
                    event.actor_type,
                    json.dumps(event.details_json, ensure_ascii=False),
                    event.created_at,
                ),
            )

    def list_audit_events(self, patient_id: str | None = None) -> list[AuditEvent]:
        query = "SELECT * FROM audit_events"
        params: tuple[str, ...] = ()
        if patient_id:
            query += " WHERE patient_id = ?"
            params = (patient_id,)
        query += " ORDER BY created_at DESC"
        with connect(self.db_path) as connection:
            rows = connection.execute(query, params).fetchall()
        return [row_to_audit_event(row) for row in rows]
