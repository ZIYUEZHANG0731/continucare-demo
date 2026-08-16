"""Read-only hospital operations and governance surface for the synthetic MVP."""

from __future__ import annotations

import json
import sqlite3
import sys
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
from continucare.config import get_settings
from continucare.product_mvp import (
    ProductRole,
    build_operations_snapshot,
    build_product_context,
)
from continucare.product_ui import inject_product_styles, render_role_context
from continucare.services.competition_demo import (
    CompetitionDemoProgress,
    read_competition_demo,
)
from continucare.ui import inject_global_styles, render_integration_status


def _status_line(label: str, complete: bool, detail: str) -> None:
    icon = "✓" if complete else "○"
    tone = "#247052" if complete else "#68746e"
    st.markdown(
        f'<div style="padding:.65rem .75rem;border-bottom:1px solid #e5ebe8">'
        f'<strong style="color:{tone}">{icon} {label}</strong><br>'
        f'<span style="font-size:.82rem;color:#64716b">{detail}</span></div>',
        unsafe_allow_html=True,
    )


st.set_page_config(
    page_title="运营与治理总台 · ContinuCare",
    layout="wide",
    initial_sidebar_state="collapsed",
)
inject_global_styles(st)
inject_product_styles(st)
st.markdown(
    """
    <span class="cc-ops-shell" aria-hidden="true"></span>
    <style>
      .cc-ops-shell{display:none}
      .stApp:has(.cc-ops-shell) [data-testid="stSidebar"],
      .stApp:has(.cc-ops-shell) [data-testid="stHeader"]{display:none!important}
      .stApp:has(.cc-ops-shell) [data-testid="stAppViewContainer"]{margin-left:0!important}
      .stApp:has(.cc-ops-shell) .block-container{padding-top:1rem;max-width:1320px}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("运营与治理总台")
st.caption("只读运营面板 · 展示实际持久化状态，不执行患者、护士或医生动作")

settings = get_settings()
progress_error = None
try:
    progress = read_competition_demo(settings.db_path)
except (LookupError, OSError, ValueError, sqlite3.Error):
    progress = CompetitionDemoProgress(integrity_issue="运营来源完整性检查未通过")
    progress_error = "运营来源完整性检查未通过"

store = SQLiteStore(settings.db_path, initialize=False) if settings.db_path.is_file() else None
try:
    snapshot = build_operations_snapshot(store, progress)
except (LookupError, OSError, ValueError, KeyError, TypeError, sqlite3.Error):
    st.error("运营数据无法安全读取；总台已停止可信聚合与验收导出。")
    st.caption("页面不会补造患者登记、运营数字或可下载快照。")
    st.stop()

if progress_error or not snapshot.integrity_ok:
    st.error("完整性或冻结边界检查未通过；总台已停止可信聚合与验收导出。")
    st.caption(snapshot.integrity_message)
    st.warning(
        "页面不会显示患者登记、运营数字或可下载快照。请先修复数据边界，再重新载入。"
    )
    st.stop()

try:
    context = build_product_context(store, ProductRole.OPERATIONS)
except (LookupError, OSError, ValueError, KeyError, TypeError, sqlite3.Error):
    st.error("当前角色范围无法安全建立；总台已停止。")
    st.stop()
render_role_context(st, context)

metric_stage, metric_queue, metric_draft, metric_brief, metric_audit = st.columns(5)
metric_stage.metric("当前流程", snapshot.stage_label)
metric_queue.metric("待人工复核", snapshot.pending_manual_review_count)
metric_draft.metric("草稿待批准", snapshot.pending_draft_approval_count)
metric_brief.metric("医生速览版本", snapshot.doctor_brief_count)
metric_audit.metric("审计事件", snapshot.audit_count)

workflow_tab, quality_tab, governance_tab, acceptance_tab = st.tabs(
    ["流程运营", "模型与数据质量", "知识与治理", "集成与验收"]
)

with workflow_tab:
    left, right = st.columns([3, 2], gap="large")
    with left:
        st.subheader("本轮角色接力")
        _status_line(
            "患者确认",
            bool(progress.milestones.get("patient_confirmed")),
            "患者只确认记录含义，不确认诊断。",
        )
        _status_line(
            "护士人工复核",
            progress.manual_task_count > 0
            and snapshot.pending_manual_review_count == 0,
            "只记录例行核对结果，不形成风险等级。",
        )
        _status_line(
            "沟通文字批准",
            snapshot.communication_ready_count > 0,
            "最多到 ready-to-send；当前没有真实发送。",
        )
        _status_line(
            "医生复诊速览",
            snapshot.doctor_brief_count > 0,
            "只整理已确认事实与护理动作。",
        )
        if progress.generation:
            st.page_link(
                snapshot.next_page,
                label=f"继续当前流程：{snapshot.next_label}",
                width="stretch",
            )
        else:
            st.page_link("app.py", label="返回演示入口并开始一轮", width="stretch")
    with right:
        st.subheader("合成患者登记")
        patients = snapshot.patients
        if not patients:
            st.info("当前还没有合成患者。")
        else:
            st.dataframe(
                [
                    {
                        "患者": item.display_name,
                        "Pathway": item.pathway_code,
                        "状态": item.status,
                        "下次复诊": item.next_visit_date,
                        "数据": "合成",
                    }
                    for item in patients
                ],
                hide_index=True,
                width="stretch",
            )
        st.caption(
            "当前患者数来自本地 patients 表；没有临床风险排序，也没有跨患者推断。"
        )

with quality_tab:
    source_col, fact_col = st.columns(2, gap="large")
    with source_col:
        st.subheader("本轮语义来源")
        st.metric("候选生成", snapshot.model_source)
        st.caption(f"模型：{snapshot.model_name or '未记录/不适用'}")
        if snapshot.model_source in {"小米 MiMo API", "火山方舟豆包 API"}:
            st.success(f"持久化记录证明本轮主抽取来自{snapshot.model_source}。")
        elif progress.generation:
            st.info("本轮使用确定性离线引擎，没有把它描述为在线模型调用。")
        else:
            st.caption("开始一轮后才会显示真实来源。")
    with fact_col:
        st.subheader("事实产物")
        st.metric("Observation", snapshot.observation_count)
        st.metric("Communication ready-to-send", snapshot.communication_ready_count)
        st.metric("Alert", snapshot.alert_count)
        st.caption("Alert 必须保持为 0，除非未来存在经过批准的临床规则。")
    st.info(snapshot.integrity_message)

with governance_tab:
    release_col, boundary_col = st.columns(2, gap="large")
    with release_col:
        st.subheader("知识边界")
        st.markdown(f"**当前 Release**：`{snapshot.knowledge_release_id or '尚未绑定'}`")
        st.metric("获批临床规则", snapshot.approved_clinical_rule_count)
        st.page_link(
            "pages/5_knowledge_evidence.py",
            label="查看独立 Knowledge 资料库",
            width="stretch",
        )
    with boundary_col:
        st.subheader("当前硬边界")
        st.markdown(
            "- 临床风险：`not_assessed`\n"
            "- 真实消息发送：关闭\n"
            "- EMR 写入：关闭\n"
            "- 身份认证与生产权限：未接入\n"
            "- 所有患者数据：合成"
        )
    st.page_link(
        "pages/4_audit_log.py",
        label="打开完整记录追溯",
        width="stretch",
    )

with acceptance_tab:
    st.subheader("外部适配器状态")
    st.caption("这里只读取本地配置，不联网探活，也不会显示 API Key。")
    render_integration_status(st)
    st.divider()
    st.subheader("本轮验收快照")
    evidence = snapshot.evidence_payload()
    st.json(evidence, expanded=False)
    st.download_button(
        "下载机器可读验收快照",
        data=json.dumps(evidence, ensure_ascii=False, indent=2),
        file_name="continucare-synthetic-mvp-evidence.json",
        mime="application/json",
        width="stretch",
    )
    st.caption("快照不包含 API Key、患者真实信息或外部系统凭据。")

st.warning(
    "本总台不是临床指挥中心：不能修改患者事实、代替护士或医生操作，也不提供风险分级。"
)
