"""SQLite persistence for versioned Layer-4 contracts and FHIR resources."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel

from continucare.db import connect, initialize_database
from continucare.errors import ConcurrentWriteConflict, is_sqlite_busy
from continucare.layer4.contracts import (
    ClinicalStateSnapshot,
    ClinicalRuleDefinition,
    DoctorReview,
    Layer4ContractRecord,
    Layer4SummaryDraft,
    MemoryEvent,
    RevisionLink,
    TimelineEvent,
)
from continucare.layer4.fhir import validate_layer4_fhir_resource
from continucare.fhir.r4 import validate_r4_resource
from continucare.layer4.manual_reviews import (
    PENDING_APPROVAL,
    READY_TO_SEND,
    communication_readiness,
    is_manual_review_communication,
    is_manual_review_task,
)
from continucare.models import AuditEvent, FollowUpMessage

_CONTRACT_MODELS: dict[str, type[BaseModel]] = {
    "clinical_rule": ClinicalRuleDefinition,
    "memory_event": MemoryEvent,
    "timeline_event": TimelineEvent,
    "revision_link": RevisionLink,
    "summary_draft": Layer4SummaryDraft,
    "doctor_review": DoctorReview,
    "state_snapshot": ClinicalStateSnapshot,
}


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _fhir_patient_reference(resource: dict[str, Any]) -> str | None:
    resource_type = resource["resourceType"]
    if resource_type == "Task":
        return resource.get("for", {}).get("reference")
    if resource_type == "Communication":
        return resource.get("subject", {}).get("reference")
    return None


def _fhir_clinical_time(resource: dict[str, Any]) -> str:
    resource_type = resource["resourceType"]
    if resource_type == "Communication":
        return resource.get("sent") or resource.get("received") or resource["meta"][
            "lastUpdated"
        ]
    if resource_type == "Task":
        return resource.get("authoredOn") or resource["meta"]["lastUpdated"]
    return resource.get("recorded") or resource["meta"]["lastUpdated"]


def _contract_identity(
    record: Layer4ContractRecord,
) -> tuple[str, str, str, str | None, str | None, str, str, str]:
    if isinstance(record, ClinicalRuleDefinition):
        return (
            "clinical_rule",
            record.rule_id,
            record.version,
            None,
            record.applicability.pathway_code,
            record.lifecycle.value,
            record.created_at,
            record.created_at,
        )
    if isinstance(record, MemoryEvent):
        return (
            "memory_event",
            record.event_id,
            "1",
            record.patient_id,
            record.pathway_code,
            "current" if record.current else "superseded",
            record.effective_start,
            record.recorded_at,
        )
    if isinstance(record, TimelineEvent):
        return (
            "timeline_event",
            record.timeline_event_id,
            "1",
            record.patient_id,
            record.pathway_code,
            record.state.value,
            record.effective_start,
            record.recorded_at,
        )
    if isinstance(record, RevisionLink):
        return (
            "revision_link",
            record.link_id,
            "1",
            record.patient_id,
            None,
            record.relationship.value,
            record.created_at,
            record.created_at,
        )
    if isinstance(record, Layer4SummaryDraft):
        return (
            "summary_draft",
            record.summary_id,
            record.version,
            record.patient_id,
            record.pathway_code,
            record.status.value,
            record.period_end,
            record.created_at,
        )
    if isinstance(record, DoctorReview):
        return (
            "doctor_review",
            record.review_id,
            record.version,
            record.patient_id,
            None,
            record.decision.value,
            record.reviewed_at,
            record.reviewed_at,
        )
    if isinstance(record, ClinicalStateSnapshot):
        return (
            "state_snapshot",
            record.snapshot_id,
            record.version,
            record.patient_id,
            record.pathway_code,
            "complete",
            record.as_of,
            record.created_at,
        )
    raise TypeError(f"unsupported Layer-4 contract type: {type(record).__name__}")


class Layer4SQLiteStore:
    """Persist exact JSON while keeping only query projections in columns."""

    def __init__(self, db_path: Path | str, *, initialize: bool = True):
        self.db_path = Path(db_path)
        if initialize:
            initialize_database(self.db_path)

    def save_fhir_resource(
        self, resource: dict[str, Any], *, patient_id: str | None
    ) -> dict[str, Any]:
        normalized = validate_layer4_fhir_resource(resource)
        resource_type = normalized["resourceType"]
        resource_id = normalized.get("id")
        meta = normalized.get("meta", {})
        version_id = meta.get("versionId")
        updated_at = meta.get("lastUpdated")
        if not resource_id or not version_id or not updated_at:
            raise ValueError(
                "Layer-4 FHIR persistence requires id, meta.versionId and meta.lastUpdated"
            )
        expected_patient = _fhir_patient_reference(normalized)
        if expected_patient is not None:
            if not patient_id:
                raise ValueError(f"{resource_type} persistence requires patient_id")
            if expected_patient != f"Patient/{patient_id}":
                raise ValueError(
                    f"{resource_type} patient reference does not match patient_id"
                )
        payload = _json(normalized)
        clinical_time = _fhir_clinical_time(normalized)
        with connect(self.db_path) as connection:
            existing = connection.execute(
                """
                SELECT resource_json, patient_id FROM layer4_fhir_resources
                WHERE resource_type = ? AND resource_id = ? AND version_id = ?
                """,
                (resource_type, resource_id, version_id),
            ).fetchone()
            if existing:
                if existing["resource_json"] != payload or existing["patient_id"] != patient_id:
                    raise ValueError(
                        "FHIR resource version is immutable and conflicts with stored JSON"
                    )
                return normalized
            connection.execute(
                """
                UPDATE layer4_fhir_resources SET is_current = 0
                WHERE resource_type = ? AND resource_id = ? AND is_current = 1
                """,
                (resource_type, resource_id),
            )
            connection.execute(
                """
                INSERT INTO layer4_fhir_resources (
                    resource_type, resource_id, version_id, patient_id, status,
                    clinical_time, resource_json, is_current, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    resource_type,
                    resource_id,
                    version_id,
                    patient_id,
                    normalized.get("status"),
                    clinical_time,
                    payload,
                    updated_at,
                    updated_at,
                ),
            )
        return normalized

    def persist_doctor_review_bundle(
        self,
        *,
        expected_source: Layer4SummaryDraft,
        provenance: dict[str, Any],
        result_summary: Layer4SummaryDraft,
        review: DoctorReview,
        audit_event: AuditEvent,
    ) -> bool:
        """CAS one exact Summary review and commit every result atomically."""

        normalized_provenance = validate_layer4_fhir_resource(
            provenance, expected_resource_type="Provenance"
        )
        patient_id = expected_source.patient_id
        if (
            result_summary.patient_id != patient_id
            or review.patient_id != patient_id
            or audit_event.patient_id != patient_id
        ):
            raise ValueError("doctor review bundle patient mismatch")
        if (
            result_summary.summary_id != expected_source.summary_id
            or review.summary_id != expected_source.summary_id
            or review.summary_version != expected_source.version
            or review.result_summary_id != result_summary.summary_id
            or review.result_summary_version != result_summary.version
        ):
            raise ValueError("doctor review bundle Summary references do not match")
        try:
            expected_result_version = str(int(expected_source.version) + 1)
        except ValueError as exc:
            raise ValueError("summary versions must be numeric") from exc
        if result_summary.version != expected_result_version:
            raise ValueError("doctor review result must be the next Summary version")
        provenance_ref = f"Provenance/{normalized_provenance['id']}"
        if review.provenance_reference != provenance_ref:
            raise ValueError("doctor review Provenance reference mismatch")
        targets = {
            item.get("reference") for item in normalized_provenance.get("target", [])
        }
        if {
            f"urn:continucare:summary:{result_summary.summary_id}:version:{result_summary.version}",
            f"urn:continucare:doctor-review:{review.review_id}:version:{review.version}",
        } - targets:
            raise ValueError("doctor review Provenance must target result and review")
        if (
            audit_event.entity_type != "Layer4SummaryDraft"
            or audit_event.entity_id != result_summary.summary_id
            or audit_event.event_type != "doctor_reviewed_summary"
        ):
            raise ValueError("doctor review audit identity is invalid")

        source_payload = _json(expected_source.model_dump(mode="json"))
        provenance_payload = _json(normalized_provenance)
        result_identity = _contract_identity(result_summary)
        result_payload = _json(result_summary.model_dump(mode="json"))
        review_identity = _contract_identity(review)
        review_payload = _json(review.model_dump(mode="json"))
        audit_payload = _json(audit_event.details_json)

        try:
            with connect(self.db_path) as connection:
                connection.execute("PRAGMA busy_timeout=0")
                connection.execute("BEGIN IMMEDIATE")
                source_row = connection.execute(
                    """
                    SELECT record_json, patient_id, is_current
                    FROM layer4_contract_records
                    WHERE record_type='summary_draft' AND record_id=?
                      AND record_version=?
                    """,
                    (expected_source.summary_id, expected_source.version),
                ).fetchone()
                provenance_row = connection.execute(
                    """
                    SELECT resource_json, patient_id FROM layer4_fhir_resources
                    WHERE resource_type='Provenance' AND resource_id=? AND version_id=?
                    """,
                    (
                        normalized_provenance["id"],
                        normalized_provenance["meta"]["versionId"],
                    ),
                ).fetchone()
                result_row = connection.execute(
                    """
                    SELECT record_json, patient_id FROM layer4_contract_records
                    WHERE record_type='summary_draft' AND record_id=?
                      AND record_version=?
                    """,
                    (result_summary.summary_id, result_summary.version),
                ).fetchone()
                review_row = connection.execute(
                    """
                    SELECT record_json, patient_id FROM layer4_contract_records
                    WHERE record_type='doctor_review' AND record_id=?
                      AND record_version=?
                    """,
                    (review.review_id, review.version),
                ).fetchone()
                audit_row = connection.execute(
                    "SELECT * FROM audit_events WHERE event_id=?",
                    (audit_event.event_id,),
                ).fetchone()
                output_rows = (provenance_row, result_row, review_row, audit_row)
                if any(row is not None for row in output_rows):
                    exact = (
                        provenance_row is not None
                        and provenance_row["resource_json"] == provenance_payload
                        and provenance_row["patient_id"] == patient_id
                        and result_row is not None
                        and result_row["record_json"] == result_payload
                        and result_row["patient_id"] == patient_id
                        and review_row is not None
                        and review_row["record_json"] == review_payload
                        and review_row["patient_id"] == patient_id
                        and audit_row is not None
                        and audit_row["patient_id"] == patient_id
                        and audit_row["entity_type"] == audit_event.entity_type
                        and audit_row["entity_id"] == audit_event.entity_id
                        and audit_row["event_type"] == audit_event.event_type
                        and audit_row["actor_type"] == audit_event.actor_type
                        and _json(json.loads(audit_row["details_json"]))
                        == audit_payload
                        and audit_row["created_at"] == audit_event.created_at
                    )
                    if exact:
                        return False
                    raise ConcurrentWriteConflict(
                        "doctor review has a conflicting or partial replay"
                    )
                if (
                    source_row is None
                    or source_row["patient_id"] != patient_id
                    or source_row["record_json"] != source_payload
                    or source_row["is_current"] != 1
                ):
                    raise ConcurrentWriteConflict(
                        "doctor review source changed; refresh and retry"
                    )

                self._doctor_review_bundle_fault("before_provenance")
                meta = normalized_provenance["meta"]
                connection.execute(
                    """
                    INSERT INTO layer4_fhir_resources (
                        resource_type, resource_id, version_id, patient_id, status,
                        clinical_time, resource_json, is_current, created_at, updated_at
                    ) VALUES ('Provenance', ?, ?, ?, NULL, ?, ?, 1, ?, ?)
                    """,
                    (
                        normalized_provenance["id"],
                        meta["versionId"],
                        patient_id,
                        _fhir_clinical_time(normalized_provenance),
                        provenance_payload,
                        meta["lastUpdated"],
                        meta["lastUpdated"],
                    ),
                )
                self._doctor_review_bundle_fault("after_provenance")

                connection.execute(
                    """
                    UPDATE layer4_contract_records SET is_current=0
                    WHERE record_type='summary_draft' AND record_id=? AND is_current=1
                    """,
                    (result_summary.summary_id,),
                )
                connection.execute(
                    """
                    INSERT INTO layer4_contract_records (
                        record_type, record_id, record_version, patient_id,
                        pathway_code, status, effective_time, record_json,
                        is_current, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        result_identity[0],
                        result_identity[1],
                        result_identity[2],
                        result_identity[3],
                        result_identity[4],
                        result_identity[5],
                        result_identity[6],
                        result_payload,
                        result_identity[7],
                        result_identity[7],
                    ),
                )
                self._doctor_review_bundle_fault("after_summary")

                connection.execute(
                    """
                    INSERT INTO layer4_contract_records (
                        record_type, record_id, record_version, patient_id,
                        pathway_code, status, effective_time, record_json,
                        is_current, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        review_identity[0],
                        review_identity[1],
                        review_identity[2],
                        review_identity[3],
                        review_identity[4],
                        review_identity[5],
                        review_identity[6],
                        review_payload,
                        review_identity[7],
                        review_identity[7],
                    ),
                )
                self._doctor_review_bundle_fault("after_review")

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
                self._doctor_review_bundle_fault("after_audit")
                self._doctor_review_bundle_fault("before_commit")
        except sqlite3.OperationalError as exc:
            if is_sqlite_busy(exc):
                raise ConcurrentWriteConflict(
                    "doctor review database is busy; retry the same request"
                ) from exc
            raise
        return True

    def _doctor_review_bundle_fault(self, stage: str) -> None:
        """Test seam for proving rollback at every DoctorReview write point."""

        return None

    def persist_manual_review_action(
        self,
        *,
        patient_id: str,
        expected_task: dict[str, Any],
        resources: list[dict[str, Any]],
        audit_event: AuditEvent,
        expected_communication: dict[str, Any] | None = None,
    ) -> bool:
        """CAS one manual-review action as an exact, all-or-nothing write set."""

        expected_task = validate_layer4_fhir_resource(
            expected_task, expected_resource_type="Task"
        )
        if not is_manual_review_task(expected_task):
            raise ValueError("manual review action requires a manual-review Task")
        if expected_task.get("for", {}).get("reference") != f"Patient/{patient_id}":
            raise ValueError("manual review action Task patient mismatch")
        expected_communication_json: str | None = None
        if expected_communication is not None:
            expected_communication = validate_layer4_fhir_resource(
                expected_communication, expected_resource_type="Communication"
            )
            if not is_manual_review_communication(expected_communication):
                raise ValueError("manual review action Communication mismatch")
            if expected_communication.get("subject", {}).get("reference") != (
                f"Patient/{patient_id}"
            ):
                raise ValueError("manual review Communication patient mismatch")
            expected_communication_json = _json(expected_communication)
        if not resources:
            raise ValueError("manual review action requires resources")
        normalized = [validate_layer4_fhir_resource(item) for item in resources]
        identities = [
            (item["resourceType"], item["id"], item["meta"]["versionId"])
            for item in normalized
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("manual review action resource identities must be unique")
        if audit_event.patient_id != patient_id:
            raise ValueError("manual review action audit patient mismatch")

        non_provenance_refs: set[str] = set()
        for item in normalized:
            resource_type = item["resourceType"]
            if resource_type == "Task":
                if not is_manual_review_task(item):
                    raise ValueError("manual review action cannot write another Task class")
                if item.get("for", {}).get("reference") != f"Patient/{patient_id}":
                    raise ValueError("manual review Task patient mismatch")
                non_provenance_refs.add(
                    f"Task/{item['id']}/_history/{item['meta']['versionId']}"
                )
            elif resource_type == "Communication":
                if not is_manual_review_communication(item):
                    raise ValueError(
                        "manual review action cannot write another Communication class"
                    )
                if item.get("subject", {}).get("reference") != f"Patient/{patient_id}":
                    raise ValueError("manual review Communication patient mismatch")
                if "sent" in item or "received" in item:
                    raise ValueError("manual review Communication must remain unsent")
                if communication_readiness(item) not in {
                    PENDING_APPROVAL,
                    READY_TO_SEND,
                }:
                    raise ValueError("manual review Communication readiness is invalid")
                non_provenance_refs.add(
                    f"Communication/{item['id']}/_history/{item['meta']['versionId']}"
                )
        provenances = [
            item for item in normalized if item["resourceType"] == "Provenance"
        ]
        if len(provenances) != 1:
            raise ValueError("manual review action requires exactly one Provenance")
        provenance_targets = {
            target.get("reference")
            for target in provenances[0].get("target", [])
        }
        if not non_provenance_refs or not non_provenance_refs.issubset(
            provenance_targets
        ):
            raise ValueError("manual review Provenance must target every action resource")

        resource_payloads = {
            identity: _json(item) for identity, item in zip(identities, normalized)
        }
        expected_task_json = _json(expected_task)
        audit_details = _json(audit_event.details_json)
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_resources: dict[tuple[str, str, str], Any] = {}
            for identity in identities:
                row = connection.execute(
                    """
                    SELECT resource_json, patient_id FROM layer4_fhir_resources
                    WHERE resource_type = ? AND resource_id = ? AND version_id = ?
                    """,
                    identity,
                ).fetchone()
                if row is not None:
                    existing_resources[identity] = row
            existing_audit = connection.execute(
                "SELECT * FROM audit_events WHERE event_id = ?",
                (audit_event.event_id,),
            ).fetchone()
            if existing_resources or existing_audit is not None:
                exact_resources = len(existing_resources) == len(identities) and all(
                    row["resource_json"] == resource_payloads[identity]
                    and row["patient_id"] == patient_id
                    for identity, row in existing_resources.items()
                )
                exact_audit = (
                    existing_audit is not None
                    and existing_audit["patient_id"] == audit_event.patient_id
                    and existing_audit["entity_type"] == audit_event.entity_type
                    and existing_audit["entity_id"] == audit_event.entity_id
                    and existing_audit["event_type"] == audit_event.event_type
                    and existing_audit["actor_type"] == audit_event.actor_type
                    and _json(json.loads(existing_audit["details_json"]))
                    == audit_details
                    and existing_audit["created_at"] == audit_event.created_at
                )
                if exact_resources and exact_audit:
                    return False
                raise ValueError("manual review action has a conflicting partial replay")

            current_task = connection.execute(
                """
                SELECT resource_json FROM layer4_fhir_resources
                WHERE resource_type = 'Task' AND resource_id = ? AND is_current = 1
                """,
                (expected_task["id"],),
            ).fetchone()
            if current_task is None or current_task["resource_json"] != expected_task_json:
                raise ValueError("manual review Task changed; refresh and retry")
            if expected_communication is not None:
                current_communication = connection.execute(
                    """
                    SELECT resource_json FROM layer4_fhir_resources
                    WHERE resource_type = 'Communication' AND resource_id = ?
                      AND is_current = 1
                    """,
                    (expected_communication["id"],),
                ).fetchone()
                if (
                    current_communication is None
                    or current_communication["resource_json"]
                    != expected_communication_json
                ):
                    raise ValueError(
                        "manual review Communication changed; refresh and retry"
                    )

            ready = [
                item
                for item in normalized
                if item["resourceType"] == "Communication"
                and communication_readiness(item) == READY_TO_SEND
            ]
            if ready:
                if expected_task.get("status") != "completed":
                    raise ValueError("only a completed Task can authorize approval")
                if (
                    expected_communication is None
                    or communication_readiness(expected_communication)
                    != PENDING_APPROVAL
                ):
                    raise ValueError("only a pending draft can be approved")
                previous_rows = connection.execute(
                    """
                    SELECT resource_json FROM layer4_fhir_resources
                    WHERE resource_type = 'Communication' AND resource_id = ?
                    """,
                    (ready[0]["id"],),
                ).fetchall()
                if any(
                    communication_readiness(json.loads(row["resource_json"]))
                    == READY_TO_SEND
                    for row in previous_rows
                ):
                    raise ValueError("manual review Communication is already approved")

            for item in normalized:
                resource_type = item["resourceType"]
                meta = item["meta"]
                connection.execute(
                    """
                    UPDATE layer4_fhir_resources SET is_current = 0
                    WHERE resource_type = ? AND resource_id = ? AND is_current = 1
                    """,
                    (resource_type, item["id"]),
                )
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
                        _fhir_clinical_time(item),
                        _json(item),
                        meta["lastUpdated"],
                        meta["lastUpdated"],
                    ),
                )
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
                    audit_details,
                    audit_event.created_at,
                ),
            )
            self._manual_review_action_fault(audit_event.event_type)
        return True

    def _manual_review_action_fault(self, event_type: str) -> None:
        """Test seam for proving rollback after all M5-B domain writes."""

        return None

    def persist_manual_review_brief(
        self,
        *,
        patient_id: str,
        expected_task: dict[str, Any],
        expected_communication: dict[str, Any],
        expected_questionnaire_response: dict[str, Any],
        expected_observations: list[dict[str, Any]],
        expected_message: FollowUpMessage,
        expected_provenances: list[dict[str, Any]],
        expected_audits: list[AuditEvent],
        expected_current_summary: Layer4SummaryDraft | None,
        summary: Layer4SummaryDraft,
        summary_provenance: dict[str, Any],
        audit_event: AuditEvent,
    ) -> bool:
        """CAS one M5-C brief after rechecking every source in one transaction."""

        if summary.summary_kind != "manual_review_brief":
            raise ValueError("M5-C persistence requires a manual-review brief")
        if summary.patient_id != patient_id or audit_event.patient_id != patient_id:
            raise ValueError("M5-C brief patient mismatch")
        task = validate_layer4_fhir_resource(
            expected_task, expected_resource_type="Task"
        )
        communication = validate_layer4_fhir_resource(
            expected_communication, expected_resource_type="Communication"
        )
        response = validate_r4_resource(
            expected_questionnaire_response,
            expected_resource_type="QuestionnaireResponse",
        )
        observations = [
            validate_r4_resource(item, expected_resource_type="Observation")
            for item in expected_observations
        ]
        provenances = [
            validate_layer4_fhir_resource(item, expected_resource_type="Provenance")
            for item in expected_provenances
        ]
        generated_provenance = validate_layer4_fhir_resource(
            summary_provenance, expected_resource_type="Provenance"
        )
        summary_reference = (
            f"urn:continucare:summary:{summary.summary_id}:version:{summary.version}"
        )
        if summary_reference not in {
            item.get("reference")
            for item in generated_provenance.get("target", [])
        }:
            raise ValueError("M5-C Provenance must target the exact Summary version")
        if not provenances or not expected_audits:
            raise ValueError("M5-C brief requires Provenance and process audit evidence")

        summary_identity = _contract_identity(summary)
        summary_payload = _json(summary.model_dump(mode="json"))
        generated_provenance_payload = _json(generated_provenance)
        audit_payload = _json(audit_event.details_json)
        with connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")

            existing_summary = connection.execute(
                """
                SELECT record_json, patient_id FROM layer4_contract_records
                WHERE record_type='summary_draft' AND record_id=? AND record_version=?
                """,
                (summary.summary_id, summary.version),
            ).fetchone()
            existing_provenance = connection.execute(
                """
                SELECT resource_json, patient_id FROM layer4_fhir_resources
                WHERE resource_type='Provenance' AND resource_id=? AND version_id=?
                """,
                (
                    generated_provenance["id"],
                    generated_provenance["meta"]["versionId"],
                ),
            ).fetchone()
            existing_audit = connection.execute(
                "SELECT * FROM audit_events WHERE event_id=?",
                (audit_event.event_id,),
            ).fetchone()
            if any(
                item is not None
                for item in (existing_summary, existing_provenance, existing_audit)
            ):
                exact = (
                    existing_summary is not None
                    and existing_summary["record_json"] == summary_payload
                    and existing_summary["patient_id"] == patient_id
                    and existing_provenance is not None
                    and existing_provenance["resource_json"]
                    == generated_provenance_payload
                    and existing_provenance["patient_id"] == patient_id
                    and existing_audit is not None
                    and existing_audit["patient_id"] == patient_id
                    and existing_audit["entity_type"] == audit_event.entity_type
                    and existing_audit["entity_id"] == audit_event.entity_id
                    and existing_audit["event_type"] == audit_event.event_type
                    and existing_audit["actor_type"] == audit_event.actor_type
                    and _json(json.loads(existing_audit["details_json"]))
                    == audit_payload
                    and existing_audit["created_at"] == audit_event.created_at
                )
                if exact:
                    return False
                raise ValueError("M5-C brief has a conflicting partial replay")

            current_summary = connection.execute(
                """
                SELECT record_json FROM layer4_contract_records
                WHERE record_type='summary_draft' AND record_id=? AND is_current=1
                """,
                (summary.summary_id,),
            ).fetchone()
            expected_summary_json = (
                _json(expected_current_summary.model_dump(mode="json"))
                if expected_current_summary is not None
                else None
            )
            if (current_summary is None) != (expected_summary_json is None) or (
                current_summary is not None
                and current_summary["record_json"] != expected_summary_json
            ):
                raise ValueError("M5-C Summary changed; refresh and retry")

            self._require_current_fhir_source(
                connection, task, patient_id=patient_id
            )
            self._require_current_fhir_source(
                connection, communication, patient_id=patient_id
            )
            qr_row = connection.execute(
                """
                SELECT resource_json, patient_id FROM fhir_questionnaire_responses
                WHERE resource_id=?
                """,
                (response["id"],),
            ).fetchone()
            if (
                qr_row is None
                or qr_row["patient_id"] != patient_id
                or _json(json.loads(qr_row["resource_json"])) != _json(response)
            ):
                raise ValueError("M5-C QuestionnaireResponse changed")
            for observation in observations:
                row = connection.execute(
                    """
                    SELECT resource_json, patient_id FROM fhir_observations
                    WHERE observation_id=?
                    """,
                    (observation["id"],),
                ).fetchone()
                if (
                    row is None
                    or row["patient_id"] != patient_id
                    or _json(json.loads(row["resource_json"])) != _json(observation)
                ):
                    raise ValueError("M5-C Observation changed")
            message_row = connection.execute(
                "SELECT * FROM followup_messages WHERE message_id=?",
                (expected_message.message_id,),
            ).fetchone()
            if message_row is None or any(
                message_row[field] != value
                for field, value in expected_message.model_dump(mode="json").items()
            ):
                raise ValueError("M5-C patient message changed")
            for provenance in provenances:
                row = connection.execute(
                    """
                    SELECT resource_json, patient_id FROM layer4_fhir_resources
                    WHERE resource_type='Provenance' AND resource_id=? AND version_id=?
                    """,
                    (provenance["id"], provenance["meta"]["versionId"]),
                ).fetchone()
                if (
                    row is None
                    or row["patient_id"] != patient_id
                    or row["resource_json"] != _json(provenance)
                ):
                    raise ValueError("M5-C Provenance changed")
            for expected_audit in expected_audits:
                row = connection.execute(
                    "SELECT * FROM audit_events WHERE event_id=?",
                    (expected_audit.event_id,),
                ).fetchone()
                if (
                    row is None
                    or row["patient_id"] != patient_id
                    or row["entity_type"] != expected_audit.entity_type
                    or row["entity_id"] != expected_audit.entity_id
                    or row["event_type"] != expected_audit.event_type
                    or row["actor_type"] != expected_audit.actor_type
                    or _json(json.loads(row["details_json"]))
                    != _json(expected_audit.details_json)
                    or row["created_at"] != expected_audit.created_at
                ):
                    raise ValueError("M5-C audit evidence changed")

            meta = generated_provenance["meta"]
            connection.execute(
                """
                UPDATE layer4_fhir_resources SET is_current=0
                WHERE resource_type='Provenance' AND resource_id=? AND is_current=1
                """,
                (generated_provenance["id"],),
            )
            connection.execute(
                """
                INSERT INTO layer4_fhir_resources (
                    resource_type, resource_id, version_id, patient_id, status,
                    clinical_time, resource_json, is_current, created_at, updated_at
                ) VALUES ('Provenance', ?, ?, ?, NULL, ?, ?, 1, ?, ?)
                """,
                (
                    generated_provenance["id"],
                    meta["versionId"],
                    patient_id,
                    _fhir_clinical_time(generated_provenance),
                    generated_provenance_payload,
                    meta["lastUpdated"],
                    meta["lastUpdated"],
                ),
            )
            connection.execute(
                """
                UPDATE layer4_contract_records SET is_current=0
                WHERE record_type='summary_draft' AND record_id=? AND is_current=1
                """,
                (summary.summary_id,),
            )
            connection.execute(
                """
                INSERT INTO layer4_contract_records (
                    record_type, record_id, record_version, patient_id,
                    pathway_code, status, effective_time, record_json,
                    is_current, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    summary_identity[0],
                    summary_identity[1],
                    summary_identity[2],
                    summary_identity[3],
                    summary_identity[4],
                    summary_identity[5],
                    summary_identity[6],
                    summary_payload,
                    summary_identity[7],
                    summary_identity[7],
                ),
            )
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
            self._manual_review_brief_fault(summary.version)
        return True

    @staticmethod
    def _require_current_fhir_source(connection, resource, *, patient_id: str) -> None:
        row = connection.execute(
            """
            SELECT resource_json, patient_id FROM layer4_fhir_resources
            WHERE resource_type=? AND resource_id=? AND is_current=1
            """,
            (resource["resourceType"], resource["id"]),
        ).fetchone()
        if (
            row is None
            or row["patient_id"] != patient_id
            or row["resource_json"] != _json(resource)
        ):
            raise ValueError("M5-C source changed; refresh and retry")

    def _manual_review_brief_fault(self, version: str) -> None:
        """Test seam proving rollback after every M5-C write."""

        return None

    def get_fhir_resource(
        self,
        resource_type: str,
        resource_id: str,
        *,
        version_id: str | None = None,
    ) -> dict[str, Any] | None:
        query = (
            "SELECT resource_json FROM layer4_fhir_resources "
            "WHERE resource_type = ? AND resource_id = ?"
        )
        params: tuple[Any, ...] = (resource_type, resource_id)
        if version_id is None:
            query += " AND is_current = 1"
        else:
            query += " AND version_id = ?"
            params += (version_id,)
        with connect(self.db_path) as connection:
            row = connection.execute(query, params).fetchone()
        if row is None:
            return None
        return validate_layer4_fhir_resource(json.loads(row["resource_json"]))

    def list_fhir_resources(
        self,
        *,
        patient_id: str | None = None,
        resource_type: str | None = None,
        status: str | None = None,
        current_only: bool = True,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if patient_id is not None:
            clauses.append("patient_id = ?")
            params.append(patient_id)
        if resource_type is not None:
            clauses.append("resource_type = ?")
            params.append(resource_type)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if current_only:
            clauses.append("is_current = 1")
        query = "SELECT resource_json FROM layer4_fhir_resources"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY clinical_time DESC, resource_id"
        with connect(self.db_path) as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [
            validate_layer4_fhir_resource(json.loads(row["resource_json"]))
            for row in rows
        ]

    def save_contract(self, record: Layer4ContractRecord) -> Layer4ContractRecord:
        (
            record_type,
            record_id,
            record_version,
            patient_id,
            pathway_code,
            status,
            effective_time,
            updated_at,
        ) = _contract_identity(record)
        payload = _json(record.model_dump(mode="json"))
        with connect(self.db_path) as connection:
            existing = connection.execute(
                """
                SELECT record_json, patient_id FROM layer4_contract_records
                WHERE record_type = ? AND record_id = ? AND record_version = ?
                """,
                (record_type, record_id, record_version),
            ).fetchone()
            if existing:
                if existing["record_json"] != payload or existing["patient_id"] != patient_id:
                    raise ValueError(
                        "Layer-4 contract version is immutable and conflicts with stored JSON"
                    )
                return record
            connection.execute(
                """
                UPDATE layer4_contract_records SET is_current = 0
                WHERE record_type = ? AND record_id = ? AND is_current = 1
                """,
                (record_type, record_id),
            )
            connection.execute(
                """
                INSERT INTO layer4_contract_records (
                    record_type, record_id, record_version, patient_id,
                    pathway_code, status, effective_time, record_json,
                    is_current, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    record_type,
                    record_id,
                    record_version,
                    patient_id,
                    pathway_code,
                    status,
                    effective_time,
                    payload,
                    updated_at,
                    updated_at,
                ),
            )
        return record

    def get_contract(
        self,
        record_type: str,
        record_id: str,
        *,
        version: str | None = None,
    ) -> Layer4ContractRecord | None:
        model = _CONTRACT_MODELS.get(record_type)
        if model is None:
            raise ValueError(f"unknown Layer-4 record type {record_type!r}")
        query = (
            "SELECT record_json FROM layer4_contract_records "
            "WHERE record_type = ? AND record_id = ?"
        )
        params: tuple[Any, ...] = (record_type, record_id)
        if version is None:
            query += " AND is_current = 1"
        else:
            query += " AND record_version = ?"
            params += (version,)
        with connect(self.db_path) as connection:
            row = connection.execute(query, params).fetchone()
        if row is None:
            return None
        return cast(Layer4ContractRecord, model.model_validate_json(row["record_json"]))

    def list_contracts(
        self,
        record_type: str,
        *,
        patient_id: str | None = None,
        pathway_code: str | None = None,
        status: str | None = None,
        current_only: bool = True,
    ) -> list[Layer4ContractRecord]:
        model = _CONTRACT_MODELS.get(record_type)
        if model is None:
            raise ValueError(f"unknown Layer-4 record type {record_type!r}")
        clauses = ["record_type = ?"]
        params: list[Any] = [record_type]
        if patient_id is not None:
            clauses.append("patient_id = ?")
            params.append(patient_id)
        if pathway_code is not None:
            clauses.append("pathway_code = ?")
            params.append(pathway_code)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if current_only:
            clauses.append("is_current = 1")
        query = (
            "SELECT record_json FROM layer4_contract_records WHERE "
            + " AND ".join(clauses)
            + " ORDER BY effective_time DESC, record_id"
        )
        with connect(self.db_path) as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [
            cast(Layer4ContractRecord, model.model_validate_json(row["record_json"]))
            for row in rows
        ]
