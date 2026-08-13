"""Human-readable workflow timeline backed by append-only audit events."""

from __future__ import annotations

import json

import streamlit as st

from continucare.adapters.sqlite_store import SQLiteStore
from continucare.config import get_settings
from continucare.presentation import actor_text, event_text
from continucare.ui import inject_global_styles, render_mode_badges


STAGES = (
    ("patient_message_submitted", "患者已提交"),
    ("extraction_completed", "证据已形成"),
    ("questionnaire_response_completed", "患者确认已发布"),
    ("manual_review_task_created", "人工复核任务已创建"),
    ("alert_created", "任务已创建"),
    ("nurse_alert_action", "护士已处理"),
    ("summary_generated", "简报已生成"),
    ("doctor_reviewed_summary", "医生已审阅"),
)


def _event_detail(event) -> str:
    details = event.details_json
    if event.event_type == "patient_message_submitted":
        return "患者原文已保存，等待形成结构化患者报告。"
    if event.event_type == "extraction_completed":
        count = len(details.get("observation_refs", []))
        return f"从患者原文形成 {count} 条带证据引用的患者报告事实。"
    if event.event_type == "semantic_analysis_completed":
        return (
            f"Care Agent 以 {details.get('mode', '—')} 模式提出候选；"
            f"生成 {details.get('clarification_count', 0)} 个澄清问题，"
            f"Safety Agent 拦截 {details.get('safety_violation_count', 0)} 项。"
        )
    if event.event_type == "semantic_candidate_patient_decision":
        return (
            f"患者决定：{details.get('decision', '—')}；"
            f"确认字段 {', '.join(details.get('confirmed_link_ids', [])) or '无'}。"
        )
    if event.event_type == "questionnaire_response_completed":
        return (
            f"患者确认后形成 {len(details.get('observation_refs', []))} 条最终 Observation；"
            "临床评估保持 not_assessed。"
        )
    if event.event_type == "manual_review_task_created":
        return "患者明确确认后创建常规护士人工复核 Task；未使用临床规则或风险分级。"
    if event.event_type == "risk_rule_matched":
        return f"规则命中，工作流优先级为 {details.get('severity', '—')}。"
    if event.event_type == "risk_evaluated":
        return "规则检查完成，本次无需创建额外医护任务。"
    if event.event_type == "alert_created":
        return (
            f"创建 {details.get('severity', '—')} 医护任务，"
            f"责任角色为 {details.get('owner_role', '—')}。"
        )
    if event.event_type in {"notification_mock_sent", "summary_notification_mock_sent"}:
        return "本地 Mock 通知已记录；没有真实发送到飞书。"
    if event.event_type == "nurse_alert_action":
        action = {
            "acknowledge": "确认收到任务",
            "escalate_to_doctor": "升级医生复核",
            "resolve": "记录结果并完成任务",
        }.get(details.get("action_type"), details.get("action_type", "更新任务"))
        return f"护士{action}：{details.get('note', '未填写说明')}"
    if event.event_type == "summary_generated":
        return (
            f"生成 {details.get('period_start', '—')} 至 "
            f"{details.get('period_end', '—')} 的证据简报。"
        )
    if event.event_type == "doctor_reviewed_summary":
        return "医生已审阅复诊前简报；系统未写入 EMR。"
    if event.event_type == "demo_reset":
        return "清空运行数据并重新初始化合成患者。"
    return "事件已写入本地审计日志。"


st.set_page_config(
    page_title="工作流证据链 · ContinuCare",
    page_icon="🧾",
    layout="wide",
    initial_sidebar_state="collapsed",
)
inject_global_styles(st)
st.title("工作流证据链")
st.error("仅使用合成数据 · 每一步业务结果都有对应的审计事件")

store = SQLiteStore(get_settings().db_path)
events = store.list_audit_events()
event_types = {event.event_type for event in events}
completed = sum(event_type in event_types for event_type, _ in STAGES)

manual_review_stages = (
    ("semantic_analysis_completed", "受控候选"),
    ("semantic_candidate_patient_decision", "患者确认"),
    ("questionnaire_response_completed", "最终证据"),
    ("manual_review_task_created", "护士任务"),
)
st.markdown("## 本次人工复核链路")
manual_columns = st.columns(len(manual_review_stages))
for column, (event_type, label) in zip(manual_columns, manual_review_stages):
    with column:
        if event_type in event_types:
            st.success(f"✓ {label}")
        else:
            st.info(f"○ {label}")
st.caption("只有患者明确确认后，最终 FHIR 证据与护士人工复核 Task 才会同时出现。")

st.markdown("## 这条随访故事走到哪一步")
st.progress(completed / len(STAGES), text=f"已完成 {completed}/{len(STAGES)} 个关键阶段")
stage_columns = st.columns(3)
for index, (event_type, label) in enumerate(STAGES):
    with stage_columns[index % 3]:
        if event_type in event_types:
            st.success(f"✓ {label}")
        else:
            st.info(f"○ {label}")

st.markdown("## 发生了什么")
st.caption("最近事件在前。主视图使用业务语言，技术字段和原始 JSON 可按需展开。")
if not events:
    st.info("暂无审计事件。")

for event in events:
    with st.container(border=True):
        label_col, time_col = st.columns([3, 1])
        with label_col:
            st.markdown(f"### {event_text(event.event_type)}")
            st.write(_event_detail(event))
        with time_col:
            st.caption(event.created_at)
            st.caption(f"执行者：{actor_text(event.actor_type)}")

        with st.expander("查看技术审计记录"):
            st.write(f"事件类型：`{event.event_type}`")
            st.write(f"事件 ID：`{event.event_id}`")
            st.write(f"实体：`{event.entity_type} / {event.entity_id}`")
            st.code(
                json.dumps(event.details_json, ensure_ascii=False, indent=2),
                language="json",
            )

with st.expander("演示模式说明"):
    render_mode_badges(st)
    st.caption("审计数据真实写入本地 SQLite；通知事件明确标注为 Mock。")
