from __future__ import annotations

from continucare.adapters.mock_extractor import MockExtractor
from continucare.adapters.mock_notifier import MockNotifier
from continucare.adapters.sqlite_store import SQLiteStore
from continucare.demo_data import DEMO_PATIENT_ID, QUANTIFIED_MESSAGE
from continucare.services.alerts import AlertService
from continucare.services.extraction import ExtractionService
from continucare.services.followup import FollowUpService
from continucare.services.summaries import SummaryService
from continucare.services.workflow import FollowUpWorkflow


def test_end_to_end_fhir_collection_and_review(tmp_path):
    store = SQLiteStore(tmp_path / "end-to-end.db")
    workflow = FollowUpWorkflow(
        FollowUpService(store),
        ExtractionService(store, MockExtractor()),
        AlertService(store, MockNotifier()),
    )

    result = workflow.submit(DEMO_PATIENT_ID, QUANTIFIED_MESSAGE)
    summary_service = SummaryService(store, MockExtractor(), MockNotifier())
    summary = summary_service.generate(DEMO_PATIENT_ID)
    summary_service.review(summary.summary_id)

    reopened = SQLiteStore(store.db_path)
    questionnaire_response = reopened.get_questionnaire_response(
        result.message.message_id
    )
    observations = reopened.list_observations_for_message(result.message.message_id)
    persisted_summary = reopened.get_summary(summary.summary_id)

    assert questionnaire_response["resourceType"] == "QuestionnaireResponse"
    assert questionnaire_response["id"] == result.message.message_id
    assert {item.code for item in observations} == {"94070-0", "75301-2"}
    assert all(item.resource["resourceType"] == "Observation" for item in observations)
    assert all(
        item.resource["derivedFrom"][0]["reference"]
        == f"QuestionnaireResponse/{result.message.message_id}"
        for item in observations
    )
    assert result.decision.severity == "not_assessed"
    assert result.alert is None
    assert reopened.list_alerts() == []
    assert persisted_summary.status == "reviewed"
    assert all(
        item.evidence_refs
        for field_name in type(persisted_summary.summary_json).model_fields
        for item in getattr(persisted_summary.summary_json, field_name)
    )
    event_types = [event.event_type for event in reopened.list_audit_events()]
    assert "patient_message_submitted" in event_types
    assert "extraction_completed" in event_types
    assert "risk_evaluated" in event_types
    assert "risk_rule_matched" not in event_types
    assert "alert_created" not in event_types
    assert "summary_generated" in event_types
    assert "doctor_reviewed_summary" in event_types
