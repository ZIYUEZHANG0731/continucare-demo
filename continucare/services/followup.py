"""Patient follow-up message intake without extraction concerns."""

from __future__ import annotations

from uuid import uuid4

from continucare.db import utc_now_iso
from continucare.fhir.questionnaires import build_free_text_questionnaire_response
from continucare.fhir.references import (
    validate_questionnaire_response_against_questionnaire,
)
from continucare.models import FollowUpMessage
from continucare.pathways.fhir_artifacts import load_glp1_questionnaire
from continucare.services.audit import record_audit_event


class FollowUpService:
    def __init__(self, store):
        self.store = store

    def submit_message(self, patient_id: str, message_text: str) -> FollowUpMessage:
        text = message_text.strip()
        if not text:
            raise ValueError("随访内容不能为空")
        patient = self.store.get_patient(patient_id)
        if patient is None or not patient.synthetic:
            raise ValueError("Demo 仅接受已登记的合成患者")

        message = FollowUpMessage(
            message_id=f"message-{uuid4().hex}",
            patient_id=patient_id,
            message_text=text,
            submitted_at=utc_now_iso(),
            processing_status="received",
        )
        self.store.save_message(message)
        questionnaire_response = build_free_text_questionnaire_response(
            response_id=message.message_id,
            patient_id=patient_id,
            authored=message.submitted_at,
            text=text,
        )
        questionnaire_response = validate_questionnaire_response_against_questionnaire(
            questionnaire_response, load_glp1_questionnaire()
        )
        self.store.save_questionnaire_response(questionnaire_response)
        record_audit_event(
            self.store,
            patient_id=patient_id,
            entity_type="QuestionnaireResponse",
            entity_id=message.message_id,
            event_type="patient_message_submitted",
            actor_type="synthetic_patient",
            details={
                "source": message.source,
                "processing_status": "received",
                "fhir_version": "4.0.1",
                "resource_type": "QuestionnaireResponse",
            },
        )
        return message
