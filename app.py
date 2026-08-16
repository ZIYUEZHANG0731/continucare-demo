"""ContinuCare Streamlit entry point."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT_TEXT = str(PROJECT_ROOT)
sys.path[:] = [item for item in sys.path if item != PROJECT_ROOT_TEXT]
sys.path.insert(0, PROJECT_ROOT_TEXT)

import continucare
import streamlit as st

if Path(continucare.__file__).resolve().parent.parent != PROJECT_ROOT:
    raise RuntimeError("Streamlit imported continucare from outside this project")

from continucare.config import get_settings
from continucare.db import initialize_database
from continucare.demo_data import SCENARIOS
from continucare.knowledge import load_builtin_bundle
from continucare.product_ui import inject_product_styles, render_demo_role_hub
from continucare.services.competition_demo import (
    CompetitionDemoStartError,
    load_technical_demo_atomically,
    read_competition_demo,
    reset_competition_demo,
)
from continucare.ui import (
    clear_demo_session_state,
    inject_global_styles,
    render_demo_guide,
    render_integration_status,
)


st.set_page_config(
    page_title="ContinuCare｜合成演示导览",
    layout="wide",
    initial_sidebar_state="collapsed",
)

settings = get_settings()
initialize_database(settings.db_path)
inject_global_styles(st)
inject_product_styles(st)
progress = read_competition_demo(settings.db_path)
try:
    load_builtin_bundle()
    knowledge_available = True
except Exception:
    knowledge_available = False

st.markdown(
    """
    <header class="cc-demo-header">
      <h1>ContinuCare <span>｜合成演示导览</span></h1>
      <p>角色切换仅用于演示，不代表已实现身份认证或权限控制。</p>
      <p class="cc-demo-boundary">不提供临床评估、诊断或风险分级；不会真实发送；业务外部系统为 Mock。患者在今日随访中发送合成回答时，系统默认调用豆包整理待确认内容。</p>
    </header>
    <p class="cc-demo-claim">患者说的话，一路跟到复诊速览。</p>
    """,
    unsafe_allow_html=True,
)

notice = st.session_state.pop("competition::notice", None)
if notice:
    st.info(notice)

render_demo_guide(
    st,
    progress,
    render_primary_action=None,
)

render_demo_role_hub(
    st,
    next_page=progress.next_page,
    next_label=progress.next_label,
)

with st.expander(
    "再用 20 秒看负向路径",
    expanded=False,
    key="cc_demo_negative_path",
):
    st.markdown(
        """
        <div class="cc-negative-path">
          <strong>患者全部拒绝</strong>
          <p>本轮结束 → 不产生患者确认记录、护士任务或医生速览 → 查看记录追溯</p>
          <p>本轮不能立即重新表述。</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown(
    """
    <section class="cc-independent-knowledge">
      <h2>独立只读资料</h2>
      <p>Knowledge 不携带患者上下文，不参与五步故事或本轮完成度。</p>
    </section>
    """,
    unsafe_allow_html=True,
)
st.page_link(
    "pages/5_knowledge_evidence.py",
    label="打开独立 Knowledge 资料库",
    width="content",
)
if not knowledge_available:
    st.caption("Knowledge CURRENT registry 暂不可用；这不改变本轮故事事实。")

with st.expander(
    "技术详情：外部适配器与当前配置",
    expanded=False,
    key="cc_demo_technical_details",
):
    st.caption(
        "以下内容只读取本地配置，不认证、不探活、不联网；Mock/disabled 状态按事实显示。"
    )
    render_integration_status(st)

has_existing_story = bool(progress.generation or progress.integrity_issue)
with st.expander(
    "管理本地演示数据",
    expanded=False,
    key="cc_demo_data_management",
):
    if has_existing_story:
        st.warning(
            "重置会替换本轮全部本地合成演示数据，包括记录追溯。"
            "代码和独立 Knowledge 资料不受影响。"
        )
    else:
        st.caption("当前没有已开始的五步故事；下方旧技术演示仍会替换本地合成数据。")

    replace_confirmed = st.checkbox(
        "我知道当前这轮合成演示记录会被替换。",
        key="competition::reset_consent",
    )
    if has_existing_story:
        with st.container(key="cc_demo_reset_action"):
            if st.button(
                "清空本轮并返回医生启动前",
                type="primary",
                width="stretch",
                disabled=not replace_confirmed,
                key="competition::reset_to_doctor",
            ):
                try:
                    reset_competition_demo(
                        settings.db_path,
                        expected_generation=progress.generation,
                    )
                except CompetitionDemoStartError as exc:
                    st.error(str(exc))
                else:
                    clear_demo_session_state(st)
                    st.session_state["competition::notice"] = (
                        "本轮已清空。请从医生页面确认并启动新的随访方案。"
                    )
                    st.rerun()

    st.divider()
    st.markdown("#### 旧技术演示")
    st.caption(
        "这些 fixtures 只用于次级技术检查，不属于五步故事；载入时同样会替换当前本地合成数据。"
    )
    scenario_title = st.selectbox(
        "选择旧技术 fixture",
        options=tuple(SCENARIOS),
        key="competition::technical_fixture",
    )
    st.code(SCENARIOS[scenario_title], language=None)
    if st.button(
        f"明确替换并载入 {scenario_title}",
        width="stretch",
        disabled=not replace_confirmed,
        key="competition::technical_load",
    ):
        try:
            load_technical_demo_atomically(settings.db_path, scenario_title)
        except CompetitionDemoStartError as exc:
            st.error(str(exc))
        else:
            clear_demo_session_state(st)
            st.session_state["competition::notice"] = (
                f"已载入旧技术演示“{scenario_title}”；它不属于五步故事。"
            )
            st.rerun()

st.caption("当前为合成产品原型，正在寻找设计合作方；不是临床试点。")
