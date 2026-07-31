"""Persistent local DataStore backed by the standard sqlite3 module."""

from __future__ import annotations

import json
from pathlib import Path

from continucare.db import connect, initialize_database
from continucare.fhir.r4 import FHIRValidationError, validate_r4_resource
from continucare.fhir.references import (
    validate_questionnaire_response_against_questionnaire,
)
from continucare.models import (
    Alert,
    AlertAction,
    AuditEvent,
    FollowUpMessage,
    Observation,
    Patient,
    Summary,
)
from continucare.repositories import (
    row_to_alert,
    row_to_alert_action,
    row_to_audit_event,
    row_to_message,
    row_to_observation,
    row_to_patient,
    row_to_summary,
)
from continucare.pathways.fhir_artifacts import load_glp1_questionnaire


class SQLiteStore:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        initialize_database(self.db_path)

    def get_patient(self, patient_id: str) -> Patient | None:
        with connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT * FROM patients WHERE patient_id = ?", (patient_id,)
            ).fetchone()
        return row_to_patient(row) if row else None

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

    def save_questionnaire_response(self, resource: dict) -> None:
        resource = validate_questionnaire_response_against_questionnaire(
            resource, load_glp1_questionnaire()
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

    def get_questionnaire_response(self, resource_id: str) -> dict | None:
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
            json.loads(row["resource_json"]), load_glp1_questionnaire()
        )

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
                        evidence_start, evidence_end, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.observation_id,
                        item.confidence_tier.value,
                        item.evidence_text,
                        item.evidence_start,
                        item.evidence_end,
                        item.created_at,
                    ),
                )

    def list_observations(self, patient_id: str) -> list[Observation]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT o.*, e.confidence_tier, e.evidence_text,
                       e.evidence_start, e.evidence_end, e.recorded_at
                FROM fhir_observations o
                JOIN observation_evidence e USING (observation_id)
                WHERE o.patient_id = ? ORDER BY o.effective_time DESC
                """,
                (patient_id,),
            ).fetchall()
        return [row_to_observation(row) for row in rows]

    def list_observations_for_message(self, message_id: str) -> list[Observation]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT o.*, e.confidence_tier, e.evidence_text,
                       e.evidence_start, e.evidence_end, e.recorded_at
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
                       e.evidence_start, e.evidence_end, e.recorded_at
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
