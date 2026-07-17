"""Append-only audit helpers."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from continucare.db import utc_now_iso
from continucare.models import AuditEvent


def record_audit_event(
    store,
    *,
    patient_id: str | None,
    entity_type: str,
    entity_id: str,
    event_type: str,
    actor_type: str,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        event_id=f"audit_{uuid4().hex}",
        patient_id=patient_id,
        entity_type=entity_type,
        entity_id=entity_id,
        event_type=event_type,
        actor_type=actor_type,
        details_json=details or {},
        created_at=utc_now_iso(),
    )
    store.append_audit_event(event)
    return event

