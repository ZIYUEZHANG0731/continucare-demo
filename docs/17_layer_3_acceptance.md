# 第三层验收：受控语义理解与 Agent 运行时

## 1. 本层目标

第三层把患者自由表达转换为“待确认的 Questionnaire 答案候选”，但不允许 Agent 直接写入 FHIR、生成风险等级、创建 Alert 或提供诊疗建议。患者明确确认后，候选才进入第二层 `CareEngine`，继续走既有 Questionnaire 校验与确定性 Observation 映射。

```text
患者原话
  → Care Agent（结构化候选/澄清问题）
  → Safety Agent v2（白名单、字段级证据、主语、否定、时间、单位）
  → 患者确认
  → CareEngine.save_draft
  → 患者最终提交
  → QuestionnaireResponse / Observation
```

## 2. 已实现能力

| 能力 | 实现 | 状态 |
|---|---|---|
| Agent 间结构化契约 | `SemanticTask / SemanticCandidate / ClarificationRequest / CandidateIssue / SemanticResult`，Pydantic `extra=forbid` | 通过 |
| 受控运行时 | 显式 Registry、Agent 版本、超时、任务幂等、工具白名单（当前为空） | 通过 |
| Care Agent v1 | 小米 MiMo JSON mode 语义适配器；异常时回退本地确定性语义 Mock | 通过（合成范围） |
| 友好表达 | 语义与措辞分离；`patient_language_v1.json` 版本化模板 | 通过 |
| 动态澄清 | 缺少“过去 24 小时”或“当前”语义时不补造，生成确认问题 | 通过 |
| Safety Agent v2 | linkId/code/answerOption/UCUM 白名单、证据跨度、字段级证据一致性、主语、否定、时间和确认要求检查 | 通过 |
| 注入与越界 | 指令型文本阻断；历史/他人描述不形成当前患者候选 | 通过（合成用例） |
| 患者确认门 | 未确认时不更新 CareSession；确认后只调用 `CareEngine.save_draft` | 通过 |
| 审计 | AgentRun 保存输入哈希、版本、模式、结构化输出；患者接受/拒绝另记 AuditEvent | 通过 |
| 混合患者端 | 对话整理 + 候选卡片 + 澄清按钮 + 完整问卷兜底 | 通过 |
| 模型接口 | `MiMoSemanticAdapter`，OpenAI-compatible `/chat/completions`，官方域名白名单 | 已实现；本地密钥不入库 |

## 3. 当前模型状态

仓库不包含 API Key。`CareAgentService` 会根据环境配置选择 `MiMoSemanticAdapter`：默认模型为 `mimo-v2.5`，使用官方 OpenAI-compatible 地址和 `response_format={"type":"json_object"}`。缺少密钥、请求失败、超时、JSON/Pydantic 校验失败时，系统回退 `local_semantic_mock`，并把实际模式写入 AgentRun 和审计。

Provider-neutral 接口仍被保留：

```python
class SemanticModelAdapter(Protocol):
    config: SemanticModelConfig

    @property
    def configured(self) -> bool: ...

    def extract(self, task: SemanticTask) -> SemanticResult: ...
```

MiMo 只返回 `link_id / answer / verbatim evidence / subject / temporality / negated`。FHIR code 和患者友好措辞由本地锁定 Questionnaire 与语言模板重新绑定；模型输出仍必须通过 `SafetyAgent.review`，不能绕过患者确认或第二层。环境变量见 `.env.example`。API 密钥只能放在部署密钥系统或本地未跟踪 `.env` 中。

## 4. 验收证据

```bash
.venv/bin/pytest -q
.venv/bin/python scripts/evaluate_semantic_layer.py
.venv/bin/python scripts/mimo_smoke_test.py  # 需要本地轮换后的 Key
```

本次基线验收结果：

- HL7 官方 R4 Schema 启用后：`68 passed`；
- Questionnaire、PlanDefinition、QuestionnaireResponse、Observation 官方 Schema 验证通过；
- 离线语义契约集：8/8 状态、linkId 集合、澄清数量和 Safety 结果完全匹配；
- MiMo 真实合成数据冒烟测试通过：明确的过去24小时呕吐与当前恶心进入确认态；“今天吐了五次”进入24小时时间窗澄清；“呕吐”不能再作为“恶心”的证据；
- 原有三条比赛 Demo 彩排：3/3 通过；
- 浏览器端：明确事实候选确认、缺失 24 小时澄清、问卷同步和最终提交通过；
- 390px 窄屏无页面横向溢出，浏览器控制台无 warning/error。

合成语义集覆盖：明确事实、缺失时间窗、单位换算、两种当前否定表达、历史描述、他人主语和提示注入。该评测只证明工程契约回归，不代表临床准确率、真实患者语言覆盖率或模型效果。

## 5. 不影响后层实现的边界

- 第二层仍是唯一 FHIR 构造与持久化入口，第三层没有复制映射逻辑。
- 第三层输出引用当前锁定的 Questionnaire linkId/code/version。
- 未配置或无法访问 MiMo 时仍可完成 UI、审计、确认和提交闭环。
- 第四层可以直接消费患者最终确认的 QuestionnaireResponse、Observation、Communication/Audit，而不依赖本地 Mock 的内部正则。

## 6. 进入真实试点前仍缺失

- 医院批准的真实语料集、标注规范、医生双人复核与分层评测；
- 真实语料上的 MiMo 分层评测、结构化输出重试、成本和延迟监控；
- 多轮会话持久化、并发冲突、身份认证、撤回/修订与隐私保留策略；
- 急症表达的医院批准固定流程；当前系统不是急救通道；
- 对否定、时间、主体、数值冲突和方言的更大规模对抗评测；
- 临床审批后的提示词、语言模板和发布流程。

因此当前结论是“第三层比赛工程基线已具备可解释的语义防线”，不是“临床验证完成”或“可接入真实患者”。
