"""Shared Streamlit chrome for the role-separated synthetic MVP."""

from __future__ import annotations

import html
from typing import Any

from continucare.product_mvp import ProductContext


def render_role_context(st: Any, context: ProductContext) -> None:
    """Render role and patient scope without providing a role switcher."""

    patient = context.patient
    patient_label = patient.display_name if patient is not None else "合成患者尚未载入"
    pathway = patient.pathway_code if patient is not None else "—"
    visit = patient.next_visit_date if patient is not None else "—"
    st.markdown(
        f"""
        <section class="cc-product-context" aria-label="当前体验范围">
          <div>
            <span class="cc-product-context__eyebrow">{html.escape(context.role_label)}</span>
            <strong>{html.escape(patient_label)}</strong>
          </div>
          <dl>
            <div><dt>Pathway</dt><dd>{html.escape(pathway)}</dd></div>
            <div><dt>下次复诊</dt><dd>{html.escape(visit)}</dd></div>
            <div><dt>数据范围</dt><dd>仅合成</dd></div>
          </dl>
        </section>
        <p class="cc-product-simulation">角色模拟模式 · 尚未接入真实身份认证或生产权限</p>
        """,
        unsafe_allow_html=True,
    )


def render_demo_role_hub(st: Any, *, next_page: str, next_label: str) -> None:
    """Render the only cross-role switchboard in the product experience."""

    st.markdown(
        """
        <section class="cc-role-hub-heading">
          <span>可点击 MVP</span>
          <h2>从同一份记录进入不同角色</h2>
          <p>下面是演示者角色切换台；角色页面本身不会提供跨角色万能操作。</p>
        </section>
        """,
        unsafe_allow_html=True,
    )
    columns = st.columns(4, gap="small")
    surfaces = (
        (
            "患者端",
            "表达、确认或拒绝系统记录的意思。",
            "pages/1_patient_followup.py",
            "进入患者端",
        ),
        (
            "护士端",
            "逐份人工安全复核患者确认记录，并决定是否上报医生。",
            "pages/2_nurse_risk_center.py",
            "进入护士安全复核台",
        ),
        (
            "医生端",
            "查看复诊前事实、来源与审核状态。",
            "pages/3_doctor_summary.py",
            "进入医生工作台",
        ),
        (
            "医院总台",
            "查看运营状态、豆包来源和治理证据。",
            "pages/6_operations.py",
            "进入运营总台",
        ),
    )
    for column, (title, detail, page, label) in zip(columns, surfaces, strict=True):
        with column, st.container(border=True):
            st.markdown(f"### {title}")
            st.caption(detail)
            st.page_link(page, label=label, width="stretch")

    st.info(f"当前流程建议下一步：{next_label}")
    if next_page == "app.py":
        st.caption("请先使用上方明确标注的豆包在线或确定性离线入口开始一轮。")
    else:
        st.page_link(next_page, label=f"继续当前流程：{next_label}", width="stretch")


PRODUCT_STYLE = """
<style>
.cc-product-context {
  display:flex; justify-content:space-between; gap:1rem; align-items:end;
  padding:.85rem 1rem; margin:.15rem 0 .35rem; border:1px solid #d7e2dd;
  border-radius:16px; background:linear-gradient(120deg,#f8fbf9,#f3f7f5);
}
.cc-product-context__eyebrow {
  display:block; color:#245846; font-size:.76rem; font-weight:720;
  letter-spacing:.06em; margin-bottom:.2rem;
}
.cc-product-context strong {font-size:1rem; color:#17211d;}
.cc-product-context dl {display:flex; gap:1.5rem; margin:0;}
.cc-product-context dl div {min-width:5.5rem;}
.cc-product-context dt {font-size:.68rem;color:#64736c;text-transform:uppercase;}
.cc-product-context dd {margin:0;font-size:.82rem;font-weight:650;color:#263a32;}
.cc-product-simulation {font-size:.74rem;color:#6b756f;margin:.2rem 0 1.05rem;}
.cc-role-hub-heading {margin:1.65rem 0 .8rem;}
.cc-role-hub-heading span {font-size:.74rem;font-weight:760;color:#28664f;letter-spacing:.08em;}
.cc-role-hub-heading h2 {margin:.15rem 0 .15rem;font-size:1.35rem;}
.cc-role-hub-heading p {margin:0;color:#5d6a64;}
@media (max-width: 760px) {
  .cc-product-context {align-items:start;flex-direction:column;}
  .cc-product-context dl {display:grid;grid-template-columns:repeat(3,1fr);gap:.7rem;width:100%;}
  .cc-product-context dl div {min-width:0;}
}
</style>
"""


def inject_product_styles(st: Any) -> None:
    st.markdown(PRODUCT_STYLE, unsafe_allow_html=True)
