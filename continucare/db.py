"""SQLite bootstrap used by the local demo."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from continucare.config import get_settings


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path is not None else get_settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(db_path: Path | str | None = None) -> None:
    with connect(db_path) as connection:
        connection.executescript(SCHEMA_SQL)
        connection.execute(
            """
            INSERT INTO demo_metadata (key, value, updated_at)
            VALUES ('data_classification', 'synthetic_only', ?)
            ON CONFLICT(key) DO NOTHING
            """,
            (utc_now_iso(),),
        )
        _seed_demo_patient(connection)


def reset_demo(db_path: Path | str | None = None) -> None:
    path = Path(db_path) if db_path is not None else get_settings().db_path
    if path.exists():
        path.unlink()
    initialize_database(path)
    with connect(path) as connection:
        connection.execute(
            """
            INSERT INTO audit_events (
                event_id, patient_id, entity_type, entity_id, event_type,
                actor_type, details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"audit_{uuid4().hex}",
                "P-DEMO-001",
                "Demo",
                "local_demo",
                "demo_reset",
                "demo_operator",
                json.dumps({"synthetic_only": True}, ensure_ascii=False),
                utc_now_iso(),
            ),
        )


def _seed_demo_patient(connection: sqlite3.Connection) -> None:
    now = datetime.now(timezone.utc)
    connection.execute(
        """
        INSERT INTO patients (
            patient_id, display_name, synthetic, pathway_code,
            enrollment_date, next_visit_date, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(patient_id) DO NOTHING
        """,
        (
            "P-DEMO-001",
            "陈女士（合成）",
            1,
            "GLP1-14D",
            now.date().isoformat(),
            (now.date() + timedelta(days=14)).isoformat(),
            "active",
            now.isoformat(),
        ),
    )


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS demo_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS patients (
    patient_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    synthetic INTEGER NOT NULL CHECK (synthetic IN (0, 1)),
    pathway_code TEXT NOT NULL,
    enrollment_date TEXT NOT NULL,
    next_visit_date TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS followup_messages (
    message_id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    message_text TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    source TEXT NOT NULL,
    processing_status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fhir_questionnaire_responses (
    resource_id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    message_id TEXT NOT NULL UNIQUE REFERENCES followup_messages(message_id) ON DELETE CASCADE,
    resource_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fhir_observations (
    observation_id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    questionnaire_response_id TEXT NOT NULL
        REFERENCES fhir_questionnaire_responses(resource_id) ON DELETE CASCADE,
    effective_time TEXT NOT NULL,
    resource_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS observation_evidence (
    observation_id TEXT PRIMARY KEY
        REFERENCES fhir_observations(observation_id) ON DELETE CASCADE,
    confidence_tier TEXT NOT NULL CHECK (
        confidence_tier IN (
            'patient_confirmed', 'verbatim_explicit',
            'model_inferred', 'needs_human_review'
        )
    ),
    evidence_text TEXT NOT NULL,
    evidence_start INTEGER NOT NULL,
    evidence_end INTEGER NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    trigger_rule_id TEXT NOT NULL,
    trigger_reason TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    owner_role TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('open', 'acknowledged', 'escalated', 'resolved')
    ),
    sla_due_at TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    resolution_reason TEXT
);

CREATE TABLE IF NOT EXISTS alert_actions (
    action_id TEXT PRIMARY KEY,
    alert_id TEXT NOT NULL REFERENCES alerts(alert_id) ON DELETE CASCADE,
    action_type TEXT NOT NULL,
    actor_role TEXT NOT NULL,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS summaries (
    summary_id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    status TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    reviewed_at TEXT
);

CREATE TABLE IF NOT EXISTS audit_events (
    event_id TEXT PRIMARY KEY,
    patient_id TEXT REFERENCES patients(patient_id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_patient_time
ON followup_messages(patient_id, submitted_at);
CREATE INDEX IF NOT EXISTS idx_observations_patient_time
ON fhir_observations(patient_id, effective_time);
CREATE INDEX IF NOT EXISTS idx_questionnaire_responses_patient_time
ON fhir_questionnaire_responses(patient_id, created_at);
CREATE INDEX IF NOT EXISTS idx_alerts_patient_status
ON alerts(patient_id, status);
CREATE INDEX IF NOT EXISTS idx_audit_patient_time
ON audit_events(patient_id, created_at);
"""
