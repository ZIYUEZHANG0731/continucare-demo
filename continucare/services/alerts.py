"""Alert creation, notification and auditable nurse state transitions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from continucare.db import utc_now_iso
from continucare.models import Alert, AlertAction, AlertStatus, RiskDecision
from continucare.services.audit import record_audit_event


class AlertService:
    def __init__(self, store, notifier):
        self.store = store
        self.notifier = notifier

    def create_from_decision(
        self, patient_id: str, decision: RiskDecision
    ) -> Alert | None:
        if not decision.create_alert:
            record_audit_event(
                self.store,
                patient_id=patient_id,
                entity_type="RiskEvaluation",
                entity_id=f"risk_{uuid4().hex}",
                event_type="risk_evaluated",
                actor_type="deterministic_rule_engine",
                details={
                    "classification": decision.severity,
                    "alert_created": False,
                    "reason": "no_approved_clinical_rule",
                },
            )
            return None

        if not all(
            (
                decision.title,
                decision.trigger_rule_id,
                decision.trigger_reason,
                decision.owner_role,
            )
        ):
            raise ValueError("Alert decision is missing required fields")

        now = datetime.now(timezone.utc)
        due = (
            now + timedelta(hours=decision.sla_hours or 0)
        ).isoformat()
        alert = Alert(
            alert_id=f"alert_{uuid4().hex}",
            patient_id=patient_id,
            severity=decision.severity,
            title=decision.title,
            trigger_rule_id=decision.trigger_rule_id,
            trigger_reason=decision.trigger_reason,
            evidence_refs=decision.evidence_refs,
            owner_role=decision.owner_role,
            sla_due_at=due,
            created_at=now.isoformat(),
        )
        record_audit_event(
            self.store,
            patient_id=patient_id,
            entity_type="RiskRule",
            entity_id=decision.trigger_rule_id,
            event_type="risk_rule_matched",
            actor_type="deterministic_rule_engine",
            details={
                "severity": decision.severity,
                "evidence_refs": decision.evidence_refs,
            },
        )
        self.store.save_alert(alert)
        record_audit_event(
            self.store,
            patient_id=patient_id,
            entity_type="Alert",
            entity_id=alert.alert_id,
            event_type="alert_created",
            actor_type="deterministic_rule_engine",
            details={
                "severity": alert.severity,
                "owner_role": alert.owner_role,
                "sla_due_at": alert.sla_due_at,
            },
        )
        delivery = self.notifier.notify_nurse(alert)
        record_audit_event(
            self.store,
            patient_id=patient_id,
            entity_type="Alert",
            entity_id=alert.alert_id,
            event_type="notification_mock_sent",
            actor_type="mock_notifier",
            details=delivery.model_dump(),
        )
        return alert

    def acknowledge(self, alert_id: str, note: str = "已确认收到") -> Alert:
        return self._transition(
            alert_id,
            status=AlertStatus.ACKNOWLEDGED,
            action_type="acknowledge",
            note=note.strip() or "已确认收到",
        )

    def escalate(self, alert_id: str, note: str = "已升级医生复核") -> Alert:
        return self._transition(
            alert_id,
            status=AlertStatus.ESCALATED,
            action_type="escalate_to_doctor",
            note=note.strip() or "已升级医生复核",
            owner_role="doctor",
        )

    def resolve(self, alert_id: str, note: str) -> Alert:
        resolution = note.strip()
        if not resolution:
            raise ValueError("关闭 Alert 必须填写处理记录")
        return self._transition(
            alert_id,
            status=AlertStatus.RESOLVED,
            action_type="resolve",
            note=resolution,
            resolution_reason=resolution,
        )

    def _transition(
        self,
        alert_id: str,
        *,
        status: AlertStatus,
        action_type: str,
        note: str,
        owner_role: str | None = None,
        resolution_reason: str | None = None,
    ) -> Alert:
        alert = self.store.get_alert(alert_id)
        if alert is None:
            raise ValueError("Alert 不存在")
        if alert.status == AlertStatus.RESOLVED:
            raise ValueError("已关闭的 Alert 不能再次操作")

        alert.status = status
        if owner_role:
            alert.owner_role = owner_role
        if status == AlertStatus.RESOLVED:
            alert.resolved_at = utc_now_iso()
            alert.resolution_reason = resolution_reason
        self.store.update_alert(alert)

        action = AlertAction(
            action_id=f"action_{uuid4().hex}",
            alert_id=alert.alert_id,
            action_type=action_type,
            actor_role="nurse",
            note=note,
            created_at=utc_now_iso(),
        )
        self.store.save_alert_action(action)
        record_audit_event(
            self.store,
            patient_id=alert.patient_id,
            entity_type="Alert",
            entity_id=alert.alert_id,
            event_type="nurse_alert_action",
            actor_type="nurse_demo_user",
            details={
                "action_id": action.action_id,
                "action_type": action.action_type,
                "status": alert.status.value,
                "note": action.note,
            },
        )
        return alert
