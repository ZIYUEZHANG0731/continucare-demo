"""ContinuCare Streamlit entry point."""

from __future__ import annotations

import streamlit as st

from continucare.config import get_settings
from continucare.db import initialize_database, reset_demo
from continucare.demo_data import SCENARIOS
from continucare.services.demo_scenarios import load_scenario
from continucare.ui import inject_global_styles, render_mode_badges


def _scenario_outcome(result) -> str:
    if result.decision.severity == "L4":
        return "已显示固定急救提示，并创建值班医护角色任务。"
    if result.decision.severity == "L2":
        return "已创建护士 24 小时复核任务；处理结果会进入医生简报。"
    return "已记录患者报告事实；当前不需要新增医护处理任务。"


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
st.subheader("最终交付不是一段 AI 回复，而是一份医生复诊前可直接审阅的证据简报")
st.error("仅使用合成数据 · 非诊断系统 · 不是医疗急救通道")

st.markdown("## 最终要得到什么")
st.caption("下面展示预置 L2 合成场景完成患者提交和护士处理后，医生会看到的最终结果。")
with st.container(border=True):
    st.markdown('<div class="cc-kicker">复诊前 30 秒简报 · 合成示例</div>', unsafe_allow_html=True)
    title_col, status_col = st.columns([3, 1])
    with title_col:
        st.markdown("### 陈女士（合成） · GLP1-14D")
        st.caption("14 天窗口 · 所有结论都可以展开回看患者原文和处理记录")
    with status_col:
        st.success("L2 已处理")

    focus, handled, confirm = st.columns(3)
    with focus:
        st.markdown("#### 本期重点")
        st.write("患者原文报告呕吐 1 次，并报告饮水意愿降低。")
        st.caption("来源：患者本人 · 可展开原文证据")
    with handled:
        st.markdown("#### 已完成处理")
        st.write("护士任务已确认并关闭，处理记录已进入简报。")
        st.caption("规则、Alert、处理动作完整留痕")
    with confirm:
        st.markdown("#### 复诊待确认")
        st.write("14 天窗口内有 13 天没有随访记录。")
        st.caption("系统不生成诊断或调整建议")

st.markdown("## 这份结果怎么形成")
step_one, step_two, step_three = st.columns(3)
with step_one:
    with st.container(border=True):
        st.markdown('<div class="cc-kicker">01 · 患者</div>', unsafe_allow_html=True)
        st.markdown("#### 提交院外状态")
        st.write("一句口语化描述被保存，并标出可核对的原文证据。")
with step_two:
    with st.container(border=True):
        st.markdown('<div class="cc-kicker">02 · 护士</div>', unsafe_allow_html=True)
        st.markdown("#### 完成医护任务")
        st.write("确定性规则创建任务，护士确认、升级或关闭并留下记录。")
with step_three:
    with st.container(border=True):
        st.markdown('<div class="cc-kicker">03 · 医生</div>', unsafe_allow_html=True)
        st.markdown("#### 审阅复诊简报")
        st.write("重点变化、处理结果、缺失数据和待确认项集中在一个首屏。")

st.markdown("## 按角色查看")
patient, nurse, doctor = st.columns(3)
with patient:
    st.markdown("### 患者端")
    st.write("看清楚本次提交记录了什么、是否创建任务、下一步由谁处理。")
    st.page_link("pages/1_patient_followup.py", label="进入患者随访 →", icon="💬")
with nurse:
    st.markdown("### 护士端")
    st.write("看清楚今天要处理什么、为什么进入队列、处理完成后留下什么结果。")
    st.page_link("pages/2_nurse_risk_center.py", label="进入护士风险中心 →", icon="🧭")
with doctor:
    st.markdown("### 医生端")
    st.write("30 秒读完重点、处理结果和待确认项，再按需展开证据。")
    st.page_link("pages/3_doctor_summary.py", label="进入医生复诊简报 →", icon="📋")

st.markdown("## 一键载入演示故事")
st.caption("每次会先清空本地运行数据，再载入一条合成患者故事。")
scenario_columns = st.columns(3)
for column, (title, message_text) in zip(scenario_columns, SCENARIOS.items()):
    with column:
        st.markdown(f"**{title}**")
        st.code(message_text, language=None)
        if st.button(f"重置并载入 {title}", key=f"load_{title}", width="stretch"):
            result = load_scenario(settings.db_path, title)
            st.success(f"{title}已载入：{_scenario_outcome(result)}")

st.divider()
if st.button("重置 Demo", type="secondary"):
    reset_demo(settings.db_path)
    st.success("本地合成 Demo 数据已重置。")

st.page_link("pages/4_audit_log.py", label="查看完整审计日志", icon="🧾")

with st.expander("演示模式与当前集成状态"):
    render_mode_badges(st)
    st.write("外部 AI：关闭 · 飞书集成：关闭 · 无 API Key 可完整运行")
    st.caption("飞书/Aily 当前未联调；第一版所有通知均为清楚标注的 Mock。")
