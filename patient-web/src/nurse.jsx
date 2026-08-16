import React, { useCallback, useEffect, useMemo, useState } from "react";
import { loadNurseState, postCommand } from "./api";
import "./nurse.css";

const KNOWN_KINDS = new Set(["ready", "empty", "waiting", "waiting_patient", "fail_closed"]);

function Brand() {
  return (
    <div className="nurse-brand">
      <span className="nurse-brand-mark">C</span>
      <span><strong>ContinuCare</strong><small>连续照护平台</small></span>
    </div>
  );
}

function Sidebar() {
  return (
    <aside className="nurse-sidebar">
      <Brand />
      <nav aria-label="护士工作台导航">
        <p>工作台</p>
        <a className="active" href="/nurse"><span>✓</span>安全复核</a>
        <a href="#patient-supplemental"><span>＋</span>患者补充</a>
        <p>协作</p>
        <a href="#review-history"><span>≡</span>处理记录</a>
      </nav>
      <div className="nurse-account"><span>护</span><div><strong>护理工作台</strong><small>角色模拟模式</small></div></div>
    </aside>
  );
}

function Topbar({ onRefresh }) {
  return (
    <header className="nurse-topbar">
      <div><span>患者管理</span><b>/</b><strong>安全复核</strong></div>
      <div className="nurse-topbar-actions">
        <span>{new Intl.DateTimeFormat("zh-CN", { dateStyle: "long" }).format(new Date())}</span>
        <button type="button" onClick={onRefresh}>刷新</button>
        <span className="nurse-role-avatar">护</span>
      </div>
    </header>
  );
}

function PatientCard({ state }) {
  const patient = state.patient || {};
  return (
    <section className="nurse-patient-card" aria-label="当前患者范围">
      <div className="nurse-patient-identity">
        <span className="nurse-patient-avatar">{(patient.displayName || "患").slice(0, 1)}</span>
        <div><div><strong>{patient.displayName || "合成患者"}</strong><span>演示数据</span></div><p>患者编号 · {patient.patientId || "—"}</p></div>
      </div>
      <dl>
        <div><dt>随访路径</dt><dd>{patient.pathwayCode || "—"}</dd></div>
        <div><dt>下次复诊</dt><dd>{patient.nextVisitDate || "—"}</dd></div>
        <div><dt>待处理</dt><dd>{state.counts?.pending || 0} 条</dd></div>
        <div><dt>已处理</dt><dd>{state.counts?.completed || 0} 条</dd></div>
      </dl>
    </section>
  );
}

function ShiftSummary({ state }) {
  const supplemental = state.supplementalReports || [];
  const total = (state.counts?.pending || 0) + (state.counts?.completed || 0);
  return (
    <section className="nurse-shift-summary" aria-label="今日护理工作概览">
      <div><span>今日收到</span><strong>{total}</strong><small>份患者确认记录</small></div>
      <div><span>等待人工处理</span><strong>{state.counts?.pending || 0}</strong><small>含正在复核</small></div>
      <div><span>已完成复核</span><strong>{state.counts?.completed || 0}</strong><small>均为护士人工决定</small></div>
      <div><span>患者补充</span><strong>{supplemental.length}</strong><small>{supplemental.filter((item) => item.status === "requested").length} 份待查看</small></div>
    </section>
  );
}

function Queue({ state, activeQueue, onQueueChange, onSelect }) {
  const tasks = activeQueue === "pending" ? state.pendingTasks : state.completedTasks;
  return (
    <section className="nurse-queue-card">
      <header><div><span>任务队列</span><h2>人工安全复核</h2></div><small>按提交时间排序</small></header>
      <div className="nurse-queue-tabs" role="tablist" aria-label="复核任务队列">
        <button type="button" role="tab" aria-selected={activeQueue === "pending"} className={activeQueue === "pending" ? "active" : ""} onClick={() => onQueueChange("pending")}>待处理 <span>{state.counts?.pending || 0}</span></button>
        <button type="button" role="tab" aria-selected={activeQueue === "completed"} className={activeQueue === "completed" ? "active" : ""} onClick={() => onQueueChange("completed")}>已处理 <span>{state.counts?.completed || 0}</span></button>
      </div>
      <div className="nurse-task-list">
        {(tasks || []).map((task) => (
          <button type="button" className={task.taskId === state.selectedTaskId ? "nurse-task active" : "nurse-task"} onClick={() => onSelect(task.taskId)} key={task.taskId}>
            <span className={`nurse-task-dot ${task.tone || "active"}`} />
            <span><strong>{task.patientLabel}</strong><small>{new Date(task.submittedAt).toLocaleString("zh-CN", { hour12: false })}</small></span>
            <em>{task.statusTitle}</em>
          </button>
        ))}
        {!tasks?.length ? <p className="nurse-empty-list">当前没有{activeQueue === "pending" ? "待处理" : "已处理"}任务。</p> : null}
      </div>
    </section>
  );
}

function AnswerGrid({ task }) {
  return (
    <section className="nurse-answer-section">
      <header><div><span>患者确认内容</span><h2>本次随访回答</h2></div><b>{task.pathwayLabel}</b></header>
      <div className="nurse-answer-grid">
        {(task.answers || []).map((item, index) => (
          <article className={item.wide ? "wide" : ""} key={`${item.question}-${index}`}>
            <p>{item.question}</p><strong>{item.answer}</strong>
          </article>
        ))}
        {!task.answers?.length ? <p className="nurse-empty-list">当前没有可显示的患者回答。</p> : null}
      </div>
      <footer><span>i</span><p>这里展示患者确认的中文原意。软件不标记异常，也不把患者自报程度转换为风险等级。</p></footer>
    </section>
  );
}

function OutcomeForm({ state, task, busy, onCommand }) {
  const [checked, setChecked] = useState(() => new Set());
  const [outcome, setOutcome] = useState(state.outcomeOptions?.[0]?.value || "");
  const [note, setNote] = useState("");
  useEffect(() => { setChecked(new Set()); setOutcome(state.outcomeOptions?.[0]?.value || ""); setNote(""); }, [task.taskId, state.outcomeOptions]);
  const selected = state.outcomeOptions?.find((item) => item.value === outcome);
  const toggle = (id) => setChecked((current) => { const next = new Set(current); next.has(id) ? next.delete(id) : next.add(id); return next; });
  const ready = checked.size === state.checklist?.length && note.trim();
  return (
    <section className="nurse-action-panel">
      <header><span>人工处理</span><h2>完成安全复核清单</h2><p>软件不会自动勾选，也不会推荐处理结果。</p></header>
      <div className="nurse-checklist">
        {(state.checklist || []).map((item) => <label key={item.id}><input type="checkbox" checked={checked.has(item.id)} onChange={() => toggle(item.id)} /><span>{item.label}</span></label>)}
      </div>
      <fieldset className="nurse-outcomes"><legend>护士人工处理结果</legend>
        {(state.outcomeOptions || []).map((item) => <label className={outcome === item.value ? "active" : ""} key={item.value}><input type="radio" name="outcome" value={item.value} checked={outcome === item.value} onChange={(event) => setOutcome(event.target.value)} /><span><strong>{item.label}</strong><small>{item.help}</small></span></label>)}
      </fieldset>
      {selected ? <p className="nurse-outcome-help">{selected.help}</p> : null}
      <label className="nurse-note"><span>人工复核说明（必填）</span><textarea rows="4" maxLength="2000" value={note} onChange={(event) => setNote(event.target.value)} placeholder="请记录实际核对了什么，以及为什么作出本次决定。" /></label>
      <button className="nurse-primary-button" type="button" disabled={busy || !ready} onClick={() => onCommand("/api/nurse/tasks/outcome", { taskId: task.taskId, outcome, note: note.trim(), checklist: Array.from(checked) })}>保存护士人工决定</button>
    </section>
  );
}

function StopAction({ action, task, busy, onCommand, onClose }) {
  const [note, setNote] = useState("");
  const label = action.label || (action.value === "reject" ? "拒绝处理" : "取消任务");
  return (
    <div className="nurse-modal-backdrop" role="presentation">
      <section className="nurse-modal" role="dialog" aria-modal="true" aria-labelledby="stop-title">
        <span>停止路径</span><h2 id="stop-title">确认{label}</h2><p>这会停止后续业务动作；已有记录仍会保留供追溯。</p>
        <label className="nurse-note"><span>停止处理说明（必填）</span><textarea rows="4" value={note} maxLength="2000" onChange={(event) => setNote(event.target.value)} /></label>
        <div><button type="button" className="nurse-secondary-button" onClick={onClose}>返回</button><button type="button" className="nurse-danger-button" disabled={busy || !note.trim()} onClick={() => onCommand(`/api/nurse/tasks/${action.value}`, { taskId: task.taskId, note: note.trim() })}>确认{label}</button></div>
      </section>
    </div>
  );
}

function TaskActions({ state, task, busy, onCommand }) {
  const [stopAction, setStopAction] = useState(null);
  const action = task.primaryAction;
  return (
    <>
      {action === "record_outcome" ? <OutcomeForm state={state} task={task} busy={busy} onCommand={onCommand} /> : null}
      {action === "acknowledge" ? <button className="nurse-primary-button" type="button" disabled={busy} onClick={() => onCommand("/api/nurse/tasks/acknowledge", { taskId: task.taskId })}>{task.primaryLabel}</button> : null}
      {action === "start" ? <button className="nurse-primary-button" type="button" disabled={busy} onClick={() => onCommand("/api/nurse/tasks/start", { taskId: task.taskId })}>{task.primaryLabel}</button> : null}
      {action === "approve_draft" ? <button className="nurse-primary-button" type="button" disabled={busy} onClick={() => onCommand("/api/nurse/tasks/approve-draft", { taskId: task.taskId })}>{task.primaryLabel}</button> : null}
      {action === "open_doctor" ? <div className="nurse-boundary-note"><span>护士决定已经保存，医生端会从同一记录链读取这条上报。</span><a href={state.links?.doctor || "http://127.0.0.1:8520/?view=collaboration"}>打开医生协作待办 →</a></div> : null}
      {task.secondaryActions?.length ? <div className="nurse-secondary-actions">{task.secondaryActions.map((item) => <button type="button" key={item.value} onClick={() => setStopAction(item)}>{item.label}</button>)}</div> : null}
      {stopAction ? <StopAction action={stopAction} task={task} busy={busy} onCommand={onCommand} onClose={() => setStopAction(null)} /> : null}
    </>
  );
}

function TaskDetail({ state, busy, onCommand }) {
  const task = state.selectedTask;
  if (!task) return <section className="nurse-detail-card nurse-empty-panel"><h2>目前没有待复核记录</h2><p>新的患者确认记录会自动进入人工安全复核队列。</p></section>;
  return (
    <section className="nurse-detail-card">
      <header className="nurse-detail-head"><div><span>患者确认记录人工安全复核</span><small>{new Date(task.submittedAt).toLocaleString("zh-CN", { hour12: false })}</small></div><b className={`tone-${task.tone}`}>{task.statusTitle}</b></header>
      <blockquote><span>患者确认的表述</span><strong>{task.confirmedStatement}</strong>{task.originalQuote ? <p>患者原话：“{task.originalQuote}”</p> : null}</blockquote>
      <AnswerGrid task={task} />
      <section className={`nurse-status-card tone-${task.tone}`}><h2>{task.statusTitle}</h2><p>{task.statusDetail}</p></section>
      {task.communicationText ? <section className="nurse-communication"><span>待人工核对的沟通文字</span><p>{task.communicationText}</p><small>{task.communicationMarker}</small></section> : null}
      <TaskActions state={state} task={task} busy={busy} onCommand={onCommand} />
      <p className="nurse-result-boundary">{state.boundaries?.result}</p>
      {task.reviewNote ? <section className="nurse-recorded"><strong>{task.outcomeLabel || "已记录处理结果"}</strong><p>{task.reviewNote}</p></section> : null}
      {task.history?.length ? <details id="review-history" className="nurse-history"><summary>查看处理记录</summary>{task.history.map((item) => <div key={`${item.version}-${item.occurredAt}`}><strong>{item.version}</strong><span>{item.status}</span><time>{item.occurredAt}</time></div>)}</details> : null}
    </section>
  );
}

function Supplemental({ state, busy, onCommand }) {
  const [notes, setNotes] = useState({});
  return (
    <section className="nurse-supplemental" id="patient-supplemental">
      <header><div><span>患者原话</span><h2>患者原话与补充说明</h2><p>包括系统无法可靠结构化、由患者发送后转入人工复核的原话；均未作临床风险判断。</p></div><b>{state.supplementalReports?.filter((item) => item.status === "requested").length || 0} 条待处理</b></header>
      <div className="nurse-supplemental-grid">
        {(state.supplementalReports || []).map((report) => {
          const isHandoff = report.reportKind === "semantic_handoff";
          const note = notes[report.reportId] ?? (isHandoff ? "已查看患者原话；内容仍为未评估，未作临床风险判断。" : "已查看患者确认的补充说明；未作临床风险判断。");
          return <article key={report.reportId}><div><span>{report.status === "requested" ? `${report.kindLabel || "待人工复核"} · ${isHandoff ? "未评估" : "待处理"}` : "已完成复核"}</span>{report.createdAt ? <time>{new Date(report.createdAt).toLocaleString("zh-CN", { hour12: false })}</time> : null}<h3>患者说：{report.originalText}</h3>{isHandoff ? <p>• 未生成结构化指标、Observation 或风险结论</p> : null}{report.meanings?.map((item, index) => <p key={`${item}-${index}`}>• {item}</p>)}</div>{report.status === "requested" ? <><textarea aria-label="患者原话复核记录" rows="3" maxLength="2000" value={note} onChange={(event) => setNotes((current) => ({ ...current, [report.reportId]: event.target.value }))} /><button type="button" disabled={busy || !note.trim()} onClick={() => onCommand("/api/nurse/supplemental/review", { reportId: report.reportId, supplementalGeneration: state.supplementalGeneration, note: note.trim() })}>确认已人工复核</button></> : <small>{report.reviewNote || "已查看"}</small>}</article>;
        })}
        {!state.supplementalReports?.length ? <p className="nurse-empty-list">当前没有待复核的患者原话或补充说明。</p> : null}
      </div>
    </section>
  );
}

function NurseWorkspace({ state, busy, onCommand, onRefresh, onSelect }) {
  const [activeQueue, setActiveQueue] = useState(state.counts?.pending ? "pending" : "completed");
  useEffect(() => { if (!state.counts?.pending) setActiveQueue("completed"); }, [state.counts?.pending]);
  const changeQueue = (queue) => {
    setActiveQueue(queue);
    const first = (queue === "pending" ? state.pendingTasks : state.completedTasks)?.[0];
    if (first && first.taskId !== state.selectedTaskId) onSelect(first.taskId);
  };
  return (
    <div className="nurse-page">
      <Sidebar />
      <main className="nurse-main">
        <Topbar onRefresh={onRefresh} />
        <section className="nurse-content">
          <header className="nurse-hero"><div><span>护理协作</span><h1>患者安全复核</h1><p>查看患者确认的中文记录，由护士决定是否补充核实或上报医生</p></div><b><i />{state.stageLabel}</b></header>
          <PatientCard state={state} />
          <ShiftSummary state={state} />
          <p className="nurse-role-boundary">{state.boundaries?.role}</p>
          <div className="nurse-workspace"><Queue state={state} activeQueue={activeQueue} onQueueChange={changeQueue} onSelect={onSelect} /><TaskDetail state={state} busy={busy} onCommand={onCommand} /></div>
          <Supplemental state={state} busy={busy} onCommand={onCommand} />
          <footer className="nurse-page-footer">仅使用合成数据 · 不提供临床判断 · 不执行真实发送</footer>
        </section>
      </main>
    </div>
  );
}

export default function NurseApp() {
  const [state, setState] = useState(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const refresh = useCallback(async (taskId = "") => {
    setError("");
    try {
      const next = await loadNurseState(typeof taskId === "string" ? taskId : "");
      if (!KNOWN_KINDS.has(next.kind)) throw new Error("服务器返回了未知护士端状态");
      setState(next);
    } catch (exception) { setError(exception.message || "护士端暂时不可用"); }
  }, []);
  useEffect(() => { refresh(); }, [refresh]);
  const selectTask = useCallback((taskId) => refresh(taskId), [refresh]);
  const command = useCallback(async (path, payload) => {
    if (!state?.generation) { setError("页面状态已经失效，请刷新"); return false; }
    setBusy(true); setError(""); setNotice("");
    try {
      await postCommand(path, { ...payload, generation: state.generation });
      setNotice("操作已保存到同一记录链。系统未进行临床风险分级。");
      await refresh(payload.taskId || state.selectedTaskId || "");
      return true;
    } catch (exception) {
      setError(exception.message || "操作未完成");
      if (exception.code === "state_conflict") await refresh();
      return false;
    } finally { setBusy(false); }
  }, [state, refresh]);

  const content = useMemo(() => {
    if (!state) return null;
    if (state.kind === "fail_closed") return <div className="nurse-state-page"><Brand /><h1>暂时无法继续</h1><p>{state.message}</p><button onClick={() => refresh()}>重新读取状态</button></div>;
    if (state.kind === "waiting" || state.kind === "waiting_patient") return <div className="nurse-state-page"><Brand /><span>护理工作台</span><h1>{state.kind === "waiting" ? "等待医生启动随访" : "等待患者提交并确认记录"}</h1><p>有新的患者确认记录后，会自动进入人工安全复核队列。</p><button onClick={() => refresh()}>刷新状态</button></div>;
    return <NurseWorkspace state={state} busy={busy} onCommand={command} onRefresh={() => refresh(state.selectedTaskId || "")} onSelect={selectTask} />;
  }, [state, busy, command, refresh, selectTask]);

  if (!state && !error) return <div className="nurse-boot"><div className="nurse-spinner" /><p>正在读取人工安全复核队列…</p></div>;
  return <>{content}{notice ? <div className="nurse-toast success" role="status"><span>{notice}</span><button onClick={() => setNotice("")}>×</button></div> : null}{error ? <div className="nurse-toast" role="alert"><span>{error}</span><button onClick={() => setError("")}>×</button></div> : null}{busy ? <div className="nurse-busy" role="status"><div className="nurse-spinner" /><span>正在保存人工操作…</span></div> : null}</>;
}
