"""Nurse-only presentation helpers for the manual safety review workspace.

The nurse surface deliberately translates persisted questionnaire answers into
human-readable Chinese.  FHIR identifiers and value[x] representations remain
available in the audit/engineering surfaces, not in the nurse's primary work.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from continucare.fhir.questionnaires import visible_questionnaire_items
from continucare.product_mvp import ProductContext
from continucare.services.patient_checkin import (
    questionnaire_candidate_confirmation_display,
)


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
    "nurse_in_progress": "人工复核中",
    "communication_pending": "复核决定已记录",
    "doctor_brief_pending": "医生速览待生成",
    "communication_ready": "沟通文字已核对",
    "doctor_brief_ready": "医生速览已生成",
    "story_complete": "本轮已完成",
    "task_rejected": "任务已停止",
    "task_cancelled": "任务已取消",
    "task_failed": "任务未完成",
    "task_entered_in_error": "记录异常",
}


@dataclass(frozen=True, slots=True)
class NurseAnswerCard:
    question: str
    answer: str
    wide: bool = False


def nurse_stage_label(progress: Any) -> str:
    """Return the current workflow stage in nurse-facing language."""

    stage = getattr(getattr(progress, "stage", None), "value", None)
    if stage is None:
        stage = str(getattr(progress, "stage", "not_started"))
    return _STAGE_LABELS.get(stage, "状态待核对")


def build_nurse_answer_cards(
    questionnaire: dict[str, Any],
    answers: dict[str, Any],
) -> tuple[NurseAnswerCard, ...]:
    """Project governed answers as Chinese question/answer pairs only."""

    cards: list[NurseAnswerCard] = []
    for item in visible_questionnaire_items(questionnaire, answers):
        link_id = str(item.get("linkId") or "")
        if not link_id or link_id not in answers:
            continue
        question, answer = questionnaire_candidate_confirmation_display(
            questionnaire,
            link_id,
            answers[link_id],
        )
        cards.append(
            NurseAnswerCard(
                question=question,
                answer=answer,
                wide=link_id == "free-text-report",
            )
        )
    return tuple(cards)


def render_nurse_header(
    st: Any,
    context: ProductContext,
    progress: Any,
    *,
    pending_count: int,
    completed_count: int,
) -> None:
    """Render the same clinical workspace shell used by the doctor surface."""

    patient = context.patient
    patient_name = patient.display_name if patient is not None else "合成患者尚未载入"
    patient_id = patient.patient_id if patient is not None else "—"
    pathway = patient.pathway_code if patient is not None else "—"
    next_visit = patient.next_visit_date if patient is not None else "—"
    initial = patient_name[:1] if patient_name else "患"
    today = datetime.now().strftime("%Y年%m月%d日")
    stage_label = nurse_stage_label(progress)

    st.markdown(
        f"""
        <span class="cc-nurse-v3" aria-hidden="true"></span>
        <aside class="cc-nurse-sidebar" aria-label="护士工作台导航">
          <div class="cc-nurse-brand">
            <span class="cc-nurse-brand__mark" aria-hidden="true">C</span>
            <span><strong>ContinuCare</strong><small>连续照护平台</small></span>
          </div>
          <nav class="cc-nurse-nav">
            <p>工作台</p>
            <a class="is-active" href="/nurse_risk_center"><span aria-hidden="true">✓</span>安全复核</a>
            <a href="#cc-nurse-supplemental"><span aria-hidden="true">＋</span>患者补充</a>
            <p>协作</p>
            <a href="/doctor_summary"><span aria-hidden="true">医</span>医生工作台</a>
            <a href="/audit_log"><span aria-hidden="true">≡</span>操作记录</a>
          </nav>
          <div class="cc-nurse-sidebar__account">
            <span aria-hidden="true">护</span>
            <div><strong>护理工作台</strong><small>当前账号在线</small></div>
          </div>
        </aside>
        <header class="cc-nurse-topbar" aria-label="护士工作台页眉">
          <div class="cc-nurse-breadcrumb"><span>患者管理</span><b>/</b><strong>安全复核</strong></div>
          <div class="cc-nurse-topbar__right"><span>{html.escape(today)}</span><span class="cc-nurse-topbar__avatar">护</span></div>
        </header>
        <section class="cc-nurse-hero" aria-labelledby="cc-nurse-page-title">
          <div>
            <p class="cc-nurse-eyebrow">护理协作</p>
            <h1 id="cc-nurse-page-title">患者安全复核</h1>
            <p>查看患者确认的中文记录，由护士决定是否补充核实或上报医生</p>
          </div>
          <div class="cc-nurse-stage"><span aria-hidden="true"></span>{html.escape(stage_label)}</div>
        </section>
        <section class="cc-nurse-patient" aria-label="当前患者范围">
          <div class="cc-nurse-patient__identity">
            <span class="cc-nurse-avatar" aria-hidden="true">{html.escape(initial)}</span>
            <div>
              <div class="cc-nurse-patient__name"><strong>{html.escape(patient_name)}</strong><span>演示数据</span></div>
              <p>患者编号 · {html.escape(patient_id)}</p>
            </div>
          </div>
          <dl class="cc-nurse-patient__meta">
            <div><dt>随访路径</dt><dd>{html.escape(pathway)}</dd></div>
            <div><dt>下次复诊</dt><dd>{html.escape(next_visit)}</dd></div>
            <div><dt>待处理</dt><dd>{pending_count} 条</dd></div>
            <div><dt>已处理</dt><dd>{completed_count} 条</dd></div>
          </dl>
        </section>
        """,
        unsafe_allow_html=True,
    )


def render_nurse_answer_cards(
    st: Any,
    cards: tuple[NurseAnswerCard, ...],
    *,
    pathway_label: str,
) -> None:
    """Render questionnaire content without exposing storage or FHIR codes."""

    if cards:
        rendered = "".join(
            (
                '<article class="cc-nurse-answer-card'
                + (" cc-nurse-answer-card--wide" if card.wide else "")
                + '"><p>'
                + html.escape(card.question)
                + "</p><strong>"
                + html.escape(card.answer)
                + "</strong></article>"
            )
            for card in cards
        )
    else:
        rendered = '<p class="cc-nurse-answer-empty">当前没有可显示的患者回答。</p>'
    st.markdown(
        f"""
        <section class="cc-nurse-answer-panel" aria-labelledby="cc-nurse-answer-title">
          <header>
            <div><p class="cc-nurse-section-kicker">患者确认内容</p><h2 id="cc-nurse-answer-title">本次随访回答</h2></div>
            <span>{html.escape(pathway_label)}</span>
          </header>
          <div class="cc-nurse-answer-grid">{rendered}</div>
          <footer><span aria-hidden="true">i</span><p>这里展示患者确认的中文原意。软件不标记异常，也不把患者自报程度转换为风险等级。</p></footer>
        </section>
        """,
        unsafe_allow_html=True,
    )


NURSE_SURFACE_STYLE = """
<style>
:root {
  --nurse-ink:#182230; --nurse-muted:#667085; --nurse-line:#e4e9ef;
  --nurse-bg:#f4f7f9; --nurse-card:#fff; --nurse-soft:#eef5f6;
  --nurse-teal:#176b68; --nurse-teal-dark:#0d4f4d; --nurse-warm:#fff8eb;
}
.cc-nurse-v3 {display:none;}
.stApp:has(.cc-nurse-v3) {background:var(--nurse-bg);color:var(--nurse-ink);}
.stApp:has(.cc-nurse-v3) [data-testid="stSidebar"],
.stApp:has(.cc-nurse-v3) [data-testid="stHeader"],
.stApp:has(.cc-nurse-v3) [data-testid="stToolbar"],
.stApp:has(.cc-nurse-v3) [data-testid="stDecoration"],
.stApp:has(.cc-nurse-v3) [data-testid="stStatusWidget"],
.stApp:has(.cc-nurse-v3) [data-testid="stFooter"],
.stApp:has(.cc-nurse-v3) #MainMenu {display:none !important;}
.stApp:has(.cc-nurse-v3) [data-testid="stAppViewContainer"] {margin-left:0 !important;}
.stApp:has(.cc-nurse-v3) .block-container {width:100%;max-width:none;padding:0 2rem 4rem 17.5rem;}
.stApp:has(.cc-nurse-v3) > div {font-family:Inter,"PingFang SC","Microsoft YaHei",sans-serif;}
.cc-nurse-sidebar {position:fixed;z-index:50;inset:0 auto 0 0;width:15.5rem;display:flex;flex-direction:column;padding:1.35rem 1rem 1rem;background:#0d2928;color:#fff;box-shadow:14px 0 40px rgba(8,31,30,.08);}
.cc-nurse-brand {display:flex;align-items:center;gap:.7rem;padding:0 .55rem 1.4rem;color:#fff;}
.cc-nurse-brand__mark {width:34px;height:34px;display:grid;place-items:center;border-radius:10px;background:#35a49d;color:#fff;font-weight:800;}
.cc-nurse-brand strong,.cc-nurse-brand small {display:block;}.cc-nurse-brand strong {font-size:.96rem;}.cc-nurse-brand small {margin-top:.15rem;color:#9bbab8;font-size:.68rem;}
.cc-nurse-nav {display:flex;flex-direction:column;gap:.3rem;}.cc-nurse-nav p {margin:1.15rem .7rem .35rem;color:#6f9996;font-size:.64rem;font-weight:800;letter-spacing:.12em;text-transform:uppercase;}
.cc-nurse-nav a {display:flex;align-items:center;gap:.75rem;padding:.72rem .78rem;border-radius:10px;color:#b9d0ce !important;font-size:.82rem;font-weight:650;text-decoration:none !important;}
.cc-nurse-nav a > span {width:1.25rem;color:#7da5a2;text-align:center;}.cc-nurse-nav a:hover {background:rgba(255,255,255,.07);color:#fff !important;}.cc-nurse-nav a.is-active {background:#1b4c49;color:#fff !important;box-shadow:inset 3px 0 #54c3ba;}
.cc-nurse-sidebar__account {display:flex;align-items:center;gap:.7rem;margin-top:auto;padding:.8rem;border-top:1px solid rgba(255,255,255,.1);}.cc-nurse-sidebar__account > span {width:34px;height:34px;display:grid;place-items:center;border-radius:10px;background:#285b57;color:#d8f0ee;font-size:.76rem;font-weight:800;}.cc-nurse-sidebar__account strong,.cc-nurse-sidebar__account small {display:block;}.cc-nurse-sidebar__account strong {color:#f4f9f8;font-size:.75rem;}.cc-nurse-sidebar__account small {margin-top:.15rem;color:#86aaa7;font-size:.64rem;}
.cc-nurse-topbar {position:sticky;z-index:30;top:0;height:64px;margin:0 -2rem;padding:0 2rem;display:flex;align-items:center;justify-content:space-between;background:rgba(255,255,255,.94);border-bottom:1px solid var(--nurse-line);backdrop-filter:blur(14px);}.cc-nurse-breadcrumb {display:flex;align-items:center;gap:.55rem;color:#98a2b3;font-size:.76rem;}.cc-nurse-breadcrumb b {font-weight:500;}.cc-nurse-breadcrumb strong {color:#344054;}.cc-nurse-topbar__right {display:flex;align-items:center;gap:.85rem;color:#667085;font-size:.72rem;}.cc-nurse-topbar__avatar {width:32px;height:32px;display:grid;place-items:center;border-radius:9px;background:#e1f1ef;color:#155f5b;font-weight:800;}
.cc-nurse-hero {display:flex;align-items:center;justify-content:space-between;gap:1rem;padding:2rem 0 1.1rem;}.cc-nurse-eyebrow,.cc-nurse-section-kicker {margin:0 0 .3rem;color:var(--nurse-teal);font-size:.64rem;font-weight:800;letter-spacing:.1em;text-transform:uppercase;}.cc-nurse-hero h1 {margin:0;color:var(--nurse-ink);font-size:1.85rem;letter-spacing:-.035em;}.cc-nurse-hero p:last-child {margin:.38rem 0 0;color:var(--nurse-muted);font-size:.82rem;}.cc-nurse-stage {display:flex;align-items:center;gap:.45rem;padding:.45rem .7rem;border:1px solid #c9e2df;border-radius:999px;background:#edf8f6;color:#155f5b;font-size:.72rem;font-weight:750;}.cc-nurse-stage span {width:7px;height:7px;border-radius:50%;background:#35a49d;}
.cc-nurse-patient {display:flex;align-items:center;justify-content:space-between;gap:1.4rem;padding:1rem 1.15rem;border-radius:18px;background:#fff;box-shadow:0 8px 28px rgba(16,24,40,.06);}.cc-nurse-patient__identity {display:flex;align-items:center;gap:.8rem;min-width:14rem;}.cc-nurse-avatar {width:45px;height:45px;display:grid;place-items:center;border-radius:13px;background:#dcefed;color:#155f5b;font-size:1.05rem;font-weight:800;}.cc-nurse-patient__name {display:flex;align-items:center;gap:.5rem;}.cc-nurse-patient__name strong {color:#1d2939;font-size:.95rem;}.cc-nurse-patient__name span {padding:.16rem .38rem;border-radius:5px;background:#eef2f6;color:#667085;font-size:.6rem;font-weight:750;}.cc-nurse-patient__identity p {margin:.25rem 0 0;color:#98a2b3;font-size:.66rem;}.cc-nurse-patient__meta {display:grid;grid-template-columns:repeat(4,minmax(6rem,1fr));margin:0;}.cc-nurse-patient__meta div {padding:0 1rem;border-left:1px solid var(--nurse-line);}.cc-nurse-patient__meta dt {color:#98a2b3;font-size:.63rem;}.cc-nurse-patient__meta dd {margin:.22rem 0 0;color:#344054;font-size:.76rem;font-weight:750;}
.st-key-cc_nurse_refresh_bar {display:flex;justify-content:flex-end;margin:.65rem 0;}.st-key-cc_nurse_refresh_bar button {min-height:34px !important;padding:.25rem .65rem !important;border:1px solid var(--nurse-line) !important;border-radius:8px !important;background:#fff !important;color:#475467 !important;font-size:.72rem !important;box-shadow:none !important;}
.cc-nurse-boundary {margin:.55rem 0 1rem;padding:.75rem .9rem;border:1px solid #cfe1df;border-radius:11px;background:#f3f9f8;color:#315b58;font-size:.78rem;line-height:1.6;}
.st-key-cc_nurse_workspace > [data-testid="stVerticalBlock"] > [data-testid="stHorizontalBlock"] {align-items:flex-start;gap:1rem;}.st-key-cc_nurse_workspace [data-testid="stColumn"] {min-width:0;padding:1rem;border:1px solid var(--nurse-line);border-radius:16px;background:#fff;box-shadow:0 8px 24px rgba(16,24,40,.035);}
.cc-nurse-sort {margin:0 0 .55rem;color:#98a2b3;font-size:.68rem;}.stApp:has(.cc-nurse-v3) [data-testid="stTabs"] [role="tablist"] {gap:.3rem;padding:.25rem;border-radius:9px;background:#f2f5f7;}.stApp:has(.cc-nurse-v3) [data-testid="stTabs"] [role="tab"] {min-height:38px;padding:.35rem .65rem;border-radius:7px;color:#667085;font-size:.74rem;font-weight:700;}.stApp:has(.cc-nurse-v3) [data-testid="stTabs"] [role="tab"][aria-selected="true"] {background:#fff;color:var(--nurse-teal-dark) !important;box-shadow:0 1px 4px rgba(16,24,40,.08);}.stApp:has(.cc-nurse-v3) [data-testid="stTabs"] .react-aria-SelectionIndicator {display:none !important;}
.stApp:has(.cc-nurse-v3) [class*="st-key-cc_nurse_task_"] button {width:100%;min-height:48px !important;height:auto !important;padding:.7rem .75rem !important;border:1px solid var(--nurse-line) !important;border-radius:10px !important;background:#fff !important;color:#27364a !important;justify-content:flex-start !important;text-align:left !important;font-size:.79rem !important;font-weight:750 !important;box-shadow:none !important;}.stApp:has(.cc-nurse-v3) [class*="st-key-cc_nurse_task_"] button:hover {border-color:#a9cfcc !important;background:#f5faf9 !important;}.stApp:has(.cc-nurse-v3) [class*="st-key-cc_nurse_task_selected_"] button {border-color:#62aaa5 !important;background:#edf7f6 !important;box-shadow:inset 3px 0 #2b8d87 !important;}.cc-nurse-task-meta {margin:-.2rem 0 .55rem;padding:0 .75rem .5rem;color:#8995a3;font-size:.64rem;line-height:1.5;}
.cc-nurse-detail-head {display:flex;flex-wrap:wrap;gap:.45rem;margin:0 0 .75rem;}.cc-nurse-detail-head dt {display:none;}.cc-nurse-detail-head dd {margin:0;padding:.3rem .5rem;border-radius:7px;background:#f2f5f7;color:#52606d;font-size:.66rem;font-weight:700;}.cc-nurse-statement {margin:0 0 .85rem;padding:.85rem .9rem;border:1px solid #d7e6e4;border-radius:12px;background:#f6fbfa;}.cc-nurse-statement span {display:block;margin-bottom:.3rem;color:#6f8987;font-size:.65rem;font-weight:750;}.cc-nurse-statement strong {color:#203b39;font-size:1.04rem;line-height:1.55;}
.cc-nurse-answer-panel {margin:.8rem 0 1rem;padding:1rem;border:1px solid var(--nurse-line);border-radius:15px;background:#fff;}.cc-nurse-answer-panel > header {display:flex;align-items:flex-end;justify-content:space-between;gap:1rem;margin-bottom:.75rem;}.cc-nurse-answer-panel h2 {margin:0;color:#1d2939;font-size:1.05rem;}.cc-nurse-answer-panel > header > span {padding:.3rem .5rem;border:1px solid #c9e2df;border-radius:7px;background:#edf8f6;color:#155f5b;font-size:.63rem;font-weight:750;}.cc-nurse-answer-grid {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.6rem;}.cc-nurse-answer-card {min-width:0;padding:.75rem .8rem;border:1px solid #e8ecef;border-radius:10px;background:#fafbfc;}.cc-nurse-answer-card--wide {grid-column:1/-1;background:#f7faf9;}.cc-nurse-answer-card p {margin:0;color:#7b8795;font-size:.66rem;line-height:1.45;}.cc-nurse-answer-card strong {display:block;margin-top:.35rem;color:#1d2939;font-size:.9rem;line-height:1.5;overflow-wrap:anywhere;}.cc-nurse-answer-panel footer {display:flex;align-items:flex-start;gap:.5rem;margin-top:.75rem;padding-top:.7rem;border-top:1px solid #eef1f3;}.cc-nurse-answer-panel footer span {flex:none;width:18px;height:18px;display:grid;place-items:center;border-radius:50%;background:#e3f1f0;color:#176b68;font-size:.65rem;font-weight:800;}.cc-nurse-answer-panel footer p,.cc-nurse-answer-empty {margin:0;color:#667085;font-size:.68rem;line-height:1.55;}
.cc-nurse-status {margin:.8rem 0;padding:.8rem .9rem;border:0;border-radius:10px;background:#edf7f6;color:#254d4a;}.cc-nurse-status--caution,.cc-nurse-status--stopped {background:var(--nurse-warm);color:#7a4a16;}.cc-nurse-status--error {background:#fff1f0;color:#8f2f27;}.cc-nurse-status h2 {margin:0 0 .2rem;font-size:.92rem;}.cc-nurse-status p {margin:0;font-size:.74rem;line-height:1.55;}.cc-nurse-action-title {margin:1rem 0 .45rem;color:#667085;font-size:.68rem;font-weight:750;text-transform:uppercase;letter-spacing:.08em;}
.stApp:has(.cc-nurse-v3) [data-testid="stCheckbox"] {padding:.3rem .55rem;border:1px solid #e5e9ed;border-radius:8px;background:#fafbfc;}.stApp:has(.cc-nurse-v3) [data-testid="stCheckbox"] label {font-size:.76rem !important;}.stApp:has(.cc-nurse-v3) [role="radiogroup"] {gap:.5rem !important;}.stApp:has(.cc-nurse-v3) [role="radiogroup"] label {min-height:44px;padding:.5rem .65rem;border:1px solid var(--nurse-line);border-radius:9px;background:#fafbfc;font-size:.76rem;}.stApp:has(.cc-nurse-v3) textarea {border-color:var(--nurse-line) !important;border-radius:10px !important;background:#fff !important;}
.st-key-cc_nurse_primary button,.st-key-cc_nurse_primary_link a {width:100%;min-height:46px !important;border:0 !important;border-radius:10px !important;background:var(--nurse-teal) !important;color:#fff !important;font-size:.82rem !important;font-weight:750 !important;box-shadow:none !important;}.st-key-cc_nurse_primary button:hover,.st-key-cc_nurse_primary_link a:hover {background:var(--nurse-teal-dark) !important;}.st-key-cc_nurse_primary_link a * {color:#fff !important;}.cc-nurse-result-boundary {margin:.75rem 0;padding:.7rem .8rem;border:1px solid #e1e7ea;border-radius:10px;background:#f7f9fa;color:#536171;font-size:.7rem;line-height:1.55;}
.stApp:has(.cc-nurse-v3) [class*="st-key-cc_nurse_disclosure_"] button,.stApp:has(.cc-nurse-v3) [class*="st-key-cc_nurse_secondary_"] button {min-height:38px !important;border:0 !important;border-radius:7px !important;background:transparent !important;color:#287e78 !important;font-size:.7rem !important;box-shadow:none !important;}.cc-nurse-history {display:grid;grid-template-columns:3rem 5rem 1fr;gap:.5rem;padding:.5rem;border-bottom:1px solid var(--nurse-line);color:#52606d;font-size:.68rem;}.cc-nurse-outcomes {display:grid;grid-template-columns:1fr 1fr;gap:.65rem;margin:.75rem 0;}.cc-nurse-outcomes section {padding:.7rem;border:1px solid var(--nurse-line);border-radius:9px;background:#fafbfc;}.cc-nurse-outcomes h3 {margin:0 0 .3rem;font-size:.75rem;}.cc-nurse-outcomes ul {margin:0;padding-left:1rem;color:#667085;font-size:.68rem;line-height:1.55;}
.st-key-cc_nurse_record_link a {display:flex;min-height:38px;align-items:center;color:#287e78 !important;font-size:.72rem !important;text-decoration:none !important;}.cc-nurse-communication {margin:.7rem 0;padding:.8rem;border:1px solid var(--nurse-line);border-radius:10px;background:#fff;}.cc-nurse-communication h3 {margin:0 0 .35rem;font-size:.8rem;}.cc-nurse-communication p {margin:.2rem 0;color:#475467;font-size:.72rem;line-height:1.55;}.cc-nurse-mock {color:#9a6700 !important;font-weight:700;}
.st-key-cc_nurse_supplemental {scroll-margin-top:5rem;margin:1rem 0;padding:1rem;border:1px solid var(--nurse-line);border-radius:15px;background:#fff;box-shadow:0 8px 24px rgba(16,24,40,.035);}.cc-nurse-supplemental-head {display:flex;align-items:end;justify-content:space-between;gap:1rem;margin-bottom:.55rem;}.cc-nurse-supplemental-head h2 {margin:0;color:#1d2939;font-size:1.05rem;}.cc-nurse-supplemental-head p:last-child {margin:.3rem 0 0;color:#667085;font-size:.7rem;}.cc-nurse-supplemental-empty {margin:0;padding:.7rem;border-radius:9px;background:#f7f9fa;color:#7b8795;font-size:.72rem;}.cc-nurse-supplemental-card {margin:.6rem 0;padding:.75rem .8rem;border:1px solid #e6eaed;border-radius:10px;background:#fafbfc;}.cc-nurse-supplemental-card strong {display:block;color:#27364a;font-size:.78rem;}.cc-nurse-supplemental-card p {margin:.35rem 0 0;color:#52606d;font-size:.7rem;line-height:1.55;}.cc-nurse-supplemental-card span {display:inline-block;margin-top:.45rem;padding:.18rem .38rem;border-radius:6px;background:#eaf4f3;color:#176b68;font-size:.62rem;font-weight:750;}
.stApp:has(.cc-nurse-v3) [data-testid="stExpander"] {border:1px solid var(--nurse-line);border-radius:12px;background:#fff;}.stApp:has(.cc-nurse-v3) [data-testid="stAlert"] {border-radius:10px;}.stApp:has(.cc-nurse-v3) [data-testid="stCaptionContainer"] {color:var(--nurse-muted);}
@media (max-width:900px) {.stApp:has(.cc-nurse-v3) .block-container {padding-left:1.5rem;}.cc-nurse-sidebar {display:none;}.cc-nurse-topbar {margin:0 -1.5rem;padding:0 1.5rem;}.cc-nurse-patient {align-items:flex-start;flex-direction:column;}.cc-nurse-patient__meta {width:100%;grid-template-columns:repeat(2,1fr);}.cc-nurse-patient__meta div:nth-child(odd) {border-left:0;padding-left:0;}}
@media (max-width:640px) {.stApp:has(.cc-nurse-v3) .block-container {padding:0 .85rem 2.5rem;}.cc-nurse-topbar {height:58px;margin:0 -.85rem;padding:0 .9rem;}.cc-nurse-topbar__right > span:first-child {display:none;}.cc-nurse-hero {align-items:flex-start;flex-direction:column;padding:1.45rem 0 .9rem;}.cc-nurse-hero h1 {font-size:1.7rem;}.cc-nurse-patient {padding:.85rem;}.cc-nurse-patient__meta {grid-template-columns:1fr;}.cc-nurse-patient__meta div {padding:.5rem 0;border-left:0;border-top:1px solid var(--nurse-line);}.st-key-cc_nurse_workspace > [data-testid="stVerticalBlock"] > [data-testid="stHorizontalBlock"] {display:block !important;}.st-key-cc_nurse_workspace [data-testid="stColumn"] {width:100% !important;margin-bottom:.8rem;}.cc-nurse-answer-grid {grid-template-columns:1fr;}.cc-nurse-answer-card--wide {grid-column:auto;}.cc-nurse-outcomes {grid-template-columns:1fr;}.cc-nurse-history {grid-template-columns:3rem 1fr;}.cc-nurse-history span:last-child {grid-column:1/-1;}}
@media (prefers-reduced-motion:reduce) {* {scroll-behavior:auto !important;transition:none !important;animation:none !important;}}
</style>
"""


def inject_nurse_surface_styles(st: Any) -> None:
    st.markdown(NURSE_SURFACE_STYLE, unsafe_allow_html=True)
