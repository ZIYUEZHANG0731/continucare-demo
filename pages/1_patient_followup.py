"""Synthetic patient follow-up intake page."""

from __future__ import annotations

import html
import json

import streamlit as st

from continucare.adapters.mock_extractor import MockExtractor
from continucare.adapters.mock_notifier import MockNotifier
from continucare.adapters.sqlite_store import SQLiteStore
from continucare.config import get_settings
from continucare.demo_data import DEMO_PATIENT_ID, SCENARIOS
from continucare.services.alerts import AlertService
from continucare.services.followup import FollowUpService
from continucare.services.extraction import ExtractionService
from continucare.services.risk_rules import EMERGENCY_NOTICE
from continucare.services.workflow import FollowUpWorkflow
from continucare.ui import inject_global_styles, render_mode_badges


def _highlight_evidence(message_text, observations):
    cursor = 0
    parts = []
    for item in sorted(observations, key=lambda value: value.evidence_start):
        if item.evidence_start < cursor:
            continue
        parts.append(html.escape(message_text[cursor : item.evidence_start]))
        parts.append(
            '<mark style="background:#fde68a;padding:0.1rem 0.2rem;border-radius:0.2rem">'
            + html.escape(message_text[item.evidence_start : item.evidence_end])
            + "</mark>"
        )
        cursor = item.evidence_end
    parts.append(html.escape(message_text[cursor:]))
    return (
        '<div style="line-height:2;padding:0.75rem;border:1px solid #e5e7eb;'
        'border-radius:0.5rem">'
        + "".join(parts)
        + "</div>"
    )


st.set_page_config(
    page_title="患者随访 · ContinuCare",
    page_icon="💬",
    layout="wide",
    initial_sidebar_state="collapsed",
)
inject_global_styles(st)
st.title("患者随访（合成数据）")
st.error("仅用于合成数据演示 · 不提供诊断、治疗或用药建议 · 不是急救通道")
st.info("本地稳定演示模式：规则/模板 Mock 抽取，不调用或冒充外部大模型。")
render_mode_badges(st)

store = SQLiteStore(get_settings().db_path)
patient = store.get_patient(DEMO_PATIENT_ID)
if patient is None:
    st.error("合成患者初始化失败，请返回首页重置 Demo。")
    st.stop()

left, right = st.columns([1, 2])
with left:
    st.markdown("#### 当前随访路径")
    st.write(patient.display_name)
    st.caption(f"患者 ID：{patient.patient_id}")
    st.write(f"Pathway：{patient.pathway_code}")
    st.write(f"下次复诊：{patient.next_visit_date}")
    st.success("合成患者")

with right:
    st.markdown("#### 快速填入合成场景")
    scenario_columns = st.columns(3)
    for column, (label, text) in zip(scenario_columns, SCENARIOS.items()):
        with column:
            if st.button(label, width="stretch"):
                st.session_state["followup_draft"] = text

    with st.form("patient_message_form", clear_on_submit=True):
        message_text = st.text_area(
            "今天的身体状态如何？",
            key="followup_draft",
            height=140,
            placeholder="请只输入演示用的合成内容",
        )
        submitted = st.form_submit_button("提交随访", type="primary")
    if submitted:
        try:
            workflow = FollowUpWorkflow(
                FollowUpService(store),
                ExtractionService(store, MockExtractor()),
                AlertService(store, MockNotifier()),
            )
            result = workflow.submit(DEMO_PATIENT_ID, message_text)
            st.session_state["latest_message_id"] = result.message.message_id
            st.success(
                "随访已处理："
                f"{len(result.extraction.observations)} 条 Observation / "
                f"工作流优先级 {result.decision.severity}"
            )
            if result.decision.severity == "L4":
                st.error(EMERGENCY_NOTICE)
        except ValueError as exc:
            st.warning(str(exc))

st.markdown("#### 已持久化的随访消息")
messages = store.list_messages(DEMO_PATIENT_ID)
if not messages:
    st.caption("暂无消息。提交后刷新或重启应用，记录仍会保留。")
for message in messages:
    with st.chat_message("user"):
        st.write(message.message_text)
        st.caption(f"{message.submitted_at} · 状态：{message.processing_status}")
        observations = store.list_observations_for_message(message.message_id)
        related_alert = next(
            (
                alert
                for alert in store.list_alerts(DEMO_PATIENT_ID)
                if message.message_id in alert.evidence_refs
            ),
            None,
        )
        if related_alert and related_alert.severity == "L4":
            st.error(EMERGENCY_NOTICE)
        if observations:
            st.markdown("**原文证据高亮**")
            st.markdown(
                _highlight_evidence(message.message_text, observations),
                unsafe_allow_html=True,
            )
            st.markdown("**结构化 Observation**")
            st.dataframe(
                [
                    {
                        "code": item.code,
                        "value": json.dumps(item.value, ensure_ascii=False),
                        "unit": item.unit or "—",
                        "confidence_tier": item.confidence_tier.value,
                        "evidence": item.evidence_text,
                        "span": f"{item.evidence_start}:{item.evidence_end}",
                    }
                    for item in observations
                ],
                hide_index=True,
                width="stretch",
            )
