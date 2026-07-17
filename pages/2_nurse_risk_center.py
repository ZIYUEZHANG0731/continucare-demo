"""Auditable nurse work queue for deterministic workflow Alerts."""

from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from continucare.adapters.mock_notifier import MockNotifier
from continucare.adapters.sqlite_store import SQLiteStore
from continucare.config import get_settings
from continucare.models import AlertStatus
from continucare.services.alerts import AlertService
from continucare.ui import inject_global_styles, render_mode_badges


def _sla_text(due_at: str | None) -> str:
    if not due_at:
        return "未设置"
    due = datetime.fromisoformat(due_at)
    remaining = due - datetime.now(timezone.utc)
    seconds = int(remaining.total_seconds())
    if seconds <= 0:
        return "已到期 / 需立即处理"
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    return f"剩余 {hours} 小时 {minutes} 分钟"


st.set_page_config(
    page_title="护士风险中心 · ContinuCare",
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="collapsed",
)
inject_global_styles(st)
st.title("护士风险中心")
st.error("仅使用合成数据 · Alert 表示工作流优先级，不是诊断结论")
st.info("飞书状态：模拟飞书通知（Mock，未配置 Token、未完成真实联调）")
render_mode_badges(st)

store = SQLiteStore(get_settings().db_path)
service = AlertService(store, MockNotifier())
alerts = [
    alert
    for alert in store.list_alerts()
    if alert.status != AlertStatus.RESOLVED
]

metric_l4, metric_l2, metric_open = st.columns(3)
metric_l4.metric("L4", sum(alert.severity == "L4" for alert in alerts))
metric_l2.metric("L2", sum(alert.severity == "L2" for alert in alerts))
metric_open.metric("待处理", len(alerts))

if not alerts:
    st.success("当前没有待处理 Alert。可从患者页提交 L2 或 L4 合成场景。")

for alert in alerts:
    with st.container(border=True):
        heading, sla = st.columns([3, 1])
        with heading:
            st.subheader(f"{alert.severity} · {alert.title}")
            st.caption(f"Alert ID：{alert.alert_id}")
        with sla:
            st.metric("SLA", _sla_text(alert.sla_due_at))

        st.write(f"状态：`{alert.status.value}` · 责任角色：`{alert.owner_role}`")
        st.write(f"触发规则：`{alert.trigger_rule_id}`")
        st.write(f"触发原因：{alert.trigger_reason}")

        with st.expander("查看原文证据链", expanded=True):
            for ref in alert.evidence_refs:
                if ref.startswith("message_"):
                    message = store.get_message(ref)
                    st.write(f"**{ref}**：{message.message_text if message else '记录缺失'}")
                elif ref.startswith("observation_"):
                    observation = store.get_observation(ref)
                    if observation:
                        st.write(
                            f"**{ref}**：{observation.code} = {observation.value}；"
                            f"证据“{observation.evidence_text}”"
                        )

        st.warning(
            "📨 模拟飞书告警卡片（Mock，未真实发送）\n\n"
            f"{alert.severity} | {alert.title}\n\n"
            f"责任角色：{alert.owner_role} | SLA：{_sla_text(alert.sla_due_at)}"
        )

        note = st.text_area(
            "处理记录（关闭时必填）",
            key=f"note_{alert.alert_id}",
            placeholder="请记录合成演示中的处理过程，不要填写真实患者信息",
        )
        acknowledge, escalate, resolve = st.columns(3)
        try:
            if acknowledge.button(
                "确认收到", key=f"ack_{alert.alert_id}", width="stretch"
            ):
                service.acknowledge(alert.alert_id, note)
                st.rerun()
            if escalate.button(
                "升级医生", key=f"escalate_{alert.alert_id}", width="stretch"
            ):
                service.escalate(alert.alert_id, note)
                st.rerun()
            if resolve.button(
                "关闭", key=f"resolve_{alert.alert_id}", width="stretch"
            ):
                service.resolve(alert.alert_id, note)
                st.rerun()
        except ValueError as exc:
            st.error(str(exc))
