"""Doctor-facing, evidence-bound pre-visit summary."""

from __future__ import annotations

import json

import streamlit as st

from continucare.adapters.mock_extractor import MockExtractor
from continucare.adapters.mock_notifier import MockNotifier
from continucare.adapters.sqlite_store import SQLiteStore
from continucare.config import get_settings
from continucare.demo_data import DEMO_PATIENT_ID
from continucare.services.summaries import SummaryService
from continucare.ui import inject_global_styles, render_mode_badges


SECTION_LABELS = {
    "overview": "14 天概览",
    "key_changes": "关键变化",
    "alerts_and_actions": "Alert 与处理结果",
    "patient_questions": "患者主要问题",
    "missing_data": "缺失数据",
    "doctor_to_confirm": "医生待确认",
}


def _render_evidence(store, refs):
    for ref in refs:
        if ref.startswith("message_"):
            item = store.get_message(ref)
            detail = item.message_text if item else "记录缺失"
        elif ref.startswith("observation_"):
            item = store.get_observation(ref)
            detail = (
                f"{item.code} = {item.value}；原文证据“{item.evidence_text}”"
                if item
                else "记录缺失"
            )
        elif ref.startswith("alert_"):
            item = store.get_alert(ref)
            detail = (
                f"{item.severity} / {item.status.value} / {item.trigger_reason}"
                if item
                else "记录缺失"
            )
        elif ref.startswith("action_"):
            item = store.get_alert_action(ref)
            detail = f"{item.action_type}：{item.note}" if item else "记录缺失"
        elif ref.startswith("P-"):
            item = store.get_patient(ref)
            detail = (
                f"合成患者路径 {item.pathway_code}，下次复诊 {item.next_visit_date}"
                if item
                else "记录缺失"
            )
        else:
            detail = "未知证据类型"
        st.write(f"- `{ref}` — {detail}")


st.set_page_config(
    page_title="医生复诊简报 · ContinuCare",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="collapsed",
)
inject_global_styles(st)
st.title("医生复诊简报")
st.error("仅使用合成数据 · 不生成诊断、治疗或用药建议")
st.warning("默认不写入 EMR。医生审阅只记录本地审计事件。")
st.info("摘要由本地模板生成；医生通知为模拟飞书通知（Mock，未联调）。")
render_mode_badges(st)

store = SQLiteStore(get_settings().db_path)
service = SummaryService(store, MockExtractor(), MockNotifier())
patient = store.get_patient(DEMO_PATIENT_ID)

header, action = st.columns([3, 1])
with header:
    st.subheader(patient.display_name if patient else DEMO_PATIENT_ID)
    if patient:
        st.caption(
            f"Pathway {patient.pathway_code} · 下次复诊 {patient.next_visit_date}"
        )
with action:
    if st.button("生成 / 刷新 14 天简报", type="primary", width="stretch"):
        service.generate(DEMO_PATIENT_ID)
        st.rerun()

summary = store.get_latest_summary(DEMO_PATIENT_ID)
if summary is None:
    st.info("尚无简报。先在患者页提交合成场景，再点击生成。")
    st.stop()

status_col, period_col = st.columns(2)
status_col.metric("审阅状态", summary.status)
period_col.metric("窗口", f"{summary.period_start} → {summary.period_end}")

observations = store.list_observations(DEMO_PATIENT_ID)
st.markdown("### Observation 趋势")
if observations:
    st.dataframe(
        [
            {
                "时间": item.effective_time,
                "字段": item.code,
                "值": json.dumps(item.value, ensure_ascii=False),
                "原文证据": item.evidence_text,
            }
            for item in observations
        ],
        hide_index=True,
        width="stretch",
    )
else:
    st.caption("当前窗口没有 Observation。")

content = summary.summary_json
for field_name, label in SECTION_LABELS.items():
    st.markdown(f"### {label}")
    items = getattr(content, field_name)
    if not items:
        st.caption("本期无有证据支持的条目。")
        continue
    for index, item in enumerate(items, start=1):
        st.write(f"{index}. {item.text}")
        with st.expander(f"Evidence · {len(item.evidence_refs)} 条", expanded=False):
            _render_evidence(store, item.evidence_refs)

if summary.reviewed_at:
    st.success(f"医生已审阅：{summary.reviewed_at}（未写入 EMR）")
elif st.button("标记已审阅", width="stretch"):
    service.review(summary.summary_id)
    st.rerun()
