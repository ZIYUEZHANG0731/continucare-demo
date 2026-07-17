from __future__ import annotations

import pytest

from continucare.adapters.mock_extractor import MockExtractor
from continucare.adapters.mock_notifier import MockNotifier
from continucare.adapters.sqlite_store import SQLiteStore
from continucare.demo_data import DEMO_PATIENT_ID, L2_MESSAGE
from continucare.models import AlertStatus
from continucare.services.alerts import AlertService
from continucare.services.extraction import ExtractionService
from continucare.services.followup import FollowUpService
from continucare.services.workflow import FollowUpWorkflow


def make_workflow(store):
    return FollowUpWorkflow(
        FollowUpService(store),
        ExtractionService(store, MockExtractor()),
        AlertService(store, MockNotifier()),
    )


def test_alert_resolution_requires_note(tmp_path):
    store = SQLiteStore(tmp_path / "alerts.db")
    result = make_workflow(store).submit(DEMO_PATIENT_ID, L2_MESSAGE)

    with pytest.raises(ValueError, match="必须填写处理记录"):
        AlertService(store, MockNotifier()).resolve(result.alert.alert_id, "   ")

    persisted = store.get_alert(result.alert.alert_id)
    assert persisted.status == AlertStatus.OPEN
    assert store.list_alert_actions(result.alert.alert_id) == []


def test_nurse_actions_are_persisted_and_audited(tmp_path):
    store = SQLiteStore(tmp_path / "actions.db")
    result = make_workflow(store).submit(DEMO_PATIENT_ID, L2_MESSAGE)
    service = AlertService(store, MockNotifier())

    service.acknowledge(result.alert.alert_id)
    service.resolve(result.alert.alert_id, "已完成合成演示复核并关闭")

    alert = store.get_alert(result.alert.alert_id)
    actions = store.list_alert_actions(result.alert.alert_id)
    audit_types = [event.event_type for event in store.list_audit_events()]
    assert alert.status == AlertStatus.RESOLVED
    assert alert.resolution_reason == "已完成合成演示复核并关闭"
    assert [action.action_type for action in actions] == ["acknowledge", "resolve"]
    assert audit_types.count("nurse_alert_action") == 2


def test_end_to_end_l2_reaches_nurse_queue(tmp_path):
    store = SQLiteStore(tmp_path / "workflow.db")

    result = make_workflow(store).submit(DEMO_PATIENT_ID, L2_MESSAGE)

    assert result.decision.severity == "L2"
    assert result.alert is not None
    assert store.get_message(result.message.message_id).processing_status == "processed"
    assert store.get_alert(result.alert.alert_id).owner_role == "nurse"
    event_types = [event.event_type for event in store.list_audit_events()]
    assert "risk_rule_matched" in event_types
    assert "alert_created" in event_types
    assert "notification_mock_sent" in event_types
