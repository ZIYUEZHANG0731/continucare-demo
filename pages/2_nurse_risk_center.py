"""A++ nurse workbench for patient-confirmed routine record checks."""

from __future__ import annotations

import html
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT_TEXT = str(PROJECT_ROOT)
sys.path[:] = [item for item in sys.path if item != PROJECT_ROOT_TEXT]
sys.path.insert(0, PROJECT_ROOT_TEXT)

import continucare
import streamlit as st

if Path(continucare.__file__).resolve().parent.parent != PROJECT_ROOT:
    raise RuntimeError("Streamlit imported continucare from outside this project")

from continucare.adapters.sqlite_store import SQLiteStore
from continucare.care_engine import CareEngine
from continucare.config import get_settings
from continucare.db import initialize_database, utc_now_iso
from continucare.demo_data import DEMO_PATIENT_ID
from continucare.presentation import (
    build_l5_governance_for_patient,
)
from continucare.product_mvp import ProductRole, build_product_context
from continucare.nurse_ui import (
    build_nurse_answer_cards,
    inject_nurse_surface_styles,
    render_nurse_answer_cards,
    render_nurse_header,
)
from continucare.layer4.manual_reviews import (
    ManualReviewQueue,
    communication_readiness,
)
from continucare.layer4.storage import Layer4SQLiteStore
from continucare.services.competition_demo import (
    CompetitionDemoConflict,
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
    inject_global_styles,
    patient_recorded_meaning,
    project_nurse_workbench,
    render_disclosure_controls,
)


TASK_STATUS_LABELS = {
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


OUTCOME_LABELS = {
    "evidence_consistent": "记录一致",
    "clarification_needed": "需要补充说明",
    "reviewed_no_escalation": "本次复核完成，未上报医生",
    "clarification_required": "需要联系患者补充核实",
    "escalated_to_doctor": "上报医生评估",
}

OUTCOME_OPTIONS = (
    "reviewed_no_escalation",
    "clarification_required",
    "escalated_to_doctor",
)

OUTCOME_HELP = {
    "reviewed_no_escalation": (
        "只记录护士本次未上报；不表示患者安全、低风险或已经完成临床评估。"
    ),
    "clarification_required": (
        "记录需要继续向患者核实；当前原型只生成未发送的沟通文字。"
    ),
    "escalated_to_doctor": (
        "记录护士人工上报，并在医生端展示该人工处理结果；系统没有自动分级。"
    ),
}

SAFETY_REVIEW_CHECKLIST = (
    "已核对患者原话和患者确认结果",
    "已核对中文回答与患者原话是否一致",
    "已核对时间窗、单位、缺失和冲突",
    "已查看患者补充说明和可用历史原始值",
    "已由护士本人决定是否需要患者补充或医生评估",
)


def _stage_value(progress) -> str:
    return getattr(progress.stage, "value", str(progress.stage))


def _task_output(task: dict, code: str) -> str | None:
    matches = []
    for item in task.get("output", []):
        codes = {
            coding.get("code")
            for coding in item.get("type", {}).get("coding", [])
        }
        if code in codes:
            matches.append(
                item.get("valueCode")
                or item.get("valueString")
                or item.get("valueReference", {}).get("reference")
            )
    return str(matches[0]) if len(matches) == 1 and matches[0] else None


def _patient_quote(response: dict | None) -> str | None:
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


def _format_time(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return "提交时间未记录"
    return parsed.strftime("%Y-%m-%d %H:%M")


def _guarded_write(action, /, *args, **kwargs):
    with demo_write_guard(
        settings.db_path,
        expected_generation=progress.generation,
    ):
        return action(*args, **kwargs)


def _run_action(action, *, feedback: str, **kwargs) -> None:
    try:
        _guarded_write(action, **kwargs)
    except CompetitionDemoConflict:
        st.error("这轮演示已在另一个页面重新开始。当前页面没有继续写入，请刷新后再操作。")
    except (LookupError, ValueError) as exc:
        st.error(str(exc))
    else:
        st.session_state["cc_nurse_notice"] = feedback
        st.session_state.pop("cc_nurse_confirm_action", None)
        st.rerun()


def _build_task_contexts(
    *,
    store: SQLiteStore,
    repository: Layer4SQLiteStore,
    service: ManualReviewWorkflowService,
    tasks: tuple[dict, ...],
) -> dict[str, dict]:
    patient = store.get_patient(DEMO_PATIENT_ID)
    patient_label = patient.display_name if patient else "合成患者"
    meanings = []
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
    contexts: dict[str, dict] = {}
    for task in tasks:
        task_id = str(task.get("id") or "")
        response_ref = str(task.get("reasonReference", {}).get("reference") or "")
        response_id = (
            response_ref.split("/", 1)[1]
            if response_ref.startswith("QuestionnaireResponse/")
            else ""
        )
        response = store.get_questionnaire_response(response_id) if response_id else None
        communications = service.list_communications_for_task(
            DEMO_PATIENT_ID,
            task_id,
        )
        communication = communications[0] if len(communications) == 1 else None
        communication_text = None
        if communication:
            payload = communication.get("payload", [])
            if len(payload) == 1:
                communication_text = payload[0].get("contentString")
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
            "confirmed_statement": confirmed_statement,
            "outcome_label": OUTCOME_LABELS.get(outcome, outcome),
            "review_note": _task_output(task, "review-note"),
            "communication_text": communication_text,
            "communication_readiness": (
                communication_readiness(communication) if communication else None
            ),
            "has_pending_brief": (
                progress.task_id == task_id
                and _stage_value(progress) == "doctor_brief_pending"
            ),
            "history": tuple(
                (
                    f"v{item.get('meta', {}).get('versionId', '—')}",
                    TASK_STATUS_LABELS.get(
                        str(item.get("status") or ""),
                        str(item.get("status") or "状态未记录"),
                    ),
                    str(item.get("meta", {}).get("lastUpdated") or "时间未记录"),
                )
                for item in history
            ),
        }
    return contexts


def _render_queue(
    tasks: tuple[NurseTaskProjection, ...],
    *,
    selected_task_id: str | None,
    prefix: str,
) -> None:
    if not tasks:
        st.markdown(
            '<div class="cc-nurse-empty">这里暂时没有记录。</div>',
            unsafe_allow_html=True,
        )
        return
    for index, item in enumerate(tasks):
        selected = item.task_id == selected_task_id
        key_prefix = "cc_nurse_task_selected" if selected else "cc_nurse_task"
        if st.button(
            f"{item.patient_label} · 人工安全复核",
            key=f"{key_prefix}_{prefix}_{index}",
            width="stretch",
        ):
            st.session_state["cc_nurse_selected_task"] = item.task_id
            st.session_state.pop("cc_nurse_confirm_action", None)
            st.rerun()
        st.markdown(
            '<div class="cc-nurse-task-meta">'
            f"{html.escape(item.patient_label)} · "
            f"{html.escape(_format_time(item.submitted_at))}<br>"
            f"{html.escape(item.status_title)}"
            "</div>",
            unsafe_allow_html=True,
        )


def _render_disclosure(task: NurseTaskProjection, *, area: str) -> None:
    choice = st.query_params.get("cc_nurse_disclosure")
    options = (
        (("patient", "查看患者原话"),)
        if area == "source"
        else (("history", "查看处理记录"),)
    )
    panel_id = (
        "cc-nurse-source-panel" if area == "source" else "cc-nurse-record-panel"
    )
    selected = render_disclosure_controls(
        st,
        query_parameter="cc_nurse_disclosure",
        page_path="/nurse_risk_center",
        options=options,
        selected=str(choice) if choice is not None else None,
        aria_label="护士任务来源" if area == "source" else "护士任务进一步查看",
        panel_id=panel_id,
    )
    if area == "source" and selected == "patient":
        quote = task.original_quote or "患者原话暂时无法读取。"
        st.markdown(
            f'<div id="{panel_id}" class="cc-nurse-result-boundary">'
            f"<strong>患者在本轮确认</strong><br>{html.escape(quote)}"
            "</div>",
            unsafe_allow_html=True,
        )
    elif area == "record" and selected == "history":
        history = "".join(
            (
                '<div class="cc-nurse-history">'
                f"<strong>{html.escape(version)}</strong>"
                f"<span>{html.escape(status)}</span>"
                f"<span>{html.escape(occurred_at)}</span>"
                "</div>"
            )
            for version, status, occurred_at in task.history
        )
        if not history:
            history = "<p>还没有先前动作。</p>"
        st.markdown(
            f'<section id="{panel_id}" aria-label="先前动作">{history}</section>',
            unsafe_allow_html=True,
        )


def _render_communication(task: NurseTaskProjection) -> None:
    if not task.communication_text:
        return
    marker = task.communication_marker or "模拟（未真实发送）"
    title = (
        "待人工核对的沟通文字"
        if task.status_title == "沟通文字待核对"
        else "已核对的沟通文字"
    )
    st.markdown(
        '<section class="cc-nurse-communication">'
        f"<h3>{html.escape(title)}</h3>"
        f"<p>{html.escape(task.communication_text)}</p>"
        f'<p class="cc-nurse-mock">{html.escape(marker)}</p>'
        "</section>",
        unsafe_allow_html=True,
    )


def _render_primary_action(task: NurseTaskProjection) -> None:
    if task.primary_action is None:
        return
    st.markdown(
        '<div class="cc-nurse-action-title">当前唯一主动作</div>',
        unsafe_allow_html=True,
    )
    if task.primary_action == "open_doctor":
        with st.container(key="cc_nurse_primary_link"):
            st.page_link(
                "pages/3_doctor_summary.py",
                label=task.primary_label or "前往复诊速览",
                width="stretch",
            )
        return

    action_notes = {
        "acknowledge": "已接手这条人工安全复核任务。",
        "start": "已开始人工复核患者确认的记录。",
        "approve_draft": "已逐字核对这段沟通文字。",
    }
    if task.primary_action == "record_outcome":
        st.markdown("#### 人工安全复核清单")
        st.caption(
            "清单只记录护士是否完成查看；软件不会根据患者数值自动勾选或推荐结果。"
        )
        checklist_values = [
            st.checkbox(
                item,
                key=f"cc_nurse_check_{task.task_id}_{index}",
            )
            for index, item in enumerate(SAFETY_REVIEW_CHECKLIST)
        ]
        checklist_complete = all(checklist_values)
        outcome = st.radio(
            "护士人工处理结果",
            options=OUTCOME_OPTIONS,
            format_func=lambda value: OUTCOME_LABELS[value],
            key=f"cc_nurse_outcome_{task.task_id}",
            horizontal=False,
        )
        st.info(OUTCOME_HELP[outcome])
        note = st.text_area(
            "人工复核说明（必填）",
            value="",
            key=f"cc_nurse_outcome_note_{task.task_id}",
            placeholder=(
                "请记录护士实际核对了什么、是否联系患者，以及为什么作出本次决定。"
            ),
        )
        can_submit = checklist_complete and bool(note.strip())
    else:
        outcome = None
        note = action_notes[task.primary_action]
        can_submit = True
    with st.container(key="cc_nurse_primary"):
        clicked = st.button(
            task.primary_label or "继续",
            key=f"cc_nurse_primary_button_{task.task_id}_{task.primary_action}",
            type="primary",
            width="stretch",
            disabled=not can_submit,
        )
    if task.primary_action == "approve_draft":
        st.caption("这一步只确认文字可进入后续演示流程，不会发送给患者。")
    if not clicked:
        return
    common = {
        "patient_id": DEMO_PATIENT_ID,
        "task_id": task.task_id,
        "note": note,
        "occurred_at": utc_now_iso(),
    }
    if task.primary_action == "acknowledge":
        _run_action(
            manual_service.acknowledge,
            feedback="已接手，下一步开始人工安全复核。",
            **common,
        )
    elif task.primary_action == "start":
        _run_action(
            manual_service.start,
            feedback="已开始人工安全复核。",
            **common,
        )
    elif task.primary_action == "record_outcome":
        _run_action(
            manual_service.record_outcome,
            feedback=(
                "护士人工上报已记录，医生端可以查看；系统未进行临床分级。"
                if outcome == "escalated_to_doctor"
                else "人工复核结果已保存，沟通文字仍待核对且没有发送。"
            ),
            outcome=outcome,
            **common,
        )
    elif task.primary_action == "approve_draft":
        communications = manual_service.list_communications_for_task(
            DEMO_PATIENT_ID,
            task.task_id,
        )
        if len(communications) != 1:
            st.error("沟通文字状态无法安全确认；页面没有继续写入。")
            return
        _run_action(
            manual_service.approve_draft,
            feedback="文字已核对；模拟，未真实发送。",
            communication_id=communications[0]["id"],
            **common,
        )


def _render_secondary_actions(task: NurseTaskProjection) -> None:
    if not task.secondary_actions:
        return
    columns = st.columns(len(task.secondary_actions))
    for column, (action, label) in zip(columns, task.secondary_actions):
        with column, st.container(key=f"cc_nurse_secondary_{action}"):
            if st.button(
                label,
                key=f"cc_nurse_secondary_button_{task.task_id}_{action}",
                width="stretch",
            ):
                st.session_state["cc_nurse_confirm_action"] = (
                    f"{task.task_id}:{action}"
                )
                st.rerun()

    selection = st.session_state.get("cc_nurse_confirm_action")
    if selection not in {
        f"{task.task_id}:{action}" for action, _ in task.secondary_actions
    }:
        return
    action = selection.rsplit(":", 1)[1]
    label = dict(task.secondary_actions)[action]
    st.markdown(
        f'<div class="cc-nurse-consequence">{html.escape(NURSE_STOP_CONSEQUENCE)}</div>',
        unsafe_allow_html=True,
    )
    note = st.text_area(
        "停止处理说明（必填）",
        value="",
        key=f"cc_nurse_stop_note_{task.task_id}_{action}",
        placeholder="请说明为什么停止后续处理。",
    )
    with st.container(key=f"cc_nurse_confirm_{action}"):
        confirmed = st.button(
            f"确认{label}",
            key=f"cc_nurse_confirm_button_{task.task_id}_{action}",
            width="stretch",
            disabled=not note.strip(),
        )
    if confirmed:
        _run_action(
            manual_service.reject if action == "reject" else manual_service.cancel,
            feedback="流程已停止；已有记录仍保留供追溯。",
            patient_id=DEMO_PATIENT_ID,
            task_id=task.task_id,
            note=note,
            occurred_at=utc_now_iso(),
        )


def _render_outcomes(task: NurseTaskProjection) -> None:
    if task.stop_reason:
        st.markdown(f"**原因：** {task.stop_reason}")
    if not task.produced and not task.not_produced:
        return
    produced = "".join(f"<li>{html.escape(item)}</li>" for item in task.produced)
    not_produced = "".join(
        f"<li>{html.escape(item)}</li>" for item in task.not_produced
    )
    st.markdown(
        '<div class="cc-nurse-outcomes">'
        f"<section><h3>已经产生</h3><ul>{produced or '<li>无</li>'}</ul></section>"
        f"<section><h3>没有产生</h3><ul>{not_produced or '<li>无</li>'}</ul></section>"
        "</div>",
        unsafe_allow_html=True,
    )


def _render_pathway_review_data(review_answers, governance_view) -> None:
    pathway_label = "本次随访"
    if governance_view is not None:
        pathway_label = (
            f"{governance_view.pathway_code} v{governance_view.pathway_version}"
        )
    render_nurse_answer_cards(
        st,
        review_answers,
        pathway_label=pathway_label,
    )


def _render_detail(
    task: NurseTaskProjection | None,
    *,
    review_answers=(),
    governance_view=None,
) -> None:
    if task is None:
        st.markdown(
            '<div class="cc-nurse-empty">选择一条记录后，这里会显示当前动作。</div>',
            unsafe_allow_html=True,
        )
        return
    st.markdown(
        '<dl class="cc-nurse-detail-head">'
        "<dt>任务类型</dt><dd>患者确认记录人工安全复核</dd>"
        f"<dt>提交时间</dt><dd>{html.escape(_format_time(task.submitted_at))}</dd>"
        "</dl>"
        '<div class="cc-nurse-statement">'
        "<span>患者确认的表述</span>"
        f"<strong>{html.escape(task.confirmed_statement)}</strong>"
        "</div>",
        unsafe_allow_html=True,
    )
    _render_pathway_review_data(review_answers, governance_view)
    _render_disclosure(task, area="source")
    st.markdown(
        f'<section class="cc-nurse-status cc-nurse-status--{html.escape(task.tone)}" '
        'aria-live="polite">'
        f"<h2>{html.escape(task.status_title)}</h2>"
        f"<p>{html.escape(task.status_detail)}</p>"
        "</section>",
        unsafe_allow_html=True,
    )
    _render_communication(task)
    _render_primary_action(task)
    st.markdown(
        f'<div class="cc-nurse-result-boundary">{html.escape(NURSE_RESULT_BOUNDARY)}</div>',
        unsafe_allow_html=True,
    )
    if task.outcome_label:
        st.markdown(f"**核对结果：** {task.outcome_label}")
    if task.review_note:
        st.caption(f"处理说明：{task.review_note}")
    _render_secondary_actions(task)
    _render_outcomes(task)
    _render_disclosure(task, area="record")
    with st.container(key="cc_nurse_record_link"):
        st.page_link(
            "pages/4_audit_log.py",
            label="查看完整接力记录",
            width="stretch",
        )


def _render_supplemental_section(*, store, progress, settings) -> None:
    if not progress.session_id:
        return
    supplemental = read_supplemental_reports(
        settings.db_path,
        session_id=progress.session_id,
    )
    if supplemental.integrity_issue:
        st.error("患者补充上报队列暂时不可读；护士端已停止写入。")
        return
    pending = [item for item in supplemental.reports if item.status == "requested"]
    reviewed = [item for item in supplemental.reports if item.status == "reviewed"]
    with st.container(key="cc_nurse_supplemental"):
        st.markdown(
            """
            <span id="cc-nurse-supplemental" aria-hidden="true"></span>
            <header class="cc-nurse-supplemental-head">
              <div><p class="cc-nurse-section-kicker">患者补充</p><h2>随访后的补充说明</h2>
              <p>仅显示患者确认的中文内容；是否需要进一步处理由护士决定。</p></div>
            </header>
            """,
            unsafe_allow_html=True,
        )
        if not supplemental.reports:
            st.markdown(
                '<p class="cc-nurse-supplemental-empty">当前没有患者确认的补充说明。</p>',
                unsafe_allow_html=True,
            )
        for report in [*pending, *reviewed]:
            meanings = tuple(
                str(item.get("evidence_text") or "").strip()
                for item in report.structured_items
                if str(item.get("evidence_text") or "").strip()
            )
            meaning_html = "".join(f"<p>• {html.escape(item)}</p>" for item in meanings)
            status_text = (
                "等待护士人工复核"
                if report.status == "requested"
                else f"已完成复核 · {report.review_note or '已查看'}"
            )
            st.markdown(
                '<article class="cc-nurse-supplemental-card">'
                f"<strong>患者说：{html.escape(report.original_text)}</strong>"
                f"{meaning_html}"
                f"<span>{html.escape(status_text)}</span>"
                "</article>",
                unsafe_allow_html=True,
            )
            if report.status != "requested":
                continue
            note = st.text_area(
                "补充说明复核记录",
                value="已查看患者确认的补充说明；未作临床风险判断。",
                key=f"cc_supplemental_review_note_{report.report_id}",
            )
            if st.button(
                "确认已人工复核",
                type="primary",
                width="stretch",
                key=f"cc_supplemental_review_{report.report_id}",
            ):
                try:
                    review_supplemental_report(
                        settings.db_path,
                        session_id=progress.session_id,
                        report_id=report.report_id,
                        expected_story_generation=progress.generation or "",
                        expected_supplemental_generation=supplemental.generation,
                        note=note,
                    )
                except (CompetitionDemoConflict, ValueError) as exc:
                    st.error(str(exc))
                else:
                    st.rerun()


st.set_page_config(
    page_title="护士安全复核台 · ContinuCare",
    layout="wide",
    initial_sidebar_state="collapsed",
)
inject_global_styles(st)
inject_nurse_surface_styles(st)

settings = get_settings()
initialize_database(settings.db_path)
progress = read_competition_demo(settings.db_path)
manual_service = None
tasks: tuple[dict, ...] = ()
contexts: dict[str, dict] = {}
governance_view = None
review_answers = ()
store = SQLiteStore(settings.db_path, initialize=False) if settings.db_path.is_file() else None
repository = None
if store is not None:
    repository = Layer4SQLiteStore(settings.db_path, initialize=False)
    manual_service = ManualReviewWorkflowService(store, layer4_store=repository)
    tasks = tuple(ManualReviewQueue(repository).list_for_patient(DEMO_PATIENT_ID))

pending_statuses = {"requested", "received", "accepted", "in-progress"}
render_nurse_header(
    st,
    build_product_context(store, ProductRole.NURSE),
    progress,
    pending_count=sum(str(item.get("status") or "") in pending_statuses for item in tasks),
    completed_count=sum(str(item.get("status") or "") not in pending_statuses for item in tasks),
)
with st.container(key="cc_nurse_refresh_bar"):
    if st.button("刷新当前状态", key="cc_nurse_refresh_shared"):
        st.rerun()
st.markdown(
    f'<div class="cc-nurse-boundary">{html.escape(NURSE_ROLE_BOUNDARY)}</div>',
    unsafe_allow_html=True,
)
if progress.integrity_issue:
    st.error("共享流程记录暂时不可读取；护士端不会继续处理。")
    st.stop()
if not progress.plan_activated:
    st.info("医生尚未启动本轮随访方案。当前没有患者记录或人工复核任务。")
    st.stop()
if progress.run_id is None:
    st.info("随访方案已经启动，正在等待患者提交并确认记录。当前没有护士任务。")
    st.stop()
if store is not None and repository is not None and manual_service is not None:
    contexts = _build_task_contexts(
        store=store,
        repository=repository,
        service=manual_service,
        tasks=tasks,
    )
    governance_view = build_l5_governance_for_patient(store, DEMO_PATIENT_ID)
    if progress.session_id:
        session = store.get_care_session(progress.session_id)
        if session is not None:
            questionnaire = CareEngine(store).questionnaire_for_session(session)
            review_answers = build_nurse_answer_cards(questionnaire, session.answers)

selected_hint = st.session_state.get("cc_nurse_selected_task")
projection = project_nurse_workbench(
    progress,
    tasks=tasks,
    task_contexts=contexts,
    selected_task_id=selected_hint,
)
if projection.selected_task_id:
    st.session_state["cc_nurse_selected_task"] = projection.selected_task_id

notice = st.session_state.pop("cc_nurse_notice", None)
if notice:
    st.info(notice)
if projection.notice_title:
    tone_class = "error" if projection.tone == "error" else "neutral"
    st.markdown(
        f'<section class="cc-nurse-status cc-nurse-status--{tone_class}">'
        f"<h2>{html.escape(projection.notice_title)}</h2>"
        f"<p>{html.escape(projection.notice_detail or '')}</p>"
        "</section>",
        unsafe_allow_html=True,
    )

all_tasks = (*projection.pending_tasks, *projection.completed_tasks)
selected_task = next(
    (item for item in all_tasks if item.task_id == projection.selected_task_id),
    None,
)
with st.container(key="cc_nurse_workspace"):
    queue_column, detail_column = st.columns([3, 7], gap="small")
    with queue_column:
        st.markdown(
            '<div class="cc-nurse-sort">按提交时间排序 · 最早提交的记录在前</div>',
            unsafe_allow_html=True,
        )
        queue_labels = [
            f"待处理（{len(projection.pending_tasks)}）",
            f"已处理（{len(projection.completed_tasks)}）",
        ]
        pending_tab, completed_tab = st.tabs(
            queue_labels,
            default=(
                queue_labels[0] if projection.pending_tasks else queue_labels[1]
            ),
            key="cc_nurse_queue_tabs",
        )
        with pending_tab:
            _render_queue(
                projection.pending_tasks,
                selected_task_id=projection.selected_task_id,
                prefix="pending",
            )
        with completed_tab:
            _render_queue(
                projection.completed_tasks,
                selected_task_id=projection.selected_task_id,
                prefix="completed",
            )
    with detail_column:
        _render_detail(
            selected_task,
            review_answers=review_answers,
            governance_view=governance_view,
        )

if store is not None:
    _render_supplemental_section(store=store, progress=progress, settings=settings)

with st.expander("演示边界", expanded=False):
    if progress.alert_count == 0 and progress.approved_clinical_rule_count == 0:
        st.caption("临床警报未启用：0 条获批规则，0 条 Alert。")
    else:
        st.caption(
            f"当前记录有 {progress.approved_clinical_rule_count} 条获批规则、"
            f"{progress.alert_count} 条 Alert；不与人工安全复核队列合并。"
        )
    st.caption("仅使用合成数据；页面没有真实发送或真实外部写入。")
