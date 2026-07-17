"""Shared visual safety cues and responsive layout rules."""

from __future__ import annotations


def inject_global_styles(st) -> None:
    st.markdown(
        """
        <style>
        .block-container {max-width: 1180px; padding-top: 2rem; padding-bottom: 4rem;}
        h1, h2, h3 {overflow-wrap: anywhere;}
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
        @media (max-width: 768px) {
            .block-container {padding: 1rem .85rem 3rem;}
            h1 {font-size: 1.9rem !important;}
            h2 {font-size: 1.45rem !important;}
            [data-testid="stButton"] button {min-height: 2.75rem;}
            [data-testid="stChatMessage"] {max-width: 100%;}
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_mode_badges(st) -> None:
    st.markdown(
        """
        <span class="cc-mode-chip">本地稳定演示</span>
        <span class="cc-mode-chip">规则/模板 Mock 抽取</span>
        <span class="cc-mode-chip">SQLite 持久化</span>
        <span class="cc-mode-chip">飞书通知 Mock · 未联调</span>
        """,
        unsafe_allow_html=True,
    )
