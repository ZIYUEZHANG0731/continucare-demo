"""Evidence-bound 14-day summary generation and doctor review workflow."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from continucare.db import utc_now_iso
from continucare.models import Summary, SummaryContext, SummaryItem
from continucare.services.audit import record_audit_event


class SummaryService:
    def __init__(self, store, extractor, notifier):
        self.store = store
        self.extractor = extractor
        self.notifier = notifier

    def generate(
        self, patient_id: str, *, period_end: date | None = None
    ) -> Summary:
        patient = self.store.get_patient(patient_id)
        if patient is None:
            raise ValueError("合成患者不存在")
        end = period_end or datetime.now(timezone.utc).date()
        start = end - timedelta(days=13)

        messages = [
            item
            for item in self.store.list_messages(patient_id)
            if start <= datetime.fromisoformat(item.submitted_at).date() <= end
        ]
        observations = [
            item
            for item in self.store.list_observations(patient_id)
            if start <= datetime.fromisoformat(item.effective_time).date() <= end
        ]
        alerts = [
            item
            for item in self.store.list_alerts(patient_id)
            if start <= datetime.fromisoformat(item.created_at).date() <= end
        ]
        actions = [
            action
            for alert in alerts
            for action in self.store.list_alert_actions(alert.alert_id)
        ]
        context = SummaryContext(
            patient=patient,
            messages=messages,
            observations=observations,
            alerts=alerts,
            alert_actions=actions,
        )
        draft = self.extractor.generate_summary(context)
        self._append_missing_dates(draft.content.missing_data, patient.patient_id, messages, start, end)
        summary = Summary(
            summary_id=f"summary_{uuid4().hex}",
            patient_id=patient_id,
            period_start=start.isoformat(),
            period_end=end.isoformat(),
            status="draft",
            summary_json=draft.content,
            created_at=utc_now_iso(),
        )
        self.store.save_summary(summary)
        record_audit_event(
            self.store,
            patient_id=patient_id,
            entity_type="Summary",
            entity_id=summary.summary_id,
            event_type="summary_generated",
            actor_type="local_template_generator",
            details={
                "period_start": summary.period_start,
                "period_end": summary.period_end,
                "evidence_bound": True,
            },
        )
        delivery = self.notifier.notify_doctor(summary)
        record_audit_event(
            self.store,
            patient_id=patient_id,
            entity_type="Summary",
            entity_id=summary.summary_id,
            event_type="summary_notification_mock_sent",
            actor_type="mock_notifier",
            details=delivery.model_dump(),
        )
        return summary

    def review(self, summary_id: str) -> Summary:
        summary = self.store.get_summary(summary_id)
        if summary is None:
            raise ValueError("Summary 不存在")
        if summary.reviewed_at:
            return summary
        reviewed_at = utc_now_iso()
        self.store.update_summary_review(summary_id, reviewed_at)
        record_audit_event(
            self.store,
            patient_id=summary.patient_id,
            entity_type="Summary",
            entity_id=summary.summary_id,
            event_type="doctor_reviewed_summary",
            actor_type="doctor_demo_user",
            details={"reviewed_at": reviewed_at, "emr_written": False},
        )
        summary.status = "reviewed"
        summary.reviewed_at = reviewed_at
        return summary

    @staticmethod
    def _append_missing_dates(
        target: list[SummaryItem], patient_id: str, messages, start: date, end: date
    ) -> None:
        submitted_dates = {
            datetime.fromisoformat(message.submitted_at).date() for message in messages
        }
        all_dates = {start + timedelta(days=offset) for offset in range(14)}
        missing = sorted(all_dates - submitted_dates)
        if not missing:
            return
        refs = [patient_id] + [message.message_id for message in messages]
        target.append(
            SummaryItem(
                text=(
                    f"14 天窗口内有 {len(missing)} 个日期没有随访记录："
                    + "、".join(item.isoformat() for item in missing)
                    + "。"
                ),
                evidence_refs=refs,
            )
        )

