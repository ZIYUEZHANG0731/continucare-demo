"""Append-only audit event viewer."""

from __future__ import annotations

import json

import streamlit as st

from continucare.adapters.sqlite_store import SQLiteStore
from continucare.config import get_settings
from continucare.ui import inject_global_styles, render_mode_badges


st.set_page_config(
    page_title="审计日志 · ContinuCare",
    page_icon="🧾",
    layout="wide",
    initial_sidebar_state="collapsed",
)
inject_global_styles(st)
st.title("审计日志")
st.error("仅使用合成数据 · 事件按时间倒序展示")
render_mode_badges(st)

store = SQLiteStore(get_settings().db_path)
events = store.list_audit_events()
if not events:
    st.info("暂无审计事件。")
for event in events:
    with st.expander(
        f"{event.created_at} · {event.event_type} · {event.entity_type}",
        expanded=False,
    ):
        st.write(f"事件 ID：`{event.event_id}`")
        st.write(f"患者：`{event.patient_id or '—'}`")
        st.write(f"实体：`{event.entity_id}`")
        st.write(f"执行者：`{event.actor_type}`")
        st.code(json.dumps(event.details_json, ensure_ascii=False, indent=2), language="json")
