"""SQLite persistence for versioned Layer-4 contracts and FHIR resources."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel

from continucare.db import connect, initialize_database
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

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
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
