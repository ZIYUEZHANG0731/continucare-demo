"""ContinuCare Streamlit entry point."""

from __future__ import annotations

import streamlit as st

from continucare.config import get_settings
from continucare.demo_data import SCENARIOS
from continucare.knowledge import load_builtin_bundle
from continucare.services.competition_demo import (
    CompetitionDemoStartError,
    CompetitionDemoStage,
    load_technical_demo_atomically,
    read_competition_demo,
    start_competition_demo,
)
from continucare.ui import (
    clear_demo_session_state,
    inject_global_styles,
    render_competition_progress,
)


st.set_page_config(
    page_title="ContinuCare Demo",
    page_icon="🫶",
    layout="wide",
    initial_sidebar_state="collapsed",
)

settings = get_settings()
inject_global_styles(st)
progress = read_competition_demo(settings.db_path)
try:
    load_builtin_bundle()
    knowledge_available = True
except Exception:
    knowledge_available = False

st.title("ContinuCare 连续照护比赛 Demo")
st.subheader("患者原话不丢失，人工门禁不绕过，复诊证据可逐项回放")
st.error("固定合成患者 · 非诊断 · 无临床分级 · 无实际发送 · 不是急救通道")

notice = st.session_state.pop("competition::notice", None)
if notice:
    st.success(notice)

with st.container(border=True):
    st.markdown('<div class="cc-kicker">M5-D · 主比赛故事</div>', unsafe_allow_html=True)
    heading, status = st.columns([3, 1])
    with heading:
        st.markdown("## 一键重置并开始完整合成故事")
        st.write("固定患者：**陈女士（合成）** · 固定原话：**“我今天拉肚子。”**")
        st.caption(
            "这一键只准备 Layer 3 未确认候选和导览；不会替患者确认、替护士处理/批准，"
            "也不会替医生生成、接受或修改简报。"
        )
    with status:
        if progress.stage == CompetitionDemoStage.STORY_COMPLETE:
            st.success("故事已完成")
        elif progress.generation:
            st.info("故事进行中")
        else:
            st.info("尚未开始")

    consent = st.checkbox(
        "我了解：开始或重新开始会替换本地合成 Demo 运行数据；不影响代码、Git 或 Knowledge manifests。",
        key="competition::reset_consent",
    )
    start_label = (
        "明确重新开始完整比赛 Demo"
        if progress.generation
        else "开始完整比赛 Demo"
    )
    if st.button(
        start_label,
        type="primary",
        width="stretch",
        disabled=not consent,
        key="competition::start",
    ):
        try:
            start_competition_demo(settings.db_path)
        except CompetitionDemoStartError as exc:
            st.error(str(exc))
        else:
            clear_demo_session_state(st)
            st.session_state["competition::notice"] = (
                "完整比赛 Demo 已准备：仅生成未确认候选；"
                "QuestionnaireResponse、Observation、Task、Communication、Summary 和 Alert 均为 0。"
            )
            st.rerun()

    st.caption(
        "主线固定使用 UnconfiguredModelAdapter + 本地确定性语义 Mock；"
        "不会读取环境中的真实模型配置，也不会访问外部 API。"
    )

render_competition_progress(st, progress)

if progress.generation:
    st.markdown("## 当前故事的安全计数")
    counts = st.columns(5)
    counts[0].metric("QR", progress.questionnaire_response_count)
    counts[1].metric("Observation", progress.observation_count)
    counts[2].metric("manual Task", progress.manual_task_count)
    counts[3].metric("Alert", progress.alert_count)
    counts[4].metric("获批 ClinicalRule", progress.approved_clinical_rule_count)
    st.caption(
        f"当前阶段：`{progress.stage.value}` · 临床评估：`not_assessed` · "
        "Communication 始终停在 preparation，ready-to-send 也不等于 sent。"
    )

st.markdown("## 按角色查看同一故事")
patient, nurse, doctor, audit = st.columns(4)
with patient:
    st.markdown("### 患者")
    st.write("主动确认候选，再查看 completed QR、final Observation 与 derivedFrom。")
    st.page_link("pages/1_patient_followup.py", label="患者随访 →", icon="💬")
with nurse:
    st.markdown("### 护士")
    st.write("显式接收、开始、记录受控结果，并人工批准中性草稿。")
    st.page_link("pages/2_nurse_risk_center.py", label="护士任务 →", icon="🧭")
with doctor:
    st.markdown("### 医生")
    st.write("只在明确动作后生成或刷新确定性证据简报。")
    st.page_link("pages/3_doctor_summary.py", label="医生简报 →", icon="📋")
with audit:
    st.markdown("### 审计")
    st.write("区分临床事实证据与只证明流程发生的 AuditEvent。")
    st.page_link("pages/4_audit_log.py", label="证据链 →", icon="🧾")

with st.container(border=True):
    st.markdown("### 独立查看腹泻 Knowledge Evidence")
    st.write("查看精确术语、Claim scope、supports / does_not_support、review 与 CoverageGap。")
    st.caption(
        "Knowledge 离线只读，不读取患者数据库，不授权 Observation、Task、Summary 或 ClinicalRule，"
        "也不参与故事完成判定。"
    )
    if knowledge_available:
        st.success("Knowledge CURRENT registry 可用 · review=not_assessed")
    else:
        st.warning("Knowledge CURRENT registry 暂不可用；不改变临床故事事实。")
    st.page_link(
        "pages/5_knowledge_evidence.py",
        label="查看腹泻采集依据 →",
        icon="📚",
    )

with st.expander("其他技术演示（会替换当前本地合成故事）"):
    st.warning("以下旧 fixture 与主比赛故事共用本地合成数据库，必须先勾选上方重置确认。")
    columns = st.columns(3)
    for column, (title, message_text) in zip(columns, SCENARIOS.items()):
        with column:
            st.markdown(f"**{title}**")
            st.code(message_text, language=None)
            if st.button(
                f"明确重置并载入 {title}",
                key=f"technical::{title}",
                width="stretch",
                disabled=not consent,
            ):
                try:
                    load_technical_demo_atomically(settings.db_path, title)
                except CompetitionDemoStartError as exc:
                    st.error(str(exc))
                else:
                    clear_demo_session_state(st)
                    st.session_state["competition::notice"] = (
                        f"已载入旧技术 fixture：{title}。它不是 M5-D 完整比赛主线。"
                    )
                    st.rerun()

with st.expander("能力边界与比赛诚实说明"):
    st.write("当前真实实现：本地 SQLite 持久化、FHIR R4 基础资源、人工门禁、版本化 Provenance 与审计。")
    st.write("当前 Mock / 合成：患者与医护身份、Layer 3 本地语义整理、所有演示数据。")
    st.write(
        "当前合同测试能力：可选飞书 Bot、Aily 与 Bitable 适配器已用 FakeTransport 验证；"
        "默认仍为 Mock/disabled，未做真实租户验证，也未发生任何外部调用。"
    )
    st.write("尚未实现：医院集成、真实患者、临床审批、自动风险分级和实际消息发送。")
