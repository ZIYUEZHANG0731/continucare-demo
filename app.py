"""ContinuCare Streamlit entry point."""

from __future__ import annotations

import streamlit as st

from continucare.config import get_settings
from continucare.db import initialize_database, reset_demo
from continucare.demo_data import SCENARIOS
from continucare.services.demo_scenarios import load_scenario
from continucare.ui import inject_global_styles, render_mode_badges


st.set_page_config(
    page_title="ContinuCare Demo",
    page_icon="🫶",
    layout="wide",
    initial_sidebar_state="collapsed",
)

settings = get_settings()
initialize_database(settings.db_path)
inject_global_styles(st)

st.title("ContinuCare 连续照护 Demo")
st.subheader("让院外随访成为复诊前可审阅、可追溯的证据")
st.error("仅使用合成数据 · 非诊断系统 · 不是医疗急救通道")

render_mode_badges(st)
st.caption("外部 AI：关闭 · 飞书集成：关闭 · 无 API Key 可完整运行")

patient, nurse, doctor = st.columns(3)
with patient:
    st.markdown("### 患者随访")
    st.write("提交合成院外状态，查看结构化观察与原文证据。")
    st.page_link("pages/1_patient_followup.py", label="进入患者随访 →", icon="💬")
with nurse:
    st.markdown("### 护士风险中心")
    st.write("查看确定性规则产生的工作流 Alert，并记录处理动作。")
    st.page_link("pages/2_nurse_risk_center.py", label="进入护士风险中心 →", icon="🧭")
with doctor:
    st.markdown("### 医生复诊简报")
    st.write("审阅带 evidence_refs 的 14 天摘要，不自动写入 EMR。")
    st.page_link("pages/3_doctor_summary.py", label="进入医生复诊简报 →", icon="📋")

st.markdown("### 预置演示场景")
scenario_columns = st.columns(3)
for column, (title, message_text) in zip(scenario_columns, SCENARIOS.items()):
    with column:
        st.markdown(f"**{title}**")
        st.code(message_text, language=None)
        if st.button(f"重置并载入 {title}", key=f"load_{title}", width="stretch"):
            result = load_scenario(settings.db_path, title)
            st.success(
                f"已载入：{result.decision.severity} / "
                f"{len(result.extraction.observations)} 条 Observation"
            )

st.divider()
if st.button("重置 Demo", type="secondary"):
    reset_demo(settings.db_path)
    st.success("本地合成 Demo 数据已重置。")

st.caption("飞书/Aily 当前未联调；第一版所有通知均为清楚标注的 Mock。")
st.page_link("pages/4_audit_log.py", label="查看完整审计日志", icon="🧾")
