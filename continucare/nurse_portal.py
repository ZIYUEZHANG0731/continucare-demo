"""Server-owned state and commands for the standalone nurse web portal.

The browser renders human-readable Chinese and submits explicit human workflow
decisions.  It never receives a clinical threshold, alert score, FHIR value[x]
representation, or permission to infer a risk classification.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from continucare.adapters.sqlite_store import SQLiteStore
from continucare.care_engine import CareEngine
from continucare.db import initialize_database, utc_now_iso
from continucare.demo_data import DEMO_PATIENT_ID
from continucare.fhir.questionnaires import questionnaire_response_answers
from continucare.layer4.manual_reviews import ManualReviewQueue, communication_readiness
from continucare.layer4.storage import Layer4SQLiteStore
from continucare.nurse_ui import build_nurse_answer_cards, nurse_stage_label
from continucare.patient_mobile import canonical_db_path
from continucare.services.competition_demo import (
    CompetitionDemoConflict,
    CompetitionDemoProgress,
    demo_write_guard,
    read_competition_demo,
)
from continucare.services.manual_review_workflow import ManualReviewWorkflowService
from continucare.services.patient_checkin import questionnaire_answer_display
from continucare.services.supplemental_reports import (
    read_supplemental_reports,
    review_supplemental_report,
)
from continucare.ui import (
    NURSE_RESULT_BOUNDARY,
    NURSE_ROLE_BOUNDARY,
    NURSE_STOP_CONSEQUENCE,
    NurseTaskProjection,
    patient_recorded_meaning,
    project_nurse_workbench,
)


class NursePortalBoundaryError(ValueError):
    """Stable rejection for an invalid standalone nurse web request."""


CHECKLIST_ITEMS = (
    ("patient-confirmation", "已核对患者原话和患者确认结果"),
    ("chinese-answer-consistency", "已核对中文回答与患者原话是否一致"),
    ("time-unit-completeness", "已核对时间窗、单位、缺失和冲突"),
    ("supplemental-history", "已查看患者补充说明和可用历史原始值"),
    ("human-disposition", "已由护士本人决定是否需要患者补充或医生评估"),
)

OUTCOME_OPTIONS = (
    (
        "reviewed_no_escalation",
        "本次复核完成，未上报医生",
        "只记录护士本次未上报；不表示患者安全、低风险或已经完成临床评估。",
    ),
    (
        "clarification_required",
        "需要联系患者补充核实",
        "记录需要继续向患者核实；当前原型只生成未发送的沟通文字。",
    ),
    (
        "escalated_to_doctor",
        "上报医生评估",
        "记录护士人工上报，并通知医生查看；系统没有自动分级。",
    ),
)

_TASK_STATUS_LABELS = {
    "requested": "等待接手",
    "received": "已接手",
    "accepted": "已接受",
    "in-progress": "正在人工复核",
    "completed": "人工复核已完成",
    "rejected": "已拒绝",
    "cancelled": "已取消",
    "failed": "未完成",
    "entered-in-error": "记录错误",
}

_OUTCOME_LABELS = {value: label for value, label, _ in OUTCOME_OPTIONS}


@dataclass(frozen=True, slots=True)
class _PortalContext:
    db_path: Any
    progress: CompetitionDemoProgress
    store: SQLiteStore
    repository: Layer4SQLiteStore
    service: ManualReviewWorkflowService
    tasks: tuple[dict[str, Any], ...]
    contexts: dict[str, dict[str, Any]]
    answer_cards: tuple[Any, ...]
    pathway_label: str


def _task_output(task: dict[str, Any], code: str) -> str | None:
    values = []
    for item in task.get("output", []):
        codes = {
            coding.get("code")
            for coding in item.get("type", {}).get("coding", [])
        }
        if code in codes:
            values.append(
                item.get("valueCode")
                or item.get("valueString")
                or item.get("valueReference", {}).get("reference")
            )
    return str(values[0]) if len(values) == 1 and values[0] else None


def _patient_quote(response: dict[str, Any] | None) -> str | None:
    if not response:
        return None
    values = [
        answer.get("valueString")
        for item in response.get("item", [])
        if item.get("linkId") == "free-text-report"
        for answer in item.get("answer", [])
        if isinstance(answer.get("valueString"), str)
        and answer.get("valueString", "").strip()
    ]
    return values[0].strip() if len(values) == 1 else None


def _build_task_contexts(
    *,
    progress: CompetitionDemoProgress,
    store: SQLiteStore,
    repository: Layer4SQLiteStore,
    service: ManualReviewWorkflowService,
    tasks: tuple[dict[str, Any], ...],
    questionnaire: dict[str, Any] | None,
    pathway_label: str,
) -> dict[str, dict[str, Any]]:
    patient = store.get_patient(DEMO_PATIENT_ID)
    patient_label = patient.display_name if patient else "合成患者"
    meanings: list[str] = []
    if progress.run_id:
        run = store.get_agent_run(progress.run_id)
        if run:
            meanings = [
                patient_recorded_meaning(item)
                for item in run.output_json.get("candidates", [])
            ]
    confirmed_statement = "；".join(item for item in meanings if item).strip()
    revision_summary = ""
    if progress.session_id:
        session = store.get_care_session(progress.session_id)
        if session is not None:
            questionnaire = CareEngine(store).questionnaire_for_session(session)
            corrections = [
                item
                for item in store.list_audit_events(session.patient_id)
                if item.entity_type == "CareSession"
                and item.entity_id == session.session_id
                and item.event_type == "patient_answer_corrected"
            ]
            lines = []
            for event in reversed(corrections):
                details = event.details_json
                lines.append(
                    f"{details['link_id']}："
                    f"{questionnaire_answer_display(questionnaire, details['link_id'], details['previous_answer'])}"
                    " → "
                    f"{questionnaire_answer_display(questionnaire, details['link_id'], details['replacement_answer'])}"
                )
            if lines:
                revision_summary = "\n患者确认的更正：" + "；".join(lines)

    contexts: dict[str, dict[str, Any]] = {}
    for task in tasks:
        task_id = str(task.get("id") or "")
        response_ref = str(task.get("reasonReference", {}).get("reference") or "")
        response_id = (
            response_ref.split("/", 1)[1]
            if response_ref.startswith("QuestionnaireResponse/")
            else ""
        )
        response = store.get_questionnaire_response(response_id) if response_id else None
        answer_cards = (
            build_nurse_answer_cards(
                questionnaire,
                questionnaire_response_answers(response),
            )
            if questionnaire is not None and response is not None
            else ()
        )
        response_summary = "；".join(
            f"{card.question}：{card.answer}"
            for card in answer_cards
            if not card.wide
        )
        communications = service.list_communications_for_task(DEMO_PATIENT_ID, task_id)
        communication = communications[0] if len(communications) == 1 else None
        payload = communication.get("payload", []) if communication else []
        communication_text = (
            payload[0].get("contentString") if len(payload) == 1 else None
        )
        history = sorted(
            (
                item
                for item in repository.list_fhir_resources(
                    patient_id=DEMO_PATIENT_ID,
                    resource_type="Task",
                    current_only=False,
                )
                if item.get("id") == task_id
            ),
            key=lambda item: int(item.get("meta", {}).get("versionId", "0")),
        )
        outcome = _task_output(task, "review-outcome")
        contexts[task_id] = {
            "patient_label": patient_label,
            "original_quote": (_patient_quote(response) or "") + revision_summary,
            "confirmed_statement": response_summary or confirmed_statement,
            "answer_cards": answer_cards,
            "pathway_label": pathway_label,
            "outcome_label": _OUTCOME_LABELS.get(outcome, outcome),
            "review_note": _task_output(task, "review-note"),
            "communication_text": communication_text,
            "communication_readiness": (
                communication_readiness(communication) if communication else None
            ),
            "has_pending_brief": (
                progress.task_id == task_id
                and getattr(progress.stage, "value", str(progress.stage))
                == "doctor_brief_pending"
            ),
            "history": tuple(
                (
                    f"v{item.get('meta', {}).get('versionId', '—')}",
                    _TASK_STATUS_LABELS.get(
                        str(item.get("status") or ""),
                        str(item.get("status") or "状态未记录"),
                    ),
                    str(item.get("meta", {}).get("lastUpdated") or "时间未记录"),
                )
                for item in history
            ),
        }
    return contexts


def _trusted_context(*, expected_generation: str | None = None) -> _PortalContext:
    db_path = canonical_db_path()
    initialize_database(db_path)
    progress = read_competition_demo(db_path)
    if progress.integrity_issue:
        raise NursePortalBoundaryError("共享随访记录暂时不可安全读取")
    if expected_generation is not None and progress.generation != expected_generation:
        raise CompetitionDemoConflict("页面状态已经变化，请刷新后继续")
    if progress.alert_count or progress.approved_clinical_rule_count:
        raise NursePortalBoundaryError("当前记录超出冻结的人工复核边界")
    store = SQLiteStore(db_path, initialize=False)
    patient = store.get_patient(DEMO_PATIENT_ID)
    if patient is None or not patient.synthetic:
        raise NursePortalBoundaryError("护士网页仅允许读取合成演示患者")
    repository = Layer4SQLiteStore(db_path, initialize=False)
    service = ManualReviewWorkflowService(store, layer4_store=repository)
    tasks = tuple(ManualReviewQueue(repository).list_for_patient(DEMO_PATIENT_ID))
    questionnaire = None
    pathway_label = patient.pathway_code
    if progress.session_id:
        session = store.get_care_session(progress.session_id)
        if session is not None:
            questionnaire = CareEngine(store).questionnaire_for_session(session)
            pathway_label = f"{session.pathway_code} v{session.pathway_version}"
    contexts = _build_task_contexts(
        progress=progress,
        store=store,
        repository=repository,
        service=service,
        tasks=tasks,
        questionnaire=questionnaire,
        pathway_label=pathway_label,
    )
    answer_cards: tuple[Any, ...] = ()
    if questionnaire is not None and progress.session_id:
        session = store.get_care_session(progress.session_id)
        if session is not None:
            answer_cards = build_nurse_answer_cards(questionnaire, session.answers)
    return _PortalContext(
        db_path=db_path,
        progress=progress,
        store=store,
        repository=repository,
        service=service,
        tasks=tasks,
        contexts=contexts,
        answer_cards=answer_cards,
        pathway_label=pathway_label,
    )


def _projection(
    context: _PortalContext,
    *,
    selected_task_id: str | None = None,
):
    return project_nurse_workbench(
        context.progress,
        tasks=context.tasks,
        task_contexts=context.contexts,
        selected_task_id=selected_task_id,
    )


def _task_row(task: NurseTaskProjection) -> dict[str, Any]:
    return {
        "taskId": task.task_id,
        "patientLabel": task.patient_label,
        "submittedAt": task.submitted_at,
        "statusTitle": task.status_title,
        "tone": task.tone,
    }


def _selected_task(task: NurseTaskProjection, context: _PortalContext) -> dict[str, Any]:
    task_context = context.contexts.get(task.task_id, {})
    answer_cards = task_context.get("answer_cards", context.answer_cards)
    return {
        **_task_row(task),
        "statusDetail": task.status_detail,
        "confirmedStatement": task.confirmed_statement,
        "originalQuote": task.original_quote,
        "primaryAction": task.primary_action,
        "primaryLabel": task.primary_label,
        "secondaryActions": [
            {"value": value, "label": label}
            for value, label in task.secondary_actions
        ],
        "outcomeLabel": task.outcome_label,
        "reviewNote": task.review_note,
        "communicationText": task.communication_text,
        "communicationMarker": task.communication_marker,
        "stopReason": task.stop_reason,
        "produced": list(task.produced),
        "notProduced": list(task.not_produced),
        "history": [
            {"version": version, "status": status, "occurredAt": occurred_at}
            for version, status, occurred_at in task.history
        ],
        "answers": [
            {"question": card.question, "answer": card.answer, "wide": card.wide}
            for card in answer_cards
        ],
        "pathwayLabel": task_context.get("pathway_label", context.pathway_label),
    }


def build_nurse_portal_state(*, selected_task_id: str | None = None) -> dict[str, Any]:
    """Return one server-authoritative nurse workbench projection."""

    try:
        context = _trusted_context()
    except NursePortalBoundaryError as exc:
        return {"version": 1, "kind": "fail_closed", "message": str(exc)}
    patient = context.store.get_patient(DEMO_PATIENT_ID)
    projection = _projection(context, selected_task_id=selected_task_id)
    selected = next(
        (
            item
            for item in (*projection.pending_tasks, *projection.completed_tasks)
            if item.task_id == projection.selected_task_id
        ),
        None,
    )
    supplemental_rows: list[dict[str, Any]] = []
    supplemental_generation = None
    if context.progress.session_id:
        supplemental = read_supplemental_reports(
            context.db_path,
            session_id=context.progress.session_id,
        )
        if supplemental.integrity_issue:
            return {
                "version": 1,
                "kind": "fail_closed",
                "message": "患者补充说明暂时不可安全读取",
            }
        supplemental_generation = supplemental.generation
        supplemental_rows = [
            {
                "reportId": report.report_id,
                "reportKind": report.report_kind,
                "kindLabel": (
                    "语义待人工复核"
                    if report.report_kind == "semantic_handoff"
                    else "患者补充"
                ),
                "clinicalAssessment": "not_assessed",
                "originalText": report.original_text,
                "status": report.status,
                "reviewNote": report.review_note,
                "createdAt": report.created_at,
                "reviewedAt": report.reviewed_at,
                "meanings": [
                    str(item.get("evidence_text") or "").strip()
                    for item in report.structured_items
                    if str(item.get("evidence_text") or "").strip()
                ],
            }
            for report in supplemental.reports
        ]
    kind = "ready" if selected is not None else "empty"
    if not context.progress.plan_activated:
        kind = "waiting"
    elif context.progress.run_id is None and not supplemental_rows:
        kind = "waiting_patient"
    return {
        "version": 1,
        "kind": kind,
        "generation": context.progress.generation,
        "stageLabel": nurse_stage_label(context.progress),
        "patient": {
            "patientId": patient.patient_id,
            "displayName": patient.display_name,
            "synthetic": True,
            "pathwayCode": patient.pathway_code,
            "nextVisitDate": patient.next_visit_date,
        },
        "counts": {
            "pending": len(projection.pending_tasks),
            "completed": len(projection.completed_tasks),
        },
        "pendingTasks": [_task_row(item) for item in projection.pending_tasks],
        "completedTasks": [_task_row(item) for item in projection.completed_tasks],
        "selectedTaskId": projection.selected_task_id,
        "selectedTask": _selected_task(selected, context) if selected else None,
        "checklist": [
            {"id": item_id, "label": label} for item_id, label in CHECKLIST_ITEMS
        ],
        "outcomeOptions": [
            {"value": value, "label": label, "help": help_text}
            for value, label, help_text in OUTCOME_OPTIONS
        ],
        "supplementalGeneration": supplemental_generation,
        "supplementalReports": supplemental_rows,
        "links": {
            "patient": os.getenv("CONTINUCARE_PATIENT_URL", "http://127.0.0.1:8510/"),
            "doctor": os.getenv(
                "CONTINUCARE_DOCTOR_URL",
                "http://127.0.0.1:8520/?view=collaboration",
            ),
        },
        "boundaries": {
            "role": NURSE_ROLE_BOUNDARY,
            "result": NURSE_RESULT_BOUNDARY,
            "stop": NURSE_STOP_CONSEQUENCE,
        },
    }


def _required_text(payload: dict[str, Any], key: str, *, max_length: int) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise NursePortalBoundaryError(f"{key} 无效")
    return value.strip()


def _command_task(
    payload: dict[str, Any],
    *,
    expected_action: str,
) -> tuple[_PortalContext, NurseTaskProjection, str]:
    generation = _required_text(payload, "generation", max_length=256)
    task_id = _required_text(payload, "taskId", max_length=256)
    context = _trusted_context(expected_generation=generation)
    projection = _projection(context, selected_task_id=task_id)
    task = next(
        (
            item
            for item in (*projection.pending_tasks, *projection.completed_tasks)
            if item.task_id == task_id
        ),
        None,
    )
    if task is None:
        raise CompetitionDemoConflict("人工复核任务已经变化，请刷新后继续")
    allowed = task.primary_action == expected_action or expected_action in {
        value for value, _ in task.secondary_actions
    }
    if not allowed:
        raise CompetitionDemoConflict("当前任务动作已经变化，请刷新后继续")
    return context, task, generation


def acknowledge_nurse_task_command(payload: dict[str, Any]) -> None:
    context, task, generation = _command_task(payload, expected_action="acknowledge")
    with demo_write_guard(context.db_path, expected_generation=generation):
        context.service.acknowledge(
            patient_id=DEMO_PATIENT_ID,
            task_id=task.task_id,
            note="护士已在独立网页接手人工安全复核任务。",
            occurred_at=utc_now_iso(),
        )


def start_nurse_task_command(payload: dict[str, Any]) -> None:
    context, task, generation = _command_task(payload, expected_action="start")
    with demo_write_guard(context.db_path, expected_generation=generation):
        context.service.start(
            patient_id=DEMO_PATIENT_ID,
            task_id=task.task_id,
            note="护士已在独立网页开始人工安全复核。",
            occurred_at=utc_now_iso(),
        )


def record_nurse_outcome_command(payload: dict[str, Any]) -> None:
    context, task, generation = _command_task(payload, expected_action="record_outcome")
    note = _required_text(payload, "note", max_length=2_000)
    outcome = _required_text(payload, "outcome", max_length=64)
    if outcome not in _OUTCOME_LABELS:
        raise NursePortalBoundaryError("人工处理结果无效")
    checklist = payload.get("checklist")
    expected = {item_id for item_id, _ in CHECKLIST_ITEMS}
    if not isinstance(checklist, list) or set(checklist) != expected or len(checklist) != len(expected):
        raise NursePortalBoundaryError("请先逐项完成人工安全复核清单")
    with demo_write_guard(context.db_path, expected_generation=generation):
        context.service.record_outcome(
            patient_id=DEMO_PATIENT_ID,
            task_id=task.task_id,
            outcome=outcome,
            note=note,
            occurred_at=utc_now_iso(),
        )


def approve_nurse_draft_command(payload: dict[str, Any]) -> None:
    context, task, generation = _command_task(payload, expected_action="approve_draft")
    communications = context.service.list_communications_for_task(
        DEMO_PATIENT_ID,
        task.task_id,
    )
    if len(communications) != 1:
        raise CompetitionDemoConflict("沟通文字状态已经变化，请刷新后继续")
    with demo_write_guard(context.db_path, expected_generation=generation):
        context.service.approve_draft(
            patient_id=DEMO_PATIENT_ID,
            task_id=task.task_id,
            communication_id=communications[0]["id"],
            note="护士已在独立网页逐字核对沟通文字。",
            occurred_at=utc_now_iso(),
        )


def close_nurse_task_command(payload: dict[str, Any], *, action: str) -> None:
    if action not in {"reject", "cancel"}:
        raise NursePortalBoundaryError("停止动作无效")
    context, task, generation = _command_task(payload, expected_action=action)
    note = _required_text(payload, "note", max_length=2_000)
    handler = context.service.reject if action == "reject" else context.service.cancel
    with demo_write_guard(context.db_path, expected_generation=generation):
        handler(
            patient_id=DEMO_PATIENT_ID,
            task_id=task.task_id,
            note=note,
            occurred_at=utc_now_iso(),
        )


def review_nurse_supplemental_command(payload: dict[str, Any]) -> None:
    generation = _required_text(payload, "generation", max_length=256)
    supplemental_generation = _required_text(
        payload,
        "supplementalGeneration",
        max_length=256,
    )
    report_id = _required_text(payload, "reportId", max_length=256)
    note = _required_text(payload, "note", max_length=2_000)
    context = _trusted_context(expected_generation=generation)
    if not context.progress.session_id:
        raise NursePortalBoundaryError("当前没有可复核的患者补充说明")
    current = read_supplemental_reports(
        context.db_path,
        session_id=context.progress.session_id,
    )
    if current.integrity_issue or current.generation != supplemental_generation:
        raise CompetitionDemoConflict("患者补充说明已经变化，请刷新后继续")
    target = next((item for item in current.reports if item.report_id == report_id), None)
    if target is None or target.status != "requested":
        raise CompetitionDemoConflict("患者补充说明已经处理，请刷新后继续")
    review_supplemental_report(
        context.db_path,
        session_id=context.progress.session_id,
        report_id=report_id,
        expected_story_generation=generation,
        expected_supplemental_generation=supplemental_generation,
        note=note,
    )
