"""Technology-independent adapter protocols."""

from __future__ import annotations

from typing import Protocol

from continucare.models import (
    Alert,
    AuditEvent,
    DeliveryResult,
    ExtractionResult,
    FollowUpMessage,
    Observation,
    Summary,
    SummaryContext,
    SummaryDraft,
)


class AIExtractor(Protocol):
    def extract(self, message: FollowUpMessage) -> ExtractionResult: ...

    def generate_summary(self, context: SummaryContext) -> SummaryDraft: ...


class DataStore(Protocol):
    def save_message(self, message: FollowUpMessage) -> None: ...

    def save_observations(self, observations: list[Observation]) -> None: ...

    def save_alert(self, alert: Alert) -> None: ...

    def update_alert(self, alert: Alert) -> None: ...

    def save_summary(self, summary: Summary) -> None: ...

    def append_audit_event(self, event: AuditEvent) -> None: ...


class NotificationChannel(Protocol):
    def notify_nurse(self, alert: Alert) -> DeliveryResult: ...

    def notify_doctor(self, summary: Summary) -> DeliveryResult: ...
