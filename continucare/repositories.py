"""SQLite row mappings kept separate from business services."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable

from continucare.models import (
    Alert,
    AlertAction,
    AuditEvent,
    FollowUpMessage,
    Observation,
    Patient,
    Summary,
)


def row_to_patient(row: sqlite3.Row) -> Patient:
    return Patient(**{**dict(row), "synthetic": bool(row["synthetic"])})


def row_to_message(row: sqlite3.Row) -> FollowUpMessage:
    return FollowUpMessage(**dict(row))


def row_to_observation(row: sqlite3.Row) -> Observation:
    data = dict(row)
    return Observation(
        resource=json.loads(data["resource_json"]),
        evidence={
            "questionnaire_response_id": data["questionnaire_response_id"],
            "confidence_tier": data["confidence_tier"],
            "evidence_text": data["evidence_text"],
            "evidence_start": data["evidence_start"],
            "evidence_end": data["evidence_end"],
            "recorded_at": data["recorded_at"],
        },
    )


def row_to_alert(row: sqlite3.Row) -> Alert:
    data = dict(row)
    data["evidence_refs"] = json.loads(data.pop("evidence_refs_json"))
    return Alert(**data)


def row_to_alert_action(row: sqlite3.Row) -> AlertAction:
    return AlertAction(**dict(row))


def row_to_summary(row: sqlite3.Row) -> Summary:
    data = dict(row)
    data["summary_json"] = json.loads(data["summary_json"])
    return Summary(**data)


def row_to_audit_event(row: sqlite3.Row) -> AuditEvent:
    data = dict(row)
    data["details_json"] = json.loads(data["details_json"])
    return AuditEvent(**data)


def placeholders(values: Iterable[Any]) -> str:
    return ", ".join("?" for _ in values)
