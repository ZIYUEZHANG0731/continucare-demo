import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { loadState, postCommand } from "./api";
import NurseApp from "./nurse";
import "./styles.css";

const KNOWN_KINDS = new Set([
  "waiting_doctor",
  "no_web_tasks",
  "collecting",
  "candidate_review",
  "clarification",
  "final_review",
  "completed",
  "supplemental_review",
  "fail_closed",
]);

const FIELD_ROLE_LABELS = {
  presence: "当前是否存在",
  severity: "程度",
  frequency: "频次",
  quantity: "数量",
  value: "数值",
};

function StatusBar() {
  return (
    <div className="status-bar" aria-hidden="true">
      <span>9:41</span>
      <span className="status-icons">● ◒ ▰</span>
    </div>
  );
}

function PageHeader({ title, subtitle, onBack }) {
  return (
    <header className="page-header">
      {onBack ? (
        <button className="icon-button" onClick={onBack} aria-label="返回">
          ‹
        </button>
      ) : null}
      <div>
        <h1>{title}</h1>
        {subtitle ? <p>{subtitle}</p> : null}
      </div>
    </header>
  );
}

function BottomNav({ active = "today" }) {
  return (
    <nav className="bottom-nav" aria-label="患者端导航">
      <button className={active === "today" ? "active" : ""}>
        <span className="nav-dot">●</span>
        <span>今日</span>
      </button>
      <button className={active === "records" ? "active" : ""}>
        <span className="nav-dot">○</span>
        <span>记录</span>
      </button>
      <button>
        <span className="nav-dot">○</span>
        <span>我的</span>
      </button>
    </nav>
  );
}

function Shell({ children, nav = true, chat = false }) {
  return (
    <main className={`app-canvas ${chat ? "chat-canvas" : ""}`}>
      <section className={`phone-shell ${chat ? "chat-shell" : ""}`}>
        <StatusBar />
        <div className="scroll-region">{children}</div>
        {nav ? <BottomNav /> : null}
      </section>
    </main>
  );
}

function ChatShell({ children, subtitle, dock = null }) {
  return (
    <main className="app-canvas chat-canvas">
      <section className="phone-shell chat-shell">
        <StatusBar />
        <div className="chat-topbar">
          <PageHeader title="今日随访" subtitle={subtitle} />
        </div>
        <div className="chat-scroll-region">{children}</div>
        {dock ? <div className="chat-dock">{dock}</div> : null}
      </section>
    </main>
  );
}

function Banner({ tone = "info", children }) {
  return <div className={`banner ${tone}`} role={tone === "error" ? "alert" : "status"}>{children}</div>;
}

function ChatBubble({ role, children, caption }) {
  return (
    <div className={`message-row ${role}`}>
      {role === "assistant" ? <span className="avatar">C</span> : null}
      <div>
        <div className="chat-bubble">{children}</div>
        {caption ? <p className="bubble-caption">{caption}</p> : null}
      </div>
    </div>
  );
}

function PrimaryButton({ children, onClick, disabled, type = "button" }) {
  return <button className="button primary" type={type} onClick={onClick} disabled={disabled}>{children}</button>;
}

function SecondaryButton({ children, onClick, disabled }) {
  return <button className="button secondary" onClick={onClick} disabled={disabled}>{children}</button>;
}

function Consent({ checked, onChange, state, compact = false }) {
  return (
    <div className={`consent-block ${compact ? "compact" : ""}`}>
      <label>
        <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
        <span>{state.consent?.label || "我确认本次只输入合成演示内容"}</span>
      </label>
      <p>{compact ? "发送即保存原话并向豆包提供最小上下文；无法可靠结构化时，原话会以未评估内容转护士复核。只有最后统一确认后才写结构化记录。" : state.consent?.detail}</p>
    </div>
  );
}

function Composer({ placeholder, onSend, disabled, busy }) {
  const [message, setMessage] = useState("");
  const send = async () => {
    const value = message.trim();
    if (!value || disabled || busy) return;
    const succeeded = await onSend(value);
    if (succeeded) setMessage("");
  };
  return (
    <div className="composer-wrap">
      <div className="composer">
        <textarea
          aria-label={placeholder}
          rows="1"
          value={message}
          maxLength={500}
          placeholder={placeholder}
          disabled={disabled || busy}
          onChange={(event) => setMessage(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void send();
            }
          }}
        />
        <button aria-label="发送" onClick={send} disabled={disabled || busy || !message.trim()}>↑</button>
      </div>
    </div>
  );
}

function PrivacyNotice({ state }) {
  return <p className="emergency">{state.emergencyNotice}</p>;
}

function Home({ state, onRefresh }) {
  const patient = state.patient || {};
  return (
    <Shell>
      <PageHeader
        title={`今天，${patient.displayName || "陈女士（合成）"}`}
        subtitle={`8月15日 · ${patient.pathwayCode || "GLP1-14D"} 随访`}
      />
      <section className="hero-card">
        <span className="eyebrow">今日随访</span>
        <h2>花 1 分钟告诉我<br />今天感觉怎么样</h2>
        <p>豆包会逐项整理进未提交草稿；全部问完后，你只需在完整资料卡上统一修改并确认一次。</p>
      </section>
      <section className="progress-section">
        <h3>今日进度</h3>
        <div className="progress-line"><span>○ 对话采集</span><span>○ 确认记录</span><span>○ 提交完成</span></div>
        <p>完成后仍可继续补充新的情况。</p>
      </section>
      {state.kind === "waiting_doctor" ? <Banner>{state.message}</Banner> : null}
      <PrimaryButton disabled={state.kind === "waiting_doctor"} onClick={onRefresh}>开始今日随访</PrimaryButton>
    </Shell>
  );
}

function NoWebTasks({ state }) {
  return (
    <Shell>
      <PageHeader title="本轮无需填写" subtitle="医生已确认随访方案" />
      <section className="hero-card">
        <span className="eyebrow">今日状态</span>
        <h2>你这边已经完成</h2>
        <p>{state.message}</p>
      </section>
      <Banner>方案中的其他记录要点由医生确认的来源继续管理，不会因患者端未填写而失效。</Banner>
    </Shell>
  );
}

function CandidateCard({ candidate, previous }) {
  return (
    <article className="candidate-card">
      <span className="eyebrow">豆包整理候选</span>
      <h3>{candidate.question}</h3>
      <strong>{previous ? `拟修改：${previous} → ${candidate.proposed}` : `拟记录：${candidate.proposed}`}</strong>
      <p>依据原话：{candidate.evidence}</p>
    </article>
  );
}

function DraftRecordCard({ message, confirmed = false }) {
  return (
    <article className="confirmed-record-card">
      <span className="eyebrow">{confirmed ? "✓ 已确认记录" : "草稿记录 · 待最终确认"}</span>
      {(message.items || []).map((item) => (
        <div className="confirmed-record-row" key={`${item.linkId}-${item.value}`}>
          <span>{item.label}</span>
          <strong>{item.value}</strong>
        </div>
      ))}
    </article>
  );
}

function ChatTimeline({ state }) {
  return (
    <section className="chat-list">
      {(state.history || []).map((message, index) => (
        ["draft_record", "confirmed_record"].includes(message.kind) ? (
          <div className="message-row assistant" key={`record-${index}`}>
            <span className="avatar">C</span>
            <DraftRecordCard message={message} confirmed={message.kind === "confirmed_record"} />
          </div>
        ) : (
          <ChatBubble role={message.role} key={`${message.role}-${index}`}>{message.text}</ChatBubble>
        )
      ))}
    </section>
  );
}

function InlineAssistantTurn({ children }) {
  return (
    <div className="message-row assistant inline-turn">
      <span className="avatar">C</span>
      <div className="assistant-stack">{children}</div>
    </div>
  );
}

function ProcessingTurn() {
  const messages = [
    "已发送，等待豆包完成整句整理",
    "正在核对当前 Pathway 与可确认内容",
    "正在等待完整结果；通过校验后会在这里更新未提交草稿",
  ];
  const [index, setIndex] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => {
      setIndex((current) => Math.min(current + 1, messages.length - 1));
    }, 1100);
    return () => window.clearInterval(timer);
  }, [messages.length]);
  return (
    <div className="message-row assistant processing-row" role="status" aria-live="polite">
      <span className="avatar">C</span>
      <div className="chat-bubble processing-bubble">
        <span>{messages[index]}</span>
        <span className="typing-dots" aria-hidden="true"><i /><i /><i /></span>
      </div>
    </div>
  );
}

function ConversationFlow({ state, children, tailKey = "" }) {
  const flowRef = useRef(null);
  useLayoutEffect(() => {
    const scroller = flowRef.current?.closest(".chat-scroll-region");
    if (!scroller) return;
    // Only move the message viewport. scrollIntoView can also move the outer
    // document and makes the send -> result transition look like two scrolls.
    scroller.scrollTop = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
  }, [state.kind, state.generation, state.supplementalGeneration, state.history?.length, tailKey]);
  return (
    <section className="conversation-flow" ref={flowRef}>
      {children}
      <div className="conversation-end" aria-hidden="true" />
    </section>
  );
}

function Conversation({ state, onSend, consent, setConsent, busy, pendingMessage }) {
  return (
    <ChatShell
      subtitle="豆包正在帮你整理，不做诊断"
      dock={(
        <>
          <Consent compact checked={consent} onChange={setConsent} state={state} />
          {(state.quickReplies || []).length ? (
            <div className="quick-reply-block">
              <p>{consent ? "直接点选回答" : "确认合成演示后可点选"}</p>
              <div className="quick-replies" aria-label="快捷回答">
                {state.quickReplies.map((option) => (
                  <button
                    key={option.value}
                    disabled={busy || !consent || !state.mimoReady}
                    onClick={() => onSend(option.label, "chat")}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </div>
          ) : null}
          {!state.mimoReady ? <Banner tone="error">豆包当前未配置，系统不会用离线模型冒充成功。</Banner> : null}
          <Composer
            placeholder="输入今天的情况…"
            disabled={!consent || !state.mimoReady}
            busy={busy}
            onSend={(message) => onSend(message, "chat")}
          />
          <PrivacyNotice state={state} />
        </>
      )}
    >
      <ConversationFlow state={state} tailKey={pendingMessage}>
        <ChatTimeline state={state} />
        <ChatBubble role="assistant" caption="系统按 Pathway 的缺失项继续提问。">
          {state.nextQuestion}
        </ChatBubble>
        {pendingMessage ? <><ChatBubble role="user">{pendingMessage}</ChatBubble><ProcessingTurn /></> : null}
      </ConversationFlow>
      {state.allowedActions?.includes("explicit_unknown") ? (
        <SecondaryButton onClick={() => onSend(null, "explicit_unknown")} disabled={busy}>暂时无法估算饮水量</SecondaryButton>
      ) : null}
    </ChatShell>
  );
}

function CandidateReview({ state, onResolve, busy }) {
  const [severity, setSeverity] = useState("");
  return (
    <ChatShell subtitle="豆包正在帮你整理，不做诊断">
      <ConversationFlow state={state}>
        <ChatTimeline state={state} />
        <InlineAssistantTurn>
          <div className="chat-bubble">我把刚才的话整理成了待确认卡片。只有你确认后才会写入结构化记录。</div>
          <div className="candidate-stack">
            {state.groupedNausea ? (
              <article className="candidate-card grouped">
                <span className="eyebrow">恶心</span>
                <h3>已识别：现在有恶心</h3>
                <p>程度必须由你选择，豆包不会替你决定。</p>
                <div className="choice-grid compact">
                  {state.severityOptions.map((option) => (
                    <button key={option.value} className={severity === option.value ? "selected" : ""} onClick={() => setSeverity(option.value)}>{option.label}</button>
                  ))}
                </div>
              </article>
            ) : null}
            {state.candidates
              .filter((item) => !state.groupedNausea || !["nausea-present", "nausea-severity"].includes(item.linkId))
              .map((candidate) => <CandidateCard candidate={candidate} key={candidate.candidateId} />)}
          </div>
          <p className="footnote">确认后卡片会保留在本次对话中；提交前仍可修改。</p>
          <div className="button-row">
            <PrimaryButton disabled={busy || (state.groupedNausea && !severity)} onClick={() => onResolve("accepted", severity)}>确认这些内容</PrimaryButton>
            <SecondaryButton disabled={busy} onClick={() => onResolve("rejected", "")}>重新回答</SecondaryButton>
          </div>
        </InlineAssistantTurn>
      </ConversationFlow>
    </ChatShell>
  );
}

function Clarification({ state, onResolve, busy }) {
  return (
    <ChatShell subtitle="豆包正在帮你整理，不做诊断">
      <ConversationFlow state={state}>
        <ChatTimeline state={state} />
        <InlineAssistantTurn>
          <div className="chat-bubble">这句话可能对应多个受控含义，请你亲自选择。</div>
          <section className="question-card">
            <span className="eyebrow">需要澄清</span>
            <h2>{state.clarification.prompt}</h2>
          </section>
          <div className="choice-grid">
            {state.clarification.options.map((option) => (
              <button key={option.value} disabled={busy} onClick={() => onResolve(option.value)}>
                <strong>{option.label}</strong><span>选择这一项后再进入记录草稿</span>
              </button>
            ))}
          </div>
          <p className="footnote">只有你明确选择的含义才会进入草稿。</p>
        </InlineAssistantTurn>
      </ConversationFlow>
    </ChatShell>
  );
}

function FinalReview({ state, onFinalize, onRevision, onAdditional, onRemoveReport, consent, setConsent, busy, pendingMessage }) {
  const [selectedLink, setSelectedLink] = useState("");
  const [addingAdditional, setAddingAdditional] = useState(false);
  return (
    <ChatShell
      subtitle="豆包正在帮你整理，不做诊断"
      dock={(selectedLink || addingAdditional) ? (
        <>
          <Banner>{addingAdditional ? "请说明想补充的其他情况。通过受控术语匹配后会回到这张草稿。" : "请用一句话说明新值。通过安全校验后会直接更新这份未提交草稿，最后仍只确认一次。"}</Banner>
          <Consent compact checked={consent} onChange={setConsent} state={state} />
          {!state.mimoReady ? <Banner tone="error">豆包当前未配置，暂时不能整理修改内容；原草稿没有变化。</Banner> : null}
          <Composer
            placeholder={addingAdditional ? "输入要补充的合成情况" : "输入要修改的合成回答"}
            disabled={!consent || !state.mimoReady}
            busy={busy}
            onSend={async (message) => {
              const succeeded = addingAdditional
                ? await onAdditional(message)
                : await onRevision(message, selectedLink);
              if (succeeded) {
                setSelectedLink("");
                setAddingAdditional(false);
              }
              return succeeded;
            }}
          />
          <SecondaryButton disabled={busy} onClick={() => { setSelectedLink(""); setAddingAdditional(false); }}>返回完整复核</SecondaryButton>
          <PrivacyNotice state={state} />
        </>
      ) : null}
    >
      <ConversationFlow state={state} tailKey={pendingMessage}>
        <ChatTimeline state={state} />
        <InlineAssistantTurn>
          <div className="chat-bubble">今天需要采集的内容已经齐了。前面的内容都只是草稿，请在这张完整资料卡上统一修改并确认一次。</div>
          <section className="review-card">
            <span className="eyebrow">{selectedLink ? "正在修改草稿" : addingAdditional ? "正在补充其他情况" : "提交前复核"}</span>
            {(state.answerGroups || []).map((group) => (
              <section className="record-point-review" key={group.recordPointId}>
                <header><span>{group.label}</span><strong>{group.summary}</strong></header>
                {group.items.map((answer) => (
                  <button className={`review-row ${selectedLink === answer.linkId ? "selected" : ""}`} key={answer.linkId} onClick={() => { setSelectedLink(answer.linkId); setAddingAdditional(false); }}>
                    <span>{FIELD_ROLE_LABELS[answer.fieldRole] || answer.label}</span><strong>{answer.value}</strong>
                  </button>
                ))}
              </section>
            ))}
            {(state.additionalReports || []).map((report) => (
              <div className="review-row review-row-static" key={report.reportId}>
                <span><strong>{report.label}</strong><small>{report.value}</small></span>
                <button className="inline-remove" disabled={busy} onClick={() => onRemoveReport(report.reportId)}>移除</button>
              </div>
            ))}
            {state.originalText ? <div className="quote"><span>患者原话</span><p>“{state.originalText}”</p></div> : null}
          </section>
          <SecondaryButton disabled={busy} onClick={() => { setSelectedLink(""); setAddingAdditional(true); }}>补充其他情况</SecondaryButton>
        </InlineAssistantTurn>
        {pendingMessage ? <><ChatBubble role="user">{pendingMessage}</ChatBubble><ProcessingTurn /></> : null}
      </ConversationFlow>
      {(selectedLink || addingAdditional) ? (
        <p className="footnote">输入框固定在底部；整理结果只更新未提交草稿。</p>
      ) : (
        <>
          <p className="footnote">不生成诊断、风险或处置建议。点击某一行可以先修改。</p>
          <PrimaryButton disabled={busy} onClick={onFinalize}>确认并提交今天记录</PrimaryButton>
        </>
      )}
    </ChatShell>
  );
}

function Completed({ state, onOpenSupplemental }) {
  const supplementalCount = state.receipt?.supplementalCount || 0;
  return (
    <ChatShell subtitle="今天的记录已完成">
      <ConversationFlow state={state}>
        <ChatTimeline state={state} />
        <InlineAssistantTurn>
          <div className="chat-bubble">
            {supplementalCount > 0
              ? `✓ 补充情况已确认并保存，已进入护士人工复核。今天的随访到这里就结束了。`
              : "✓ 今天的随访已记录，并进入护士人工复核队列。"}
          </div>
          <section className="receipt-card inline-receipt">
            <span className="eyebrow">记录回执</span>
            <h3>{state.receipt?.recordPointCount || 0} 个随访记录要点</h3>
            <p>{state.receipt?.answerCount || 0} 项受控字段已保存</p>
            <h3>{state.receipt?.originalCount || 0} 条已确认患者原话</h3>
            {supplementalCount > 0 ? <h3>{supplementalCount} 条已确认补充情况</h3> : null}
            <p>来源：豆包整理 + 患者确认</p>
          </section>
          <p className="section-copy">如有新的情况，可以随时继续在这段对话里补充。</p>
          <PrimaryButton onClick={onOpenSupplemental}>继续补充上报</PrimaryButton>
        </InlineAssistantTurn>
      </ConversationFlow>
    </ChatShell>
  );
}

function Supplemental({ state, onSend, onResolve, consent, setConsent, busy, pendingMessage }) {
  const pending = state.pendingSupplemental;
  const [options, setOptions] = useState({});
  return (
    <ChatShell
      subtitle="补充上报继续接在今天的对话里"
      dock={!pending ? (
        <>
          <Consent compact checked={consent} onChange={setConsent} state={state} />
          {!state.mimoReady ? <Banner tone="error">豆包当前未配置，暂时不能整理补充上报；已完成记录不会改变。</Banner> : null}
          <Composer placeholder="输入一条合成补充上报" disabled={!consent || !state.mimoReady} busy={busy} onSend={onSend} />
          <PrivacyNotice state={state} />
        </>
      ) : null}
    >
      <ConversationFlow state={state} tailKey={pendingMessage}>
        <ChatTimeline state={state} />
        <section className="memory-card">
          <span className="eyebrow">今天我已经记得</span>
          {(state.answerGroups || []).map((group) => <p key={group.recordPointId}>{group.label} · {group.summary}</p>)}
        </section>
        {(state.reports || []).map((report, index) => (
          <React.Fragment key={`${report.originalText}-${index}`}>
            <ChatBubble role="user">{report.originalText}</ChatBubble>
            <ChatBubble role="assistant">这条补充上报已保存，已进入护士人工复核。</ChatBubble>
          </React.Fragment>
        ))}
        {pendingMessage ? <><ChatBubble role="user">{pendingMessage}</ChatBubble><ProcessingTurn /></> : null}
        {pending ? (
          <>
            <ChatBubble role="user">{pending.originalText}</ChatBubble>
            <section className="supplemental-card">
              <span className="eyebrow">豆包匹配到新的上报</span>
              {pending.items.map((item) => <CandidateCard candidate={item} key={item.candidateId} />)}
              {pending.unmatched ? <p>当前受控指标未匹配；确认后只保留原话，不会伪造 Observation。</p> : null}
              {pending.clarifications.map((clarification) => (
                <div className="clarification-block" key={clarification.clarificationId}>
                  <h3>{clarification.prompt}</h3>
                  {clarification.options.map((option) => (
                    <button className={options[clarification.clarificationId] === option.value ? "selected" : ""} key={option.value} onClick={() => setOptions((old) => ({ ...old, [clarification.clarificationId]: option.value }))}>{option.label}</button>
                  ))}
                </div>
              ))}
            </section>
            <div className="button-row">
              <PrimaryButton disabled={busy || pending.clarifications.some((item) => !options[item.clarificationId])} onClick={() => onResolve("accepted", options)}>确认补充</PrimaryButton>
              <SecondaryButton disabled={busy} onClick={() => onResolve("rejected", {})}>重新说明</SecondaryButton>
            </div>
          </>
        ) : (
          <ChatBubble role="assistant">现在还有什么想补充告诉护士吗？</ChatBubble>
        )}
      </ConversationFlow>
    </ChatShell>
  );
}

function FailClosed({ state, onRefresh }) {
  return (
    <Shell nav={false}>
      <PageHeader title="暂时无法继续" subtitle="患者端已停止写入" />
      <Banner tone="error">{state.message || "共享记录不可安全读取"}</Banner>
      <SecondaryButton onClick={onRefresh}>重新读取状态</SecondaryButton>
    </Shell>
  );
}

function App() {
  const [state, setState] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [consent, setConsent] = useState(false);
  const [panel, setPanel] = useState("default");
  const [pendingMessage, setPendingMessage] = useState("");

  const refresh = useCallback(async () => {
    setError("");
    try {
      const next = await loadState();
      if (!KNOWN_KINDS.has(next.kind)) throw new Error("服务器返回了未知患者端状态");
      setState(next);
      if (next.kind !== "completed") setPanel("default");
    } catch (exception) {
      setError(exception.message || "患者端暂时不可用");
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const command = useCallback(async (path, payload) => {
    const consumesConsent = payload?.syntheticConfirmed === true;
    if (consumesConsent) setConsent(false);
    const sentMessage = path === "/api/chat" && typeof payload?.message === "string" ? payload.message.trim() : "";
    if (sentMessage) setPendingMessage(sentMessage);
    setBusy(true);
    setError("");
    try {
      await postCommand(path, payload);
      await refresh();
      return true;
    } catch (exception) {
      setError(exception.message || "操作未完成");
      if (exception.code === "state_conflict") await refresh();
      return false;
    } finally {
      if (sentMessage) setPendingMessage("");
      setBusy(false);
    }
  }, [refresh]);

  const content = useMemo(() => {
    if (!state) return null;
    if (state.kind === "waiting_doctor") return <Home state={state} onRefresh={refresh} />;
    if (state.kind === "no_web_tasks") return <NoWebTasks state={state} />;
    if (state.kind === "collecting") return <Conversation state={state} consent={consent} setConsent={setConsent} busy={busy} pendingMessage={pendingMessage} onSend={(message, action) => action === "explicit_unknown" ? command("/api/explicit-unknown", { generation: state.generation }) : command("/api/chat", { generation: state.generation, message, syntheticConfirmed: consent })} />;
    if (state.kind === "candidate_review") return <CandidateReview state={state} busy={busy} onResolve={(decision, severity) => command("/api/candidates/resolve", { generation: state.generation, decision, ...(severity ? { nauseaSeverity: severity } : {}) })} />;
    if (state.kind === "clarification") return <Clarification state={state} busy={busy} onResolve={(optionId) => command("/api/clarification/resolve", { generation: state.generation, optionId })} />;
    if (state.kind === "final_review") return <FinalReview state={state} consent={consent} setConsent={setConsent} busy={busy} pendingMessage={pendingMessage} onFinalize={() => command("/api/finalize", { generation: state.generation })} onRevision={(message, selectedRevisionLinkId) => command("/api/chat", { generation: state.generation, message, selectedRevisionLinkId, syntheticConfirmed: consent })} onAdditional={(message) => command("/api/chat", { generation: state.generation, message, syntheticConfirmed: consent })} onRemoveReport={(reportId) => command("/api/draft-reports/remove", { generation: state.generation, reportId })} />;
    if (state.kind === "completed" && panel !== "supplemental") return <Completed state={state} onOpenSupplemental={() => setPanel("supplemental")} />;
    if (state.kind === "completed" || state.kind === "supplemental_review") return <Supplemental state={state} consent={consent} setConsent={setConsent} busy={busy} pendingMessage={pendingMessage} onSend={(message) => command("/api/chat", { generation: state.generation, supplementalGeneration: state.supplementalGeneration, message, syntheticConfirmed: consent })} onResolve={async (decision, clarificationOptions) => {
      const succeeded = await command("/api/supplemental/resolve", { generation: state.generation, supplementalGeneration: state.supplementalGeneration, decision, clarificationOptions });
      if (succeeded && decision === "accepted") setPanel("default");
      return succeeded;
    }} />;
    return <FailClosed state={state} onRefresh={refresh} />;
  }, [state, panel, refresh, consent, busy, pendingMessage, command]);

  if (!state && !error) return <div className="boot-screen"><div className="spinner" /><p>正在读取今天的随访…</p></div>;
  return (
    <>
      {content}
      {error ? <div className="toast" role="alert"><span>{error}</span><button onClick={() => setError("")}>×</button></div> : null}
    </>
  );
}

const rootApp = window.location.pathname.startsWith("/nurse") ? <NurseApp /> : <App />;
createRoot(document.getElementById("root")).render(<React.StrictMode>{rootApp}</React.StrictMode>);
