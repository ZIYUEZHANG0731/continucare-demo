"""Synthetic patient follow-up intake with outcome-first presentation."""

from __future__ import annotations

import html
import json

import streamlit as st

from continucare.adapters.mock_extractor import MockExtractor
from continucare.adapters.mock_notifier import MockNotifier
from continucare.adapters.sqlite_store import SQLiteStore
from continucare.config import get_settings
from continucare.demo_data import DEMO_PATIENT_ID, SCENARIOS
from continucare.presentation import (
    alert_next_step,
    alert_status_text,
    observation_text,
    owner_text,
)
from continucare.services.alerts import AlertService
from continucare.services.extraction import ExtractionService
from continucare.services.followup import FollowUpService
from continucare.services.workflow import FollowUpWorkflow
from continucare.ui import inject_global_styles, render_mode_badges


def _highlight_evidence(message_text, observations):
    cursor = 0
    parts = []
    for item in sorted(observations, key=lambda value: value.evidence_start):
        if item.evidence_start < cursor:
            continue
        parts.append(html.escape(message_text[cursor : item.evidence_start]))
        parts.append(
            '<mark style="background:#fde68a;padding:0.1rem 0.2rem;border-radius:0.2rem">'
            + html.escape(message_text[item.evidence_start : item.evidence_end])
            + "</mark>"
        )
        cursor = item.evidence_end
    parts.append(html.escape(message_text[cursor:]))
    return '<div class="cc-quote">' + "".join(parts) + "</div>"


def _related_alert(store, patient_id, message_id):
    return next(
        (
            alert
            for alert in store.list_alerts(patient_id)
            if message_id in alert.evidence_refs
        ),
        None,
    )


st.set_page_config(
    page_title="患者随访 · ContinuCare",
    page_icon="💬",
    layout="wide",
    initial_sidebar_state="collapsed",
)
inject_global_styles(st)
st.title("患者随访（合成数据）")
st.error("仅用于合成数据演示 · 不提供诊断、治疗或用药建议 · 不是急救通道")

store = SQLiteStore(get_settings().db_path)
patient = store.get_patient(DEMO_PATIENT_ID)
if patient is None:
    st.error("合成患者初始化失败，请返回首页重置 Demo。")
    st.stop()

submission_notice = st.session_state.pop("submission_notice", None)
if submission_notice:
    st.success(submission_notice)

messages = store.list_messages(DEMO_PATIENT_ID)
latest_message = messages[0] if messages else None

st.markdown("## 本次随访产生了什么结果")
if latest_message is None:
    st.info("尚未提交随访。提交后，这里会直接显示记录结果、是否创建医护任务以及下一步。")
else:
    latest_observations = store.list_observations_for_message(latest_message.message_id)
    latest_alert = _related_alert(store, DEMO_PATIENT_ID, latest_message.message_id)

    with st.container(border=True):
        st.markdown('<div class="cc-kicker">本次处理结果</div>', unsafe_allow_html=True)
        summary_col, status_col = st.columns([3, 1])
        with summary_col:
            if latest_alert and latest_alert.status.value == "resolved":
                st.markdown("### 医护任务已完成，处理结果已进入医生简报")
            elif latest_alert and latest_alert.severity == "L4":
                st.markdown("### 已显示固定急救提示，并创建值班医护任务")
            elif latest_alert:
                st.markdown("### 已创建护士 24 小时复核任务")
            else:
                st.markdown("### 已保存本次患者报告和可确认的标准化事实")
        with status_col:
            if latest_alert:
                st.metric("工作流状态", f"{latest_alert.severity} · {alert_status_text(latest_alert)}")
            else:
                st.metric("临床分级", "未评估")

        st.markdown("**患者本次原话**")
        st.markdown(
            f'<div class="cc-quote">{html.escape(latest_message.message_text)}</div>',
            unsafe_allow_html=True,
        )

        st.markdown("**系统记录的患者报告事实**")
        if latest_observations:
            fact_html = "".join(
                f'<span class="cc-fact">{html.escape(observation_text(item))}</span>'
                for item in latest_observations
            )
            st.markdown(fact_html, unsafe_allow_html=True)
        else:
            st.caption("本次原文已保存，但没有形成预置演示范围内的结构化事实。")

        st.markdown("**下一步**")
        if latest_alert and latest_alert.status.value == "resolved":
            st.success(
                "医护任务已经完成并留痕。医生生成复诊前简报后，可以看到患者原话、"
                "规则触发原因和最终处理记录。"
            )
            st.caption(f"最终处理结果：{latest_alert.resolution_reason or '任务已完成'}")
        elif latest_alert:
            st.info(alert_next_step(latest_alert))
            st.caption(
                f"当前责任角色：{owner_text(latest_alert.owner_role)} · "
                f"任务状态：{alert_status_text(latest_alert)}"
            )
        else:
            st.info(
                "本次报告已进入随访时间线。当前没有获批的自动临床规则，"
                "因此系统不生成风险等级或医护任务。"
            )

        with st.expander("为什么会得到这个结果？查看原文证据与结构化记录"):
            st.markdown("**原文中被采用的证据**")
            st.markdown(
                _highlight_evidence(latest_message.message_text, latest_observations),
                unsafe_allow_html=True,
            )
            st.dataframe(
                [
                    {
                        "患者报告事实": observation_text(item),
                        "FHIR code": item.code,
                        "code system": item.code_system,
                        "结构化值": json.dumps(item.value, ensure_ascii=False),
                        "原文证据": item.evidence_text,
                        "证据位置": f"{item.evidence_start}:{item.evidence_end}",
                    }
                    for item in latest_observations
                ],
                hide_index=True,
                width="stretch",
            )
            st.caption(
                "FHIR Observation 与原始 QuestionnaireResponse 分开保存，"
                "并通过 derivedFrom 建立来源关系。"
            )

st.markdown("## 提交一条新的合成随访")
profile, intake = st.columns([1, 2])
with profile:
    with st.container(border=True):
        st.markdown("#### 当前路径")
        st.write(patient.display_name)
        st.write(f"Pathway：{patient.pathway_code}")
        st.write(f"下次复诊：{patient.next_visit_date}")
        st.caption(f"患者 ID：{patient.patient_id} · 合成患者")

with intake:
    scenario_columns = st.columns(3)
    for column, (label, text) in zip(scenario_columns, SCENARIOS.items()):
        with column:
            if st.button(label, width="stretch"):
                st.session_state["followup_draft"] = text

    with st.form("patient_message_form", clear_on_submit=True):
        message_text = st.text_area(
            "今天的身体状态如何？",
            key="followup_draft",
            height=120,
            placeholder="请只输入演示用的合成内容",
        )
        submitted = st.form_submit_button("提交并查看结果", type="primary")
    if submitted:
        try:
            workflow = FollowUpWorkflow(
                FollowUpService(store),
                ExtractionService(store, MockExtractor()),
                AlertService(store, MockNotifier()),
            )
            result = workflow.submit(DEMO_PATIENT_ID, message_text)
            if result.alert:
                notice = "提交成功：已记录患者报告，并创建护士复核任务。"
            else:
                notice = (
                    "提交成功：FHIR 原始回答已保存；当前未启用自动临床分级。"
                )
            st.session_state["submission_notice"] = notice
            st.rerun()
        except ValueError as exc:
            st.warning(str(exc))

if len(messages) > 1:
    with st.expander(f"查看更早的随访记录（{len(messages) - 1} 条）"):
        for message in messages[1:]:
            st.markdown(f"- {message.submitted_at} · {message.message_text}")

with st.expander("演示模式说明"):
    render_mode_badges(st)
    st.caption("抽取为本地规则/模板 Mock；所有数据均为合成数据。")
