"""End-to-end patient intake orchestration used by UI and tests."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from continucare.models import Alert, ExtractionResult, FollowUpMessage, RiskDecision
from continucare.services.risk_rules import evaluate_risk


class WorkflowResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    message: FollowUpMessage
    extraction: ExtractionResult
    decision: RiskDecision
    alert: Alert | None


class FollowUpWorkflow:
    def __init__(self, followup_service, extraction_service, alert_service):
        self.followup_service = followup_service
        self.extraction_service = extraction_service
        self.alert_service = alert_service

    def submit(self, patient_id: str, message_text: str) -> WorkflowResult:
        message = self.followup_service.submit_message(patient_id, message_text)
        extraction = self.extraction_service.process_message(message)
        decision = evaluate_risk(extraction.observations)
        alert = self.alert_service.create_from_decision(patient_id, decision)
        self.followup_service.store.update_message_status(message.message_id, "processed")
        message.processing_status = "processed"
        return WorkflowResult(
            message=message,
            extraction=extraction,
            decision=decision,
            alert=alert,
        )

