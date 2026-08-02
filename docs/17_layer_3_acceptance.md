# 第三层验收：受控语义理解与 Agent 运行时

## 1. 本层目标

第三层把患者自由表达转换为“待确认的 Questionnaire 答案候选”，但不允许 Agent 直接写入 FHIR、生成风险等级、创建 Alert 或提供诊疗建议。患者明确确认后，候选才进入第二层 `CareEngine`，继续走既有 Questionnaire 校验与确定性 Observation 映射。

```text
患者原话
  → ConversationContext（本次每日随访全部轮次 + 唯一待确认动作）
  → TemporalContext（患者时区 + 当地日期 + 每日随访实例）
  → Care Agent（MiMo 结构化候选 + 不带 code 的症状检索词）
  → Terminology Resolver（仓库 GLP-1 目录检索 / code 与版本校验）
       ├─ 唯一命中 → 候选卡片
       ├─ 多候选 → 语义区分按钮
       └─ 未命中 → 保留原话 / 待术语与医生复核
  → Safety Agent v4 硬规则（白名单、证据、值、依赖、主语、否定、时间、单位、上下文绑定）
  → MiMo Safety Critic（候选语义复核 + 遗漏项检查，只能降级）
  → 必要时按 linkId 定向补抽取，否则回到 Questionnaire 澄清
  → MiMo Language Rewriter（事实锁定；不合规时回退固定模板）
  → 患者确认
  → CareEngine.save_draft / ConfirmedSymptomReport
  → 患者最终提交
  → QuestionnaireResponse / Observation
```

## 2. 已实现能力

| 能力 | 实现 | 状态 |
|---|---|---|
| Agent 间结构化契约 | `SemanticTask / SemanticCandidate / ClarificationRequest / CandidateIssue / SemanticResult`，Pydantic `extra=forbid` | 通过 |
| 受控运行时 | 显式 Registry、Agent 版本、超时、任务幂等、工具白名单（当前为空） | 通过 |
| Care Agent v1 | 小米 MiMo JSON mode 语义适配器；异常时回退本地确定性语义 Mock | 通过（合成范围） |
| 友好表达 | Care Agent 内部 Language Rewriter 使用独立 MiMo Prompt；本地锁定数字、症状、程度、肯否和时间，失败时回退 `patient_language_v1.json` | 通过（合成范围） |
| 动态澄清 | 缺少“过去 24 小时”或“当前”语义时不补造，生成确认问题 | 通过 |
| 短期连续对话 | 以每日 `followup_occurrence_id` 为边界保留本次随访全部轮次；“是的/不是/不确定”只能绑定唯一待处理动作 | 通过（合成范围） |
| 基础长期记忆 | 已完成 Observation 最多 50 条作为只读跨日上下文；Prompt 明确禁止把历史事实当作今天证据 | 通过（合成范围） |
| GLP-1 术语目录 | 40+ 患者可报告概念、中文别名、SNOMED CT 版本、说明书来源、审核状态和 Questionnaire binding | 通过（原型核验，院端待复核） |
| 统一术语检索 | 已知 Questionnaire 候选同样必须产生仓库匹配；模型不能提供最终 code | 通过 |
| 新症状结构化 | 唯一命中后显示确认卡；多 code 时按钮消歧；未命中不猜 code；确认后形成同格式 Observation 并标记 `patient_reported_new` | 通过（合成范围） |
| 简短追问回答 | “轻度/2次/800毫升”可绑定唯一个已发布 Questionnaire 问题；保留当前原话、上轮 actionId 和再次确认门 | 通过（合成范围） |
| 患者本地时间 | IANA 时区、UTC/当地接收时间、“今天/昨天/过去24小时/现在”和稳定 `followup_occurrence_id` 均进入 AgentRun | 通过（合成范围） |
| 临床有效时间贯通 | 候选确认后持久化 `effective_start/end` 与解析依据；最终 Observation 区分 `issued` 与 `effective[x]` | 通过（合成范围） |
| 完整性补偿 | Safety Critic 标记有逐字证据的遗漏 `linkId`，Care Agent 只对该字段补抽取；仍不能确定时使用已发布 Questionnaire 追问 | 通过（合成范围） |
| Safety Agent v4 硬规则 | linkId/code/answerOption/UCUM、enableWhen、证据跨度、字段和值一致性、主语、否定、时间、唯一上下文绑定和确认要求检查 | 通过 |
| MiMo Safety Critic | 在硬规则之后独立复核候选和遗漏项；不能恢复硬拒绝、不能覆盖顶层阻断规则；JSON 不合规只重试一次 | 通过（合成范围） |
| 注入与越界 | 指令型文本阻断；历史/他人描述不形成当前患者候选 | 通过（合成用例） |
| 患者确认门 | 未确认时不更新 CareSession；确认后只调用 `CareEngine.save_draft` | 通过 |
| 审计 | 单个顶层 AgentRun 保存各阶段 agent/model/prompt、状态、Token、延迟、请求 ID 和回退原因；患者接受/拒绝另记 AuditEvent | 通过 |
| 混合患者端 | 对话整理 + 候选卡片 + 澄清按钮 + 完整问卷兜底 | 通过 |
| 模型接口 | `MiMoSemanticAdapter`，OpenAI-compatible `/chat/completions`，官方域名白名单 | 已实现；本地密钥不入库 |

## 3. 当前模型状态

仓库不包含 API Key。`CareAgentService` 会根据环境配置选择 `MiMoSemanticAdapter`：默认模型为 `mimo-v2.5`，使用官方 OpenAI-compatible 地址和 `response_format={"type":"json_object"}`。同一模型配置下运行三个彼此独立的 Prompt：`mimo-semantic-extraction-v4`、`mimo-safety-critic-v2` 和 `mimo-language-rewrite-v1`。

主抽取缺少密钥、请求失败、超时或 JSON/Pydantic 校验失败时，系统回退 `local_semantic_mock`。Safety Critic 不合规时严格重试一次，仍失败则保留确定性硬规则结果；Language Rewriter 不可用、合同不合规或事实完整性检查失败时，保留本地固定模板。每一次实际模式都会进入 AgentRun 的 `stage_traces`。

Provider-neutral 接口仍被保留：

```python
class SemanticModelAdapter(Protocol):
    config: SemanticModelConfig

    @property
    def configured(self) -> bool: ...

    def extract(self, task: SemanticTask) -> SemanticResult: ...
```

抽取 MiMo 返回 `link_id / answer / verbatim evidence / subject / temporality / negated`，并可额外返回不带 code 的 `symptom_mentions` 检索词。无论已知字段还是患者新增症状，FHIR code 都必须由版本化仓库目录检索和重新绑定；MiMo 提供的 code 没有入口。明确的中文数字和已发布 choice 别名可由本地确定性代码归一化，但候选值仍必须与逐字证据一致。对“轻度”这类省略主题的简短回答，只允许与唯一未完成的已发布问题建立 `context_binding`；绑定无法回溯时硬规则拒绝。Language Rewriter 只处理展示措辞；任何数字、单位、症状、程度、肯否或时间变化都会被拒绝。

Safety LLM 是附加 Critic，不是最终裁判：它只能保留、拒绝或要求澄清硬规则已经允许的候选，不能恢复硬拒绝，也不能把普通历史描述升级为系统级阻断。所有候选仍不能绕过患者确认或第二层。环境变量见 `.env.example`；API 密钥只能放在部署密钥系统或本地未跟踪 `.env` 中。

## 4. 验收证据

```bash
.venv/bin/pytest -q
.venv/bin/python scripts/evaluate_semantic_layer.py
.venv/bin/python scripts/mimo_smoke_test.py  # 需要本地轮换后的 Key
.venv/bin/python scripts/evaluate_mimo_live.py --fail-on-mismatch
```

本次基线验收结果：

- 官方 HL7 FHIR R4 Schema 全量测试：`89 passed`，0 failed，0 skipped；下载地址、SHA-256 和独立资源验证结果已写入机器可读报告；
- 既有 Questionnaire、PlanDefinition、QuestionnaireResponse、Observation 官方 Schema 验证路径未改变；
- 离线语义契约集：8/8 状态、linkId 集合、澄清数量和 Safety 结果完全匹配；
- 2026-08-02 冻结配置 MiMo 单轮真实合成评测：业务结果 10/10 精确，完整三 Prompt 链路 10/10，原始模型输出 10/10；总计 34,228 tokens，平均端到端延迟 8,468 ms；原始逐例报告已入库；
- 连续对话/时间增量后的 MiMo v3/v2 真实合成烟雾测试：两次均稳定得到 `呃吐次数=2 / 恶心=是 / 恶心程度=轻度`，且 Language Rewriter 2/2 通过；Safety Critic 1/2 严格 JSON 通过，1/2 触发确定性硬规则回退，未影响最终候选和确认门；
- 关键用例“过去24小时我吐了2次，现在有点恶心”稳定得到 `呕吐次数=2 / 恶心=是 / 恶心程度=轻度`，Safety Critic 与语言事实校验均通过；
- “今天吐了五次”只进入24小时时间窗澄清；呕吐不能支持恶心，食量少不能支持液体摄入；字符串中文数字可在证据一致时确定性归一化；
- 原有三条比赛 Demo 彩排：3/3 通过；
- 浏览器端：明确事实候选确认、缺失 24 小时澄清、问卷同步和最终提交通过；
- 390px 窄屏无页面横向溢出，浏览器控制台无 warning/error。

合成语义集覆盖：明确事实、依赖字段遗漏、缺失时间窗、单位换算、中文数字、值与证据冲突、程度冲突、当前否定、历史描述、他人主语、提示注入、Safety JSON 重试和语言事实回退。该评测只证明工程契约回归；10 例单次运行不能代表临床准确率、真实患者语言覆盖率或稳定的模型性能。

## 5. 不影响后层实现的边界

- 第二层仍是唯一 FHIR 构造与持久化入口，第三层没有复制映射逻辑。
- 第三层输出引用当前锁定的 Questionnaire linkId/code/version。
- 未配置或无法访问 MiMo 时仍可完成 UI、审计、确认和提交闭环。
- 第四层只能通过 `Layer4InputReader` 消费与 completed CareSession 关联的最终 QuestionnaireResponse、由其派生的最终 Observation 和 AuditEvent；代码端口不暴露 AgentRun、候选、聊天轮次、模型原始输出或 Mock 正则中间结果，并有反向依赖测试。

## 6. 进入真实试点前仍缺失

- 医院批准的真实语料集、标注规范、医生双人复核与分层评测；
- 真实语料上的 MiMo 分层评测、结构化输出重试、成本和延迟监控；
- Clinical Memory Agent 的趋势、状态快照、撤回/修订和遗忘策略尚未实现；当前只有已确认 Observation 的只读跨日上下文；
- 目标医院 SNOMED CT 地区版本/许可/术语服务器、医生与术语人员审批；当前目录是比赛原型覆盖集，不宣称穷尽所有可能症状；
- 急症表达的医院批准固定流程；当前系统不是急救通道；
- 对否定、时间、主体、数值冲突和方言的更大规模对抗评测；
- Safety/Language Prompt 与固定回退文案仍需医院临床审批；工程版本、评测报告和 Git 回滚点已经固化为 `continucare-layer3-v1.0.0` / `layer3-v1.0.0`；
- 延迟与成本优化；当前三个模型阶段串行运行，冻结评测平均约 8.5 秒。

因此当前结论是“第三层比赛工程基线已具备可解释的语义防线”，不是“临床验证完成”或“可接入真实患者”。
