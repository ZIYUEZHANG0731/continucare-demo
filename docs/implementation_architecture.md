# ContinuCare 本地 Demo 实施架构

## 当前主流程：受控语义第三层 + Questionnaire 第二层

```text
Patient.pathway_code
  └─ CareEngine.start_or_resume
       ├─ Pathway Registry / 版本锁定
       ├─ CareSession 草稿与生命周期
       └─ FHIR Questionnaire
            ├─ 动态患者端 Renderer（完整问卷兜底）
                 ├─ answerOption / enableWhen
                 ├─ boolean / choice / integer / quantity / text
                 └─ QuestionnaireResponse Builder + 语义校验
                      ├─ 完整 FHIR QuestionnaireResponse
                      ├─ 确定性 Observation Mapping Policy
                      │    └─ FHIR Observation + derivedFrom
            └─ Care Agent 对话辅助
                 ├─ SemanticTask（只含锁定问卷白名单）
                 ├─ MiMoSemanticAdapter（OpenAI-compatible JSON mode）
                 ├─ 本地语义 Mock 回退
                 ├─ Safety Agent v2
                 └─ 候选卡片 / 动态澄清
                      └─ 患者确认后 CareEngine.save_draft

SQLiteStore 原子提交
  ├─ CareSession
  ├─ AgentRun（版本、模式、输入哈希、结构化输出）
  ├─ 完整 FHIR JSON
  ├─ 检索投影和证据元数据
  ├─ Summary / Review
  └─ AuditEvent
```

手工结构化答案不经过 Mock 或 LLM。自由文本只由第三层提出候选；未经过 Safety Agent 与患者确认不能进入 CareSession。当前 `clinical_rules=[]`，完成采集后保持 `not_assessed / no Alert`。

## 第三层强制边界

- `AgentRuntime`：只运行显式注册 Agent；当前 Care Agent 工具白名单为空，不能直接操作数据库或 FHIR。
- `SemanticModelAdapter`：Provider-neutral 适配协议；仓库不包含密钥。
- `MiMoSemanticAdapter`：仅允许小米官方 HTTPS 域名，严格解析 JSON/Pydantic 契约，不信任模型提供的 FHIR code 或患者措辞。
- `CareSemanticAgent`：MiMo 可用时调用适配器，失败或未配置时回退本地 Mock。
- `SafetyAgent`：拒绝未知 linkId/code、无效 answerOption/UCUM、错误证据跨度、错误主语/时间和无需确认的候选。
- `PatientLanguageRenderer`：只负责版本化措辞，不改变 linkId、值、时间范围或单位。
- `CareAgentService`：只有患者接受候选或澄清选项时才能调用 `CareEngine.save_draft`。
- Agent 不生成诊断、治疗、用药建议、风险等级或 Alert。

## 兼容测试流程

旧 `FollowUpService → MockExtractor` 自由文本链继续用于固定测试夹具和彩排回归，但不再是患者端主流程。它不会调用外部模型，也不能代表第三层生产语义理解能力。

## 强制边界

- `CareEngine`：锁定 Pathway/Questionnaire 版本，管理草稿、完成、停止和幂等提交。
- `build_questionnaire_response`：只能按照 Questionnaire item type 构造 FHIR `answer.value[x]`。
- `validate_questionnaire_response_against_questionnaire`：检查 canonical、版本、linkId、回答类型、answerOption、required、repeats 和 enableWhen。
- `ObservationMappingPolicy`：只有登记的 linkId 和转换方式可以形成 Observation。
- `validate_r4_resource`：写入前使用 R4 生成模型拒绝未知字段和无效基础结构。
- `validate_official_json_schema`：CI 使用 HL7 官方 `fhir.schema.json.zip` 做独立验证。
- `evaluate_risk`：当前没有获批规则，始终返回 `not_assessed`；未来规则必须经版本化临床审批后才可启用。
- `SQLiteStore`：保存完整资源；数据库索引列只是投影，不能替代 FHIR 资源。

业务服务不导入 Streamlit、飞书 SDK 或特定外部模型 SDK。医院对接时须增加目标 Profile、CapabilityStatement、术语服务和安全规范，而不是修改或伪造 FHIR 基础字段。
