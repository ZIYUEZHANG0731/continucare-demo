from __future__ import annotations

from types import SimpleNamespace

from continucare.doctor_ui import (
    DOCTOR_SURFACE_STYLE,
    build_doctor_followup_overview,
    build_doctor_metric_dashboard,
    doctor_stage_label,
    render_doctor_header,
    render_doctor_followup_overview,
    render_doctor_metric_dashboard,
)
from continucare.models import Patient
from continucare.product_mvp import ProductContext, ProductRole


class _MarkdownSink:
    def __init__(self) -> None:
        self.fragments: list[str] = []

    def markdown(self, value, **_kwargs) -> None:
        self.fragments.append(str(value))


def _context(display_name: str = "陈女士") -> ProductContext:
    return ProductContext(
        role=ProductRole.DOCTOR,
        role_label="医生端 · 复诊工作台",
        patient=Patient(
            patient_id="synthetic-patient-001",
            display_name=display_name,
            synthetic=True,
            pathway_code="GLP1-14D",
            enrollment_date="2026-08-01",
            next_visit_date="2026-08-17",
            status="active",
            created_at="2026-08-01T00:00:00+00:00",
        ),
    )


def _observation(
    observation_id: str,
    *,
    code: str,
    value,
    effective_time: str,
    metric_id: str | None = None,
    unit: str | None = None,
    display: str = "Synthetic metric",
    claims: tuple[str, ...] = ("claim-1",),
):
    return SimpleNamespace(
        observation_id=observation_id,
        code=code,
        code_display=display,
        value=value,
        unit=unit,
        effective_time=effective_time,
        message_id=f"response-{observation_id}",
        evidence=SimpleNamespace(
            questionnaire_response_id=f"response-{observation_id}",
            evidence_text=f"原始回答 {observation_id}",
            metric_id=metric_id,
            evidence_claim_ids=list(claims),
            knowledge_release_id="cn-glp1-l1-v1.0.3" if claims else None,
        ),
    )


def test_doctor_stage_label_covers_known_and_future_states():
    known = SimpleNamespace(stage=SimpleNamespace(value="doctor_brief_pending"))
    future = SimpleNamespace(stage=SimpleNamespace(value="future-stage"))

    assert doctor_stage_label(known) == "待生成速览"
    assert doctor_stage_label(future) == "状态待核对"


def test_doctor_header_escapes_patient_content_and_keeps_scope_visible():
    sink = _MarkdownSink()

    render_doctor_header(
        sink,
        _context('<script>alert("x")</script>'),
        SimpleNamespace(stage=SimpleNamespace(value="plan_activated")),
    )

    markup = "\n".join(sink.fragments)
    assert '<script>alert("x")</script>' not in markup
    assert "&lt;script&gt;alert(&quot;" in markup
    assert "演示数据" in markup
    assert "等待患者提交" in markup
    assert "cc-doctor-patient__meta" in markup
    assert "cc-doctor-sidebar" in markup


def test_doctor_surface_is_scoped_and_responsive():
    assert ".stApp:has(.cc-doctor-v3)" in DOCTOR_SURFACE_STYLE
    assert "@media (max-width:640px)" in DOCTOR_SURFACE_STYLE
    assert "@media (prefers-reduced-motion:reduce)" in DOCTOR_SURFACE_STYLE
    assert "cc_doctor_activation_card" in DOCTOR_SURFACE_STYLE
    assert "cc_doctor_workspace" in DOCTOR_SURFACE_STYLE
    assert "cc_doctor_metric_" in DOCTOR_SURFACE_STYLE


def test_metric_dashboard_routes_quantities_and_severity_to_expected_charts():
    dashboard = build_doctor_metric_dashboard(
        [
            _observation(
                "weight-1",
                code="29463-7",
                value=73500,
                unit="g",
                effective_time="2026-08-01T08:00:00+00:00",
            ),
            _observation(
                "weight-2",
                code="29463-7",
                value=72.4,
                unit="kg",
                effective_time="2026-08-08T08:00:00+00:00",
            ),
            _observation(
                "nausea-1",
                code="81660-3",
                value="LA6751-7",
                metric_id="nausea_severity_current",
                effective_time="2026-08-08T09:00:00+00:00",
            ),
            _observation(
                "vomiting-1",
                code="94070-0",
                value=2,
                metric_id="vomiting_count_24h",
                unit="/d",
                effective_time="2026-08-08T09:05:00+00:00",
            ),
            _observation(
                "fluid-1",
                code="75301-2",
                value=1800,
                metric_id="fluid_intake_24h_estimated",
                unit="mL/(24.h)",
                effective_time="2026-08-08T09:10:00+00:00",
            ),
        ]
    )
    cards = {item.metric_key: item for item in dashboard.cards}

    assert cards["body_weight"].chart_kind == "line"
    assert [item.value for item in cards["body_weight"].points] == [73.5, 72.4]
    assert cards["nausea"].title == "恶心"
    assert cards["nausea"].chart_kind == "bar"
    assert cards["nausea"].points[0].display == "中度"
    assert cards["vomiting_count"].chart_kind == "bar"
    assert cards["fluid_intake"].chart_kind == "line"
    assert "不足以判断趋势" in cards["fluid_intake"].summary
    assert all(item.source_ids for item in cards.values())


def test_missing_severity_explains_negative_nausea_and_cites_the_record():
    dashboard = build_doctor_metric_dashboard(
        [
            _observation(
                "nausea-present",
                code="422587007",
                value=False,
                metric_id="nausea_present_now",
                display="Nausea",
                effective_time="2026-08-09T09:00:00+00:00",
            ),
            _observation(
                "lightheadedness",
                code="386705008",
                value=True,
                metric_id=None,
                display="Lightheadedness",
                claims=(),
                effective_time="2026-08-09T09:05:00+00:00",
            ),
        ]
    )
    cards = {item.metric_key: item for item in dashboard.cards}

    assert cards["nausea"].points == ()
    assert cards["nausea"].summary == "最近一次记录：无恶心；程度不适用。"
    assert len(cards["nausea"].source_ids) == 1
    assert cards["symptom_status"].chart_kind == "status"
    assert "恶心无" not in cards["symptom_status"].summary
    assert "头晕有" in cards["symptom_status"].summary
    prototype = next(
        item
        for item in dashboard.sources
        if item.observation_reference.endswith("lightheadedness")
    )
    assert prototype.evidence_claim_ids == ()
    assert prototype.knowledge_release_id == "未绑定（补充上报/原型）"


def test_metric_dashboard_renders_product_native_svg_without_streamlit_charts():
    dashboard = build_doctor_metric_dashboard(
        [
            _observation(
                "weight-1",
                code="29463-7",
                value=73.5,
                unit="kg",
                effective_time="2026-08-01T08:00:00+00:00",
            ),
            _observation(
                "weight-2",
                code="29463-7",
                value=72.4,
                unit="kg",
                effective_time="2026-08-08T08:00:00+00:00",
            ),
        ]
    )
    sink = _MarkdownSink()

    render_doctor_metric_dashboard(sink, dashboard)

    markup = "\n".join(sink.fragments)
    assert "cc-doctor-metric-grid" in markup
    assert "<svg" in markup
    assert "较首次记录减少 1.1 kg" in markup
    assert "数据来源" in markup
    assert "不把估计值解释为临床结论" not in markup


def test_followup_overview_is_generated_from_available_dates_and_metrics():
    dashboard = build_doctor_metric_dashboard(
        [
            _observation(
                "weight-day-1",
                code="29463-7",
                value=73.5,
                unit="kg",
                effective_time="2026-08-01T08:00:00+00:00",
            ),
            _observation(
                "weight-day-2",
                code="29463-7",
                value=72.4,
                unit="kg",
                effective_time="2026-08-02T08:00:00+00:00",
            ),
            _observation(
                "nausea-day-2",
                code="422587007",
                value=True,
                metric_id="nausea_present_now",
                display="Nausea",
                effective_time="2026-08-02T09:00:00+00:00",
            ),
        ]
    )

    overview = build_doctor_followup_overview(dashboard)

    assert overview.title == "2 天随访小结"
    assert overview.period_label == "2026年08月01日—08月02日"
    assert overview.record_day_count == 2
    assert overview.metric_count == 2
    assert "体重趋势：最近记录 72.4 kg，较首次记录减少 1.1 kg" in overview.sentences[0].text
    assert "恶心：最近一次记录：有恶心；程度尚未记录" in overview.sentences[1].text
    assert overview.latest_status is None
    assert set(overview.missing_metrics) == {"24 小时呕吐次数", "液体摄入"}


def test_followup_overview_handles_empty_and_partial_patient_data():
    overview = build_doctor_followup_overview(build_doctor_metric_dashboard([]))

    assert overview.title == "随访小结"
    assert overview.period_label == "暂无上报"
    assert overview.record_day_count == 0
    assert overview.metric_count == 0
    assert overview.sentences == ()
    assert overview.latest_status is None
    assert len(overview.missing_metrics) == 4


def test_followup_overview_render_keeps_summary_sources_and_missing_items():
    dashboard = build_doctor_metric_dashboard(
        [
            _observation(
                "fluid-only",
                code="75301-2",
                value=1600,
                unit="mL",
                metric_id="fluid_intake_24h_estimated",
                effective_time="2026-08-03T08:00:00+00:00",
            )
        ]
    )
    sink = _MarkdownSink()

    render_doctor_followup_overview(
        sink,
        build_doctor_followup_overview(dashboard),
    )

    markup = "\n".join(sink.fragments)
    assert "当日随访小结" in markup
    assert "液体摄入：最近记录为 1600 mL/24h" in markup
    assert "[S1]" in markup
    assert "本期未记录" in markup
