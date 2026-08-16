"""Doctor-only product chrome for the Streamlit review workspace.

This module intentionally owns presentation only.  It does not introduce a
second workflow state machine and never mutates persisted clinical facts.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime
from textwrap import dedent
from typing import Any

from continucare.product_mvp import ProductContext


_STAGE_LABELS = {
    "not_started": "待启动",
    "plan_activated": "等待患者提交",
    "candidate_ready": "等待患者确认",
    "candidate_unsure": "患者暂未确认",
    "candidate_rejected": "患者已停止本轮",
    "patient_collecting": "患者填写中",
    "patient_review_ready": "等待患者提交",
    "patient_confirmed": "患者已确认",
    "task_requested": "等待护士接手",
    "nurse_received": "护士已接手",
    "nurse_in_progress": "护士核对中",
    "communication_pending": "护理记录已完成",
    "doctor_brief_pending": "待生成速览",
    "communication_ready": "待医生复核",
    "doctor_brief_ready": "速览已生成",
    "story_complete": "本轮已完成",
    "task_rejected": "任务已停止",
    "task_cancelled": "任务已取消",
    "task_failed": "任务未完成",
    "task_entered_in_error": "记录异常",
}


def doctor_stage_label(progress: Any) -> str:
    """Return a stable clinician-facing label for a persisted demo stage."""

    stage = getattr(getattr(progress, "stage", None), "value", None)
    if stage is None:
        stage = str(getattr(progress, "stage", "not_started"))
    return _STAGE_LABELS.get(stage, "状态待核对")


def render_doctor_header(st: Any, context: ProductContext, progress: Any) -> None:
    """Render a production-style doctor workspace shell and patient header."""

    patient = context.patient
    patient_name = patient.display_name if patient is not None else "合成患者尚未载入"
    pathway = patient.pathway_code if patient is not None else "—"
    next_visit = patient.next_visit_date if patient is not None else "—"
    patient_status = patient.status if patient is not None else "未载入"
    initial = patient_name[:1] if patient_name else "患"
    stage_label = doctor_stage_label(progress)
    enrollment_date = patient.enrollment_date if patient is not None else "—"
    today = datetime.now().strftime("%Y年%m月%d日")

    st.markdown(
        f"""
        <span class="cc-doctor-v3" aria-hidden="true"></span>
        <aside class="cc-doctor-sidebar" aria-label="医生工作台导航">
          <div class="cc-doctor-brand cc-doctor-brand--sidebar">
            <span class="cc-doctor-brand__mark" aria-hidden="true">C</span>
            <span><strong>ContinuCare</strong><small>连续照护平台</small></span>
          </div>
          <nav class="cc-doctor-nav">
            <p>工作台</p>
            <a class="is-active" href="/doctor_summary"><span aria-hidden="true">⌂</span>患者随访</a>
            <a href="#cc-doctor-metrics"><span aria-hidden="true">⌁</span>健康趋势</a>
            <a href="#cc-doctor-sources"><span aria-hidden="true">◫</span>数据来源</a>
            <p>协作</p>
            <a href="/audit_log"><span aria-hidden="true">✓</span>操作记录</a>
            <a href="/knowledge_evidence"><span aria-hidden="true">◇</span>知识库</a>
          </nav>
          <div class="cc-doctor-sidebar__account">
            <span aria-hidden="true">医</span>
            <div><strong>医生工作台</strong><small>当前账号在线</small></div>
          </div>
        </aside>
        <header class="cc-doctor-topbar" aria-label="医生工作台页眉">
          <div class="cc-doctor-breadcrumb">
            <span>患者管理</span><b>/</b><strong>随访详情</strong>
          </div>
          <div class="cc-doctor-topbar__right">
            <span>{html.escape(today)}</span>
            <span class="cc-doctor-topbar__avatar" aria-hidden="true">医</span>
          </div>
        </header>
        <section class="cc-doctor-hero" aria-labelledby="cc-doctor-page-title">
          <div>
            <p class="cc-doctor-eyebrow">患者随访</p>
            <h1 id="cc-doctor-page-title">患者随访详情</h1>
            <p>查看近期上报、健康趋势与护理协作进展</p>
          </div>
          <div class="cc-doctor-stage"><span aria-hidden="true"></span>{html.escape(stage_label)}</div>
        </section>
        <section class="cc-doctor-patient" aria-label="当前患者范围">
          <div class="cc-doctor-patient__identity">
            <span class="cc-doctor-avatar" aria-hidden="true">{html.escape(initial)}</span>
            <div>
              <div class="cc-doctor-patient__name">
                <strong>{html.escape(patient_name)}</strong>
                <span>演示数据</span>
              </div>
              <p>患者编号 · {html.escape(getattr(patient, "patient_id", "—") if patient is not None else "—")}</p>
            </div>
          </div>
          <dl class="cc-doctor-patient__meta">
            <div><dt>随访路径</dt><dd>{html.escape(pathway)}</dd></div>
            <div><dt>加入日期</dt><dd>{html.escape(enrollment_date)}</dd></div>
            <div><dt>下次复诊</dt><dd>{html.escape(next_visit)}</dd></div>
            <div><dt>患者状态</dt><dd>{html.escape(patient_status)}</dd></div>
          </dl>
        </section>
        """,
        unsafe_allow_html=True,
    )


def render_activation_steps(st: Any) -> None:
    """Explain the doctor-owned activation without implying clinical approval."""

    st.markdown(
        """
        <div class="cc-doctor-activation-copy">
          <p class="cc-doctor-section-kicker">本轮随访</p>
          <h2>确认并启动 14 天随访</h2>
          <p>患者会在自己的页面完成固定合成反馈；后续事实仍须由患者明确确认。</p>
        </div>
        <ol class="cc-doctor-stepper" aria-label="随访流程">
          <li class="is-current"><span>1</span><div><strong>医生启动</strong><small>锁定随访版本</small></div></li>
          <li><span>2</span><div><strong>患者确认</strong><small>确认自己的表达</small></div></li>
          <li><span>3</span><div><strong>护士核对</strong><small>完成人工记录核对</small></div></li>
          <li><span>4</span><div><strong>医生复核</strong><small>查看复诊速览</small></div></li>
        </ol>
        <div class="cc-doctor-safety-note">
          <span aria-hidden="true">i</span>
          <p><strong>操作边界</strong>这是随访路径激活，不是处方、治疗方案或风险判断；临床评估保持为未评估。</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_waiting_panel(st: Any) -> None:
    """Render the activated-but-waiting state as an intentional product state."""

    st.markdown(
        """
        <section class="cc-doctor-waiting" aria-live="polite">
          <div class="cc-doctor-waiting__icon" aria-hidden="true"><span></span></div>
          <p class="cc-doctor-section-kicker">随访已启动</p>
          <h2>正在等待患者提交反馈</h2>
          <p>患者提交并确认后，护理核对与医生速览会依次出现在本工作台。</p>
          <div class="cc-doctor-waiting__track" aria-hidden="true">
            <span class="is-done"></span><span class="is-current"></span><span></span><span></span>
          </div>
          <small>医生启动完成 · 下一步由患者操作</small>
        </section>
        """,
        unsafe_allow_html=True,
    )


def render_workspace_heading(st: Any, *, summary_version: str | None) -> None:
    """Introduce the review workspace without manufacturing clinical facts."""

    version = f"速览 v{summary_version}" if summary_version else "实时汇总"
    st.markdown(
        f"""
        <section class="cc-doctor-workspace-heading">
          <div>
            <p class="cc-doctor-section-kicker">随访摘要</p>
            <h2>本轮随访概览</h2>
            <p>快速查看患者最新上报与护理协作进展</p>
          </div>
          <span>{html.escape(version)}</span>
        </section>
        """,
        unsafe_allow_html=True,
    )


@dataclass(frozen=True, slots=True)
class DoctorMetricSource:
    source_id: str
    observation_reference: str
    response_reference: str
    effective_time: str
    original_text: str
    metric_id: str
    evidence_claim_ids: tuple[str, ...]
    knowledge_release_id: str


@dataclass(frozen=True, slots=True)
class DoctorMetricPoint:
    timestamp: str
    label: str
    value: float
    display: str
    source_id: str
    status_name: str | None = None


@dataclass(frozen=True, slots=True)
class DoctorMetricCard:
    metric_key: str
    title: str
    chart_kind: str
    chart_label: str
    unit: str
    summary: str
    caption: str
    points: tuple[DoctorMetricPoint, ...]
    source_ids: tuple[str, ...]
    primary_status: DoctorMetricPoint | None = None


@dataclass(frozen=True, slots=True)
class DoctorMetricDashboard:
    cards: tuple[DoctorMetricCard, ...]
    sources: tuple[DoctorMetricSource, ...]


@dataclass(frozen=True, slots=True)
class DoctorOverviewSentence:
    text: str
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DoctorFollowupOverview:
    title: str
    period_label: str
    intro: str
    record_day_count: int
    metric_count: int
    source_count: int
    sentences: tuple[DoctorOverviewSentence, ...]
    latest_status: DoctorOverviewSentence | None
    missing_metrics: tuple[str, ...]


_METRIC_SPECS = {
    "body_weight": {
        "title": "体重趋势",
        "chart_kind": "line",
        "chart_label": "体重（kg）",
        "unit": "kg",
        "metric_ids": ("body_weight",),
        "codes": ("29463-7",),
        "caption": "按记录时间展示；只反映已保存数值，不解释变化原因。",
    },
    "nausea": {
        "title": "恶心",
        "chart_kind": "bar",
        "chart_label": "程度等级",
        "unit": "1 轻度 · 2 中度 · 3 重度",
        "metric_ids": ("nausea_severity_current",),
        "codes": ("81660-3",),
        "caption": "等级来自患者主动选择；柱高仅用于展示，不代表临床评分。",
    },
    "vomiting_count": {
        "title": "24 小时呕吐次数",
        "chart_kind": "bar",
        "chart_label": "次数",
        "unit": "次/24h",
        "metric_ids": ("vomiting_count_24h",),
        "codes": ("94070-0",),
        "caption": "按患者报告的 24 小时窗口展示。",
    },
    "fluid_intake": {
        "title": "液体摄入",
        "chart_kind": "line",
        "chart_label": "摄入量（mL/24h）",
        "unit": "mL/24h",
        "metric_ids": ("fluid_intake_24h_estimated",),
        "codes": ("75301-2",),
        "caption": "按患者估计值展示；不把估计值解释为临床结论。",
    },
}

_SEVERITY_VALUES = {
    "LA6752-5": (1.0, "轻度"),
    "LA6751-7": (2.0, "中度"),
    "LA6750-9": (3.0, "重度"),
}

_BOOLEAN_LABELS = {
    "422587007": "恶心",
    "21522001": "腹痛",
    "386705008": "头晕",
}


def _metric_key(observation: Any) -> str | None:
    metric_id = str(
        getattr(getattr(observation, "evidence", None), "metric_id", "") or ""
    )
    code = str(getattr(observation, "code", "") or "")
    for key, spec in _METRIC_SPECS.items():
        if metric_id in spec["metric_ids"] or code in spec["codes"]:
            return key
    return None


def _time_label(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return value or "时间未记录"
    return parsed.strftime("%m-%d")


def _normalized_value(observation: Any, metric_key: str) -> tuple[float, str] | None:
    value = getattr(observation, "value", None)
    if metric_key == "nausea":
        return _SEVERITY_VALUES.get(str(value))
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if metric_key == "body_weight":
        unit = str(getattr(observation, "unit", "") or "")
        if unit in {"g", "gram"}:
            number /= 1000
        elif unit in {"[lb_av]", "lb", "lbs"}:
            number *= 0.45359237
        return number, f"{number:g} kg"
    if metric_key == "vomiting_count":
        return number, f"{number:g} 次/24h"
    if metric_key == "fluid_intake":
        return number, f"{number:g} mL/24h"
    return number, f"{number:g}"


def _source_for(observation: Any, source_id: str) -> DoctorMetricSource:
    evidence = getattr(observation, "evidence", None)
    observation_id = str(getattr(observation, "observation_id", "未记录"))
    response_id = str(
        getattr(evidence, "questionnaire_response_id", None)
        or getattr(observation, "message_id", "未记录")
    )
    return DoctorMetricSource(
        source_id=source_id,
        observation_reference=f"Observation/{observation_id}",
        response_reference=f"QuestionnaireResponse/{response_id}",
        effective_time=str(getattr(observation, "effective_time", "") or "时间未记录"),
        original_text=str(getattr(evidence, "evidence_text", "") or "原文未记录"),
        metric_id=str(getattr(evidence, "metric_id", "") or "未绑定（补充上报/原型）"),
        evidence_claim_ids=tuple(getattr(evidence, "evidence_claim_ids", ()) or ()),
        knowledge_release_id=str(
            getattr(evidence, "knowledge_release_id", "") or "未绑定（补充上报/原型）"
        ),
    )


def _card_summary(
    title: str,
    points: tuple[DoctorMetricPoint, ...],
) -> tuple[str, tuple[str, ...]]:
    if not points:
        return f"当前没有可展示的{title}记录。", ()
    latest = points[-1]
    source_ids = tuple(dict.fromkeys(point.source_id for point in points))
    if len(points) == 1:
        return (
            f"当前仅有 1 条记录，最近值为 {latest.display}；不足以判断趋势。",
            source_ids,
        )
    return (
        f"共有 {len(points)} 条记录，最近值为 {latest.display}；图表只展示记录变化。",
        source_ids,
    )


def build_doctor_metric_dashboard(
    observations: tuple[Any, ...] | list[Any],
) -> DoctorMetricDashboard:
    """Project charts and citations from persisted Observations without inference."""

    ordered = sorted(
        observations,
        key=lambda item: (
            str(getattr(item, "effective_time", "") or ""),
            str(getattr(item, "observation_id", "") or ""),
        ),
    )
    source_by_observation: dict[str, DoctorMetricSource] = {}
    source_id_by_observation: dict[str, str] = {}
    for observation in ordered:
        observation_id = str(getattr(observation, "observation_id", "") or "")
        if not observation_id or observation_id in source_by_observation:
            continue
        source_id = f"S{len(source_by_observation) + 1}"
        source_id_by_observation[observation_id] = source_id
        source_by_observation[observation_id] = _source_for(observation, source_id)

    grouped: dict[str, list[DoctorMetricPoint]] = {
        key: [] for key in _METRIC_SPECS
    }
    status_points: list[DoctorMetricPoint] = []
    nausea_presence: DoctorMetricPoint | None = None
    for observation in ordered:
        observation_id = str(getattr(observation, "observation_id", "") or "")
        source_id = source_id_by_observation.get(observation_id)
        if source_id is None:
            continue
        effective_time = str(getattr(observation, "effective_time", "") or "")
        metric_key = _metric_key(observation)
        if metric_key is not None:
            normalized = _normalized_value(observation, metric_key)
            if normalized is None:
                continue
            number, display = normalized
            grouped[metric_key].append(
                DoctorMetricPoint(
                    timestamp=effective_time,
                    label=_time_label(effective_time),
                    value=number,
                    display=display,
                    source_id=source_id,
                )
            )
            continue
        value = getattr(observation, "value", None)
        if not isinstance(value, bool):
            continue
        code = str(getattr(observation, "code", "") or "")
        status_name = _BOOLEAN_LABELS.get(
            code,
            str(getattr(observation, "code_display", "症状状态") or "症状状态"),
        )
        point = DoctorMetricPoint(
            timestamp=effective_time,
            label=_time_label(effective_time),
            value=1.0 if value else 0.0,
            display="有" if value else "无",
            source_id=source_id,
            status_name=status_name,
        )
        if code == "422587007":
            nausea_presence = point
        else:
            status_points.append(point)

    cards: list[DoctorMetricCard] = []
    for metric_key, spec in _METRIC_SPECS.items():
        points = tuple(grouped[metric_key])
        summary, source_ids = _card_summary(str(spec["title"]), points)
        primary_status = None
        if metric_key == "nausea" and nausea_presence is not None:
            primary_status = nausea_presence
            source_ids = tuple(
                dict.fromkeys((*source_ids, nausea_presence.source_id))
            )
            if nausea_presence.value <= 0:
                summary = "最近一次记录：无恶心；程度不适用。"
            elif points:
                summary = f"最近一次记录：有恶心，程度{points[-1].display}。"
            else:
                summary = "最近一次记录：有恶心；程度尚未记录。"
        cards.append(
            DoctorMetricCard(
                metric_key=metric_key,
                title=str(spec["title"]),
                chart_kind=str(spec["chart_kind"]),
                chart_label=str(spec["chart_label"]),
                unit=str(spec["unit"]),
                summary=summary,
                caption=str(spec["caption"]),
                points=points,
                source_ids=source_ids,
                primary_status=primary_status,
            )
        )

    if status_points:
        latest_by_status: dict[str, DoctorMetricPoint] = {}
        for point in status_points:
            latest_by_status[point.status_name or "症状状态"] = point
        latest_points = tuple(latest_by_status.values())
        summary = "最近记录：" + "；".join(
            f"{point.status_name}{point.display}" for point in latest_points
        ) + "。"
        cards.append(
            DoctorMetricCard(
                metric_key="symptom_status",
                title="症状状态",
                chart_kind="status",
                chart_label="患者自报状态",
                unit="有 / 无",
                summary=summary,
                caption="状态来自患者确认记录；不等于临床风险判断。",
                points=latest_points,
                source_ids=tuple(point.source_id for point in latest_points),
            )
        )

    used_source_ids = {
        source_id for card in cards for source_id in card.source_ids
    }
    sources = tuple(
        source
        for source in source_by_observation.values()
        if source.source_id in used_source_ids
    )
    return DoctorMetricDashboard(cards=tuple(cards), sources=sources)


def _source_links(source_ids: tuple[str, ...]) -> str:
    """Keep evidence visible without filling the card with dozens of links."""

    if not source_ids:
        return ""
    first = source_ids[0]
    label = first if len(source_ids) == 1 else f"{first}–{source_ids[-1]}"
    title = "、".join(source_ids)
    return (
        '<a class="cc-doctor-citation" '
        f'href="#cc-doctor-source-{html.escape(first)}" '
        f'title="包含来源：{html.escape(title)}">'
        f'[{html.escape(label)}]</a>'
    )


def _number_text(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _production_summary(card: DoctorMetricCard) -> str:
    if card.metric_key == "nausea" and card.source_ids:
        return card.summary
    points = card.points
    if not points:
        return "暂无记录"
    latest = points[-1]
    if len(points) == 1:
        return f"最近记录为 {latest.display}"
    if card.metric_key in {"body_weight", "fluid_intake"}:
        delta = latest.value - points[0].value
        if abs(delta) < 1e-9:
            change = "与首次记录持平"
        else:
            direction = "增加" if delta > 0 else "减少"
            unit = "kg" if card.metric_key == "body_weight" else "mL/24h"
            change = f"较首次记录{direction} {_number_text(abs(delta))} {unit}"
        return f"最近记录 {latest.display}，{change}"
    if card.metric_key == "nausea":
        highest = max(points, key=lambda item: item.value)
        return f"最近记录为{latest.display}，期间最高为{highest.display}"
    if card.metric_key == "vomiting_count":
        positive_days = sum(1 for point in points if point.value > 0)
        return (
            f"最近记录 {latest.display}，"
            f"{len(points)} 条记录中 {positive_days} 条高于 0"
        )
    return card.summary


def doctor_metric_summary(card: DoctorMetricCard) -> str:
    """Return the concise, product-facing summary for a metric card."""

    return _production_summary(card)


def _parsed_date(timestamp: str):
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        return None


def _period_label(dates: tuple[Any, ...]) -> str:
    if not dates:
        return "暂无上报"
    first, last = dates[0], dates[-1]
    if first == last:
        return first.strftime("%Y年%m月%d日")
    if first.year == last.year:
        return f"{first.strftime('%Y年%m月%d日')}—{last.strftime('%m月%d日')}"
    return f"{first.strftime('%Y年%m月%d日')}—{last.strftime('%Y年%m月%d日')}"


def _latest_status_sentence(card: DoctorMetricCard) -> str:
    positive = [
        point.status_name or "症状"
        for point in card.points
        if point.value > 0
    ]
    negative = [
        point.status_name or "症状"
        for point in card.points
        if point.value <= 0
    ]
    parts: list[str] = []
    if positive:
        parts.append("有" + "、".join(positive))
    if negative:
        parts.append("无" + "、".join(negative))
    return "最新症状记录：" + "；".join(parts) if parts else card.summary


def build_doctor_followup_overview(
    dashboard: DoctorMetricDashboard,
) -> DoctorFollowupOverview:
    """Build a patient-agnostic summary from whatever metric records exist."""

    dates = tuple(
        sorted(
            {
                parsed
                for source in dashboard.sources
                if (parsed := _parsed_date(source.effective_time)) is not None
            }
        )
    )
    record_day_count = len(dates)
    title = (
        "随访小结"
        if record_day_count == 0
        else "当日随访小结"
        if record_day_count == 1
        else f"{record_day_count} 天随访小结"
    )
    metric_cards = tuple(
        card
        for card in dashboard.cards
        if card.chart_kind != "status" and card.source_ids
    )
    status_card = next(
        (
            card
            for card in dashboard.cards
            if card.chart_kind == "status" and card.points
        ),
        None,
    )
    missing_metrics = tuple(
        card.title
        for card in dashboard.cards
        if card.chart_kind != "status" and not card.source_ids
    )
    if record_day_count:
        intro = f"{_period_label(dates)}共记录 {record_day_count} 天"
        if metric_cards:
            intro += f"，形成 {len(metric_cards)} 个记录要点。"
        elif status_card is not None:
            intro += "，已形成最新症状状态。"
        else:
            intro += "。"
    else:
        intro = "当前还没有可用于生成随访小结的患者上报。"
    sentences = tuple(
        DoctorOverviewSentence(
        text=f"{card.title}：{_production_summary(card).rstrip('。')}。",
            source_ids=card.source_ids,
        )
        for card in metric_cards
    )
    latest_status = (
        DoctorOverviewSentence(
            text=_latest_status_sentence(status_card) + "。",
            source_ids=status_card.source_ids,
        )
        if status_card is not None
        else None
    )
    return DoctorFollowupOverview(
        title=title,
        period_label=_period_label(dates),
        intro=intro,
        record_day_count=record_day_count,
        metric_count=len(metric_cards),
        source_count=len(dashboard.sources),
        sentences=sentences,
        latest_status=latest_status,
        missing_metrics=missing_metrics,
    )


def render_doctor_followup_overview(
    st: Any,
    overview: DoctorFollowupOverview,
) -> None:
    """Render the generated follow-up summary as the primary doctor overview."""

    summary_rows = "".join(
        '<li><span aria-hidden="true"></span><p>'
        f'{html.escape(sentence.text)} {_source_links(sentence.source_ids)}'
        "</p></li>"
        for sentence in overview.sentences
    )
    if not summary_rows:
        summary_rows = '<li class="is-empty"><p>暂无可总结的趋势指标</p></li>'
    status = ""
    if overview.latest_status is not None:
        status = (
            '<div class="cc-doctor-overview-status"><span>最新状态</span><p>'
            f'{html.escape(overview.latest_status.text)} '
            f'{_source_links(overview.latest_status.source_ids)}</p></div>'
        )
    missing = ""
    if overview.missing_metrics:
        chips = "".join(
            f"<span>{html.escape(label)}</span>" for label in overview.missing_metrics
        )
        missing = (
            '<div class="cc-doctor-overview-missing"><strong>本期未记录</strong>'
            f"<div>{chips}</div></div>"
        )
    st.markdown(
        dedent(
            f"""
            <section class="cc-doctor-overview" aria-labelledby="cc-doctor-overview-title">
              <header>
                <div><p>数据汇总</p><h3 id="cc-doctor-overview-title">{html.escape(overview.title)}</h3></div>
                <span>{html.escape(overview.period_label)}</span>
              </header>
              <p class="cc-doctor-overview-intro">{html.escape(overview.intro)}</p>
              <dl class="cc-doctor-overview-stats">
                <div><dt>记录天数</dt><dd>{overview.record_day_count}</dd></div>
                <div><dt>记录要点</dt><dd>{overview.metric_count}</dd></div>
                <div><dt>来源记录</dt><dd>{overview.source_count}</dd></div>
              </dl>
              <div class="cc-doctor-overview-body">
                <h4>关键变化</h4><ul>{summary_rows}</ul>
              </div>
              {status}
              {missing}
            </section>
            """
        ).strip(),
        unsafe_allow_html=True,
    )


def _line_chart_svg(card: DoctorMetricCard) -> str:
    points = card.points
    if not points:
        return '<div class="cc-doctor-chart-empty"><p>暂无数据</p></div>'
    width, height = 640.0, 226.0
    left, right, top, bottom = 44.0, 622.0, 20.0, 178.0
    values = [point.value for point in points]
    low, high = min(values), max(values)
    padding = max((high - low) * 0.18, max(abs(high), 1.0) * 0.025)
    low, high = low - padding, high + padding
    x_step = (right - left) / max(len(points) - 1, 1)

    def x_at(index: int) -> float:
        return left + index * x_step if len(points) > 1 else (left + right) / 2

    def y_at(value: float) -> float:
        return top + (high - value) / max(high - low, 1.0) * (bottom - top)

    coords = [(x_at(index), y_at(point.value)) for index, point in enumerate(points)]
    polyline = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    area = f"{left:.1f},{bottom:.1f} {polyline} {right:.1f},{bottom:.1f}"
    grid = "".join(
        f'<line x1="{left}" y1="{top + index * (bottom-top)/3:.1f}" x2="{right}" y2="{top + index * (bottom-top)/3:.1f}" />'
        for index in range(4)
    )
    dots = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.2"><title>{html.escape(point.label)} · {html.escape(point.display)}</title></circle>'
        for (x, y), point in zip(coords, points)
    )
    labels = "".join(
        f'<text x="{x:.1f}" y="207" text-anchor="middle">{html.escape(point.label)}</text>'
        for x, point in zip((item[0] for item in coords), points)
    )
    return dedent(f"""
    <div class="cc-doctor-chart" role="img" aria-label="{html.escape(card.title)}">
      <svg viewBox="0 0 {width:g} {height:g}" preserveAspectRatio="none">
        <g class="cc-chart-grid">{grid}</g>
        <polygon class="cc-chart-area" points="{area}" />
        <polyline class="cc-chart-line" points="{polyline}" />
        <g class="cc-chart-dots">{dots}</g>
        <g class="cc-chart-labels">{labels}</g>
      </svg>
    </div>
    """).strip()


def _bar_chart_svg(card: DoctorMetricCard) -> str:
    points = card.points
    if not points:
        return '<div class="cc-doctor-chart-empty"><p>暂无数据</p></div>'
    width, height = 640.0, 226.0
    left, right, top, bottom = 40.0, 624.0, 22.0, 178.0
    maximum = max(max(point.value for point in points), 1.0)
    slot = (right - left) / len(points)
    bar_width = min(slot * 0.52, 52.0)
    grid = "".join(
        f'<line x1="{left}" y1="{top + index * (bottom-top)/3:.1f}" x2="{right}" y2="{top + index * (bottom-top)/3:.1f}" />'
        for index in range(4)
    )
    bars: list[str] = []
    for index, point in enumerate(points):
        x = left + slot * index + (slot - bar_width) / 2
        bar_height = point.value / maximum * (bottom - top)
        y = bottom - bar_height
        value_label = point.display if card.metric_key == "nausea" else _number_text(point.value)
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{bar_height:.1f}" rx="7">'
            f'<title>{html.escape(point.label)} · {html.escape(point.display)}</title></rect>'
            f'<text class="cc-chart-value" x="{x + bar_width/2:.1f}" y="{max(y - 7, 13):.1f}" text-anchor="middle">{html.escape(value_label)}</text>'
            f'<text x="{x + bar_width/2:.1f}" y="207" text-anchor="middle">{html.escape(point.label)}</text>'
        )
    return dedent(f"""
    <div class="cc-doctor-chart" role="img" aria-label="{html.escape(card.title)}">
      <svg viewBox="0 0 {width:g} {height:g}" preserveAspectRatio="none">
        <g class="cc-chart-grid">{grid}</g>
        <g class="cc-chart-bars">{''.join(bars)}</g>
      </svg>
    </div>
    """).strip()


def _metric_card_markup(card: DoctorMetricCard) -> str:
    if card.chart_kind == "status":
        items = "".join(
            '<div class="cc-doctor-status-item">'
            f'<span>{html.escape(point.status_name or "症状")}</span>'
            f'<strong class="is-{"yes" if point.value else "no"}">{html.escape(point.display)}</strong>'
            f'<small>{html.escape(point.label)} · '
            f'<a href="#cc-doctor-source-{html.escape(point.source_id)}">[{html.escape(point.source_id)}]</a></small>'
            "</div>"
            for point in card.points
        )
        return dedent(f"""
        <article class="cc-doctor-metric-card cc-doctor-metric-card--status">
          <header><div><p>最新状态</p><h3>{html.escape(card.title)}</h3></div><span>{html.escape(card.unit)}</span></header>
          <div class="cc-doctor-status-grid">{items}</div>
          <footer><p>{html.escape(card.summary)} {_source_links(card.source_ids)}</p></footer>
        </article>
        """).strip()

    latest = card.points[-1] if card.points else card.primary_status
    latest_value = latest.display if latest is not None else "—"
    if latest is not None and card.metric_key == "nausea" and card.primary_status is latest:
        latest_value = f"{latest.display}恶心"
    latest_time = f"最近更新 {latest.label}" if latest is not None else "等待患者上报"
    chart = _line_chart_svg(card) if card.chart_kind == "line" else _bar_chart_svg(card)
    return dedent(f"""
    <article class="cc-doctor-metric-card">
      <header>
        <div><p>健康指标</p><h3>{html.escape(card.title)}</h3></div>
        <span>{len(card.points)} 条记录</span>
      </header>
      <div class="cc-doctor-metric-latest">
        <strong>{html.escape(latest_value)}</strong><small>{html.escape(latest_time)}</small>
      </div>
      {chart}
      <footer><p>{html.escape(_production_summary(card))} {_source_links(card.source_ids)}</p></footer>
    </article>
    """).strip()


def render_doctor_metric_dashboard(st: Any, dashboard: DoctorMetricDashboard) -> None:
    """Render a self-contained dashboard without Streamlit chart chrome."""

    cards = "".join(_metric_card_markup(card) for card in dashboard.cards)
    source_items = "".join(
        dedent(f"""
        <article id="cc-doctor-source-{html.escape(source.source_id)}" class="cc-doctor-metric-source">
          <header><strong>[{html.escape(source.source_id)}]</strong><span>{html.escape(source.observation_reference)}</span></header>
          <p>{html.escape(source.original_text)}</p>
          <dl>
            <div><dt>患者上报</dt><dd>{html.escape(source.response_reference)}</dd></div>
            <div><dt>记录时间</dt><dd>{html.escape(source.effective_time)}</dd></div>
            <div><dt>指标标识</dt><dd>{html.escape(source.metric_id)}</dd></div>
            <div><dt>知识库版本</dt><dd>{html.escape(source.knowledge_release_id)}</dd></div>
          </dl>
        </article>
        """).strip()
        for source in dashboard.sources
    )
    sources = ""
    if dashboard.sources:
        sources = dedent(f"""
        <details id="cc-doctor-sources" class="cc-doctor-sources">
          <summary><span><strong>数据来源</strong><small>查看患者原始上报与标准化记录</small></span><b>{len(dashboard.sources)} 条</b></summary>
          <div class="cc-doctor-source-list">{source_items}</div>
        </details>
        """).strip()
    st.markdown(
        dedent(f"""
        <section id="cc-doctor-metrics" class="cc-doctor-metrics">
          <header class="cc-doctor-metric-heading">
            <div><p class="cc-doctor-section-kicker">健康趋势</p><h2>近期指标</h2><p>患者每日上报记录</p></div>
            <span>近 7 天</span>
          </header>
          <div class="cc-doctor-metric-grid">{cards}</div>
          {sources}
        </section>
        """).strip(),
        unsafe_allow_html=True,
    )


DOCTOR_SURFACE_STYLE = """
<style>
:root {
  --doc-ink:#182230; --doc-muted:#667085; --doc-line:#e4e9ef;
  --doc-bg:#f4f7f9; --doc-card:#ffffff; --doc-soft:#eef5f6;
  --doc-teal:#176b68; --doc-teal-dark:#0d4f4d; --doc-blue:#2f6fed;
  --doc-warm:#fff8eb; --doc-danger:#b42318;
}
.cc-doctor-v3 {display:none;}
.stApp:has(.cc-doctor-v3) {background:var(--doc-bg); color:var(--doc-ink);}
.stApp:has(.cc-doctor-v3) [data-testid="stSidebar"],
.stApp:has(.cc-doctor-v3) [data-testid="stHeader"] {display:none !important;}
.stApp:has(.cc-doctor-v3) [data-testid="stAppViewContainer"] {margin-left:0 !important;}
.stApp:has(.cc-doctor-v3) .block-container {max-width:1240px; padding:0 1.5rem 4rem;}
.stApp:has(.cc-doctor-v3) > div {font-family:Inter,"PingFang SC","Microsoft YaHei",sans-serif;}
.stApp:has(.cc-doctor-v3) [class*="st-key-cc_doctor_legacy_title"] {display:none !important;}
.cc-doctor-topbar {
  height:68px; display:flex; align-items:center; justify-content:space-between;
  border-bottom:1px solid var(--doc-line); margin:0 calc(50% - 50vw) 0;
  padding:0 max(1.5rem, calc((100vw - 1240px)/2 + 1.5rem)); background:#fff;
}
.cc-doctor-brand {display:flex;align-items:center;gap:.7rem;color:var(--doc-ink);}
.cc-doctor-brand__mark {
  width:34px;height:34px;display:grid;place-items:center;border-radius:10px;
  background:var(--doc-teal);color:#fff;font-weight:800;font-size:1rem;
  box-shadow:0 6px 16px rgba(23,107,104,.18);
}
.cc-doctor-brand strong {display:block;font-size:.96rem;letter-spacing:.01em;line-height:1.15;}
.cc-doctor-brand small {display:block;color:var(--doc-muted);font-size:.68rem;margin-top:.15rem;}
.cc-doctor-role {
  display:flex;align-items:center;gap:.45rem;padding:.42rem .7rem;border:1px solid #cfe0df;
  border-radius:999px;background:#f3f9f8;color:var(--doc-teal-dark);font-size:.78rem;font-weight:700;
}
.cc-doctor-role span,.cc-doctor-stage span {width:7px;height:7px;border-radius:50%;background:#19a277;box-shadow:0 0 0 3px #d9f3ea;}
.cc-doctor-hero {display:flex;justify-content:space-between;align-items:end;gap:1rem;padding:2.25rem 0 1.15rem;}
.cc-doctor-eyebrow,.cc-doctor-section-kicker {margin:0 0 .35rem;color:var(--doc-teal);font-size:.7rem;font-weight:800;letter-spacing:.11em;text-transform:uppercase;}
.cc-doctor-hero h1 {margin:0 !important;padding:0 !important;border:0 !important;font-size:2.15rem !important;line-height:1.15 !important;letter-spacing:-.035em !important;color:var(--doc-ink) !important;}
.cc-doctor-hero p:last-child {margin:.5rem 0 0;color:var(--doc-muted);font-size:.92rem;}
.cc-doctor-stage {display:flex;align-items:center;gap:.55rem;padding:.52rem .75rem;border:1px solid var(--doc-line);border-radius:10px;background:#fff;color:#344054;font-size:.78rem;font-weight:700;box-shadow:0 3px 12px rgba(16,24,40,.04);}
.cc-doctor-patient {display:flex;align-items:center;justify-content:space-between;gap:1.25rem;padding:1rem 1.15rem;background:var(--doc-card);border:1px solid var(--doc-line);border-radius:16px;box-shadow:0 8px 24px rgba(16,24,40,.045);}
.cc-doctor-patient__identity {display:flex;align-items:center;gap:.8rem;min-width:16rem;}
.cc-doctor-avatar {width:42px;height:42px;display:grid;place-items:center;border-radius:12px;background:#dcefee;color:var(--doc-teal-dark);font-size:1rem;font-weight:800;}
.cc-doctor-patient__name {display:flex;align-items:center;gap:.55rem;}
.cc-doctor-patient__name strong {font-size:1rem;color:var(--doc-ink);}
.cc-doctor-patient__name span {padding:.18rem .4rem;border-radius:5px;background:#f2f4f7;color:#667085;font-size:.66rem;font-weight:700;}
.cc-doctor-patient__identity p {margin:.22rem 0 0;color:var(--doc-muted);font-size:.73rem;}
.cc-doctor-patient__meta {display:grid;grid-template-columns:repeat(3,minmax(6.4rem,1fr));gap:0;margin:0;}
.cc-doctor-patient__meta div {padding:.05rem 1.1rem;border-left:1px solid var(--doc-line);}
.cc-doctor-patient__meta dt {color:var(--doc-muted);font-size:.68rem;margin-bottom:.2rem;}
.cc-doctor-patient__meta dd {margin:0;color:#344054;font-size:.82rem;font-weight:700;}
.cc-doctor-boundary {margin:.7rem 0 .25rem !important;padding:.55rem .7rem !important;border:0 !important;border-radius:8px;background:#edf5ff;color:#315273 !important;font-size:.78rem !important;line-height:1.55 !important;}
.stApp:has(.cc-doctor-v3) [class*="st-key-cc_doctor_refresh_bar"] {margin:.35rem 0 1rem;}
.stApp:has(.cc-doctor-v3) [class*="st-key-cc_doctor_refresh_shared"] button {min-height:34px !important;padding:.25rem .7rem !important;border:1px solid var(--doc-line) !important;border-radius:8px !important;background:#fff !important;color:#475467 !important;font-size:.75rem !important;box-shadow:none !important;}
.stApp:has(.cc-doctor-v3) [class*="st-key-cc_doctor_refresh_shared"] button p {font-size:0 !important;}
.stApp:has(.cc-doctor-v3) [class*="st-key-cc_doctor_refresh_shared"] button p::after {content:"刷新数据";font-size:.75rem;}
.st-key-cc_doctor_activation_card,.st-key-cc_doctor_waiting_card {margin-top:.8rem;padding:1.35rem 1.45rem 1.45rem;border:1px solid var(--doc-line);border-radius:18px;background:#fff;box-shadow:0 12px 30px rgba(16,24,40,.055);}
.cc-doctor-activation-copy h2,.cc-doctor-waiting h2 {margin:0;color:var(--doc-ink);font-size:1.35rem;letter-spacing:-.02em;}
.cc-doctor-activation-copy > p:last-child {margin:.45rem 0 0;color:var(--doc-muted);font-size:.88rem;}
.cc-doctor-stepper {display:grid;grid-template-columns:repeat(4,1fr);gap:.75rem;list-style:none;margin:1.35rem 0;padding:0;}
.cc-doctor-stepper li {position:relative;display:flex;gap:.65rem;align-items:center;padding:.75rem;border:1px solid var(--doc-line);border-radius:12px;background:#fafbfc;}
.cc-doctor-stepper li > span {width:28px;height:28px;display:grid;place-items:center;flex:none;border-radius:8px;background:#eef1f5;color:#667085;font-size:.72rem;font-weight:800;}
.cc-doctor-stepper li strong,.cc-doctor-stepper li small {display:block;}
.cc-doctor-stepper li strong {font-size:.78rem;color:#344054;}
.cc-doctor-stepper li small {margin-top:.15rem;color:#98a2b3;font-size:.66rem;}
.cc-doctor-stepper li.is-current {border-color:#a8cfcd;background:#f1f8f7;}
.cc-doctor-stepper li.is-current > span {background:var(--doc-teal);color:#fff;}
.cc-doctor-safety-note {display:flex;gap:.7rem;align-items:start;padding:.75rem .85rem;border-radius:10px;background:var(--doc-warm);color:#7a4e13;}
.cc-doctor-safety-note > span {width:20px;height:20px;display:grid;place-items:center;flex:none;border-radius:50%;background:#f0b44d;color:#fff;font-size:.7rem;font-weight:800;}
.cc-doctor-safety-note p {margin:0;font-size:.76rem;line-height:1.55;}
.stApp:has(.cc-doctor-v3) [class*="st-key-cc_doctor_activate_plan"] button {min-height:46px !important;border:0 !important;border-radius:10px !important;background:var(--doc-teal) !important;color:#fff !important;font-weight:750 !important;box-shadow:0 8px 18px rgba(23,107,104,.18) !important;}
.cc-doctor-waiting {padding:.75rem 0;text-align:center;}
.cc-doctor-waiting__icon {width:54px;height:54px;display:grid;place-items:center;margin:0 auto 1rem;border-radius:16px;background:#e8f5f3;}
.cc-doctor-waiting__icon span {width:16px;height:16px;border-radius:50%;background:var(--doc-teal);box-shadow:0 0 0 7px rgba(23,107,104,.13);}
.cc-doctor-waiting > p:not(.cc-doctor-section-kicker) {max-width:34rem;margin:.55rem auto;color:var(--doc-muted);font-size:.86rem;}
.cc-doctor-waiting__track {display:grid;grid-template-columns:repeat(4,1fr);gap:.35rem;max-width:25rem;margin:1.25rem auto .55rem;}
.cc-doctor-waiting__track span {height:5px;border-radius:10px;background:#e4e7ec;}
.cc-doctor-waiting__track .is-done {background:var(--doc-teal);}.cc-doctor-waiting__track .is-current {background:#9dc9c7;}
.cc-doctor-waiting small {color:#98a2b3;font-size:.7rem;}
.cc-doctor-workspace-heading {display:flex;align-items:end;justify-content:space-between;gap:1rem;margin:1.5rem 0 .75rem;}
.cc-doctor-workspace-heading h2 {margin:0;color:var(--doc-ink);font-size:1.45rem;letter-spacing:-.025em;}
.cc-doctor-workspace-heading div > p:last-child {margin:.35rem 0 0;color:var(--doc-muted);font-size:.8rem;}
.cc-doctor-workspace-heading > span {padding:.35rem .55rem;border:1px solid var(--doc-line);border-radius:7px;background:#fff;color:#667085;font-size:.7rem;font-weight:700;}
.st-key-cc_doctor_workspace {padding:1rem 1.1rem 1.15rem;border:1px solid var(--doc-line);border-radius:16px;background:#fff;box-shadow:0 10px 28px rgba(16,24,40,.045);}
.st-key-cc_doctor_workspace [data-testid="stHorizontalBlock"] {gap:1.2rem !important;}
.st-key-cc_doctor_workspace [data-testid="stColumn"]:last-child {padding:0 0 0 1.1rem !important;border-left:1px solid var(--doc-line) !important;}
.cc-doctor-overview {padding:.15rem 0 .1rem;}
.cc-doctor-overview > header {display:flex;align-items:flex-start;justify-content:space-between;gap:1rem;padding-bottom:.8rem;border-bottom:1px solid #edf0f2;}
.cc-doctor-overview > header p {margin:0 0 .28rem;color:#27817b;font-size:.64rem;font-weight:800;letter-spacing:.08em;text-transform:uppercase;}
.cc-doctor-overview > header h3 {margin:0;color:#182230;font-size:1.15rem;letter-spacing:-.02em;}
.cc-doctor-overview > header > span {padding:.3rem .5rem;border-radius:7px;background:#f1f6f5;color:#567471;font-size:.65rem;font-weight:700;}
.cc-doctor-overview-intro {margin:.85rem 0;color:#344054;font-size:.82rem;line-height:1.65;}
.cc-doctor-overview-stats {display:grid;grid-template-columns:repeat(3,1fr);gap:.55rem;margin:.75rem 0;}
.cc-doctor-overview-stats div {padding:.62rem .7rem;border:1px solid #e8ecef;border-radius:9px;background:#f9fafb;}
.cc-doctor-overview-stats dt {color:#8a97a5;font-size:.62rem;}.cc-doctor-overview-stats dd {margin:.18rem 0 0;color:#1d2939;font-size:1rem;font-weight:800;}
.cc-doctor-overview-body {margin-top:.9rem;}.cc-doctor-overview-body h4 {margin:0 0 .45rem;color:#344054;font-size:.72rem;}
.cc-doctor-overview-body ul {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.45rem .75rem;margin:0;padding:0;list-style:none;}
.cc-doctor-overview-body li {display:grid;grid-template-columns:7px 1fr;gap:.5rem;align-items:start;padding:.58rem .62rem;border-radius:8px;background:#f7f9fa;}
.cc-doctor-overview-body li > span {width:7px;height:7px;margin-top:.35rem;border-radius:50%;background:#3a9892;}
.cc-doctor-overview-body li p {margin:0;color:#475467;font-size:.7rem;line-height:1.55;}
.cc-doctor-overview-body li.is-empty {display:block;grid-column:1 / -1;color:#98a2b3;}
.cc-doctor-overview-status {display:flex;align-items:flex-start;gap:.7rem;margin-top:.75rem;padding:.65rem .72rem;border:1px solid #cfe3e1;border-radius:9px;background:#f0f8f7;}
.cc-doctor-overview-status > span {flex:none;padding:.2rem .38rem;border-radius:5px;background:#d9eeeb;color:#176b68;font-size:.62rem;font-weight:800;}
.cc-doctor-overview-status p {margin:0;color:#315b58;font-size:.72rem;line-height:1.55;}
.cc-doctor-overview-missing {display:flex;align-items:center;gap:.65rem;margin-top:.65rem;color:#7a8795;font-size:.65rem;}
.cc-doctor-overview-missing > div {display:flex;flex-wrap:wrap;gap:.35rem;}.cc-doctor-overview-missing span {padding:.2rem .4rem;border-radius:6px;background:#f1f3f5;}
.cc-doctor-facts {display:grid !important;grid-template-columns:repeat(2,1fr);gap:.65rem;margin:0 0 .8rem !important;border:0 !important;}
.cc-doctor-fact {display:block !important;min-height:105px;padding:.8rem .85rem !important;border:1px solid var(--doc-line) !important;border-radius:11px;background:#fafbfc;}
.cc-doctor-fact dt {color:var(--doc-muted) !important;font-size:.68rem;font-weight:700 !important;}
.cc-doctor-fact dd {margin:.55rem 0 0 !important;color:var(--doc-ink) !important;font-size:.9rem !important;line-height:1.45 !important;font-weight:700 !important;}
.cc-doctor-fact:last-child {background:#f1f8f7;border-color:#c9e0de !important;}.cc-doctor-fact:last-child dd {color:var(--doc-teal-dark) !important;}
.cc-doctor-notice {margin:.55rem 0 !important;padding:.75rem .85rem !important;border:0 !important;border-radius:10px;background:#f2f7ff !important;}
.cc-doctor-notice h2 {font-size:.9rem !important;color:#254c78;}.cc-doctor-notice p {font-size:.76rem !important;color:#56718e !important;}
.cc-doctor-notice--caution,.cc-doctor-notice--stopped {background:var(--doc-warm) !important;}.cc-doctor-notice--error {background:#fff1f0 !important;}
.cc-doctor-summary {margin:.75rem 0 !important;padding:1rem !important;border:1px solid #cfe1df !important;border-radius:12px;background:#f5faf9;}
.cc-doctor-summary h2 {font-size:.74rem !important;color:var(--doc-teal) !important;text-transform:uppercase;letter-spacing:.08em;}
.cc-doctor-summary p {margin-top:.45rem !important;color:var(--doc-ink) !important;font-size:1.03rem !important;line-height:1.7 !important;font-weight:650 !important;}
.cc-doctor-source-title {margin:0 0 .45rem !important;padding:0 0 .55rem !important;border-bottom:1px solid var(--doc-line) !important;color:var(--doc-ink) !important;font-size:.82rem !important;}
.stApp:has(.cc-doctor-v3) [class*="st-key-cc_doctor_source_"] button {min-height:38px !important;padding:.4rem .5rem !important;border:0 !important;border-radius:7px !important;background:transparent !important;color:#475467 !important;font-size:.75rem !important;}
.stApp:has(.cc-doctor-v3) [class*="st-key-cc_doctor_source_"] button:hover,.stApp:has(.cc-doctor-v3) [class*="st-key-cc_doctor_source_active_"] button {background:#f2f6f7 !important;color:var(--doc-teal-dark) !important;}
.cc-doctor-decision-head {margin:1.15rem 0 .55rem !important;padding:1.05rem 1.1rem 0 !important;border:1px solid var(--doc-line) !important;border-bottom:0 !important;border-radius:16px 16px 0 0;background:#fff;}
.cc-doctor-decision-head h2 {font-size:1.15rem !important;color:var(--doc-ink);}.cc-doctor-decision-head p {color:var(--doc-muted) !important;font-size:.78rem !important;}
.stApp:has(.cc-doctor-v3) [class*="st-key-cc_doctor_decisions_"] {margin-top:-.6rem;padding:.85rem 1.1rem .45rem;border-left:1px solid var(--doc-line);border-right:1px solid var(--doc-line);background:#fff;}
.stApp:has(.cc-doctor-v3) [class*="st-key-cc_doctor_decisions_"] [role="radiogroup"] {gap:.65rem !important;}
.stApp:has(.cc-doctor-v3) [class*="st-key-cc_doctor_decisions_"] [role="radiogroup"] label {min-height:48px !important;border:1px solid var(--doc-line) !important;border-radius:10px !important;background:#fafbfc !important;color:#344054 !important;}
.cc-doctor-reject-boundary {margin:0 !important;padding:.35rem 1.1rem .9rem !important;border:1px solid var(--doc-line);border-top:0;border-radius:0 0 16px 16px;background:#fff;color:var(--doc-muted) !important;font-size:.72rem !important;}
.st-key-cc_doctor_submit_decision button,.st-key-cc_doctor_primary button {border:0 !important;border-radius:10px !important;background:var(--doc-teal) !important;color:#fff !important;box-shadow:none !important;}
.cc-doctor-disclosure,.cc-doctor-recorded-decision,.cc-doctor-outcomes,.cc-doctor-knowledge {padding:1rem 1.1rem !important;border:1px solid var(--doc-line) !important;border-radius:14px !important;background:#fff;}
.cc-doctor-quote {color:#27364a !important;font-size:1.15rem !important;}.cc-doctor-source-copy {color:#475467 !important;}
.stApp:has(.cc-doctor-v3) [data-testid="stExpander"] {border:1px solid var(--doc-line);border-radius:12px;background:#fff;}
.stApp:has(.cc-doctor-v3) [data-testid="stAlert"] {border-radius:10px;}
.stApp:has(.cc-doctor-v3) [data-testid="stCaptionContainer"] {color:var(--doc-muted);}
.stApp:has(.cc-doctor-v3) [class*="st-key-cc_doctor_supplemental"] {margin-top:1rem;padding:1rem 1.1rem;border:1px solid var(--doc-line);border-radius:14px;background:#fff;}
.cc-doctor-metric-heading {display:flex;align-items:end;justify-content:space-between;gap:1rem;margin:1.5rem 0 .75rem;}
.cc-doctor-metric-heading h2 {margin:0;color:var(--doc-ink);font-size:1.45rem;letter-spacing:-.025em;}
.cc-doctor-metric-heading div > p:last-child {margin:.35rem 0 0;color:var(--doc-muted);font-size:.8rem;}
.cc-doctor-metric-heading > span {padding:.35rem .55rem;border:1px solid #b9d6d4;border-radius:7px;background:#eef7f6;color:var(--doc-teal-dark);font-size:.7rem;font-weight:750;}
.stApp:has(.cc-doctor-v3) [class*="st-key-cc_doctor_metric_"] {height:100%;padding:1rem 1.05rem .85rem;border:1px solid var(--doc-line);border-radius:15px;background:#fff;box-shadow:0 8px 22px rgba(16,24,40,.035);}
.cc-doctor-metric-card__head {display:flex;align-items:start;justify-content:space-between;gap:.75rem;min-height:3rem;}
.cc-doctor-metric-card__head span {display:block;margin-bottom:.25rem;color:var(--doc-teal);font-size:.65rem;font-weight:800;letter-spacing:.08em;}
.cc-doctor-metric-card__head h3 {margin:0;color:var(--doc-ink);font-size:1rem;line-height:1.35;}
.cc-doctor-metric-card__head small {max-width:9rem;color:var(--doc-muted);font-size:.65rem;line-height:1.4;text-align:right;}
.cc-doctor-metric-summary {min-height:3.1rem;margin:.65rem 0 .35rem;padding:.65rem .7rem;border-radius:8px;background:#f6f8fa;color:#475467;font-size:.76rem;line-height:1.55;}
.cc-doctor-citation,.cc-doctor-status-item a {color:var(--doc-blue) !important;font-weight:750;text-decoration:none !important;}
.cc-doctor-chart-empty {height:230px;display:grid;place-content:center;text-align:center;border:1px dashed #d6dce3;border-radius:10px;background:#fafbfc;color:#98a2b3;}
.cc-doctor-chart-empty span {width:32px;height:32px;display:grid;place-items:center;margin:0 auto .45rem;border-radius:50%;background:#eef1f4;font-size:1.1rem;}
.cc-doctor-chart-empty p {margin:0;font-size:.75rem;}
.cc-doctor-single-point {height:230px;display:grid;place-content:center stretch;padding:0 1.2rem;text-align:center;border-radius:10px;background:linear-gradient(180deg,#fbfcfd,#f5f8fa);}
.cc-doctor-single-point > div {position:relative;display:grid;place-items:center;min-width:15rem;}.cc-doctor-single-point > div::before {content:"";position:absolute;left:0;right:0;top:50%;height:1px;background:#d8e0e6;}.cc-doctor-single-point span {position:relative;width:14px;height:14px;border:4px solid #d8eeeb;border-radius:50%;background:var(--doc-teal);box-shadow:0 0 0 4px #fff;}.cc-doctor-single-point strong {position:relative;margin-top:.7rem;padding:.22rem .45rem;border-radius:6px;background:#fff;color:var(--doc-teal-dark);font-size:.8rem;box-shadow:0 2px 8px rgba(16,24,40,.08);}
.cc-doctor-single-point p {margin:.6rem 0 0;color:#98a2b3;font-size:.68rem;}
.cc-doctor-status-grid {display:grid;grid-template-columns:repeat(2,1fr);gap:.55rem;min-height:230px;align-content:start;padding:.35rem 0;}
.cc-doctor-status-item {display:grid;grid-template-columns:1fr auto;gap:.3rem .6rem;padding:.75rem;border:1px solid var(--doc-line);border-radius:10px;background:#fafbfc;}
.cc-doctor-status-item > span {color:#475467;font-size:.76rem;font-weight:700;}
.cc-doctor-status-item > strong {padding:.12rem .4rem;border-radius:999px;font-size:.68rem;}.cc-doctor-status-item > strong.is-yes {background:#fff1ef;color:#b42318;}.cc-doctor-status-item > strong.is-no {background:#eaf7f2;color:#067647;}
.cc-doctor-status-item > small {grid-column:1 / -1;color:#98a2b3;font-size:.64rem;}
.cc-doctor-metric-source {padding:.85rem 0;border-bottom:1px solid var(--doc-line);scroll-margin-top:1rem;}
.cc-doctor-metric-source:last-child {border-bottom:0;}.cc-doctor-metric-source h4 {margin:0 0 .45rem;color:var(--doc-ink);font-size:.82rem;}.cc-doctor-metric-source p {margin:.2rem 0;color:#475467;font-size:.76rem;line-height:1.55;}
.cc-doctor-metric-source dl {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.35rem .8rem;margin:.55rem 0 0;}.cc-doctor-metric-source dl div {min-width:0;}.cc-doctor-metric-source dt {color:#98a2b3;font-size:.64rem;}.cc-doctor-metric-source dd {margin:.1rem 0 0;color:#475467;font-size:.7rem;overflow-wrap:anywhere;}

/* Production workspace shell */
.stApp:has(.cc-doctor-v3) [data-testid="stToolbar"],
.stApp:has(.cc-doctor-v3) [data-testid="stDecoration"],
.stApp:has(.cc-doctor-v3) [data-testid="stStatusWidget"],
.stApp:has(.cc-doctor-v3) [data-testid="stFooter"],
.stApp:has(.cc-doctor-v3) #MainMenu {display:none !important;}
.stApp:has(.cc-doctor-v3) .block-container {
  width:100%;max-width:none;padding:0 2rem 4rem 17.5rem;
}
.cc-doctor-sidebar {
  position:fixed;z-index:50;inset:0 auto 0 0;width:15.5rem;
  display:flex;flex-direction:column;padding:1.35rem 1rem 1rem;
  background:#0d2928;color:#fff;box-shadow:14px 0 40px rgba(8,31,30,.08);
}
.cc-doctor-brand--sidebar {padding:0 .55rem 1.4rem;color:#fff;}
.cc-doctor-brand--sidebar .cc-doctor-brand__mark {background:#35a49d;box-shadow:none;}
.cc-doctor-brand--sidebar small {color:#9bbab8;}
.cc-doctor-nav {display:flex;flex-direction:column;gap:.3rem;}
.cc-doctor-nav p {margin:1.15rem .7rem .35rem;color:#6f9996;font-size:.64rem;font-weight:800;letter-spacing:.12em;text-transform:uppercase;}
.cc-doctor-nav a {display:flex;align-items:center;gap:.75rem;padding:.72rem .78rem;border-radius:10px;color:#b9d0ce !important;font-size:.82rem;font-weight:650;text-decoration:none !important;transition:background .18s ease,color .18s ease;}
.cc-doctor-nav a > span {width:1.25rem;color:#7da5a2;font-size:1rem;text-align:center;}
.cc-doctor-nav a:hover {background:rgba(255,255,255,.07);color:#fff !important;}
.cc-doctor-nav a.is-active {background:#1b4c49;color:#fff !important;box-shadow:inset 3px 0 #54c3ba;}
.cc-doctor-nav a.is-active > span {color:#75d6cf;}
.cc-doctor-sidebar__account {display:flex;align-items:center;gap:.7rem;margin-top:auto;padding:.8rem;border-top:1px solid rgba(255,255,255,.1);}
.cc-doctor-sidebar__account > span {width:34px;height:34px;display:grid;place-items:center;border-radius:10px;background:#285b57;color:#d8f0ee;font-size:.76rem;font-weight:800;}
.cc-doctor-sidebar__account strong,.cc-doctor-sidebar__account small {display:block;}
.cc-doctor-sidebar__account strong {color:#f4f9f8;font-size:.75rem;}.cc-doctor-sidebar__account small {margin-top:.15rem;color:#86aaa7;font-size:.64rem;}
.cc-doctor-topbar {
  position:sticky;z-index:30;top:0;height:64px;margin:0 -2rem;
  padding:0 2rem;background:rgba(255,255,255,.94);backdrop-filter:blur(14px);
}
.cc-doctor-breadcrumb {display:flex;align-items:center;gap:.55rem;color:#98a2b3;font-size:.76rem;}
.cc-doctor-breadcrumb b {font-weight:500;}.cc-doctor-breadcrumb strong {color:#344054;font-weight:700;}
.cc-doctor-topbar__right {display:flex;align-items:center;gap:.85rem;color:#667085;font-size:.72rem;}
.cc-doctor-topbar__avatar {width:32px;height:32px;display:grid;place-items:center;border-radius:9px;background:#e1f1ef;color:#155f5b;font-weight:800;}
.cc-doctor-hero {padding:2rem 0 1.1rem;align-items:center;}
.cc-doctor-hero h1 {font-size:1.85rem !important;}.cc-doctor-hero p:last-child {font-size:.82rem;}
.cc-doctor-stage {border-color:#c9e2df;background:#edf8f6;color:#155f5b;box-shadow:none;}
.cc-doctor-patient {border:0;border-radius:18px;box-shadow:0 8px 28px rgba(16,24,40,.06);}
.cc-doctor-patient__meta {grid-template-columns:repeat(4,minmax(6.2rem,1fr));}
.cc-doctor-patient__name span {background:#eef2f6;color:#667085;}
.cc-doctor-boundary {display:none !important;}

/* Product-native metric cards and SVG charts */
.cc-doctor-metrics {scroll-margin-top:5rem;}
.cc-doctor-metric-heading {margin:1.8rem 0 .85rem;}
.cc-doctor-metric-heading h2 {font-size:1.35rem;}
.cc-doctor-metric-heading div > p:last-child {font-size:.76rem;}
.cc-doctor-metric-heading > span {padding:.42rem .68rem;border:1px solid var(--doc-line);background:#fff;color:#475467;}
.cc-doctor-metric-grid {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1rem;}
.cc-doctor-metric-card {min-width:0;padding:1.1rem 1.15rem 1rem;border:1px solid #e7ebef;border-radius:17px;background:#fff;box-shadow:0 8px 26px rgba(16,24,40,.045);overflow:hidden;}
.cc-doctor-metric-card > header {display:flex;align-items:flex-start;justify-content:space-between;gap:1rem;}
.cc-doctor-metric-card > header p {margin:0 0 .24rem;color:#7b8a9a;font-size:.62rem;font-weight:800;letter-spacing:.08em;text-transform:uppercase;}
.cc-doctor-metric-card > header h3 {margin:0;color:#1d2939;font-size:.95rem;line-height:1.35;}
.cc-doctor-metric-card > header > span {padding:.27rem .48rem;border-radius:7px;background:#f1f5f5;color:#52716f;font-size:.63rem;font-weight:750;}
.cc-doctor-metric-latest {display:flex;align-items:baseline;gap:.65rem;margin:.85rem 0 .25rem;}
.cc-doctor-metric-latest strong {color:#101828;font-size:1.55rem;line-height:1.2;letter-spacing:-.035em;}
.cc-doctor-metric-latest small {color:#98a2b3;font-size:.65rem;}
.cc-doctor-chart {height:226px;margin:.25rem -.15rem 0;}
.cc-doctor-chart svg {width:100%;height:100%;overflow:visible;}
.cc-chart-grid line {stroke:#edf0f3;stroke-width:1;vector-effect:non-scaling-stroke;}
.cc-chart-area {fill:rgba(37,148,140,.09);}
.cc-chart-line {fill:none;stroke:#228a83;stroke-width:3;stroke-linecap:round;stroke-linejoin:round;vector-effect:non-scaling-stroke;}
.cc-chart-dots circle {fill:#fff;stroke:#228a83;stroke-width:3;vector-effect:non-scaling-stroke;}
.cc-chart-bars rect {fill:#358f89;}.cc-chart-bars rect:nth-of-type(2n) {fill:#55aaa4;}
.cc-chart-labels text,.cc-chart-bars text {fill:#8b98a6;font-size:10px;font-family:Inter,"PingFang SC",sans-serif;}
.cc-chart-bars .cc-chart-value {fill:#52606d;font-size:9px;font-weight:700;}
.cc-doctor-metric-card > footer {margin-top:.3rem;padding-top:.75rem;border-top:1px solid #eef1f4;}
.cc-doctor-metric-card > footer p {margin:0;color:#536171;font-size:.73rem;line-height:1.55;}
.cc-doctor-citation,.cc-doctor-status-item a {color:#287e78 !important;font-weight:800;text-decoration:none !important;}
.cc-doctor-metric-card--status {grid-column:1 / -1;}
.cc-doctor-metric-card--status .cc-doctor-status-grid {grid-template-columns:repeat(3,1fr);min-height:0;margin-top:.9rem;padding:0;}
.cc-doctor-status-item {padding:.8rem .85rem;background:#f8fafb;border-color:#e9edf1;}
.cc-doctor-status-item > strong.is-yes {background:#fff0ee;color:#b42318;}.cc-doctor-status-item > strong.is-no {background:#e9f7f1;color:#067647;}
.cc-doctor-sources {scroll-margin-top:5rem;margin-top:1rem;border:1px solid #e4e9ed;border-radius:15px;background:#fff;overflow:hidden;}
.cc-doctor-sources > summary {display:flex;align-items:center;justify-content:space-between;gap:1rem;padding:1rem 1.1rem;cursor:pointer;list-style:none;}
.cc-doctor-sources > summary::-webkit-details-marker {display:none;}
.cc-doctor-sources > summary span strong,.cc-doctor-sources > summary span small {display:block;}.cc-doctor-sources > summary span strong {color:#27364a;font-size:.82rem;}.cc-doctor-sources > summary span small {margin-top:.22rem;color:#8a97a5;font-size:.67rem;}.cc-doctor-sources > summary b {padding:.26rem .45rem;border-radius:7px;background:#f1f5f5;color:#587370;font-size:.64rem;}
.cc-doctor-source-list {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 1rem;max-height:38rem;padding:0 1.1rem 1rem;border-top:1px solid #edf0f2;overflow:auto;}
.cc-doctor-metric-source {padding:.85rem .15rem;}
.cc-doctor-metric-source header {display:flex;gap:.5rem;align-items:center;}.cc-doctor-metric-source header strong {color:#287e78;font-size:.7rem;}.cc-doctor-metric-source header span {color:#52606d;font-size:.68rem;overflow-wrap:anywhere;}
.cc-doctor-metric-source > p {padding:.5rem .6rem;border-radius:7px;background:#f7f9fa;color:#344054;font-size:.72rem;}
@media (max-width:900px) {
  .stApp:has(.cc-doctor-v3) .block-container {padding-left:1.5rem;}
  .cc-doctor-sidebar {display:none;}
  .cc-doctor-topbar {margin:0 -1.5rem;padding:0 1.5rem;}
  .cc-doctor-patient {align-items:flex-start;flex-direction:column;}
  .cc-doctor-patient__meta {width:100%;grid-template-columns:repeat(2,1fr);}.cc-doctor-patient__meta div:nth-child(odd) {border-left:0;padding-left:0;}
  .cc-doctor-stepper {grid-template-columns:repeat(2,1fr);}
  .cc-doctor-facts {grid-template-columns:1fr !important;}.cc-doctor-fact {min-height:0;}
  .cc-doctor-overview-body ul {grid-template-columns:1fr;}
  .cc-doctor-status-grid {grid-template-columns:1fr;}
}
@media (max-width:640px) {
  .stApp:has(.cc-doctor-v3) .block-container {padding:0 .85rem 2.5rem;}
  .cc-doctor-topbar {height:58px;margin:0 -.85rem;padding:0 .9rem;}.cc-doctor-topbar__right > span:first-child {display:none;}
  .cc-doctor-hero {align-items:start;flex-direction:column;padding:1.45rem 0 .9rem;}.cc-doctor-hero h1 {font-size:1.8rem !important;}
  .cc-doctor-patient {padding:.85rem;}.cc-doctor-patient__meta {grid-template-columns:1fr;}.cc-doctor-patient__meta div {padding:.5rem 0;border-left:0;border-top:1px solid var(--doc-line);}
  .cc-doctor-stepper {grid-template-columns:1fr;}.cc-doctor-workspace-heading,.cc-doctor-metric-heading {align-items:start;flex-direction:column;}
  .st-key-cc_doctor_workspace [data-testid="stHorizontalBlock"] {display:block !important;}
  .st-key-cc_doctor_workspace [data-testid="stColumn"]:last-child {margin-top:1rem;padding:1rem 0 0 !important;border-left:0 !important;border-top:1px solid var(--doc-line);}
  .stApp:has(.cc-doctor-v3) [data-testid="stHorizontalBlock"]:has([class*="st-key-cc_doctor_metric_"]) {display:block !important;}
  .stApp:has(.cc-doctor-v3) [data-testid="stHorizontalBlock"]:has([class*="st-key-cc_doctor_metric_"]) > [data-testid="stColumn"] {width:100% !important;flex:1 1 100% !important;margin-bottom:.75rem;}
  .cc-doctor-metric-source dl {grid-template-columns:1fr;}
  .cc-doctor-overview-stats {grid-template-columns:repeat(3,1fr);}.cc-doctor-overview > header {flex-direction:column;}
  .cc-doctor-metric-grid {grid-template-columns:1fr;}.cc-doctor-metric-card--status {grid-column:auto;}
  .cc-doctor-metric-card--status .cc-doctor-status-grid {grid-template-columns:1fr;}
  .cc-doctor-source-list {grid-template-columns:1fr;}
}
@media (prefers-reduced-motion:reduce) {* {scroll-behavior:auto !important;transition:none !important;animation:none !important;}}
</style>
"""


def inject_doctor_surface_styles(st: Any) -> None:
    st.markdown(DOCTOR_SURFACE_STYLE, unsafe_allow_html=True)
