# ContinuCare 本地 Demo 实施架构

```text
患者提交
  └─ FollowUpService
       ├─ FHIR QuestionnaireResponse（患者原始答案）
       └─ Questionnaire 一致性校验
            └─ ExtractionService / MockExtractor
                 ├─ FHIR Observation（仅明确、可编码事实）
                 ├─ derivedFrom → QuestionnaireResponse
                 └─ 独立证据元数据（原文位置、置信层级）
                      └─ evaluate_risk
                           └─ clinical_rules=[] → not_assessed / no Alert

SQLiteStore
  ├─ 完整 FHIR JSON
  ├─ 检索投影和证据元数据
  ├─ Summary / Review
  └─ AuditEvent
```

## 强制边界

- `validate_r4_resource`：写入前使用 R4 生成模型拒绝未知字段和无效基础结构。
- `validate_questionnaire_response_against_questionnaire`：检查 canonical、版本、linkId 和回答类型。
- `validate_official_json_schema`：CI 使用 HL7 官方 `fhir.schema.json.zip` 做独立验证。
- `MockExtractor`：只处理有明确原文和受控术语映射的合成内容，不做疾病推断。
- `evaluate_risk`：当前没有获批规则，始终返回 `not_assessed`；未来规则必须经版本化临床审批后才可启用。
- `SQLiteStore`：保存完整资源；不得用数据库自定义列替代 FHIR 资源。

业务服务不导入 Streamlit、飞书 SDK 或特定外部模型 SDK。医院对接时须增加目标 Profile、CapabilityStatement、术语服务和安全规范，而不是修改或伪造 FHIR 基础字段。
