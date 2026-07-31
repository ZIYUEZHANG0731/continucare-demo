# 13. FHIR R4 合规策略与上线门槛

## 1. 基线

ContinuCare 临床数据边界固定为 **HL7 FHIR R4 4.0.1**。选择 R4 是因为 Observation 在该版本已为 Normative，且项目当前对接目标尚未指定医院专属 Implementation Guide。

“FHIR 合规”必须分层表述：

1. **Base Resource 结构合规**：字段、类型、基数、choice element 和基础不变量符合 R4；
2. **Profile 合规**：符合目标国家、地区或医院的 StructureDefinition；
3. **Terminology 合规**：编码、显示名称、版本和 ValueSet 绑定通过术语服务器验证；
4. **Reference 合规**：资源引用可解析，QuestionnaireResponse 与 Questionnaire 一致；
5. **业务与临床合规**：规则、权限、审批和责任流程由医院确认。

只有第 1 层通过时，只能表述为“FHIR R4 base-resource structurally conformant”，不得宣称已经可被任何医院直接接入。

## 2. 项目内的强制边界

- 患者问题：FHIR `Questionnaire`；
- 患者某次回答：FHIR `QuestionnaireResponse`；
- 患者报告形成的临床事实：FHIR `Observation`；
- 可复用路径动作：FHIR `PlanDefinition`；
- 原始答案通过 `Observation.derivedFrom` 回到 QuestionnaireResponse；
- 抽取置信度和字符位置保存在独立应用证据表，不添加伪造的 FHIR 原生字段；
- 临床资源以完整 FHIR JSON 保存，数据库索引列只作为检索投影，不能替代资源本体。

## 3. 自动验证门槛

任何临床资源写入前必须：

1. 通过 SMART on FHIR `fhirclient 4.4.0` 的 R4 生成模型；
2. 拒绝未知字段和缺少必填字段的资源；
3. 在测试中通过 HL7 官方 `fhir.schema.json.zip`；
4. 校验 `resourceType`、FHIR `id`、subject、value[x] 和 derivedFrom；
5. 通过资源间引用和 Questionnaire/QuestionnaireResponse 一致性测试。

HL7 官方说明 JSON Schema 不能完成术语绑定、全部不变量、Profile、问卷和业务规则验证，因此生产 CI 还必须使用官方 FHIR Validator CLI 和目标 Implementation Guide：

- 验证说明：https://hl7.org/fhir/R4/validation.html
- 官方 Schema：https://hl7.org/fhir/R4/fhir.schema.json.zip
- Observation：https://hl7.org/fhir/R4/observation.html

## 4. 术语门槛

新增 Observation code 前必须同时提交：

- `system`、`code`、`display`、术语版本；
- 官方术语页面或目标术语服务器查询证据；
- 适用的 value[x]、ValueSet 和 UCUM 单位；
- 指标定义、时间窗、数据来源和缺失值处理；
- 临床信源文档更新；
- 术语审核人。

严禁凭模型记忆或搜索摘要猜测编码。生产部署必须对 LOINC/SNOMED CT 执行 `$validate-code` 或医院认可的等效验证。

## 5. 临床规则门槛

任何自动追问、分级、报警、SLA 或处置动作必须具有：

- 明确适用人群、药物、适应证和时间窗；
- 可定位到章节的监管资料、指南或院内规范；
- 规则版本和变更历史；
- 临床审批人、术语审批人和批准日期；
- 回顾性测试集、边界案例和漏报/误报记录；
- 可回退方案。

缺少任一项时采取 fail-closed：可保存原始回答和标准 Observation，但不得自动生成临床优先级或处置建议。

## 6. 当前限制

当前 FHIR canonical 使用稳定 `urn:uuid`，在没有组织自有域名时比伪造域名更诚实。真实部署前必须由实施机构发布可解析、版本化的 HTTPS canonical，并形成项目 Implementation Guide、CapabilityStatement、SearchParameter 和 API 安全规范。

因此，当前版本已经建立严格的 FHIR R4 基础资源边界，但仍是临床审核草案，不宣称已经完成目标医院 Profile 和生产接口认证。
