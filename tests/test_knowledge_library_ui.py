from __future__ import annotations

import ast
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

from streamlit.testing.v1 import AppTest

from continucare.knowledge import LoadMode, load_builtin_bundle
from continucare.knowledge.resolvers import CatalogTermResolution
from continucare.ui import project_knowledge_library


ROOT = Path(__file__).parents[1]
KNOWLEDGE_PAGE = ROOT / "pages" / "5_knowledge_evidence.py"
UI_SOURCE = ROOT / "continucare" / "ui.py"


class _KnowledgeDOM(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids: list[tuple[str, dict[str, str | None]]] = []
        self.controls: list[dict[str, str | None]] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if "id" in attributes:
            self.ids.append((tag, attributes))
        if tag == "a" and "aria-controls" in attributes:
            self.controls.append(attributes)


def _rendered_dom(app) -> _KnowledgeDOM:
    parser = _KnowledgeDOM()
    parser.feed("\n".join(str(item.value) for item in app.markdown))
    return parser


def _registry_with_view(registry, view):
    return SimpleNamespace(
        mode=registry.mode,
        symptom_views=lambda: (view,),
        sources=registry.sources,
        source_content_status=registry.source_content_status,
    )


def test_current_library_has_four_catalog_resolved_topics_in_stable_registry_order():
    projection = project_knowledge_library(load_builtin_bundle())

    assert [item.topic_id for item in projection.topics] == [
        "diarrhea",
        "nausea",
        "vomiting",
        "abdominal-pain",
    ]
    assert [item.name for item in projection.topics] == ["腹泻", "恶心", "呕吐", "腹痛"]
    assert all(item.catalog_resolved for item in projection.topics)
    assert projection.selected_topic_id == "diarrhea"


def test_topic_names_and_codes_come_from_exact_catalog_resolution():
    registry = load_builtin_bundle()
    projection = project_knowledge_library(registry)

    for topic, view in zip(projection.topics, registry.symptom_views()):
        concept = view.catalog_resolution.concept
        assert concept is not None
        assert topic.name == concept.preferred_zh
        assert topic.catalog_system == concept.coding.system
        assert topic.catalog_code == concept.coding.code


def test_support_limit_claim_scope_and_review_are_kept_separate():
    projection = project_knowledge_library(
        load_builtin_bundle(),
        selected_topic_id="nausea",
    )
    topic = projection.selected_topic
    assert topic is not None

    assert len(topic.claims) == 2
    assert topic.supports
    assert topic.does_not_support
    assert all(claim.statement for claim in topic.claims)
    assert all(claim.scope_json.startswith("{") for claim in topic.claims)
    assert all(claim.review_aggregate == "not_assessed" for claim in topic.claims)
    assert not any("资料完整" in item for item in topic.supports)


def test_coverage_gap_present_and_absent_have_truthful_distinct_language():
    registry = load_builtin_bundle()
    view = registry.symptom_views()[0]
    with_gap = project_knowledge_library(
        _registry_with_view(registry, view),
    ).selected_topic
    no_gap = project_knowledge_library(
        _registry_with_view(registry, replace(view, gaps=())),
    ).selected_topic

    assert with_gap is not None and with_gap.gaps
    assert with_gap.coverage_message == "仍有未解决的资料缺口"
    assert no_gap is not None and not no_gap.gaps
    assert no_gap.coverage_message == "当前未登记覆盖缺口"


def test_no_claim_state_does_not_invent_support_or_scope():
    registry = load_builtin_bundle()
    view = replace(registry.symptom_views()[0], claims=(), sources=())
    topic = project_knowledge_library(
        _registry_with_view(registry, view)
    ).selected_topic

    assert topic is not None
    assert topic.claims == ()
    assert topic.supports == ()
    assert topic.does_not_support == ()


def test_unresolved_catalog_keeps_exact_state_without_inventing_a_name_or_code():
    registry = load_builtin_bundle()
    view = replace(
        registry.symptom_views()[0],
        catalog_resolution=CatalogTermResolution(
            resolved=False,
            detail="exact catalog term unavailable",
        ),
    )
    topic = project_knowledge_library(
        _registry_with_view(registry, view)
    ).selected_topic

    assert topic is not None
    assert not topic.catalog_resolved
    assert topic.name is None
    assert topic.catalog_code is None
    assert topic.catalog_detail == "exact catalog term unavailable"


def test_current_and_historical_are_explicit_technical_modes():
    current = project_knowledge_library(load_builtin_bundle(mode=LoadMode.CURRENT))
    historical = project_knowledge_library(
        load_builtin_bundle(mode=LoadMode.HISTORICAL)
    )

    assert current.mode == "CURRENT"
    assert historical.mode == "HISTORICAL"
    assert all(item.mode == "CURRENT" for item in current.topics)
    assert all(item.mode == "HISTORICAL" for item in historical.topics)


def test_official_sources_versions_locators_bindings_and_unbound_sources_are_preserved():
    projection = project_knowledge_library(
        load_builtin_bundle(),
        selected_topic_id="nausea",
    )
    topic = projection.selected_topic
    assert topic is not None

    assert topic.sources
    assert all(source.title for source in topic.sources)
    assert all(source.issuing_authority for source in topic.sources)
    assert all(source.document_version for source in topic.sources)
    assert all(source.url.startswith("http") for source in topic.sources)
    assert any(source.locators for source in topic.sources)
    assert topic.bindings
    assert all(binding.pathway_scope for binding in topic.bindings)
    assert projection.unbound_sources
    assert {item.source_ref.split("@", 1)[0] for item in projection.unbound_sources} >= {
        "hpo-v2026-06-23",
        "nci-pro-ctcae-official-site",
    }


def test_fixed_independence_contract_and_local_selection_do_not_change_library_facts():
    registry = load_builtin_bundle()
    diarrhea = project_knowledge_library(registry, selected_topic_id="diarrhea")
    nausea = project_knowledge_library(registry, selected_topic_id="nausea")

    assert diarrhea.independence_notice == "这里只说明采集依据，没有对这位患者做过评估。"
    assert diarrhea.readonly_notice == "本页只读，不读取患者故事，不创建记录，不参与本轮完成判定。"
    assert diarrhea.topics == nausea.topics
    assert diarrhea.unbound_sources == nausea.unbound_sources
    assert diarrhea.selected_topic_id == "diarrhea"
    assert nausea.selected_topic_id == "nausea"


def test_page_imports_only_offline_knowledge_and_has_no_patient_or_story_runtime_dependencies():
    source = KNOWLEDGE_PAGE.read_text("utf-8")
    tree = ast.parse(source, filename=str(KNOWLEDGE_PAGE))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(item.name for item in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    forbidden = (
        "continucare.config",
        "continucare.db",
        "continucare.adapters",
        "continucare.services",
        "continucare.layer4",
        "continucare.care_agent",
        "continucare.care_engine",
        "continucare.models",
        "urllib",
        "requests",
        "httpx",
    )

    assert not any(
        name == prefix or name.startswith(prefix + ".")
        for name in imports
        for prefix in forbidden
    )
    assert "get_settings" not in source
    assert "SQLiteStore" not in source
    assert "competition_demo" not in source
    assert "render_competition_progress" not in source
    assert "render_integration_status" not in source
    assert "patient_id" not in source
    assert "DEMO_PATIENT" not in source
    assert "自动预选" not in source


def test_page_loads_without_a_database_and_topic_switching_is_local(monkeypatch, tmp_path):
    missing_db = tmp_path / "knowledge-must-not-create.db"
    monkeypatch.setenv("CONTINUCARE_DB_PATH", str(missing_db))

    app = AppTest.from_file(str(KNOWLEDGE_PAGE), default_timeout=10).run()

    assert not app.exception
    assert app.title[0].value == "Knowledge 资料库"
    assert app.radio[0].value == "diarrhea"
    assert app.radio[0].options == ["腹泻", "恶心", "呕吐", "腹痛"]
    app.radio[0].set_value("nausea").run()
    assert not app.exception
    assert app.radio[0].value == "nausea"
    assert not missing_db.exists()
    visible = "\n".join(item.value for item in app.markdown)
    assert "症状采集参考" in visible
    assert "这里只说明采集依据，没有对这位患者做过评估" in visible
    assert "支持什么" in visible
    assert "不支持什么" in visible


def test_source_layer_is_separate_from_patient_source_styling_and_exposes_history_on_demand():
    ui_source = UI_SOURCE.read_text("utf-8")
    app = AppTest.from_file(str(KNOWLEDGE_PAGE), default_timeout=10).run()

    collapsed = _rendered_dom(app)
    assert len(collapsed.controls) == 1
    assert collapsed.controls[0]["aria-expanded"] == "false"
    targets = [
        item for item in collapsed.ids if item[1]["id"] == "cc-knowledge-sources-panel"
    ]
    assert len(targets) == 1
    assert targets[0][0] == "span"
    assert targets[0][1]["hidden"] is None
    assert targets[0][1]["aria-hidden"] == "true"
    assert "tabindex" not in targets[0][1]

    app.query_params["cc_knowledge_details"] = "sources"
    app.run()
    assert not app.exception
    visible = "\n".join(item.value for item in app.markdown)
    assert "CURRENT / HISTORICAL" in visible
    assert "未绑定的 link-only 来源" in visible
    assert "页面加载不会访问官方来源 URL" in visible
    assert ".cc-knowledge-source" in ui_source
    assert ".cc-knowledge-shell" in ui_source
    assert ".cc-patient-quote" in ui_source
    assert "cc-knowledge-source cc-patient" not in ui_source
    assert "render_disclosure_controls" in KNOWLEDGE_PAGE.read_text("utf-8")
    assert "grid-template-columns:repeat(2, minmax(0, 1fr))" in ui_source
    assert "min-height:48px" in ui_source
    assert 'aria-expanded="{str(active).lower()}"' in ui_source
    expanded = _rendered_dom(app)
    assert expanded.controls[0]["aria-expanded"] == "true"
    targets = [
        item for item in expanded.ids if item[1]["id"] == "cc-knowledge-sources-panel"
    ]
    assert len(targets) == 1
    assert targets[0][0] == "section"
    assert "cc-knowledge-details-head" in (
        targets[0][1].get("class") or ""
    ).split()
    assert "hidden" not in targets[0][1]
    assert targets[0][1].get("aria-hidden") != "true"
    assert "来源与版本" in visible

    app.query_params["cc_knowledge_details"] = "future-value"
    app.run()
    unknown = _rendered_dom(app)
    assert unknown.controls[0]["aria-expanded"] == "false"
    assert sum(
        item[1]["id"] == "cc-knowledge-sources-panel" for item in unknown.ids
    ) == 1
