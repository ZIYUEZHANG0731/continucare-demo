"""Server-owned view model and commands for the synthetic patient mobile UI.

The browser never decides Pathway scope, terminology matches, completion, or
FHIR writes.  It renders this discriminated projection and posts only explicit
patient choices together with the projection generation it observed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from continucare.adapters.sqlite_store import SQLiteStore
from continucare.agents.contracts import SemanticCandidate, SemanticResult
from continucare.care_agent import CareAgentService
from continucare.care_agent.model_api import SemanticModelConfig, UnconfiguredModelAdapter
from continucare.care_engine import CareEngine
from continucare.config import get_settings
from continucare.db import initialize_database, utc_now_iso
from continucare.demo_data import DEMO_PATIENT_ID
from continucare.fhir.questionnaires import flatten_questionnaire_items
from continucare.models import CareSessionStatus
from continucare.record_points import group_answer_rows, validate_questionnaire_contract
from continucare.services.competition_demo import (
    CompetitionDemoConflict,
    CompetitionDemoProgress,
    CompetitionDemoStartError,
    competition_mimo_configured,
    demo_write_guard,
    read_competition_demo,
    submit_patient_chat_turn,
)
from continucare.services.confirmed_review import ConfirmedReviewService
from continucare.services.audit import build_audit_event
from continucare.services.patient_checkin import (
    OPENING_PROMPT,
    UNKNOWN_ALLOWED_LINK_IDS,
    project_patient_checkin,
    questionnaire_answer_display,
    questionnaire_candidate_confirmation_display,
    questionnaire_choice_options,
    record_explicit_unknown,
)
from continucare.services.plan_collection import (
    active_patient_link_ids,
    patient_collection_projection,
)
from continucare.services.supplemental_reports import (
    read_supplemental_reports,
    resolve_supplemental_turn,
    submit_supplemental_report_turn,
)
from continucare.terminology import load_supplemental_terminology_backend


class PatientMobileBoundaryError(ValueError):
    """Stable, non-sensitive rejection for the local patient API."""


def _filter_collection_to_questionnaire(
    collection: dict[str, Any] | None,
    planned_link_ids: tuple[str, ...],
) -> dict[str, Any] | None:
    """Keep the patient plan projection inside the locked Questionnaire."""

    if collection is None:
        return None
    planned = set(planned_link_ids)
    filtered_record_points = [
        item
        for item in collection.get("recordPoints", [])
        if any(link_id in planned for link_id in item.get("linkIds", []))
    ]
    return {
        **collection,
        "linkIds": planned_link_ids,
        "patientQuestionMetricIds": tuple(
            metric_id
            for item in filtered_record_points
            for metric_id in item.get("metricIds", [])
        ),
        "recordPoints": filtered_record_points,
    }


MOBILE_FIELD_LABELS = {
    "body-weight": "体重",
    "nausea-present": "恶心",
    "nausea-severity": "恶心程度",
    "vomiting-count-24h": "呕吐次数",
    "fluid-intake-24h-estimated": "饮水量",
    "abdominal-pain-present": "腹痛",
}


@dataclass(frozen=True)
class _Context:
    db_path: Path
    progress: CompetitionDemoProgress
    store: SQLiteStore
    session: Any
    engine: CareEngine
    questionnaire: dict[str, Any]
    checkin: Any
    collection: dict[str, Any] | None


def canonical_db_path() -> Path:
    """Resolve the same configured database used by the Streamlit role pages."""

    configured = get_settings().db_path
    if not configured.is_absolute():
        configured = Path(__file__).resolve().parents[1] / configured
    return configured.resolve()


def _trusted_context(*, expected_generation: str | None = None) -> _Context:
    db_path = canonical_db_path()
    initialize_database(db_path)
    progress = read_competition_demo(db_path)
    if progress.integrity_issue:
        raise PatientMobileBoundaryError("共享随访记录暂时不可安全读取")
    if progress.alert_count or progress.approved_clinical_rule_count:
        raise PatientMobileBoundaryError("当前记录超出冻结的患者端医疗边界")
    if expected_generation is not None and progress.generation != expected_generation:
        raise CompetitionDemoConflict("页面状态已经变化，请刷新后继续")
    store = SQLiteStore(db_path, initialize=False)
    patient = store.get_patient(DEMO_PATIENT_ID)
    if patient is None or not patient.synthetic:
        raise PatientMobileBoundaryError("患者端仅允许读取合成演示患者")
    if not progress.plan_activated or not progress.session_id:
        raise PatientMobileBoundaryError("医生尚未启动今天的随访")
    session = store.get_care_session(progress.session_id)
    if session is None or session.patient_id != DEMO_PATIENT_ID:
        raise PatientMobileBoundaryError("今天的随访会话不可读取")
    engine = CareEngine(store)
    questionnaire = engine.questionnaire_for_session(session)
    validate_questionnaire_contract(questionnaire)
    collection = patient_collection_projection(
        db_path,
        patient_id=patient.patient_id,
        pathway_code=session.pathway_code,
    )
    planned_link_ids = active_patient_link_ids(
        db_path,
        patient_id=patient.patient_id,
        pathway_code=session.pathway_code,
        questionnaire=questionnaire,
    )
    collection = _filter_collection_to_questionnaire(collection, planned_link_ids)
    collection_link_ids = planned_link_ids
    checkin = project_patient_checkin(
        session,
        questionnaire,
        explicit_unknown_link_ids={
            link_id
            for link_id, resolution in progress.collection_resolutions.items()
            if resolution == "explicit_unknown"
        },
        collection_link_ids=collection_link_ids,
    )
    return _Context(
        db_path=db_path,
        progress=progress,
        store=store,
        session=session,
        engine=engine,
        questionnaire=questionnaire,
        checkin=checkin,
        collection=collection,
    )


def _candidate_label(candidate: SemanticCandidate, questionnaire: dict[str, Any]) -> dict[str, Any]:
    question, proposed = questionnaire_candidate_confirmation_display(
        questionnaire, candidate.link_id, candidate.answer
    )
    match = candidate.terminology_match
    questionnaire_link_ids = {
        item["linkId"]
        for item in flatten_questionnaire_items(questionnaire.get("item", []))
    }
    if match is not None and candidate.link_id not in questionnaire_link_ids:
        question = f"症状：{match.preferred_zh}"
        proposed = "确认上报"
    return {
        "candidateId": candidate.candidate_id,
        "linkId": candidate.link_id,
        "question": question,
        "proposed": proposed,
        "evidence": candidate.evidence_text,
        "terminology": (
            {
                "preferredZh": match.preferred_zh,
                "catalogStatus": match.source_catalog_status,
            }
            if match is not None
            else None
        ),
    }


def _history(context: _Context) -> list[dict[str, Any]]:
    provisional = context.session.status == CareSessionStatus.IN_PROGRESS
    source_contexts = (
        context.store.list_active_provisional_answer_contexts(
            context.session.session_id
        )
        if provisional
        else context.store.list_active_answer_contexts(context.session.session_id)
    )
    active = {
        (item.source_run_id, item.link_id)
        for item in source_contexts
    }
    source_reports = (
        context.store.list_active_provisional_symptom_reports(
            context.session.session_id
        )
        if provisional
        else context.store.list_active_symptom_reports(context.session.session_id)
    )
    active_reports = {
        (item.source_run_id, item.concept_id): item for item in source_reports
    }
    supplemental = read_supplemental_reports(
        context.db_path, session_id=context.session.session_id
    )
    if supplemental.integrity_issue:
        raise PatientMobileBoundaryError("患者原话人工复核状态暂时不可安全读取")
    semantic_handoffs = {
        item.source_run_id: item
        for item in supplemental.reports
        if item.report_kind == "semantic_handoff"
    }
    rows: list[dict[str, Any]] = [
        {"kind": "message", "role": "assistant", "text": OPENING_PROMPT}
    ]
    for record in reversed(context.store.list_agent_runs(context.session.session_id)):
        rows.append(
            {"kind": "message", "role": "user", "text": record.input_text}
        )
        if record.run_id in semantic_handoffs:
            rows.append(
                {
                    "kind": "message",
                    "role": "assistant",
                    "text": (
                        "我没有足够把握把这句话写成结构化指标。"
                        "原话已保留并进入护士人工复核；"
                        "当前未生成问卷答案、Observation 或风险结论。"
                    ),
                }
            )
            continue
        result = SemanticResult.model_validate(record.output_json)
        current = [
            item
            for item in result.candidates
            if (record.run_id, item.link_id) in active
            or (
                item.terminology_match is not None
                and (
                    record.run_id,
                    item.terminology_match.concept_id,
                )
                in active_reports
            )
        ]
        if current:
            confirmed_items = []
            for item in current:
                question, proposed = questionnaire_candidate_confirmation_display(
                    context.questionnaire, item.link_id, item.answer
                )
                if item.terminology_match is not None and (
                    record.run_id,
                    item.terminology_match.concept_id,
                ) in active_reports:
                    label = f"其他症状 · {item.terminology_match.preferred_zh}"
                    proposed = "已纳入整份草稿"
                else:
                    label = MOBILE_FIELD_LABELS.get(
                        item.link_id, question.rstrip("？?。")
                    )
                confirmed_items.append(
                    {
                        "linkId": item.link_id,
                        "label": label,
                        "value": proposed,
                        "evidence": item.evidence_text,
                    }
                )
            if confirmed_items:
                rows.append(
                    {
                        "kind": "draft_record" if provisional else "confirmed_record",
                        "role": "assistant",
                        "text": "待最终确认草稿" if provisional else "已确认记录",
                        "items": confirmed_items,
                    }
                )
    return rows


def _base_state(context: _Context) -> dict[str, Any]:
    patient = context.store.get_patient(DEMO_PATIENT_ID)
    return {
        "version": 1,
        "generation": context.progress.generation,
        "patient": {
            "displayName": patient.display_name,
            "synthetic": True,
            "pathwayCode": patient.pathway_code,
            "nextVisitDate": patient.next_visit_date,
        },
        "links": {
            "nurse": os.getenv("CONTINUCARE_NURSE_URL", "http://127.0.0.1:8510/nurse"),
            "doctor": os.getenv("CONTINUCARE_DOCTOR_URL", "http://127.0.0.1:8520/"),
        },
        "mimoReady": competition_mimo_configured(),
        "activePlan": context.collection,
        "consent": {
            "label": "我确认本次只输入合成演示内容",
            "detail": (
                "发送后，原话会保存在本地演示库，并与完成语义整理所需的最小问卷上下文"
                "传给火山方舟豆包；最后统一确认才决定是否写入问卷、补充记录或 Observation。"
                "若无法形成可安全确认的候选，原话会作为未评估内容进入护士人工复核；"
                "不会生成 Observation、问卷答案或风险结论。"
            ),
        },
        "emergencyNotice": (
            "这里不是急救通道。如情况紧急，请立即联系当地急救服务或前往急诊。"
        ),
        "history": _history(context),
    }


def _pending_result(context: _Context) -> tuple[Any | None, SemanticResult | None]:
    record = (
        context.store.get_agent_run(context.progress.run_id)
        if context.progress.run_id
        else None
    )
    return (
        record,
        SemanticResult.model_validate(record.output_json) if record is not None else None,
    )


def build_patient_mobile_state() -> dict[str, Any]:
    """Return one server-authoritative, discriminated patient UI projection."""

    db_path = canonical_db_path()
    initialize_database(db_path)
    progress = read_competition_demo(db_path)
    if progress.integrity_issue:
        return {"version": 1, "kind": "fail_closed", "message": "共享随访记录暂时不可安全读取"}
    store = SQLiteStore(db_path, initialize=False)
    patient = store.get_patient(DEMO_PATIENT_ID)
    if patient is None or not patient.synthetic:
        return {"version": 1, "kind": "fail_closed", "message": "患者端仅允许读取合成演示患者"}
    if progress.alert_count or progress.approved_clinical_rule_count:
        return {"version": 1, "kind": "fail_closed", "message": "当前记录超出冻结的患者端医疗边界"}
    if not progress.plan_activated or not progress.session_id:
        return {
            "version": 1,
            "kind": "waiting_doctor",
            "generation": progress.generation,
            "patient": {
                "displayName": patient.display_name,
                "synthetic": True,
                "pathwayCode": patient.pathway_code,
                "nextVisitDate": patient.next_visit_date,
            },
            "links": {
                "nurse": os.getenv("CONTINUCARE_NURSE_URL", "http://127.0.0.1:8510/nurse"),
                "doctor": os.getenv("CONTINUCARE_DOCTOR_URL", "http://127.0.0.1:8520/"),
            },
            "allowedActions": [],
            "message": "医生尚未启动今天的随访，请先在医生端确认随访方案。",
        }
    try:
        context = _trusted_context()
    except (PatientMobileBoundaryError, ValueError):
        return {"version": 1, "kind": "fail_closed", "message": "患者随访状态不可安全读取"}
    if context.session.status == CareSessionStatus.IN_PROGRESS:
        confirmed = context.store.list_active_answer_contexts(
            context.session.session_id
        )
        provisional = context.store.list_active_provisional_answer_contexts(
            context.session.session_id
        )
        if confirmed or (
            context.store.list_active_symptom_reports(context.session.session_id)
            and not provisional
        ):
            return {
                "version": 1,
                "kind": "fail_closed",
                "message": (
                    "当前故事使用旧版逐轮确认语义，不能静默改成统一最终确认；"
                    "请在演示总台明确重新开始今天的合成随访。"
                ),
            }
    state = _base_state(context)
    if (
        context.session.status == CareSessionStatus.IN_PROGRESS
        and context.collection is not None
        and not context.collection.get("linkIds")
    ):
        state.update(
            {
                "kind": "no_web_tasks",
                "allowedActions": [],
                "message": (
                    "医生已确认本轮随访方案，目前没有需要你在患者端"
                    "填写的记录要点。"
                ),
            }
        )
        return state
    record, result = _pending_result(context)
    pending = (
        [
            item
            for item in result.candidates
            if context.progress.candidate_decisions.get(item.candidate_id) is None
        ]
        if result is not None
        else []
    )
    if pending and record is not None:
        by_link = {item.link_id: item for item in pending}
        nausea = by_link.get("nausea-present")
        severity = by_link.get("nausea-severity")
        grouped = bool(
            nausea is not None
            and nausea.answer is True
            and severity is not None
            and "nausea-present" not in context.session.answers
            and "nausea-severity" not in context.session.answers
        )
        state.update(
            {
                "kind": "candidate_review",
                "allowedActions": ["resolve_candidates"],
                "runId": record.run_id,
                "candidates": [
                    _candidate_label(item, context.questionnaire) for item in pending
                ],
                "groupedNausea": grouped,
                "severityOptions": [
                    {"value": value, "label": label}
                    for value, label in questionnaire_choice_options(
                        context.questionnaire, "nausea-severity"
                    )
                ]
                if grouped
                else [],
            }
        )
        return state
    pending_clarifications = (
        [
            item
            for item in result.clarifications
            if context.progress.candidate_decisions.get(item.clarification_id) is None
        ]
        if result is not None
        else []
    )
    if pending_clarifications and record is not None:
        clarification = pending_clarifications[0]
        state.update(
            {
                "kind": "clarification",
                "allowedActions": ["resolve_clarification"],
                "runId": record.run_id,
                "clarification": {
                    "clarificationId": clarification.clarification_id,
                    "prompt": clarification.prompt,
                    "options": [
                        {"value": item.option_id, "label": item.label}
                        for item in clarification.options
                    ],
                },
            }
        )
        return state
    if context.session.status == CareSessionStatus.IN_PROGRESS and context.checkin.ready_to_submit:
        items = {
            item["linkId"]: item
            for item in flatten_questionnaire_items(context.questionnaire.get("item", []))
        }
        answers = [
            {
                "linkId": link_id,
                "label": MOBILE_FIELD_LABELS.get(
                    link_id, items[link_id].get("text", link_id)
                ),
                "value": questionnaire_answer_display(
                    context.questionnaire, link_id, context.session.answers.get(link_id)
                ),
            }
            for link_id in context.checkin.answered_link_ids
        ]
        answers.extend(
            {
                "linkId": link_id,
                "label": MOBILE_FIELD_LABELS.get(
                    link_id, items[link_id].get("text", link_id)
                ),
                "value": "暂时无法估算",
            }
            for link_id in context.checkin.explicit_unknown_link_ids
        )
        state.update(
            {
                "kind": "final_review",
                "allowedActions": [
                    "finalize",
                    "chat_revision",
                    "remove_additional_report",
                    "add_additional_report",
                ],
                "answers": answers,
                "answerGroups": group_answer_rows(answers),
                "revisionOptions": [
                    {"linkId": item["linkId"], "label": item["label"]} for item in answers
                ],
                "originalText": context.session.answers.get("free-text-report", ""),
                "additionalReports": [
                    {
                        "reportId": item.draft_report_id,
                        "label": f"其他症状 · {item.preferred_zh}",
                        "value": "将随整份资料进入护士复核",
                        "evidence": item.evidence_text,
                    }
                    for item in context.store.list_active_provisional_symptom_reports(
                        context.session.session_id
                    )
                ],
            }
        )
        return state
    if context.session.status == CareSessionStatus.COMPLETED:
        supplemental = read_supplemental_reports(
            context.db_path, session_id=context.session.session_id
        )
        if supplemental.integrity_issue:
            state.update({"kind": "fail_closed", "message": "补充上报记录暂时不可安全读取", "allowedActions": []})
            return state
        report_rows = [
            {
                "originalText": item.original_text,
                "status": item.status,
                "structuredItems": [
                    _candidate_label(
                        SemanticCandidate.model_validate(raw), context.questionnaire
                    )
                    for raw in item.structured_items
                ],
                "observationCount": len(item.observation_ids),
            }
            for item in supplemental.reports
            if item.report_kind == "patient_supplemental"
        ]
        questionnaire_items = {
            item["linkId"]: item
            for item in flatten_questionnaire_items(
                context.questionnaire.get("item", [])
            )
        }
        completed_answers = [
            {
                "linkId": link_id,
                "label": MOBILE_FIELD_LABELS.get(
                    link_id,
                    str(
                        questionnaire_items.get(link_id, {}).get("text")
                        or link_id
                    ).rstrip("？?。"),
                ),
                "value": questionnaire_answer_display(
                    context.questionnaire, link_id, value
                ),
            }
            for link_id, value in context.session.answers.items()
            if link_id != "free-text-report"
        ]
        state.update(
            {
                "supplementalGeneration": supplemental.generation,
                "reports": report_rows,
                "answers": completed_answers,
                "answerGroups": group_answer_rows(completed_answers),
            }
        )
        if supplemental.pending_run_id:
            state.update(
                {
                    "kind": "supplemental_review",
                    "allowedActions": ["resolve_supplemental"],
                    "pendingSupplemental": {
                        "originalText": supplemental.pending_text,
                        "items": [
                            _candidate_label(
                                SemanticCandidate.model_validate(raw),
                                context.questionnaire,
                            )
                            for raw in supplemental.pending_items
                        ],
                        "clarifications": [
                            {
                                "clarificationId": raw["clarification_id"],
                                "prompt": raw["prompt"],
                                "options": [
                                    {
                                        "value": option["option_id"],
                                        "label": option["label"],
                                    }
                                    for option in raw.get("options", [])
                                ],
                            }
                            for raw in supplemental.pending_clarifications
                        ],
                        "unmatched": not supplemental.pending_items
                        and not supplemental.pending_clarifications,
                    },
                }
            )
        else:
            state.update(
                {
                    "kind": "completed",
                    "allowedActions": ["chat_supplemental"],
                    "receipt": {
                        "answerCount": len(context.checkin.answered_link_ids),
                        "recordPointCount": len(
                            group_answer_rows(completed_answers)
                        ),
                        "originalCount": 1 if context.session.answers.get("free-text-report") else 0,
                        "supplementalCount": len(report_rows),
                    },
                }
            )
        return state
    state.update(
        {
            "kind": "collecting",
            "allowedActions": [
                "chat",
                *( ["explicit_unknown"] if context.checkin.next_link_id in UNKNOWN_ALLOWED_LINK_IDS else [] ),
            ],
            "nextQuestion": context.checkin.next_prompt or OPENING_PROMPT,
            "nextLinkId": context.checkin.next_link_id,
            "quickReplies": [
                {"value": value, "label": label}
                for value, label in questionnaire_choice_options(
                    context.questionnaire, context.checkin.next_link_id or ""
                )
            ],
        }
    )
    return state


def submit_chat_command(payload: dict[str, Any]) -> None:
    expected = _required_text(payload, "generation", max_length=256)
    message = _required_text(payload, "message", max_length=500)
    consent = payload.get("syntheticConfirmed") is True
    context = _trusted_context(expected_generation=expected)
    if context.session.status == CareSessionStatus.COMPLETED:
        supplemental_generation = _required_text(
            payload, "supplementalGeneration", max_length=256
        )
        current = read_supplemental_reports(
            context.db_path, session_id=context.session.session_id
        )
        if current.integrity_issue or current.generation != supplemental_generation:
            raise CompetitionDemoConflict("补充上报状态已经变化，请刷新后继续")
        if current.pending_run_id:
            raise CompetitionDemoConflict("请先处理当前待确认的补充上报")
        submit_supplemental_report_turn(
            context.db_path,
            session_id=context.session.session_id,
            expected_story_generation=expected,
            expected_supplemental_generation=supplemental_generation,
            message_text=message,
            synthetic_confirmed=consent,
        )
        return
    if context.session.status != CareSessionStatus.IN_PROGRESS:
        raise PatientMobileBoundaryError("当前状态不能继续对话")
    selected_revision = payload.get("selectedRevisionLinkId")
    if selected_revision is not None:
        if not isinstance(selected_revision, str):
            raise PatientMobileBoundaryError("修改目标无效")
        allowed = {
            *context.checkin.answered_link_ids,
            *context.checkin.explicit_unknown_link_ids,
        }
        if selected_revision not in allowed or not context.checkin.ready_to_submit:
            raise PatientMobileBoundaryError("当前状态不能修改该指标")
    target = (
        context.checkin.next_link_id if selected_revision is None else None
    )
    submit_patient_chat_turn(
        context.db_path,
        expected_generation=expected,
        message_text=message,
        synthetic_confirmed=consent,
        target_link_id=target,
        selected_revision_link_id=selected_revision,
        auto_stage_draft=True,
    )


def resolve_candidates_command(payload: dict[str, Any]) -> None:
    expected = _required_text(payload, "generation", max_length=256)
    decision = _required_text(payload, "decision", max_length=16)
    if decision not in {"accepted", "rejected"}:
        raise PatientMobileBoundaryError("候选处理方式无效")
    context = _trusted_context(expected_generation=expected)
    record, result = _pending_result(context)
    pending = [
        item
        for item in (result.candidates if result else [])
        if context.progress.candidate_decisions.get(item.candidate_id) is None
    ]
    if record is None or not pending:
        raise CompetitionDemoConflict("待确认内容已经变化，请刷新后继续")
    overrides: dict[str, Any] = {}
    by_link = {item.link_id: item for item in pending}
    grouped = bool(
        by_link.get("nausea-present") is not None
        and by_link["nausea-present"].answer is True
        and by_link.get("nausea-severity") is not None
        and "nausea-present" not in context.session.answers
        and "nausea-severity" not in context.session.answers
    )
    if decision == "accepted" and grouped:
        selected = _required_text(payload, "nauseaSeverity", max_length=64)
        allowed = dict(
            questionnaire_choice_options(context.questionnaire, "nausea-severity")
        )
        if selected not in allowed:
            raise PatientMobileBoundaryError("恶心程度不属于当前锁定问卷选项")
        overrides["nausea-severity"] = selected
    elif payload.get("nauseaSeverity") is not None:
        raise PatientMobileBoundaryError("当前候选不接受程度选择")
    service = CareAgentService(
        context.store,
        care_engine=context.engine,
        model_adapter=UnconfiguredModelAdapter(SemanticModelConfig()),
        patient_timezone=get_settings().patient_timezone,
        terminology_backend=(
            load_supplemental_terminology_backend()
            if record.task_id.startswith("patient-checkin:")
            else None
        ),
    )
    with demo_write_guard(context.db_path, expected_generation=expected):
        if decision == "accepted":
            include_original = "free-text-report" not in context.session.answers
            service.confirm_candidates(
                record.run_id,
                [item.candidate_id for item in pending],
                include_original_text=include_original,
                track_original_text_context=include_original,
                answer_overrides=overrides,
            )
        else:
            service.reject_candidates(
                record.run_id, [item.candidate_id for item in pending]
            )


def resolve_clarification_command(payload: dict[str, Any]) -> None:
    expected = _required_text(payload, "generation", max_length=256)
    option_id = _required_text(payload, "optionId", max_length=128)
    context = _trusted_context(expected_generation=expected)
    record, result = _pending_result(context)
    pending = [
        item
        for item in (result.clarifications if result else [])
        if context.progress.candidate_decisions.get(item.clarification_id) is None
    ]
    if record is None or len(pending) != 1:
        raise CompetitionDemoConflict("澄清问题已经变化，请刷新后继续")
    clarification = pending[0]
    if option_id not in {item.option_id for item in clarification.options}:
        raise PatientMobileBoundaryError("澄清选项无效")
    service = CareAgentService(
        context.store,
        care_engine=context.engine,
        model_adapter=UnconfiguredModelAdapter(SemanticModelConfig()),
        patient_timezone=get_settings().patient_timezone,
        terminology_backend=(
            load_supplemental_terminology_backend()
            if record.task_id.startswith("patient-checkin:")
            else None
        ),
    )
    include_original = "free-text-report" not in context.session.answers
    with demo_write_guard(context.db_path, expected_generation=expected):
        service.resolve_clarification_for_final_review(
            record.run_id,
            clarification.clarification_id,
            option_id,
            include_original_text=include_original,
            track_original_text_context=include_original,
        )


def explicit_unknown_command(payload: dict[str, Any]) -> None:
    expected = _required_text(payload, "generation", max_length=256)
    context = _trusted_context(expected_generation=expected)
    if (
        context.session.status != CareSessionStatus.IN_PROGRESS
        or context.checkin.next_link_id not in UNKNOWN_ALLOWED_LINK_IDS
    ):
        raise PatientMobileBoundaryError("当前指标不能记录为暂时无法估算")
    with demo_write_guard(context.db_path, expected_generation=expected):
        record_explicit_unknown(
            context.store, context.session, context.checkin.next_link_id
        )


def finalize_command(payload: dict[str, Any]) -> None:
    expected = _required_text(payload, "generation", max_length=256)
    context = _trusted_context(expected_generation=expected)
    if context.session.status != CareSessionStatus.IN_PROGRESS or not context.checkin.ready_to_submit:
        raise PatientMobileBoundaryError("今天需要采集的指标尚未全部确认")
    service = CareAgentService(
        context.store,
        care_engine=context.engine,
        model_adapter=UnconfiguredModelAdapter(SemanticModelConfig()),
        patient_timezone=get_settings().patient_timezone,
        terminology_backend=load_supplemental_terminology_backend(),
    )
    review = ConfirmedReviewService(
        context.store, care_agent=service, care_engine=context.engine
    )
    with demo_write_guard(context.db_path, expected_generation=expected):
        review.submit_confirmed_draft(
            context.session.session_id,
            require_single_final_confirmation=True,
        )


def remove_additional_report_command(payload: dict[str, Any]) -> None:
    expected = _required_text(payload, "generation", max_length=256)
    draft_report_id = _required_text(payload, "reportId", max_length=256)
    context = _trusted_context(expected_generation=expected)
    if (
        context.session.status != CareSessionStatus.IN_PROGRESS
        or not context.checkin.ready_to_submit
    ):
        raise PatientMobileBoundaryError("当前状态不能修改其他症状")
    reports = context.store.list_active_provisional_symptom_reports(
        context.session.session_id
    )
    report = next(
        (item for item in reports if item.draft_report_id == draft_report_id), None
    )
    if report is None:
        raise CompetitionDemoConflict("其他症状草稿已经变化，请刷新")
    removed_at = utc_now_iso()
    audit = build_audit_event(
        patient_id=context.session.patient_id,
        entity_type="CareSession",
        entity_id=context.session.session_id,
        event_type="patient_draft_symptom_removed",
        actor_type="synthetic_patient",
        created_at=removed_at,
        details={
            "session_id": context.session.session_id,
            "draft_report_id": report.draft_report_id,
            "source_run_id": report.source_run_id,
            "concept_id": report.concept_id,
            "confirmation_status": "removed_before_final_confirmation",
            "clinical_assessment": "not_assessed",
        },
    )
    with demo_write_guard(context.db_path, expected_generation=expected):
        context.store.remove_provisional_symptom_report(
            expected_session=context.session,
            draft_report_id=report.draft_report_id,
            removed_at=removed_at,
            audit_event=audit,
        )


def resolve_supplemental_command(payload: dict[str, Any]) -> None:
    expected = _required_text(payload, "generation", max_length=256)
    supplemental_generation = _required_text(
        payload, "supplementalGeneration", max_length=256
    )
    decision = _required_text(payload, "decision", max_length=16)
    if decision not in {"accepted", "rejected"}:
        raise PatientMobileBoundaryError("补充上报处理方式无效")
    context = _trusted_context(expected_generation=expected)
    if context.session.status != CareSessionStatus.COMPLETED:
        raise PatientMobileBoundaryError("当前状态不能处理补充上报")
    current = read_supplemental_reports(
        context.db_path, session_id=context.session.session_id
    )
    if (
        current.integrity_issue
        or current.generation != supplemental_generation
        or current.pending_run_id is None
    ):
        raise CompetitionDemoConflict("补充上报状态已经变化，请刷新后继续")
    supplied = payload.get("clarificationOptions", {})
    if not isinstance(supplied, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in supplied.items()
    ):
        raise PatientMobileBoundaryError("补充上报澄清选项无效")
    expected_options: dict[str, set[str]] = {
        raw["clarification_id"]: {
            item["option_id"] for item in raw.get("options", [])
        }
        for raw in current.pending_clarifications
    }
    if decision == "accepted":
        if set(supplied) != set(expected_options) or any(
            supplied[key] not in options for key, options in expected_options.items()
        ):
            raise PatientMobileBoundaryError("请先完成全部补充上报澄清选择")
    elif supplied:
        raise PatientMobileBoundaryError("放弃补充上报时不能提交澄清选项")
    resolve_supplemental_turn(
        context.db_path,
        session_id=context.session.session_id,
        run_id=current.pending_run_id,
        decision=decision,
        expected_story_generation=expected,
        expected_supplemental_generation=supplemental_generation,
        clarification_options=supplied,
    )


def _required_text(payload: dict[str, Any], key: str, *, max_length: int) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise PatientMobileBoundaryError(f"{key} 无效")
    return value.strip()
