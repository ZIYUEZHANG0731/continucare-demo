"""Read-only, offline symptom-centered Knowledge Evidence view."""

from __future__ import annotations

import json

import streamlit as st
from streamlit.errors import StreamlitPageNotFoundError

from continucare.knowledge import LoadMode, load_builtin_bundle
from continucare.knowledge.models import SourcedClinicalClaim, artifact_key
from continucare.ui import inject_global_styles


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def _source_url(source) -> str:
    return str(source.canonical_url or source.access_urls[0].url)


def _home_link(label: str) -> None:
    """Use native multipage navigation, with an AppTest-only fallback."""

    try:
        st.page_link("app.py", label=label)
    except (StreamlitPageNotFoundError, KeyError):
        st.markdown(f"[{label}](/)")


st.set_page_config(
    page_title="症状知识证据 · ContinuCare",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="collapsed",
)
inject_global_styles(st)

st.title("症状中心 Knowledge Evidence")
st.error("只读离线视图 · 不读取患者数据 · 不授权任何临床运行时行为")
_home_link("← 返回完整比赛 Demo 导览")
st.caption(
    "四个条目只是当前比赛 fixture snapshot，不是常见症状排名、固定分母、"
    "target_number、覆盖率目标或完整症状库。"
)

mode_label = st.radio(
    "注册表视图",
    ("CURRENT", "HISTORICAL"),
    horizontal=True,
    help="HISTORICAL 会保留失效或未解析记录用于审计，不代表当前可用。",
)
mode = LoadMode.CURRENT if mode_label == "CURRENT" else LoadMode.HISTORICAL
registry = load_builtin_bundle(mode=mode)
views = registry.symptom_views()

st.markdown("## 当前 fixture")
summary_columns = st.columns(4)
for column, view in zip(summary_columns, views):
    with column:
        with st.container(border=True):
            resolution = view.catalog_resolution
            if resolution.resolved:
                concept = resolution.concept
                assert concept is not None
                st.markdown(f"### {concept.preferred_zh}")
                st.code(concept.coding.code, language=None)
                st.caption(concept.coding.display or "display 未登记")
            else:
                st.markdown(f"### {view.record.symptom_index_id}")
                st.warning("精确 catalog term 未解析")
            st.caption(
                f"{view.record.symptom_index_id}@{view.record.record_version} · "
                f"{mode_label}"
            )

options = {view.record.symptom_index_id: view for view in views}
selected_id = st.selectbox("查看一个症状的精确证据关系", tuple(options))
view = options[selected_id]
record = view.record
resolution = view.catalog_resolution

st.markdown("## 精确 terminology catalog 解析")
with st.container(border=True):
    st.write(
        "Catalog ref："
        f"`{record.catalog_term.catalog_id} | {record.catalog_term.catalog_version} | "
        f"{record.catalog_term.concept_id}`"
    )
    st.write(f"索引：`{record.symptom_index_id}@{record.record_version}` · {mode_label}")
    if resolution.resolved:
        concept = resolution.concept
        assert concept is not None
        st.success("exact catalog term 已解析；名称与编码来自该 catalog 版本。")
        left, right = st.columns(2)
        with left:
            st.write(f"标准名称：**{concept.preferred_zh}**")
            st.write(f"Code system：`{concept.coding.system}`")
        with right:
            st.write(f"Code：`{concept.coding.code}`")
            st.write(f"Version：`{concept.coding.version or 'not_available'}`")
    else:
        st.warning(f"HISTORICAL unresolved：{resolution.detail}")

st.markdown("## Claim 与精确适用范围")
st.caption("scope 与 Claim 同屏显示；不同 Pathway/version 的声明不会合并。")
if not view.claims:
    st.info("没有登记 Claim。")
for claim in view.claims:
    summary = view.review_summaries[claim.ref.key()]
    with st.container(border=True):
        st.markdown(f"### `{claim.claim_id}@{claim.claim_version}`")
        st.write(claim.statement)
        st.write(f"Lifecycle：`{claim.lifecycle}` · Review aggregate：`{summary.aggregate}`")
        supports_col, limits_col = st.columns(2)
        with supports_col:
            st.success("支持范围\n\n" + "\n\n".join(f"- {item}" for item in claim.supports))
        with limits_col:
            st.warning(
                "不支持范围\n\n"
                + "\n\n".join(f"- {item}" for item in claim.does_not_support)
            )
        st.markdown("**Visible exact scope**")
        st.code(_json(claim.applicable_scope.model_dump(mode="json")), language="json")
        if isinstance(claim, SourcedClinicalClaim):
            source_map = {item.ref.key(): item for item in view.sources}
            for citation in claim.citations:
                source = source_map[citation.source.key()]
                st.markdown(f"**Source：{source.title}**")
                st.write(
                    f"机构：{source.issuing_authority or 'not_available'} · "
                    f"文档版本：`{source.document_version or 'not_available'}` · "
                    f"Source ref：`{source.source_id}@{source.record_version}`"
                )
                st.write(f"Locator：`{_json(citation.locator.model_dump(mode='json'))}`")
                st.write(
                    f"Access：`{source.access.mode}` · "
                    f"Integrity：`{view.source_content_status[source.ref.key()]}` · "
                    f"License：`{source.license_terms_uri or 'not_registered'}`"
                )
                st.markdown(f"[打开官方来源]({_source_url(source)})")

st.markdown("## Binding 与 Pathway 隔离")
if not view.bindings:
    st.info("没有 exact Binding；Knowledge 不会据此创建 runtime artifact。")
for binding in view.bindings:
    with st.container(border=True):
        st.write(f"Binding：`{binding.binding_id}@{binding.binding_version}`")
        st.write(
            f"Pathway scope：`{binding.pathway.pathway_code} | "
            f"{binding.pathway.pathway_version}`"
        )
        st.write(f"Artifact：`{' | '.join(artifact_key(binding.artifact))}`")
        st.caption("informational_only · runtime_authority=none")

st.markdown("## Unresolved CoverageGap")
if not view.gaps:
    st.info("没有登记 gap。")
for gap in view.gaps:
    with st.container(border=True):
        st.markdown(f"**`{gap.gap_id}@{gap.gap_version}`**")
        st.write(gap.reason)
        st.caption(
            f"{gap.gap_kind} · {gap.pathway.pathway_code} | "
            f"{gap.pathway.pathway_version} · lifecycle={gap.lifecycle}"
        )

st.markdown("## 未绑定的官方 link-only 候选来源")
st.caption(
    "以下来源没有绑定到四个症状、GLP1 Questionnaire 或 Observation；"
    "它们的存在不形成临床适用性。"
)
unbound_sources = tuple(
    item
    for item in registry.sources
    if item.registered_by == "Organization/continucare-m5k-link-only-registration"
)
for source in unbound_sources:
    with st.container(border=True):
        st.markdown(f"### {source.title}")
        st.write(
            f"Source ref：`{source.source_id}@{source.record_version}` · "
            f"Access：`{source.access.mode}` · Integrity："
            f"`{registry.source_content_status[source.ref.key()]}`"
        )
        st.write(
            f"机构：{source.issuing_authority or 'not_available'} · "
            f"版本：`{source.document_version or 'not_available'}` · "
            f"License/terms：`{source.license_terms_uri or 'not_registered'}`"
        )
        if source.source_id == "hpo-v2026-06-23":
            st.info(
                "HPO 仅作为表型/症状概念组织候选；疾病—表型关联不是患者诊断依据，"
                "且本切片没有 HPO→SNOMED mapping 或 runtime 变更。"
            )
        if source.source_id == "nci-pro-ctcae-official-site":
            st.warning(
                "PRO-CTCAE 面向肿瘤临床研究，是 clinician CTCAE 的 companion；"
                "不是诊断、预后或治疗工具。本切片未绑定 GLP1、未内置题目/选项/翻译，"
                "也未验证 study registration 或 agreement 状态。"
            )
        st.markdown(f"[打开官方入口]({_source_url(source)})")

st.markdown("## 安全声明")
st.warning(
    "本页不是诊断，不是风险等级；不授权 Task、Summary、Observation 或 ClinicalRule；"
    "不包含真实患者数据。浏览不会写数据库、发起模型调用或创建任何临床资源。"
)
st.caption(
    "synthetic expressions：未新增。现有 runtime aliases 不被复制为 Knowledge 证据；"
    "患者表达来源与验证缺口已作为 CoverageGap 显示。"
)
_home_link("返回首页确认故事进度 →")
