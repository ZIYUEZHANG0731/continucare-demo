"""Shared visual safety cues and responsive layout rules."""

from __future__ import annotations

import html
from urllib.parse import urlparse


COMPETITION_STEP_LABELS = (
    ("candidate_ready", "候选已准备"),
    ("patient_confirmed", "患者已确认"),
    ("task_requested", "任务已创建"),
    ("nurse_received", "护士已接收"),
    ("nurse_in_progress", "护士处理中"),
    ("communication_pending", "草稿待批准"),
    ("doctor_brief_pending", "pending 简报"),
    ("communication_ready", "草稿已批准"),
    ("doctor_brief_ready", "ready 简报"),
)


def inject_global_styles(st) -> None:
    st.markdown(
        """
        <style>
        .block-container {max-width: 1180px; padding-top: 2rem; padding-bottom: 4rem;}
        h1, h2, h3 {
            overflow-wrap: anywhere;
            word-break: break-word;
            white-space: normal !important;
            max-width: 100%;
        }
        [data-testid="stHeadingWithActionElements"] {
            white-space: normal !important;
            min-width: 0;
        }
        [data-testid="stAlert"], [data-testid="stExpander"],
        [data-testid="stChatMessage"], [data-testid="stVerticalBlockBorderWrapper"] {
            overflow-wrap: anywhere;
        }
        [data-testid="stChatMessage"] {max-width: 680px;}
        code {white-space: pre-wrap !important; overflow-wrap: anywhere;}
        .cc-mode-chip {
            display:inline-block; padding:.35rem .7rem; border-radius:999px;
            background:#ecfeff; color:#155e75; border:1px solid #a5f3fc;
            font-size:.82rem; font-weight:650; margin:0 .35rem .35rem 0;
        }
        .cc-kicker {color:#0f766e;font-size:.78rem;font-weight:750;letter-spacing:.08em;text-transform:uppercase;}
        .cc-result-title {font-size:1.18rem;font-weight:750;margin:.15rem 0 .4rem;}
        .cc-muted {color:#64748b;font-size:.88rem;}
        .cc-quote {
            padding:.85rem 1rem; border-left:4px solid #14b8a6;
            background:#f0fdfa; border-radius:0 .55rem .55rem 0;
            font-size:1.02rem; line-height:1.75;
        }
        .cc-fact {
            display:inline-block; padding:.34rem .62rem; margin:.15rem .25rem .15rem 0;
            border-radius:.45rem; background:#fff7ed; border:1px solid #fed7aa;
            color:#9a3412; font-size:.86rem; font-weight:650;
        }
        .cc-chain-step {
            padding:.7rem .85rem; margin:.35rem 0; border-radius:.55rem;
            background:#f8fafc; border:1px solid #e2e8f0;
        }
        @media (max-width: 768px) {
            .block-container {padding: 1rem .85rem 3rem;}
            h1 {font-size: 1.75rem !important; line-height: 1.25 !important;}
            h2 {font-size: 1.35rem !important; line-height: 1.3 !important;}
            h3 {font-size: 1.12rem !important; line-height: 1.38 !important;}
            [data-testid="stHorizontalBlock"] {gap: .75rem;}
            [data-testid="stMetric"] {min-width: 0;}
            [data-testid="stButton"] button {min-height: 2.75rem;}
            [data-testid="stChatMessage"] {max-width: 100%;}
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_mode_badges(st) -> None:
    model_label = html.escape(semantic_model_label())
    st.markdown(
        f"""
        <span class="cc-mode-chip">本地稳定演示</span>
        <span class="cc-mode-chip">{model_label}</span>
        <span class="cc-mode-chip">Safety Agent v4 · 规则 + 可选 MiMo Critic</span>
        <span class="cc-mode-chip">SQLite 持久化</span>
        <span class="cc-mode-chip">外部适配器默认离线 · 未联调</span>
        """,
        unsafe_allow_html=True,
    )


def render_competition_progress(st, progress, *, show_next: bool = True) -> None:
    """Render persisted-fact milestones without caching a second UI state."""

    st.markdown("## 完整比赛 Demo 进度")
    completed = sum(
        bool(progress.milestones.get(step)) for step, _ in COMPETITION_STEP_LABELS
    )
    st.progress(
        completed / len(COMPETITION_STEP_LABELS),
        text=f"持久化事实已完成 {completed}/{len(COMPETITION_STEP_LABELS)} 项",
    )
    for offset in range(0, len(COMPETITION_STEP_LABELS), 3):
        row = COMPETITION_STEP_LABELS[offset : offset + 3]
        columns = st.columns(len(row))
        for column, (step, label) in zip(columns, row):
            with column:
                if progress.milestones.get(step):
                    st.success(f"✓ {label}")
                else:
                    st.info(f"○ {label}")
    if progress.integrity_issue:
        st.error(progress.integrity_issue)
    if progress.knowledge_available:
        st.caption("Knowledge CURRENT registry：可用（独立只读，不参与临床进度判定）")
    elif progress.knowledge_error:
        st.warning(progress.knowledge_error)
    if progress.is_terminal:
        if progress.stage.value == "story_complete":
            st.success(f"流程终态：{progress.terminal_reason}")
        else:
            st.warning(f"流程终态：{progress.terminal_reason}")
    if show_next and progress.generation and progress.is_terminal:
        with st.container(border=True):
            st.markdown(f"**{progress.next_label}**")
            st.caption(progress.next_help)
            st.page_link(
                progress.next_page,
                label=f"{progress.next_label} →",
                icon="🧾",
            )
            st.page_link(
                "app.py",
                label="返回首页（不会自动重新开始） →",
                icon="↩️",
            )
    elif show_next and progress.generation:
        with st.container(border=True):
            st.markdown(f"**推荐下一步：{progress.next_label}**")
            st.caption(progress.next_help)
            st.page_link(
                progress.next_page,
                label=f"{progress.next_label} →",
                icon="🧭",
            )
    render_integration_status(st)
    if progress.is_terminal:
        current_url = getattr(getattr(st, "context", None), "url", None)
        current_path = urlparse(current_url).path.rstrip("/") if current_url else ""
        is_home = bool(current_url) and current_path == ""
        is_audit = bool(current_url) and current_path.endswith("/audit_log")
        if not (is_home or is_audit):
            st.stop()


def render_integration_status(st) -> None:
    """Render one pure config projection; this performs no auth or health check."""

    from continucare.adapters.factory import read_adapter_statuses

    statuses = read_adapter_statuses()
    st.markdown("### 可选外部适配器状态")
    labels = {
        "feishu": ("飞书", "未进行真实租户联调"),
        "aily": ("Aily", "未进行真实 API 调用"),
        "bitable": ("Bitable", "未写入外部数据"),
    }
    for capability in ("feishu", "aily", "bitable"):
        status = statuses[capability]
        title, honest_boundary = labels[capability]
        if status.selected_mode == "mock":
            mode_text = "Mock fallback"
        elif status.selected_mode == "disabled":
            mode_text = "disabled"
        elif status.external_calls_allowed:
            mode_text = "test_tenant 已配置（本轮未验证）"
        else:
            mode_text = "test_tenant fail-closed"
        missing = (
            f" · 缺少配置：{', '.join(status.missing_config_keys)}"
            if status.missing_config_keys
            else ""
        )
        st.caption(
            f"{title}：{mode_text} / {honest_boundary}{missing} · "
            "live_tenant_verified=false · production_ready=false"
        )


def clear_demo_session_state(st) -> None:
    """Drop browser-only widget/navigation hints after an explicit reset."""

    prefixes = ("care::", "semantic::", "manual_", "competition::")
    exact = {"care_submission_notice"}
    for key in list(st.session_state):
        if key in exact or key.startswith(prefixes):
            del st.session_state[key]


def semantic_model_label() -> str:
    from continucare.care_agent.model_api import build_model_adapter

    adapter = build_model_adapter()
    if adapter.configured:
        return f"MiMo {adapter.config.model_name} 已启用"
    return "Care Agent 语义 Mock 回退"
