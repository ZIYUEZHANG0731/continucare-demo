from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from continucare.adapters.sqlite_store import SQLiteStore
from continucare.care_agent import CareAgentService
from continucare.care_agent.model_api import SemanticModelConfig, UnconfiguredModelAdapter
from continucare.care_engine import CareEngine
from continucare.demo_data import DEMO_PATIENT_ID, MANUAL_REVIEW_MESSAGE
from continucare.layer4.manual_reviews import ManualReviewQueue
from continucare.layer4.storage import Layer4SQLiteStore
from continucare.nurse_ui import NURSE_SURFACE_STYLE, build_nurse_answer_cards
from continucare.services.competition_demo import demo_write_guard, start_competition_demo
from continucare.services.confirmed_review import ConfirmedReviewService
from continucare.services.competition_demo import (
    CompetitionDemoProgress,
    CompetitionDemoStage,
    read_competition_demo,
)
from continucare.ui import (
    NURSE_RESULT_BOUNDARY,
    NURSE_ROLE_BOUNDARY,
    NURSE_STOP_CONSEQUENCE,
    project_nurse_workbench,
)


ROOT = Path(__file__).parents[1]
NURSE_PAGE = ROOT / "pages" / "2_nurse_risk_center.py"
UI_SOURCE = ROOT / "continucare" / "ui.py"
NURSE_UI_SOURCE = ROOT / "continucare" / "nurse_ui.py"


def _progress(stage: CompetitionDemoStage, **updates) -> CompetitionDemoProgress:
    return CompetitionDemoProgress(
        stage=stage,
        generation="session:run",
        task_id="task-1",
        **updates,
    )


def _task(
    status: str,
    *,
    task_id: str = "task-1",
    authored_on: str = "2026-08-14T09:00:00+00:00",
    note: str | None = None,
) -> dict:
    resource = {
        "resourceType": "Task",
        "id": task_id,
        "status": status,
        "authoredOn": authored_on,
        "reasonReference": {"reference": "QuestionnaireResponse/response-1"},
        "input": [
            {
                "valueReference": {
                    "reference": "Observation/observation-1",
                }
            }
        ],
    }
    if note is not None:
        resource["note"] = [{"text": note}]
    return resource


def _context(**updates) -> dict:
    return {
        "patient_label": "合成患者",
        "original_quote": "我今天拉肚子。",
        "confirmed_statement": "今天有腹泻",
        "history": (("v1", "等待接手", "2026-08-14T09:00:00+00:00"),),
        **updates,
    }


def _project(stage, task, **context_updates):
    progress = _progress(stage)
    return project_nurse_workbench(
        progress,
        tasks=(task,),
        task_contexts={task["id"]: _context(**context_updates)},
    )


def _only_task(projection):
    tasks = (*projection.pending_tasks, *projection.completed_tasks)
    assert len(tasks) == 1
    return tasks[0]


def _visible_task_text(task) -> str:
    values = (
        task.status_title,
        task.status_detail,
        task.original_quote,
        task.confirmed_statement,
        task.primary_label,
        task.outcome_label,
        task.review_note,
        task.communication_text,
        task.communication_marker,
        task.stop_reason,
        *task.produced,
        *task.not_produced,
        *(label for _, label in task.secondary_actions),
    )
    return "\n".join(str(value) for value in values if value)


class _RenderedDOM(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids: list[tuple[str, dict[str, str | None]]] = []
        self.controls: list[dict[str, str | None]] = []
        self.text: list[str] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if "id" in attributes:
            self.ids.append((tag, attributes))
        if tag == "a" and "aria-controls" in attributes:
            self.controls.append(attributes)

    def handle_data(self, data):
        if data.strip():
            self.text.append(data.strip())


def _rendered_dom(app) -> _RenderedDOM:
    parser = _RenderedDOM()
    parser.feed("\n".join(str(item.value) for item in app.markdown))
    return parser


def _seed_requested_task(db_path) -> None:
    progress = start_competition_demo(db_path)
    store = SQLiteStore(db_path, initialize=False)
    engine = CareEngine(store)
    agent = CareAgentService(
        store,
        care_engine=engine,
        model_adapter=UnconfiguredModelAdapter(SemanticModelConfig()),
        patient_timezone="Asia/Shanghai",
    )
    confirmed = ConfirmedReviewService(store, care_agent=agent, care_engine=engine)
    record = store.get_agent_run(progress.run_id)
    candidate_ids = [
        item["candidate_id"] for item in record.output_json["candidates"]
    ]
    with demo_write_guard(db_path, expected_generation=progress.generation):
        confirmed.accept_all(progress.run_id, candidate_ids)


def test_empty_workbench_is_truthful_and_has_no_business_action():
    projection = project_nurse_workbench(CompetitionDemoProgress())

    assert projection.state == "empty"
    assert projection.notice_title == "目前没有待复核记录"
    assert projection.pending_tasks == ()
    assert projection.completed_tasks == ()
    assert projection.selected_task_id is None


@pytest.mark.parametrize(
    ("stage", "status", "title", "action", "label", "secondary"),
    [
        (
            CompetitionDemoStage.TASK_REQUESTED,
            "requested",
            "等待接手",
            "acknowledge",
            "接手这项安全复核",
            (("cancel", "取消任务"),),
        ),
        (
            CompetitionDemoStage.NURSE_RECEIVED,
            "received",
            "已接手",
            "start",
            "开始人工复核",
            (("reject", "拒绝处理"), ("cancel", "取消任务")),
        ),
        (
            CompetitionDemoStage.NURSE_IN_PROGRESS,
            "in-progress",
            "正在人工复核",
            "record_outcome",
            "保存护士人工决定",
            (("cancel", "取消任务"),),
        ),
    ],
)
def test_active_task_states_have_exactly_one_primary_business_action(
    stage, status, title, action, label, secondary
):
    item = _only_task(_project(stage, _task(status)))

    assert item.queue == "pending"
    assert item.status_title == title
    assert item.primary_action == action
    assert item.primary_label == label
    assert item.primary_writes is True
    assert item.secondary_actions == secondary
    assert item.confirmed_statement == "今天有腹泻"
    assert item.original_quote == "我今天拉肚子。"


def test_completed_pending_without_brief_only_links_to_doctor_read_only():
    item = _only_task(
        _project(
            CompetitionDemoStage.COMMUNICATION_PENDING,
            _task("completed"),
            communication_readiness="pending-approval",
            communication_text="合成沟通文字。",
            has_pending_brief=False,
        )
    )

    assert item.queue == "pending"
    assert item.status_title == "沟通文字待核对"
    assert item.primary_action == "open_doctor"
    assert item.primary_label == "前往复诊速览"
    assert item.primary_writes is False
    assert "查看页面不会自动生成" in item.status_detail


def test_completed_pending_with_brief_allows_only_text_confirmation():
    item = _only_task(
        _project(
            CompetitionDemoStage.DOCTOR_BRIEF_PENDING,
            _task("completed"),
            communication_readiness="pending-approval",
            communication_text="合成沟通文字。",
            has_pending_brief=True,
        )
    )

    assert item.queue == "pending"
    assert item.primary_action == "approve_draft"
    assert item.primary_label == "确认文字已核对"
    assert item.primary_writes is True
    assert item.secondary_actions == ()
    assert item.communication_marker == "模拟（未真实发送）"


def test_completed_ready_is_processed_and_never_claims_delivery():
    item = _only_task(
        _project(
            CompetitionDemoStage.COMMUNICATION_READY,
            _task("completed"),
            communication_readiness="ready-to-send",
            communication_text="合成沟通文字。",
        )
    )

    assert item.queue == "completed"
    assert item.status_title == "人工安全复核已完成"
    assert item.primary_action == "open_doctor"
    assert item.primary_writes is False
    assert "本演示不会发送" in item.status_detail
    assert "已发送" not in _visible_task_text(item)


@pytest.mark.parametrize(
    ("stage", "status", "tone"),
    [
        (CompetitionDemoStage.TASK_REJECTED, "rejected", "stopped"),
        (CompetitionDemoStage.TASK_CANCELLED, "cancelled", "stopped"),
        (CompetitionDemoStage.TASK_FAILED, "failed", "error"),
        (
            CompetitionDemoStage.TASK_ENTERED_IN_ERROR,
            "entered-in-error",
            "error",
        ),
    ],
)
def test_all_task_terminals_are_read_only_and_use_only_persisted_reason(
    stage, status, tone
):
    item = _only_task(
        _project(stage, _task(status, note="持久化处理说明。"))
    )

    assert item.queue == "completed"
    assert item.tone == tone
    assert item.primary_action is None
    assert item.primary_writes is False
    assert item.secondary_actions == ()
    assert item.stop_reason == "持久化处理说明。"


def test_missing_terminal_reason_is_explicitly_not_recorded():
    item = _only_task(
        _project(
            CompetitionDemoStage.TASK_ENTERED_IN_ERROR,
            _task("entered-in-error"),
        )
    )

    assert item.stop_reason == "未记录"
    assert "记录错误" in item.status_title
    assert item.primary_action is None


def test_story_complete_is_fully_read_only_and_not_clinical_completion():
    item = _only_task(
        _project(
            CompetitionDemoStage.STORY_COMPLETE,
            _task("completed"),
            communication_readiness="ready-to-send",
            communication_text="合成沟通文字。",
        )
    )

    assert item.queue == "completed"
    assert item.primary_action is None
    assert item.secondary_actions == ()
    assert "不代表临床完成、治疗完成或风险解除" in item.status_detail
    assert "真实消息发送" in item.not_produced


def test_integrity_issue_removes_every_action_and_does_not_expose_raw_detail():
    progress = _progress(
        CompetitionDemoStage.TASK_REQUESTED,
        integrity_issue="raw sqlite detail",
    )
    projection = project_nurse_workbench(
        progress,
        tasks=(_task("requested"),),
        task_contexts={"task-1": _context()},
    )
    item = _only_task(projection)

    assert projection.state == "error"
    assert item.primary_action is None
    assert item.secondary_actions == ()
    assert "raw sqlite detail" not in (projection.notice_detail or "")


def test_unknown_stage_and_unknown_task_status_both_fail_closed():
    unknown_progress = SimpleNamespace(
        stage="future-stage",
        integrity_issue=None,
    )
    projection = project_nurse_workbench(
        unknown_progress,
        tasks=(_task("requested"),),
        task_contexts={"task-1": _context()},
    )
    unknown_task = _only_task(
        _project(
            CompetitionDemoStage.TASK_REQUESTED,
            _task("future-task-status"),
        )
    )

    assert projection.state == "error"
    assert _only_task(projection).primary_action is None
    assert unknown_task.tone == "error"
    assert unknown_task.primary_action is None


def test_persisted_stage_and_current_task_status_mismatch_fails_closed():
    projection = project_nurse_workbench(
        _progress(CompetitionDemoStage.NURSE_RECEIVED),
        tasks=(_task("requested"),),
        task_contexts={"task-1": _context()},
    )
    item = _only_task(projection)

    assert projection.state == "error"
    assert item.tone == "error"
    assert item.primary_action is None
    assert item.secondary_actions == ()
    assert "状态与当前记录链不一致" in item.status_detail


def test_missing_patient_source_fails_closed_before_any_business_action():
    projection = project_nurse_workbench(
        _progress(CompetitionDemoStage.TASK_REQUESTED),
        tasks=(_task("requested"),),
        task_contexts={
            "task-1": _context(original_quote=None),
        },
    )
    item = _only_task(projection)

    assert item.tone == "error"
    assert item.primary_action is None
    assert item.secondary_actions == ()
    assert "来源不完整" in item.status_detail


def test_every_legal_competition_state_projects_without_inventing_an_empty_task():
    for stage in CompetitionDemoStage:
        projection = project_nurse_workbench(_progress(stage), tasks=())
        assert projection.state == "empty"
        assert projection.pending_tasks == ()
        assert projection.completed_tasks == ()


def test_queue_uses_original_submission_time_oldest_first_and_auto_selects():
    tasks = (
        _task(
            "requested",
            task_id="task-later",
            authored_on="2026-08-14T10:00:00+00:00",
        ),
        _task(
            "requested",
            task_id="task-earlier",
            authored_on="2026-08-14T08:00:00+00:00",
        ),
    )
    projection = project_nurse_workbench(
        _progress(CompetitionDemoStage.TASK_REQUESTED),
        tasks=tasks,
        task_contexts={item["id"]: _context() for item in tasks},
    )

    assert [item.task_id for item in projection.pending_tasks] == [
        "task-earlier",
        "task-later",
    ]
    assert projection.selected_task_id == "task-earlier"


def test_knowledge_fields_do_not_participate_in_the_projection():
    base = _progress(CompetitionDemoStage.TASK_REQUESTED)
    with_knowledge_error = base.model_copy(
        update={
            "knowledge_available": True,
            "knowledge_error": "independent knowledge state",
        }
    )
    kwargs = {
        "tasks": (_task("requested"),),
        "task_contexts": {"task-1": _context()},
    }

    assert project_nurse_workbench(base, **kwargs) == project_nurse_workbench(
        with_knowledge_error,
        **kwargs,
    )


def test_frozen_boundaries_are_natural_chinese_and_explain_consequences():
    assert NURSE_ROLE_BOUNDARY == (
        "系统只把每份患者确认记录交给护士；是否需要补充或上报医生，由护士人工决定。"
    )


def test_nurse_answer_cards_translate_persisted_values_to_chinese_only():
    questionnaire = json.loads(
        (
            ROOT
            / "continucare"
            / "pathways"
            / "data"
            / "fhir"
            / "glp1_followup_questionnaire_v1.json"
        ).read_text("utf-8")
    )
    cards = build_nurse_answer_cards(
        questionnaire,
        {
            "nausea-present": True,
            "nausea-severity": {
                "code": "LA6750-9",
                "display": "Severe",
                "system": "http://loinc.org",
                "version": "2.82",
            },
            "vomiting-count-24h": 2,
            "fluid-intake-24h-estimated": {
                "value": 800,
                "unit": "mL",
                "system": "http://unitsofmeasure.org",
                "code": "mL",
            },
            "abdominal-pain-present": False,
            "free-text-report": "今天恶心比较明显。",
        },
    )

    rendered = {(item.question, item.answer) for item in cards}
    assert ("现在有恶心吗？", "是") in rendered
    assert ("恶心程度最接近哪一项？", "重度") in rendered
    assert ("现在有腹痛吗？", "否") in rendered
    assert any(item.answer == "800 毫升" for item in cards)
    assert all("LA6750-9" not in item.answer for item in cards)
    assert all("http://loinc.org" not in item.answer for item in cards)
    assert all("value" not in item.answer for item in cards)
    assert ".cc-nurse-sidebar" in NURSE_SURFACE_STYLE
    assert NURSE_RESULT_BOUNDARY == (
        "护士结果是人工工作流决定，不形成系统风险等级、诊断或治疗建议；"
        "本次未上报也不表示患者安全。"
    )
    assert NURSE_STOP_CONSEQUENCE == (
        "这会停止后续业务动作；不会生成新的沟通文字或医生速览。"
        "已有记录仍会保留供追溯。"
    )


def test_nurse_page_removes_alert_dashboard_and_keeps_role_styles_scoped():
    source = NURSE_PAGE.read_text("utf-8")
    ui_source = UI_SOURCE.read_text("utf-8")
    nurse_ui_source = NURSE_UI_SOURCE.read_text("utf-8")

    assert "render_nurse_header" in source
    assert "render_nurse_answer_cards" in source
    assert "project_nurse_workbench" in source
    assert "按提交时间排序" in source
    assert "患者确认记录人工安全复核" in source
    assert "SAFETY_REVIEW_CHECKLIST" in source
    assert '"reviewed_no_escalation"' in source
    assert '"clarification_required"' in source
    assert '"escalated_to_doctor"' in source
    assert "AlertService" not in source
    assert ".metric(" not in source
    assert "剩余 SLA" not in source
    assert "MockNotifier" not in source
    assert "render_competition_progress" not in source
    assert "demo_write_guard" in source
    assert "expected_generation=progress.generation" in source
    assert "render_disclosure_controls" in source
    assert 'st.expander("演示边界"' in source
    assert "st.dataframe" not in source
    assert "工程追溯" not in source
    assert "标准化 Observation" not in source
    assert ".cc-nurse-v3" in nurse_ui_source
    assert '.stApp:has(.cc-nurse-v3) [data-testid="stSidebar"]' in nurse_ui_source
    assert ".st-key-cc_nurse_primary button" in nurse_ui_source
    assert "cc-nurse-answer-grid" in nurse_ui_source
    assert "cc-nurse-sidebar" in nurse_ui_source
    assert "@media (prefers-reduced-motion:reduce)" in nurse_ui_source
    assert 'aria-expanded="{str(active).lower()}"' in ui_source


def test_nurse_disclosures_render_distinct_unique_targets_and_fail_closed(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "nurse-disclosure.db"
    _seed_requested_task(db_path)
    monkeypatch.setenv("CONTINUCARE_DB_PATH", str(db_path))
    monkeypatch.setattr("streamlit.page_link", lambda *_args, **_kwargs: None)

    app = AppTest.from_file(str(NURSE_PAGE), default_timeout=10).run()
    assert not app.exception
    collapsed = _rendered_dom(app)
    assert [item["aria-controls"] for item in collapsed.controls] == [
        "cc-nurse-source-panel",
        "cc-nurse-record-panel",
    ]
    assert all(item["aria-expanded"] == "false" for item in collapsed.controls)
    for panel_id in ("cc-nurse-source-panel", "cc-nurse-record-panel"):
        targets = [item for item in collapsed.ids if item[1]["id"] == panel_id]
        assert len(targets) == 1
        assert targets[0][0] == "span"
        assert targets[0][1]["hidden"] is None
        assert targets[0][1]["aria-hidden"] == "true"
        assert "tabindex" not in targets[0][1]

    app.query_params["cc_nurse_disclosure"] = "patient"
    app.run()
    patient = _rendered_dom(app)
    assert [item["aria-expanded"] for item in patient.controls] == ["true", "false"]
    source_targets = [
        item for item in patient.ids if item[1]["id"] == "cc-nurse-source-panel"
    ]
    record_targets = [
        item for item in patient.ids if item[1]["id"] == "cc-nurse-record-panel"
    ]
    assert len(source_targets) == len(record_targets) == 1
    assert source_targets[0][0] == "div" and "hidden" not in source_targets[0][1]
    assert record_targets[0][0] == "span" and record_targets[0][1]["hidden"] is None
    assert MANUAL_REVIEW_MESSAGE in patient.text

    app.query_params["cc_nurse_disclosure"] = "history"
    app.run()
    history = _rendered_dom(app)
    assert [item["aria-expanded"] for item in history.controls] == ["false", "true"]
    source_targets = [
        item for item in history.ids if item[1]["id"] == "cc-nurse-source-panel"
    ]
    record_targets = [
        item for item in history.ids if item[1]["id"] == "cc-nurse-record-panel"
    ]
    assert len(source_targets) == len(record_targets) == 1
    assert source_targets[0][0] == "span" and source_targets[0][1]["hidden"] is None
    assert record_targets[0][0] == "section" and "hidden" not in record_targets[0][1]
    assert "v1" in history.text

    app.query_params["cc_nurse_disclosure"] = "future-value"
    app.run()
    unknown = _rendered_dom(app)
    assert all(item["aria-expanded"] == "false" for item in unknown.controls)
    assert sum(
        item[1]["id"] == "cc-nurse-source-panel" for item in unknown.ids
    ) == 1
    assert sum(
        item[1]["id"] == "cc-nurse-record-panel" for item in unknown.ids
    ) == 1


def test_nurse_streamlit_requires_checklist_and_can_manually_escalate_to_doctor(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "nurse-manual-escalation.db"
    _seed_requested_task(db_path)
    monkeypatch.setenv("CONTINUCARE_DB_PATH", str(db_path))
    monkeypatch.setattr("streamlit.page_link", lambda *_args, **_kwargs: None)

    app = AppTest.from_file(str(NURSE_PAGE), default_timeout=10).run()
    next(
        item for item in app.button if item.label == "接手这项安全复核"
    ).click().run()
    next(
        item for item in app.button if item.label == "开始人工复核"
    ).click().run()

    submit = next(
        item for item in app.button if item.label == "保存护士人工决定"
    )
    assert submit.disabled is True
    checklist_labels = {
        "已核对患者原话和患者确认结果",
        "已核对中文回答与患者原话是否一致",
        "已核对时间窗、单位、缺失和冲突",
        "已查看患者补充说明和可用历史原始值",
        "已由护士本人决定是否需要患者补充或医生评估",
    }
    for checkbox in app.checkbox:
        if checkbox.label in checklist_labels:
            checkbox.check()
    next(
        item for item in app.radio if item.label == "护士人工处理结果"
    ).set_value("escalated_to_doctor")
    next(
        item for item in app.text_area if item.label == "人工复核说明（必填）"
    ).set_value("护士已人工查看患者确认事实，请医生进行临床评估。")
    app.run()

    submit = next(
        item for item in app.button if item.label == "保存护士人工决定"
    )
    assert submit.disabled is False
    submit.click().run()
    assert not app.exception

    repository = Layer4SQLiteStore(db_path, initialize=False)
    tasks = ManualReviewQueue(repository).list_for_patient(DEMO_PATIENT_ID)
    assert len(tasks) == 1
    task = tasks[0]
    outcome = next(
        item["valueCode"]
        for item in task["output"]
        if item["type"]["coding"][0]["code"] == "review-outcome"
    )
    assert outcome == "escalated_to_doctor"
    assert task["statusReason"]["coding"][0]["code"] == (
        "human-escalated-to-doctor"
    )
    assert read_competition_demo(db_path).stage == (
        CompetitionDemoStage.COMMUNICATION_PENDING
    )
    assert SQLiteStore(db_path, initialize=False).list_alerts(DEMO_PATIENT_ID) == []

    doctor = AppTest.from_file(
        str(ROOT / "pages" / "3_doctor_summary.py"), default_timeout=10
    ).run()
    assert not doctor.exception
    assert any(
        "护士已人工上报这份患者确认记录" in item.value
        and "系统未进行临床风险分级" in item.value
        for item in doctor.warning
    )


def test_visible_projection_never_exposes_internal_or_delivery_capability_terms():
    cases = [
        _project(CompetitionDemoStage.TASK_REQUESTED, _task("requested")),
        _project(CompetitionDemoStage.NURSE_RECEIVED, _task("received")),
        _project(CompetitionDemoStage.NURSE_IN_PROGRESS, _task("in-progress")),
        _project(
            CompetitionDemoStage.DOCTOR_BRIEF_PENDING,
            _task("completed"),
            communication_readiness="pending-approval",
            communication_text="合成沟通文字。",
            has_pending_brief=True,
        ),
        _project(
            CompetitionDemoStage.COMMUNICATION_READY,
            _task("completed"),
            communication_readiness="ready-to-send",
            communication_text="合成沟通文字。",
        ),
    ]
    prohibited = (
        "ready-to-send",
        "pending-approval",
        "FHIR",
        "SLA",
        "pending ",
        "可以发送",
        "等待发送",
        "发送准备完成",
        "已发送",
    )
    for projection in cases:
        visible = _visible_task_text(_only_task(projection))
        assert all(term not in visible for term in prohibited)
