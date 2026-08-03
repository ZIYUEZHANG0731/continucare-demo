# 第四层增强项验收：指标数量无关的受控 LLM Summary

- 版本：0.1.0（2026-08-02）
- 范围：动态 Fact Ledger、MiMo 结构化编排、事实/证据锁、确定性回退、版本与 Provenance
- 结论：**受控 Summary Agent 工程边界与真实 MiMo 合成数据验收均已通过；LLM 只能组织已有事实编号，不能生成临床事实或自由改写正文。提交配置保持安全默认关闭，本地验收配置已显式启用。**

## 1. 为什么不为每个指标设计固定 Prompt

系统无法预先知道一个 Pathway 最终会有 3 个、30 个还是更多指标。医生以后审批的新指标也不应要求修改 Summary Agent 的代码或增加新的模型字段。

本实现因此不定义 `weight / nausea / vomiting / ...` 这类固定 Summary Schema，而使用统一的动态事实单元：

```text
任意数量的 TimelineEvent / MetricState / NumericTrend
→ 本地转换为 SummaryFact[]
→ LLM 仅返回 fact_id 的分组与顺序
→ 本地验证全部必选 fact_id
→ 本地用 canonical_text 渲染 SummaryEvidenceItem
→ 医生审阅
```

每个 `SummaryFact` 只有通用字段：

- `fact_id`：稳定事实编号；
- `kind`：Timeline、指标状态或原始数值趋势；
- `section`：该事实允许出现的唯一栏目；
- `canonical_text`：由确定性代码生成、不可被模型改写的正文；
- `evidence_refs`：对应的 Observation、Timeline 或 State Snapshot 证据；
- `mandatory`：是否必须进入摘要；
- `priority`：只用于排序提示，不授权模型删除事实；
- `requires_doctor_confirmation`：是否必须突出人工确认。

因此指标定义由 `ClinicalStateSnapshot.metric_definitions` 动态提供。增加新指标后，只要第四层已产生对应 `MetricState / NumericTrend`，Summary Agent 会自动生成新事实，不需要认识该指标的医学名称。

## 2. LLM 被允许做什么

模型严格输出：

```json
{
  "groups": [
    {
      "group_id": "group-1",
      "section": "key_changes",
      "fact_ids": ["summary-fact-...", "summary-fact-..."]
    }
  ]
}
```

模型只拥有两项能力：

1. 在既定栏目内排列事实；
2. 把同栏目、长度允许的多个事实组织为一个展示条目。

模型输出合同中没有 `text`、`summary`、`diagnosis`、`risk`、`recommendation` 等字段。最终医生看到的文字由本地渲染器逐字取回 `canonical_text`，不是模型生成的文字。

## 3. 本地事实与证据门禁

模型响应必须同时满足：

1. JSON 严格合同通过，额外字段直接拒绝；
2. 不得引用 Fact Ledger 之外的 ID；
3. 同一 fact 不能重复；
4. fact 不能移动到其他 section；
5. 所有 `mandatory=true` 的事实必须且只能覆盖一次；
6. 单组最多 25 个事实，整体最多 100 组；
7. 本地渲染后的单条正文不能超过合同长度；
8. 每个 SummaryEvidenceItem 至少保留一条原始 EvidenceReference。

当前 Timeline、指标状态和趋势事实均为 mandatory。这意味着 v1 可以整理展示，但不能为了“更像摘要”而漏掉某个指标。将来若允许非关键事实省略，必须先形成单独的临床产品规则与评测集，不能只改 Prompt。

## 4. 不知道指标数量时的容量策略

Fact Ledger 本身不固定指标数。真实模型调用设置 `MAX_LLM_FACTS=200`，这是上下文与可审计性的运行门，不是临床指标 Schema：

- 0 个事实：不调用模型，生成空的确定性摘要；
- 1–200 个事实且本地正文总长度不超过 60,000 字符：可进入受控 LLM 编排；
- 超过 200 个事实或 60,000 字符：不截断、不抽样、不让模型选择，改用确定性逐事实渲染；
- 无模型配置、请求失败或输出违规：同样使用全部事实确定性回退。

因此系统不会因为指标数量超出模型容量而静默漏数据。后续若医生配置导致事实长期超过上限，应通过时间窗、Pathway Summary 模板或医生批准的分层展示解决，而不是让模型自行决定哪些临床事实不重要。

## 5. 指标状态如何变成医生可读事实

本地模板只陈述已持久化结果：

- `current`：显示最新值、单位和有效时间；
- `stale`：显示 last known，并明确“不代表当前状态”；
- `unknown`：明确系统没有可用值及已知原因；
- `conflict`：明确存在冲突，系统没有选择值；
- `calculated trend`：显示首值、末值、差值和 increasing/decreasing/unchanged，并明确“不表示好转或恶化”；
- 数据不足、单位不一致或冲突：明确没有计算方向或差值。

例如未来 GLP-1 Pathway 审批了体重、恶心、呕吐、饮水量和腹痛五个指标，Summary Agent 接收的是五组通用状态/趋势事实。若后来医生再批准“注射部位反应”，第六个指标会以相同合同进入，不需要增加 `injection_site_reaction` 专用 Prompt 字段。

这里不生成诊断、风险等级、用药依从性或处置建议。只有上游真实采集、医生批准的指标定义或已审批规则产生了相应事实时，摘要才可以引用它们。

## 6. 回退与审计

稳定回退原因包括：

| reason code | 含义 |
|---|---|
| `no_summary_facts` | 当期没有可总结事实 |
| `fact_ledger_limit_exceeded` | 事实数量超过模型运行门 |
| `fact_ledger_size_exceeded` | 事实正文总长度超过模型运行门 |
| `summary_model_not_configured` | Summary LLM 未启用或未配置 |
| `summary_model_request_failed` | 模型请求失败 |
| `summary_model_output_rejected` | 模型输出违反本地合同或事实门禁 |

成功的模型版本保存 provider、model、Prompt、Agent、token usage、request ID 和 outline digest。失败回退版本不保留模型执行元数据，避免被误认为模型摘要，但会保存明确的 fallback reason。

每个版本同时保存：

- 全部 `source_fact_ids`；
- Timeline 来源 ID；
- 精确的 State Snapshot 版本引用；
- 每条正文的 EvidenceReference；
- Summary 生成 Provenance；
- 模型/Prompt/Agent 版本或确定性回退版本。

相同事实清单和相同模型配置重复调用时不再次请求模型，也不制造新版本；State Snapshot 或 Timeline 改变后才生成新的 Summary 版本。现有 `DoctorReviewService` 继续对该不可变 Summary 版本执行接受、修改或拒绝。

## 7. 配置与当前启用状态

配置项：

```dotenv
CONTINUCARE_USE_SUMMARY_LLM=false
CONTINUCARE_SUMMARY_PROMPT_VERSION=mimo-summary-outline-v1
```

提交用 `.env.example` 默认 `false`，防止新部署意外把真实患者内容发送给外部模型。本次正式验收已在被 Git 忽略的本地 `.env` 中显式设置为 `true`，并只发送固定合成事实。代码已提供 `MiMoControlledSummaryAdapter` 与 `ControlledSummaryService`；旧 Streamlit 医生页面仍未切换到第四层服务。页面接入、真实身份与病历写回仍属于后续角色应用和医院集成层。

## 8. 专项验收

启用 HL7 官方 R4 Schema 校验时，整仓结果为：

```text
169 passed
0 failed
0 skipped
```

本增强项新增 10 个专项场景，覆盖：

- 不预先写死指标名称时，已有指标和医生未来定义的未知指标都动态进入 Fact Ledger；
- 最终正文逐条等于本地 canonical text；
- 模型伪造 fact ID、遗漏 mandatory fact、重复 fact、跨 section 移动均被拒绝；
- 模型请求失败和未配置都有明确确定性回退；
- 同一 Fact Ledger 幂等，State Snapshot 新版本产生 Summary 新版本；
- MiMo 使用 JSON mode、零温度，额外自由文本字段触发严格 Schema 重试；
- MiMo 首次输出虽是合法 JSON 但遗漏必选事实时，在同一受控阶段严格重试一次；
- Summary Provenance 包含精确的 State Snapshot 和原始证据版本。

## 9. 真实 MiMo 正式验收

固定用例集为 `summary_live_cases_v1.json`，使用本地密钥、官方 MiMo API、`mimo-v2.5`、`mimo-summary-outline-v1` 和 JSON mode 单次运行。结果：

```text
5/5 cases passed
64/64 facts covered exactly once
6,734 total tokens
17,678 ms total latency
3,536 ms average latency
all cases completed in one schema attempt
```

五类用例分别验证：

1. GLP-1 多栏目状态、趋势、缺失、冲突与任务；
2. 模型事先不知道名称的医生自定义指标；
3. 事实正文中的提示注入仍被视为普通数据；
4. 40 个动态指标自动按每组最多 25 个拆分为 25+15，零遗漏、零重复。
5. 完整的 Observation → Clinical Memory → State Snapshot → 真实 MiMo → 本地渲染 → Summary/Provenance 持久化链路，并验证相同输入不会二次调用模型。

每个用例均通过以下九项检查：真实 provider、模型版本、Prompt 版本、事实恰好覆盖一次、本地正文逐字一致、证据覆盖一致、医生确认标记一致、输出只有严格 outline、usage/request ID 审计完整。

验收调试中观察到一次模型响应未通过事实覆盖门。为避免把稳定性建立在人工重跑上，MiMo 适配器现在会在首次输出通过 JSON Schema 后立即执行同一套本地 fact-ID/栏目/mandatory 门禁；若不通过，在同一受控阶段严格重试一次，第二次仍失败则由服务确定性回退。最终冻结运行的五个用例均在第一次尝试通过。

复现命令：

```bash
.venv/bin/python scripts/evaluate_summary_live.py --fail-on-mismatch
```

机器可读原始报告：[`evaluations/layer4_controlled_summary_v1.0.0_mimo_live.json`](evaluations/layer4_controlled_summary_v1.0.0_mimo_live.json)。

该结论表示受控模型工程链路通过固定合成数据验收，不构成临床有效性、真实患者数据出境、医院上线或病历写回批准。真实患者使用前仍需医院隐私评估、数据处理协议、模型供应商审批、身份权限和临床试点。
