"""Technology-neutral persistence port for Layer-4 services."""

from __future__ import annotations

from typing import Any, Protocol

from continucare.layer4.contracts import Layer4ContractRecord, Layer4SummaryDraft
from continucare.models import AuditEvent, FollowUpMessage


class Layer4Repository(Protocol):
    def save_fhir_resource(
        self, resource: dict[str, Any], *, patient_id: str | None
    ) -> dict[str, Any]: ...

    def get_fhir_resource(
        self,
        resource_type: str,
        resource_id: str,
        *,
        version_id: str | None = None,
    ) -> dict[str, Any] | None: ...

    def list_fhir_resources(
        self,
        *,
        patient_id: str | None = None,
        resource_type: str | None = None,
        status: str | None = None,
        current_only: bool = True,
    ) -> list[dict[str, Any]]: ...

    def save_contract(self, record: Layer4ContractRecord) -> Layer4ContractRecord: ...

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
    ) -> bool: ...

    def get_contract(
        self,
        record_type: str,
        record_id: str,
        *,
        version: str | None = None,
    ) -> Layer4ContractRecord | None: ...

    def list_contracts(
        self,
        record_type: str,
        *,
        patient_id: str | None = None,
        pathway_code: str | None = None,
        status: str | None = None,
        current_only: bool = True,
    ) -> list[Layer4ContractRecord]: ...
