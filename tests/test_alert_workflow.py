from __future__ import annotations

import pytest

from continucare.adapters.mock_extractor import MockExtractor
from continucare.adapters.mock_notifier import MockNotifier
from continucare.adapters.sqlite_store import SQLiteStore
from continucare.demo_data import DEMO_PATIENT_ID, QUANTIFIED_MESSAGE
from continucare.models import AlertStatus, RiskDecision
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


def create_synthetic_workflow_alert(store):
    """Exercise the generic task workflow without inventing a clinical rule."""

    decision = RiskDecision(
        severity="test_only",
        create_alert=True,
        title="合成工作流测试任务",
        trigger_rule_id="TEST-ONLY-WORKFLOW",
        trigger_reason="非临床测试夹具",
        owner_role="nurse",
        sla_hours=24,
    )
    alert = AlertService(store, MockNotifier()).create_from_decision(
        DEMO_PATIENT_ID, decision
    )
    assert alert is not None
    return alert


def test_alert_resolution_requires_note(tmp_path):
    store = SQLiteStore(tmp_path / "alerts.db")
    alert = create_synthetic_workflow_alert(store)

    with pytest.raises(ValueError, match="必须填写处理记录"):
        AlertService(store, MockNotifier()).resolve(alert.alert_id, "   ")

    persisted = store.get_alert(alert.alert_id)
    assert persisted.status == AlertStatus.OPEN
    assert store.list_alert_actions(alert.alert_id) == []


def test_nurse_actions_are_persisted_and_audited(tmp_path):
    store = SQLiteStore(tmp_path / "actions.db")
    alert = create_synthetic_workflow_alert(store)
    service = AlertService(store, MockNotifier())

    service.acknowledge(alert.alert_id)
    service.resolve(alert.alert_id, "已完成合成工作流复核并关闭")

    persisted = store.get_alert(alert.alert_id)
    actions = store.list_alert_actions(alert.alert_id)
    audit_types = [event.event_type for event in store.list_audit_events()]
    assert persisted.status == AlertStatus.RESOLVED
    assert persisted.resolution_reason == "已完成合成工作流复核并关闭"
    assert [action.action_type for action in actions] == ["acknowledge", "resolve"]
    assert audit_types.count("nurse_alert_action") == 2


def test_patient_workflow_fails_closed_without_approved_rule(tmp_path):
    store = SQLiteStore(tmp_path / "workflow.db")

    result = make_workflow(store).submit(DEMO_PATIENT_ID, QUANTIFIED_MESSAGE)

    assert result.decision.severity == "not_assessed"
    assert result.alert is None
    assert store.get_message(result.message.message_id).processing_status == "processed"
    event_types = [event.event_type for event in store.list_audit_events()]
    assert "risk_evaluated" in event_types
    assert "risk_rule_matched" not in event_types
    assert "alert_created" not in event_types
    assert "notification_mock_sent" not in event_types
