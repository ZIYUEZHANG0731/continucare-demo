from __future__ import annotations

from datetime import datetime, timezone

from continucare.adapters.mock_extractor import MockExtractor
from continucare.adapters.mock_notifier import MockNotifier
from continucare.adapters.sqlite_store import SQLiteStore
from continucare.demo_data import DEMO_PATIENT_ID, L2_MESSAGE
from continucare.services.alerts import AlertService
from continucare.services.extraction import ExtractionService
from continucare.services.followup import FollowUpService
from continucare.services.summaries import SummaryService
from continucare.services.workflow import FollowUpWorkflow


def build_story(store):
    workflow = FollowUpWorkflow(
        FollowUpService(store),
        ExtractionService(store, MockExtractor()),
        AlertService(store, MockNotifier()),
    )
    result = workflow.submit(DEMO_PATIENT_ID, L2_MESSAGE)
    AlertService(store, MockNotifier()).resolve(
        result.alert.alert_id, "已完成合成演示复核"
    )
    return result


def all_summary_items(summary):
    content = summary.summary_json
    for field_name in type(content).model_fields:
        yield from getattr(content, field_name)


def test_summary_bullets_have_evidence_refs(tmp_path):
    store = SQLiteStore(tmp_path / "summary.db")
    build_story(store)

    summary = SummaryService(store, MockExtractor(), MockNotifier()).generate(
        DEMO_PATIENT_ID,
        period_end=datetime.now(timezone.utc).date(),
    )

    items = list(all_summary_items(summary))
    assert items
    assert all(item.evidence_refs for item in items)
    alert_items = summary.summary_json.alerts_and_actions
    assert len(alert_items) == 1
    assert "已完成合成演示复核" in alert_items[0].text


def test_doctor_review_is_persisted_and_audited(tmp_path):
    store = SQLiteStore(tmp_path / "review.db")
    build_story(store)
    service = SummaryService(store, MockExtractor(), MockNotifier())
    summary = service.generate(DEMO_PATIENT_ID)

    reviewed = service.review(summary.summary_id)
    reopened = SQLiteStore(store.db_path).get_summary(summary.summary_id)

    assert reviewed.status == "reviewed"
    assert reopened.reviewed_at is not None
    events = SQLiteStore(store.db_path).list_audit_events()
    assert "doctor_reviewed_summary" in [event.event_type for event in events]


def test_medication_question_is_recorded_without_advice(tmp_path):
    store = SQLiteStore(tmp_path / "question.db")
    workflow = FollowUpWorkflow(
        FollowUpService(store),
        ExtractionService(store, MockExtractor()),
        AlertService(store, MockNotifier()),
    )
    workflow.submit(DEMO_PATIENT_ID, "我想问药要不要调整？")

    summary = SummaryService(store, MockExtractor(), MockNotifier()).generate(
        DEMO_PATIENT_ID
    )

    assert [item.text for item in summary.summary_json.patient_questions] == [
        "患者希望医生确认是否需要调整。"
    ]
