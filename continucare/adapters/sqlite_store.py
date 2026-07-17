"""Persistent local DataStore backed by the standard sqlite3 module."""

from __future__ import annotations

import json
from pathlib import Path

from continucare.db import connect, initialize_database
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
            connection.executemany(
                """
                INSERT INTO observations (
                    observation_id, patient_id, message_id, code, value_json,
                    unit, effective_time, source, confidence_tier, evidence_text,
                    evidence_start, evidence_end, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item.observation_id,
                        item.patient_id,
                        item.message_id,
                        item.code,
                        json.dumps(item.value, ensure_ascii=False),
                        item.unit,
                        item.effective_time,
                        item.source,
                        item.confidence_tier.value,
                        item.evidence_text,
                        item.evidence_start,
                        item.evidence_end,
                        item.created_at,
                    )
                    for item in observations
                ],
            )

    def list_observations(self, patient_id: str) -> list[Observation]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM observations
                WHERE patient_id = ? ORDER BY effective_time DESC
                """,
                (patient_id,),
            ).fetchall()
        return [row_to_observation(row) for row in rows]

    def list_observations_for_message(self, message_id: str) -> list[Observation]:
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM observations
                WHERE message_id = ? ORDER BY evidence_start
                """,
                (message_id,),
            ).fetchall()
        return [row_to_observation(row) for row in rows]

    def get_observation(self, observation_id: str) -> Observation | None:
        with connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT * FROM observations WHERE observation_id = ?",
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
