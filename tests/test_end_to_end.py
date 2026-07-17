from __future__ import annotations

from continucare.adapters.mock_extractor import MockExtractor
from continucare.adapters.mock_notifier import MockNotifier
from continucare.adapters.sqlite_store import SQLiteStore
from continucare.demo_data import DEMO_PATIENT_ID, L2_MESSAGE
from continucare.models import AlertStatus
from continucare.services.alerts import AlertService
from continucare.services.extraction import ExtractionService
from continucare.services.followup import FollowUpService
from continucare.services.summaries import SummaryService
from continucare.services.workflow import FollowUpWorkflow


def test_end_to_end_l2_workflow(tmp_path):
    store = SQLiteStore(tmp_path / "end-to-end.db")
    workflow = FollowUpWorkflow(
        FollowUpService(store),
        ExtractionService(store, MockExtractor()),
        AlertService(store, MockNotifier()),
    )

    result = workflow.submit(DEMO_PATIENT_ID, L2_MESSAGE)
    AlertService(store, MockNotifier()).acknowledge(result.alert.alert_id)
    AlertService(store, MockNotifier()).resolve(
        result.alert.alert_id, "已完成合成演示复核并留痕"
    )
    summary_service = SummaryService(store, MockExtractor(), MockNotifier())
    summary = summary_service.generate(DEMO_PATIENT_ID)
    summary_service.review(summary.summary_id)

    reopened = SQLiteStore(store.db_path)
    assert reopened.get_alert(result.alert.alert_id).status == AlertStatus.RESOLVED
    persisted_summary = reopened.get_summary(summary.summary_id)
    assert persisted_summary.status == "reviewed"
    assert all(
        item.evidence_refs
        for field_name in type(persisted_summary.summary_json).model_fields
        for item in getattr(persisted_summary.summary_json, field_name)
    )
    event_types = [event.event_type for event in reopened.list_audit_events()]
    assert "patient_message_submitted" in event_types
    assert "extraction_completed" in event_types
    assert "risk_rule_matched" in event_types
    assert "alert_created" in event_types
    assert "notification_mock_sent" in event_types
    assert "nurse_alert_action" in event_types
    assert "summary_generated" in event_types
    assert "doctor_reviewed_summary" in event_types
