"""Patient follow-up message intake without extraction concerns."""

from __future__ import annotations

from uuid import uuid4

from continucare.db import utc_now_iso
from continucare.models import FollowUpMessage
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
            message_id=f"message_{uuid4().hex}",
            patient_id=patient_id,
            message_text=text,
            submitted_at=utc_now_iso(),
            processing_status="received",
        )
        self.store.save_message(message)
        record_audit_event(
            self.store,
            patient_id=patient_id,
            entity_type="FollowUpMessage",
            entity_id=message.message_id,
            event_type="patient_message_submitted",
            actor_type="synthetic_patient",
            details={"source": message.source, "processing_status": "received"},
        )
        return message

