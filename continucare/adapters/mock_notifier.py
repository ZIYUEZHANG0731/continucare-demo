"""Explicitly labelled local substitute for future Feishu delivery."""

from __future__ import annotations

from uuid import uuid4

from continucare.models import Alert, DeliveryResult, Summary


class MockNotifier:
    label = "模拟飞书通知（Mock，未联调）"

    def notify_nurse(self, alert: Alert) -> DeliveryResult:
        return DeliveryResult(
            delivered=True,
            channel="mock_feishu",
            delivery_id=f"mock_delivery_{uuid4().hex}",
            label=self.label,
            detail=(
                f"本地模拟卡片：{alert.severity} / {alert.title} / "
                f"责任角色 {alert.owner_role}"
            ),
        )

    def notify_doctor(self, summary: Summary) -> DeliveryResult:
        return DeliveryResult(
            delivered=True,
            channel="mock_feishu",
            delivery_id=f"mock_delivery_{uuid4().hex}",
            label=self.label,
            detail=f"本地模拟简报通知：{summary.summary_id}",
        )

