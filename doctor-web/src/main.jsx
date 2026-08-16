import React, { useCallback, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { confirmPlan, createSession, loadDashboard, loadPatients, loadPlanning } from "./api";
import "./styles.css";

function Citation({ ids = [] }) {
  if (!ids.length) return null;
  const label = ids.length === 1 ? ids[0] : `${ids[0]}–${ids[ids.length - 1]}`;
  return <a className="citation" href={`#source-${ids[0]}`} title={`来源：${ids.join("、")}`}>[{label}]</a>;
}

function Login({ onLogin, busy, error }) {
  const [key, setKey] = useState("");
  return <main className="login-page"><section className="login-card"><div className="brand-mark">C</div><p className="eyebrow">ContinuCare</p><h1>医生工作台</h1><p className="login-copy">使用医院配置的访问密钥登录。</p><form onSubmit={(event) => { event.preventDefault(); void onLogin(key); }}><label htmlFor="access-key">访问密钥</label><input id="access-key" type="password" autoComplete="current-password" value={key} onChange={(event) => setKey(event.target.value)} required />{error ? <p className="form-error" role="alert">{error}</p> : null}<button type="submit" disabled={busy || !key.trim()}>{busy ? "正在登录…" : "登录"}</button></form></section></main>;
}

function Sidebar({ view, onViewChange, collaborationCount = 0 }) {
  return <aside className="sidebar"><div className="brand"><span className="brand-mark">C</span><div><strong>ContinuCare</strong><small>连续照护平台</small></div></div><nav aria-label="医生工作台导航"><p>工作台</p><button className={view === "planning" ? "active" : ""} onClick={() => onViewChange("planning")}><span>＋</span>创建随访方案</button><button className={view === "monitoring" ? "active" : ""} onClick={() => onViewChange("monitoring")}><span>⌁</span>随访观察</button><button className={view === "collaboration" ? "active" : ""} onClick={() => onViewChange("collaboration")}><span>↗</span>护理协作{collaborationCount ? <b className="nav-count">{collaborationCount}</b> : null}</button><p>当前方案</p><button onClick={() => { onViewChange("planning"); setTimeout(() => document.querySelector("#ehr-context")?.scrollIntoView(), 0); }}><span>▤</span>患者档案</button><button onClick={() => { onViewChange("planning"); setTimeout(() => document.querySelector("#plan-candidates")?.scrollIntoView(), 0); }}><span>✓</span>监测指标</button></nav><div className="account"><span>医</span><div><strong>医生工作台</strong><small>安全会话已启用</small></div></div></aside>;
}

function PatientCard({ patient }) {
  return <section className="patient-card" id="patient"><div className="patient-identity"><span>{patient.displayName.slice(0, 1)}</span><div><div><h2>{patient.displayName}</h2>{patient.synthetic ? <b>演示数据</b> : null}</div><p>患者编号 · {patient.patientId}</p></div></div><dl><div><dt>随访路径</dt><dd>{patient.pathwayCode}</dd></div><div><dt>加入日期</dt><dd>{patient.enrollmentDate}</dd></div><div><dt>下次复诊</dt><dd>{patient.nextVisitDate}</dd></div><div><dt>患者状态</dt><dd>{patient.status}</dd></div></dl></section>;
}

function PatientToolbar({ patients, patientId, onPatientChange, onRefresh, busy }) {
  return <div className="toolbar"><label>当前患者<select value={patientId} onChange={(event) => onPatientChange(event.target.value)}>{patients.map((item) => <option value={item.patientId} key={item.patientId}>{item.displayName}</option>)}</select></label><button onClick={onRefresh} disabled={busy}>{busy ? "正在刷新…" : "刷新数据"}</button></div>;
}

function ContextCard({ item }) {
  return <article className="context-card"><header><span>{item.category}</span><b>{item.status === "confirmed" ? "已确认" : "患者自报"}</b></header><h3>{item.label}</h3><p>{item.value}</p><footer><span>{item.planningUse}</span><small>{item.sourceReference} · {item.recordedAt}</small></footer></article>;
}

function EvidenceDetails({ candidate }) {
  return <details className="candidate-evidence"><summary>查看推荐依据与来源 <b>{candidate.evidence.length}</b></summary><div>{candidate.evidence.map((evidence) => <article key={evidence.claimId}><p>{evidence.claim}</p><span>{evidence.authority} · {evidence.locator}</span>{evidence.canonicalUrl ? <a href={evidence.canonicalUrl} target="_blank" rel="noreferrer">打开来源</a> : null}</article>)}</div></details>;
}

function buildRecordPoints(candidates) {
  const grouped = new Map();
  candidates.forEach((candidate) => {
    const metadata = candidate.recordPoint || { recordPointId: `metric:${candidate.metricId}`, displayName: candidate.displayName, fields: [], kind: "metric" };
    const current = grouped.get(metadata.recordPointId) || { recordPointId: metadata.recordPointId, metadata, candidates: [], metricIds: [], categoryId: candidate.categoryId };
    current.candidates.push(candidate); current.metricIds.push(candidate.metricId); grouped.set(metadata.recordPointId, current);
  });
  return [...grouped.values()];
}

function RecordPointCard({ point, selection, onToggle, onFrequency }) {
  const required = point.candidates.some((item) => item.required);
  const checked = point.metricIds.every((metricId) => Boolean(selection[metricId]?.selected));
  const lead = point.candidates[0];
  const frequency = selection[lead.metricId]?.frequency || lead.defaultFrequency;
  const evidence = [...new Map(point.candidates.flatMap((item) => item.evidence).map((item) => [item.claimId, item])).values()];
  const contexts = [...new Map(point.candidates.flatMap((item) => item.contextUsed).map((item) => [item.contextId, item])).values()];
  const intents = [...new Set(point.candidates.map((item) => item.clinicalIntent))];
  const evidenceCandidate = { ...lead, evidence };
  return <article className={`candidate-card ${checked ? "selected" : ""} ${required ? "required" : ""}`}><div className="candidate-main"><label className={`metric-switch ${required ? "locked" : ""}`} title={required ? "当前治疗路径的核心记录要点" : ""}><input type="checkbox" checked={checked} disabled={required} onChange={() => onToggle(point.metricIds)} /><span aria-hidden="true" /><em>{required ? "核心必选" : checked ? "已选择" : "未选择"}</em></label><div className="candidate-copy"><div className="candidate-title"><span className={lead.priority === "重点建议" || required ? "priority high" : "priority"}>{lead.priority}</span><h3>{point.metadata.displayName}</h3></div><p>{intents.join("；")}</p>{point.metadata.fields?.length ? <div className="record-point-fields">{point.metadata.fields.map((field) => <span key={field.metricId}><b>{field.enableWhen ? "条件补充" : "主项"}</b>{field.label}</span>)}</div> : null}<div className="reason"><b>推荐原因</b><span>{lead.reason}</span></div><div className="context-chips">{contexts.map((item) => <span key={item.contextId}>{item.label}：{item.value}</span>)}</div></div><label className="frequency">记录频率<select value={frequency} disabled={!checked} onChange={(event) => onFrequency(point.metricIds, event.target.value)}>{lead.frequencyOptions.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}</select></label></div><EvidenceDetails candidate={evidenceCandidate} /></article>;
}

function CustomMetricBuilder({ config, items, onAdd, onRemove, onFrequency }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState({ displayName: "", dataType: "quantity", unit: "", clinicalIntent: "", frequency: "weekly" });
  const add = () => {
    const displayName = draft.displayName.trim();
    if (!displayName) return;
    onAdd({ ...draft, displayName, clinicalIntent: draft.clinicalIntent.trim(), localId: `new-${Date.now()}-${Math.random().toString(16).slice(2)}` });
    setDraft({ displayName: "", dataType: "quantity", unit: "", clinicalIntent: "", frequency: "weekly" });
    setOpen(false);
  };
  return <section className="custom-metrics"><header><div><span>医生自主补充</span><h3>自定义监测指标</h3><p>知识库未覆盖的个体化项目，可由医生直接加入本轮方案。</p></div><button type="button" onClick={() => setOpen((value) => !value)} disabled={items.length >= config.maximum}>{open ? "取消" : "＋ 添加指标"}</button></header>{open ? <div className="custom-form"><label>指标名称<input value={draft.displayName} maxLength="40" placeholder="例如：睡眠时长" onChange={(event) => setDraft({ ...draft, displayName: event.target.value })} /></label><label>数据类型<select value={draft.dataType} onChange={(event) => setDraft({ ...draft, dataType: event.target.value, unit: event.target.value === "quantity" ? draft.unit : "" })}>{config.dataTypes.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label><label>单位（可选）<input value={draft.unit} maxLength="16" disabled={draft.dataType !== "quantity"} placeholder="如：小时" onChange={(event) => setDraft({ ...draft, unit: event.target.value })} /></label><label>记录频率<select value={draft.frequency} onChange={(event) => setDraft({ ...draft, frequency: event.target.value })}>{config.frequencyOptions.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label><label className="custom-description">记录说明（可选）<input value={draft.clinicalIntent} maxLength="160" placeholder="告诉患者如何记录" onChange={(event) => setDraft({ ...draft, clinicalIntent: event.target.value })} /></label><button type="button" className="add-custom" onClick={add} disabled={!draft.displayName.trim()}>加入方案</button></div> : null}<div className="custom-list">{items.map((item) => <article key={item.metricId || item.localId}><div><strong>{item.displayName}</strong><span>{config.dataTypes.find((type) => type.value === item.dataType)?.label || item.dataType}{item.unit ? ` · ${item.unit}` : ""}</span>{item.clinicalIntent ? <p>{item.clinicalIntent}</p> : null}</div><label>频率<select value={item.frequency} onChange={(event) => onFrequency(item.metricId || item.localId, event.target.value)}>{config.frequencyOptions.map((frequency) => <option key={frequency.value} value={frequency.value}>{frequency.label}</option>)}</select></label><button type="button" onClick={() => onRemove(item.metricId || item.localId)} aria-label={`移除${item.displayName}`}>移除</button></article>)}</div></section>;
}

function PlanSummary({ planning, selection, customMetrics, startDate, endDate, saving, error, success, onStartDate, onEndDate, onConfirm }) {
  const recordPoints = buildRecordPoints(planning.candidates);
  const selected = recordPoints.filter((point) => point.metricIds.every((metricId) => selection[metricId]?.selected));
  const daily = selected.filter((point) => selection[point.metricIds[0]]?.frequency === "daily").length;
  const customDaily = customMetrics.filter((item) => item.frequency === "daily").length;
  const duration = startDate && endDate ? Math.max(0, Math.round((new Date(`${endDate}T00:00:00`) - new Date(`${startDate}T00:00:00`)) / 86400000) + 1) : 0;
  return <aside className="plan-summary"><div className="summary-kicker"><span>方案设置</span>{planning.currentPlan ? <b>当前 v{planning.currentPlan.planVersion}</b> : <b>新方案</b>}</div><h2>{planning.pathway.name}</h2><p>整体周期默认延续至下次复诊，可按实际安排调整。</p><div className="date-fields"><label>开始日期<input type="date" value={startDate} onChange={(event) => onStartDate(event.target.value)} /></label><label>结束日期<input type="date" value={endDate} min={startDate} onChange={(event) => onEndDate(event.target.value)} /></label></div><dl className="plan-stats"><div><dt>随访天数</dt><dd>{duration || "—"}</dd></div><div><dt>已选记录要点</dt><dd>{selected.length + customMetrics.length}</dd></div><div><dt>每日记录要点</dt><dd>{daily + customDaily}</dd></div></dl><div className="plan-scope"><span>方案范围</span><strong>{planning.activation.scopeLabel}</strong><small>目标规则 {planning.knowledge.goalRuleSetId}<br />用药知识 {planning.knowledge.releaseId}</small></div>{error ? <p className="plan-message error" role="alert">{error}</p> : null}{success ? <p className="plan-message success" role="status">{success}</p> : null}<button className="confirm-plan" onClick={onConfirm} disabled={saving || !selected.length || !startDate || !endDate}>{saving ? "正在保存方案…" : planning.activation.buttonLabel}</button><p className="confirmation-note">确认后保存医生选择、档案快照、规则版本和引用关系。</p></aside>;
}

function PlanningWorkspace({ planning, onConfirm, saving, saveError, success }) {
  const [selection, setSelection] = useState({});
  const [customMetrics, setCustomMetrics] = useState([]);
  const [startDate, setStartDate] = useState(planning.period.startDate);
  const [endDate, setEndDate] = useState(planning.period.endDate);
  useEffect(() => {
    const savedById = Object.fromEntries((planning.currentPlan?.items || []).map((item) => [item.metricId, item]));
    const savedRecordPointIds = new Set((planning.currentPlan?.recordPoints || []).map((item) => item.recordPointId));
    const hasSavedPlan = Boolean(planning.currentPlan);
    setSelection(Object.fromEntries(planning.candidates.map((candidate) => [candidate.metricId, { selected: candidate.required || (hasSavedPlan ? Boolean(savedById[candidate.metricId]) || savedRecordPointIds.has(candidate.recordPoint?.recordPointId) : candidate.selectedByDefault), frequency: savedById[candidate.metricId]?.frequency || (planning.currentPlan?.items || []).find((item) => item.recordPointId === candidate.recordPoint?.recordPointId)?.frequency || candidate.defaultFrequency }])));
    setCustomMetrics((planning.currentPlan?.items || []).filter((item) => item.sourceType === "doctor_custom"));
    setStartDate(planning.currentPlan?.period.startDate || planning.period.startDate);
    setEndDate(planning.currentPlan?.period.endDate || planning.period.endDate);
  }, [planning.proposalId, planning.currentPlan?.planVersion]);
  const toggle = (metricIds) => setSelection((current) => { const selected = metricIds.every((metricId) => current[metricId]?.selected); return Object.fromEntries(Object.entries(current).map(([metricId, value]) => [metricId, metricIds.includes(metricId) ? { ...value, selected: !selected } : value])); });
  const frequency = (metricIds, value) => setSelection((current) => Object.fromEntries(Object.entries(current).map(([metricId, item]) => [metricId, metricIds.includes(metricId) ? { ...item, frequency: value } : item])));
  const removeCustom = (id) => setCustomMetrics((items) => items.filter((item) => (item.metricId || item.localId) !== id));
  const customFrequency = (id, value) => setCustomMetrics((items) => items.map((item) => (item.metricId || item.localId) === id ? { ...item, frequency: value } : item));
  const submit = () => onConfirm({ patientId: planning.patientId, proposalId: planning.proposalId, startDate, endDate, items: [...planning.candidates.filter((item) => selection[item.metricId]?.selected).map((item) => ({ metricId: item.metricId, frequency: selection[item.metricId].frequency })), ...customMetrics.map((item) => ({ metricId: item.metricId, isCustom: true, displayName: item.displayName, clinicalIntent: item.clinicalIntent, dataType: item.dataType, unit: item.unit, frequency: item.frequency }))] });
  const recordPoints = buildRecordPoints(planning.candidates);
  const groups = planning.workflow.categories.filter((group) => group.categoryId !== "doctor_custom").map((group) => ({ ...group, recordPoints: recordPoints.filter((point) => point.categoryId === group.categoryId) })).filter((group) => group.recordPoints.length);
  return <><section className="planning-flow" aria-label="方案生成进度"><div className="done"><b>1</b><span><strong>读取电子档案</strong><small>{planning.ehr.sourceSystem}</small></span></div><i /><div className="done"><b>2</b><span><strong>组合目标与知识</strong><small>{planning.knowledge.goalRuleSetId}</small></span></div><i /><div className="current"><b>3</b><span><strong>医生确认方案</strong><small>{recordPoints.length}个系统记录要点</small></span></div></section>{planning.currentPlan ? <section className="current-plan-banner"><span>✓</span><div><strong>当前方案已保存 · v{planning.currentPlan.planVersion}</strong><p>{planning.currentPlan.period.startDate} 至 {planning.currentPlan.period.endDate}，共 {planning.currentPlan.recordPointCount || buildRecordPoints(planning.currentPlan.items || []).length} 个记录要点。继续调整并确认会保存为新版本。</p></div></section> : null}<section className="section-heading" id="ehr-context"><p className="eyebrow">患者档案</p><h2>本次规划使用的信息</h2><p>仅提取与当前诊疗目标和随访路径直接相关的最小必要信息</p></section><div className="context-grid">{planning.ehr.context.map((item) => <ContextCard item={item} key={item.contextId} />)}</div><section className="privacy-strip"><div><b>未参与规划</b><span>{planning.ehr.excludedData.join("、")}</span></div><div><b>待后续核对</b><span>{planning.ehr.missingItems.join("；")}</span></div></section><div className="planning-layout"><div><section className="section-heading" id="plan-candidates"><p className="eyebrow">随访记录要点</p><h2>确定本轮记录内容</h2><p>症状以一个记录要点展示，其内根据症状规则补充程度、频次或数量</p></section>{groups.map((group) => <section className="metric-group" key={group.categoryId}><header><div><h3>{group.label}</h3><p>{group.description}</p></div><span>{group.recordPoints.length} 个记录要点</span></header><div className="candidate-list">{group.recordPoints.map((point) => <RecordPointCard key={point.recordPointId} point={point} selection={selection} onToggle={toggle} onFrequency={frequency} />)}</div></section>)}<CustomMetricBuilder config={planning.workflow.customMetric} items={customMetrics} onAdd={(item) => setCustomMetrics((items) => [...items, item])} onRemove={removeCustom} onFrequency={customFrequency} /></div><PlanSummary planning={planning} selection={selection} customMetrics={customMetrics} startDate={startDate} endDate={endDate} saving={saving} error={saveError} success={success} onStartDate={setStartDate} onEndDate={setEndDate} onConfirm={submit} /></div></>;
}

function LineChart({ points, title }) {
  if (!points.length) return <div className="chart-empty">暂无记录</div>;
  const width = 640, height = 226, left = 40, right = 620, top = 22, bottom = 176;
  const values = points.map((item) => Number(item.value)); const min = Math.min(...values), max = Math.max(...values); const padding = Math.max((max - min) * 0.18, Math.max(Math.abs(max), 1) * 0.025); const low = min - padding, high = max + padding;
  const x = (index) => points.length === 1 ? (left + right) / 2 : left + index * (right - left) / (points.length - 1); const y = (value) => top + (high - value) / Math.max(high - low, 1) * (bottom - top); const path = points.map((point, index) => `${x(index)},${y(Number(point.value))}`).join(" "); const area = `${left},${bottom} ${path} ${right},${bottom}`;
  return <div className="chart" role="img" aria-label={title}><svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none"><g className="grid">{[0, 1, 2, 3].map((row) => <line key={row} x1={left} x2={right} y1={top + row * (bottom - top) / 3} y2={top + row * (bottom - top) / 3} />)}</g><polygon className="area" points={area} /><polyline className="line" points={path} />{points.map((point, index) => <circle key={`${point.timestamp}-${index}`} cx={x(index)} cy={y(Number(point.value))} r="4"><title>{point.label} · {point.display}</title></circle>)}{points.map((point, index) => <text key={`label-${point.timestamp}-${index}`} x={x(index)} y="207" textAnchor="middle">{point.label}</text>)}</svg></div>;
}

function BarChart({ points, title }) {
  if (!points.length) return <div className="chart-empty">暂无记录</div>;
  const width = 640, height = 226, left = 40, right = 620, top = 24, bottom = 176; const maximum = Math.max(...points.map((item) => Number(item.value)), 1); const slot = (right - left) / points.length; const barWidth = Math.min(slot * 0.52, 52);
  return <div className="chart" role="img" aria-label={title}><svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none"><g className="grid">{[0, 1, 2, 3].map((row) => <line key={row} x1={left} x2={right} y1={top + row * (bottom - top) / 3} y2={top + row * (bottom - top) / 3} />)}</g>{points.map((point, index) => { const x = left + slot * index + (slot - barWidth) / 2; const barHeight = Number(point.value) / maximum * (bottom - top); const y = bottom - barHeight; return <g key={`${point.timestamp}-${index}`} className="bar"><rect x={x} y={y} width={barWidth} height={barHeight} rx="7"><title>{point.label} · {point.display}</title></rect><text className="value" x={x + barWidth / 2} y={Math.max(y - 7, 13)} textAnchor="middle">{point.display.replace(/\s*(次\/24h)?$/, "")}</text><text x={x + barWidth / 2} y="207" textAnchor="middle">{point.label}</text></g>; })}</svg></div>;
}

function MetricCard({ metric }) {
  if (metric.chartKind === "status") return <article className="metric-card status-card"><header><div><p>最新状态</p><h3>{metric.title}</h3></div><span>{metric.unit}</span></header><div className="status-grid">{metric.points.map((point) => <div className="status-item" key={point.statusName}><span>{point.statusName}</span><strong className={point.value ? "yes" : "no"}>{point.display}</strong><small>{point.label} · <Citation ids={[point.sourceId]} /></small></div>)}</div><footer><p>{metric.summary} <Citation ids={metric.sourceIds} /></p></footer></article>;
  return <article className="metric-card"><header><div><p>健康指标</p><h3>{metric.title}</h3></div><span>{metric.points.length} 条记录</span></header><div className="metric-latest"><strong>{metric.latest?.display || "—"}</strong><small>{metric.latest ? `最近更新 ${metric.latest.label}` : "等待上报"}</small></div>{metric.chartKind === "line" ? <LineChart points={metric.points} title={metric.title} /> : <BarChart points={metric.points} title={metric.title} />}<footer><p>{metric.summary} <Citation ids={metric.sourceIds} /></p></footer></article>;
}

function Overview({ overview }) {
  return <section className="overview-card" id="overview"><header><div><p className="eyebrow">数据汇总</p><h2>{overview.title}</h2></div><span>{overview.periodLabel}</span></header><p className="overview-intro">{overview.intro}</p><dl className="overview-stats"><div><dt>记录天数</dt><dd>{overview.recordDayCount}</dd></div><div><dt>记录要点</dt><dd>{overview.metricCount}</dd></div><div><dt>来源记录</dt><dd>{overview.sourceCount}</dd></div></dl><div className="changes"><h3>关键变化</h3><ul>{overview.sentences.length ? overview.sentences.map((item, index) => <li key={index}><span /><p>{item.text} <Citation ids={item.sourceIds} /></p></li>) : <li className="empty">暂无可总结的记录要点</li>}</ul></div>{overview.latestStatus ? <div className="latest-status"><span>最新状态</span><p>{overview.latestStatus.text} <Citation ids={overview.latestStatus.sourceIds} /></p></div> : null}{overview.missingMetrics.length ? <div className="missing"><strong>本期未记录</strong><div>{overview.missingMetrics.map((item) => <span key={item}>{item}</span>)}</div></div> : null}</section>;
}

function Sources({ sources }) {
  return <details className="sources" id="sources"><summary><span><strong>数据来源</strong><small>患者原始上报与标准化记录</small></span><b>{sources.length} 条</b></summary><div className="source-list">{sources.map((source) => <article id={`source-${source.sourceId}`} key={source.sourceId}><header><strong>[{source.sourceId}]</strong><span>{source.observationReference}</span></header><p>{source.originalText}</p><dl><div><dt>患者上报</dt><dd>{source.responseReference}</dd></div><div><dt>记录时间</dt><dd>{source.effectiveTime}</dd></div><div><dt>指标标识</dt><dd>{source.metricId}</dd></div><div><dt>知识库版本</dt><dd>{source.knowledgeReleaseId}</dd></div></dl></article>)}</div></details>;
}

function CollaborationInbox({ collaboration, links }) {
  const escalations = collaboration?.escalations || [];
  return <section className="collaboration-inbox"><header><div><p className="eyebrow">人工协作</p><h2>护士上报待办</h2><p>{collaboration?.boundary}</p></div><div className="role-links"><a href={links?.patient}>患者端</a><a href={links?.nurse}>护士端</a></div></header>{escalations.length ? <div className="escalation-list">{escalations.map((item) => <article key={item.taskId}><div className="escalation-head"><div><span>护士人工上报</span><h3>{item.statusLabel}</h3></div><time>{item.nurseReviewedAt ? new Date(item.nurseReviewedAt).toLocaleString("zh-CN", { hour12: false }) : "时间未记录"}</time></div><section><strong>护士复核说明</strong><p>{item.nurseNote}</p></section><div className="escalation-answers">{item.answers.map((answer) => <div className={answer.wide ? "wide" : ""} key={`${answer.question}-${answer.answer}`}><span>{answer.question}</span><strong>{answer.answer}</strong></div>)}</div><footer>临床评估状态：未评估 · 需由医生本人判断</footer></article>)}</div> : <div className="collaboration-empty"><span>✓</span><h3>当前没有护士上报待办</h3><p>护士选择“上报医生评估”后，事项会从共享记录链出现在这里。</p></div>}</section>;
}

function Dashboard({ state, planning, patients, patientId, onPatientChange, onRefresh, onConfirmPlan, busy, saving, saveError, success, initialView }) {
  const [view, setView] = useState(initialView); const generated = useMemo(() => new Date(state.generatedAt).toLocaleString("zh-CN", { hour12: false }), [state.generatedAt]); const title = view === "planning" ? "创建随访方案" : view === "collaboration" ? "护理协作" : "随访观察";
  return <div className="app-shell"><Sidebar view={view} onViewChange={setView} collaborationCount={state.collaboration?.pendingCount} /><main className="workspace"><header className="topbar"><div><span>患者管理</span><b>/</b><strong>{title}</strong></div><div><div className="mobile-view-switch"><button className={view === "planning" ? "active" : ""} onClick={() => setView("planning")}>方案</button><button className={view === "monitoring" ? "active" : ""} onClick={() => setView("monitoring")}>观察</button><button className={view === "collaboration" ? "active" : ""} onClick={() => setView("collaboration")}>协作</button></div><small>更新于 {generated}</small><span className="doctor-avatar">医</span></div></header><section className="page-title"><div><p className="eyebrow">{view === "planning" ? "随访计划" : view === "collaboration" ? "护理协作" : "患者随访"}</p><h1>{view === "planning" ? "创建随访方案" : view === "collaboration" ? "护士上报待办" : "患者随访详情"}</h1><p>{view === "planning" ? "结合电子档案与知识库，由医生确定监测内容和周期" : view === "collaboration" ? "接收护士人工复核后明确上报的事项" : "查看近期上报、健康趋势与连续照护进展"}</p></div><span className="stage">● {view === "planning" ? (planning.currentPlan ? "方案已开启" : "等待医生确认") : view === "collaboration" ? `${state.collaboration?.pendingCount || 0} 条待办` : state.workspace.stageLabel}</span></section><PatientCard patient={state.patient} /><PatientToolbar patients={patients} patientId={patientId || state.patient.patientId} onPatientChange={onPatientChange} onRefresh={onRefresh} busy={busy} />{view === "planning" ? <PlanningWorkspace planning={planning} onConfirm={onConfirmPlan} saving={saving} saveError={saveError} success={success} /> : view === "collaboration" ? <CollaborationInbox collaboration={state.collaboration} links={state.links} /> : <><section className="section-heading"><p className="eyebrow">随访摘要</p><h2>本轮随访概览</h2><p>根据患者实际上报自动生成</p></section><Overview overview={state.overview} /><section className="section-heading metric-heading" id="metrics"><p className="eyebrow">健康趋势</p><h2>近期指标</h2><p>患者每日上报记录</p></section><div className="metric-grid">{state.metrics.map((metric) => <MetricCard metric={metric} key={metric.metricKey} />)}</div><Sources sources={state.sources} /></>}</main></div>;
}

function App() {
  const [state, setState] = useState(null); const [planning, setPlanning] = useState(null); const [patients, setPatients] = useState([]); const [patientId, setPatientId] = useState(""); const [busy, setBusy] = useState(true); const [saving, setSaving] = useState(false); const [error, setError] = useState(""); const [saveError, setSaveError] = useState(""); const [success, setSuccess] = useState(""); const [loginRequired, setLoginRequired] = useState(false);
  const refresh = useCallback(async (nextPatientId = patientId) => { setBusy(true); setError(""); setSuccess(""); try { const [dashboard, planningState, patientRows] = await Promise.all([loadDashboard(nextPatientId), loadPlanning(nextPatientId), loadPatients()]); setState(dashboard); setPlanning(planningState); setPatients(patientRows); setPatientId(dashboard.patient.patientId); setLoginRequired(false); } catch (requestError) { if (requestError.code === "authentication_required") setLoginRequired(true); else setError(requestError.message); } finally { setBusy(false); } }, [patientId]);
  useEffect(() => { void refresh(""); }, []);
  const login = async (accessKey) => { setBusy(true); setError(""); try { await createSession(accessKey); await refresh(""); } catch (requestError) { setError(requestError.message); setBusy(false); } };
  const savePlan = async (payload) => { setSaving(true); setSaveError(""); setSuccess(""); try { const saved = await confirmPlan(payload); const [refreshedPlanning, refreshedDashboard] = await Promise.all([loadPlanning(payload.patientId), loadDashboard(payload.patientId)]); setPlanning(refreshedPlanning); setState(refreshedDashboard); setSuccess(`随访方案已保存为 v${saved.planVersion}；共选择 ${saved.recordPointCount} 个记录要点。`); } catch (requestError) { setSaveError(requestError.message); } finally { setSaving(false); } };
  if (loginRequired) return <Login onLogin={login} busy={busy} error={error} />;
  if (!state || !planning) return <main className="boot"><div className="spinner" />{error ? <p role="alert">{error}</p> : <p>正在读取患者档案与知识库…</p>}<button onClick={() => refresh("")} disabled={busy}>重试</button></main>;
  const requestedView = new URLSearchParams(window.location.search).get("view");
  const initialView = ["planning", "monitoring", "collaboration"].includes(requestedView) ? requestedView : "planning";
  return <Dashboard state={state} planning={planning} patients={patients} patientId={patientId} onPatientChange={(value) => refresh(value)} onRefresh={() => refresh(patientId)} onConfirmPlan={savePlan} busy={busy} saving={saving} saveError={saveError} success={success} initialView={initialView} />;
}

createRoot(document.getElementById("root")).render(<React.StrictMode><App /></React.StrictMode>);
