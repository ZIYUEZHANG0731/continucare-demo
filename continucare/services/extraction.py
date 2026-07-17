"""Extraction orchestration: persist facts, status and audit evidence."""

from __future__ import annotations

from continucare.models import ExtractionResult, FollowUpMessage
from continucare.services.audit import record_audit_event


class ExtractionService:
    def __init__(self, store, extractor):
        self.store = store
        self.extractor = extractor

    def process_message(self, message: FollowUpMessage) -> ExtractionResult:
        existing = self.store.list_observations_for_message(message.message_id)
        if existing:
            return ExtractionResult(
                observations=existing,
                extractor_mode=getattr(self.extractor, "mode", "local_mock_rules"),
            )

        result = self.extractor.extract(message)
        self.store.save_observations(result.observations)
        self.store.update_message_status(message.message_id, "extracted")
        record_audit_event(
            self.store,
            patient_id=message.patient_id,
            entity_type="FollowUpMessage",
            entity_id=message.message_id,
            event_type="extraction_completed",
            actor_type="local_mock_extractor",
            details={
                "extractor_mode": result.extractor_mode,
                "observation_refs": [
                    item.observation_id for item in result.observations
                ],
            },
        )
        return result

