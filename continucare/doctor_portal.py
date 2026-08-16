"""Server-owned read model for the standalone doctor web application."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from continucare.adapters.sqlite_store import SQLiteStore
from continucare.care_engine import CareEngine
from continucare.config import get_settings
from continucare.db import initialize_database, utc_now_iso
from continucare.demo_data import DEMO_PATIENT_ID
from continucare.doctor_ui import (
    build_doctor_followup_overview,
    build_doctor_metric_dashboard,
    doctor_metric_summary,
    doctor_stage_label,
)
from continucare.fhir.questionnaires import questionnaire_response_answers
from continucare.layer4.manual_reviews import ManualReviewQueue
from continucare.layer4.storage import Layer4SQLiteStore
from continucare.nurse_ui import build_nurse_answer_cards
from continucare.pathways import load_builtin_pathways
from continucare.services.competition_demo import read_competition_demo


class DoctorPortalBoundaryError(ValueError):
    """Stable rejection for missing or out-of-scope doctor portal data."""


def canonical_db_path() -> Path:
    configured = get_settings().db_path
    if not configured.is_absolute():
        configured = Path(__file__).resolve().parents[1] / configured
    return configured.resolve()


def _allowed_patient_ids() -> set[str]:
    import os

    configured = {
        item.strip()
        for item in os.getenv("CONTINUCARE_DOCTOR_PATIENT_IDS", "").split(",")
        if item.strip()
    }
    return configured or {DEMO_PATIENT_ID}


def list_doctor_patients() -> list[dict[str, Any]]:
    db_path = canonical_db_path()
    initialize_database(db_path)
    allowed = _allowed_patient_ids()
    store = SQLiteStore(db_path, initialize=False)
    return [
        {
            "patientId": patient.patient_id,
            "displayName": patient.display_name,
            "pathwayCode": patient.pathway_code,
            "nextVisitDate": patient.next_visit_date,
            "status": patient.status,
            "synthetic": patient.synthetic,
        }
        for patient in store.list_patients()
        if patient.patient_id in allowed
    ]


def _metric_payload(card) -> dict[str, Any]:
    latest = card.points[-1] if card.points else card.primary_status
    latest_display = latest.display if latest is not None else None
    if latest is not None and card.metric_key == "nausea" and card.primary_status is latest:
        latest_display = f"{latest.display}恶心"
    return {
        "metricKey": card.metric_key,
        "title": card.title,
        "chartKind": card.chart_kind,
        "unit": card.unit,
        "summary": doctor_metric_summary(card),
        "latest": (
            {
                "display": latest_display,
                "label": latest.label,
                "value": latest.value,
            }
            if latest is not None
            else None
        ),
        "sourceIds": list(card.source_ids),
        "points": [
            {
                "timestamp": point.timestamp,
                "label": point.label,
                "value": point.value,
                "display": point.display,
                "sourceId": point.source_id,
                "statusName": point.status_name,
            }
            for point in card.points
        ],
    }


def _task_output(task: dict[str, Any], code: str) -> str | None:
    values = [
        item.get("valueCode") or item.get("valueString")
        for item in task.get("output", [])
        if code
        in {
            coding.get("code")
            for coding in item.get("type", {}).get("coding", [])
        }
    ]
    return str(values[0]) if len(values) == 1 and values[0] else None


def _doctor_escalations(
    *, db_path: Path, store: SQLiteStore, patient_id: str
) -> list[dict[str, Any]]:
    repository = Layer4SQLiteStore(db_path, initialize=False)
    tasks = ManualReviewQueue(repository).list_for_patient(patient_id)
    sessions = store.list_care_sessions(patient_id)
    questionnaire = None
    if sessions:
        try:
            questionnaire = CareEngine(store).questionnaire_for_session(sessions[0])
        except (LookupError, ValueError):
            questionnaire = None
    rows: list[dict[str, Any]] = []
    for task in tasks:
        if _task_output(task, "review-outcome") != "escalated_to_doctor":
            continue
        response_ref = str(task.get("reasonReference", {}).get("reference") or "")
        response = (
            store.get_questionnaire_response(response_ref.split("/", 1)[1])
            if response_ref.startswith("QuestionnaireResponse/")
            else None
        )
        cards = (
            build_nurse_answer_cards(
                questionnaire,
                questionnaire_response_answers(response),
            )
            if questionnaire is not None and response is not None
            else ()
        )
        rows.append(
            {
                "taskId": str(task.get("id") or ""),
                "status": "awaiting_doctor_assessment",
                "statusLabel": "待医生评估",
                "submittedAt": str(task.get("authoredOn") or ""),
                "nurseReviewedAt": str(
                    task.get("executionPeriod", {}).get("end")
                    or task.get("meta", {}).get("lastUpdated")
                    or ""
                ),
                "nurseNote": _task_output(task, "review-note") or "护士未填写说明",
                "clinicalAssessment": "not_assessed",
                "answers": [
                    {
                        "question": card.question,
                        "answer": card.answer,
                        "wide": card.wide,
                    }
                    for card in cards
                ],
            }
        )
    return sorted(
        rows,
        key=lambda item: (item["nurseReviewedAt"], item["taskId"]),
        reverse=True,
    )


def build_doctor_portal_state(patient_id: str = DEMO_PATIENT_ID) -> dict[str, Any]:
    """Return one read-only dashboard projection for an authorized patient."""

    if patient_id not in _allowed_patient_ids():
        raise DoctorPortalBoundaryError("该患者不在当前医生的授权范围内")
    db_path = canonical_db_path()
    initialize_database(db_path)
    store = SQLiteStore(db_path, initialize=False)
    patient = store.get_patient(patient_id)
    if patient is None:
        raise DoctorPortalBoundaryError("患者记录不存在")
    pathways = load_builtin_pathways()
    pathway = pathways.get(patient.pathway_code)
    if pathway is None:
        raise DoctorPortalBoundaryError("患者随访路径不可读取")
    observations = tuple(
        store.list_final_observations(
            patient_id,
            pathway_code=pathway.code,
            pathway_version=pathway.version,
        )
    )
    dashboard = build_doctor_metric_dashboard(observations)
    overview = build_doctor_followup_overview(dashboard)
    escalations = _doctor_escalations(
        db_path=db_path,
        store=store,
        patient_id=patient_id,
    )
    stage_label = patient.status
    if patient_id == DEMO_PATIENT_ID:
        try:
            stage_label = doctor_stage_label(read_competition_demo(db_path))
        except (LookupError, OSError, ValueError):
            stage_label = "状态待同步"
    return {
        "version": 1,
        "generatedAt": utc_now_iso(),
        "patient": {
            "patientId": patient.patient_id,
            "displayName": patient.display_name,
            "synthetic": patient.synthetic,
            "pathwayCode": patient.pathway_code,
            "pathwayVersion": pathway.version,
            "enrollmentDate": patient.enrollment_date,
            "nextVisitDate": patient.next_visit_date,
            "status": patient.status,
        },
        "workspace": {"stageLabel": stage_label},
        "overview": {
            "title": overview.title,
            "periodLabel": overview.period_label,
            "intro": overview.intro,
            "recordDayCount": overview.record_day_count,
            "metricCount": overview.metric_count,
            "sourceCount": overview.source_count,
            "sentences": [
                {"text": item.text, "sourceIds": list(item.source_ids)}
                for item in overview.sentences
            ],
            "latestStatus": (
                {
                    "text": overview.latest_status.text,
                    "sourceIds": list(overview.latest_status.source_ids),
                }
                if overview.latest_status is not None
                else None
            ),
            "missingMetrics": list(overview.missing_metrics),
        },
        "metrics": [_metric_payload(card) for card in dashboard.cards],
        "collaboration": {
            "pendingCount": len(escalations),
            "escalations": escalations,
            "boundary": "护士上报只创建医生待评估事项；系统未自动生成临床结论。",
        },
        "links": {
            "patient": os.getenv("CONTINUCARE_PATIENT_URL", "http://127.0.0.1:8510/"),
            "nurse": os.getenv("CONTINUCARE_NURSE_URL", "http://127.0.0.1:8510/nurse"),
        },
        "sources": [
            {
                "sourceId": source.source_id,
                "observationReference": source.observation_reference,
                "responseReference": source.response_reference,
                "effectiveTime": source.effective_time,
                "originalText": source.original_text,
                "metricId": source.metric_id,
                "evidenceClaimIds": list(source.evidence_claim_ids),
                "knowledgeReleaseId": source.knowledge_release_id,
            }
            for source in dashboard.sources
        ],
    }
