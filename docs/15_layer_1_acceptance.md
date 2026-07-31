# 15. 第一层验收报告：临床知识与 FHIR 契约层

- 版本：1.0.0（2026-07-31）
- 验收结论：**第一层工程基线通过，可进入第二层；医院实施基线尚未通过。**

## 1. 第一层目标

第一层必须为前端、Care Engine、Agent 和后续工作流提供同一份机器可读的临床契约，并做到：

- 问什么、如何回答、形成什么临床事实都有标准结构；
- 每个指标都有术语、单位、时间窗和权威来源；
- 原始回答与标准化事实双向可追溯；
- 无效 FHIR 资源不能进入数据库；
- 未经临床批准的规则不能运行；
- 当前实现能力和目标医院落地能力明确区分。

## 2. 当前交付物清单

| 交付物 | 文件或目录 | 状态 |
|---|---|---:|
| FHIR R4 校验边界 | `continucare/fhir/r4.py` | 已实现 |
| QuestionnaireResponse 跨资源校验 | `continucare/fhir/references.py` | 已实现，当前支持平面问卷 |
| Observation Builder | `continucare/fhir/observations.py` | 已实现 |
| QuestionnaireResponse Builder | `continucare/fhir/questionnaires.py` | 已实现自由文本入口 |
| 受控术语常量 | `continucare/fhir/terminology.py` | 已实现当前指标 |
| Pathway 治理模型 | `continucare/pathways/models.py` | 已实现 |
| Pathway Registry | `continucare/pathways/registry.py` | 已实现 |
| GLP1-14D 治理清单 | `continucare/pathways/data/glp1_14d_v1.json` | Draft |
| FHIR Questionnaire | `continucare/pathways/data/fhir/glp1_followup_questionnaire_v1.json` | Draft |
| FHIR PlanDefinition | `continucare/pathways/data/fhir/glp1_followup_plan_definition_v1.json` | Draft |
| 指标与临床信源包 | `docs/clinical/glp1_14d_observation_evidence.md` | 临床审核草案 |
| FHIR 合规策略 | `docs/13_fhir_conformance_policy.md` | 已建立 |
| 官方 Schema 验证脚本 | `scripts/validate_fhir_r4.py` | 已实现 |
| 自动化测试 | `tests/test_fhir_conformance.py`、`tests/test_pathways.py` 等 | 通过 |

## 3. 当前 Pathway 内容

### 3.1 Questionnaire

| `linkId` | 类型 | 标准含义 | 当前用途 |
|---|---|---|---|
| `nausea-present` | boolean | SNOMED CT `422587007` Nausea | 明确询问恶心是否存在 |
| `nausea-severity` | choice | LOINC `81660-3` 与 LOINC Answers | 恶心存在时显示受控程度选项 |
| `vomiting-count-24h` | integer | LOINC `94070-0` | 过去 24 小时呕吐次数 |
| `fluid-intake-24h-estimated` | quantity | LOINC `75301-2` | 过去 24 小时估计液体摄入 |
| `abdominal-pain-present` | boolean | SNOMED CT `21522001` | 明确询问腹痛是否存在 |
| `free-text-report` | text | 原始患者补充说明 | 保留按钮之外的自然语言 |

### 3.2 当前允许生成的 Observation

| 指标 | `Observation.code` | `value[x]` | UCUM/答案 | 时间 |
|---|---|---|---|---|
| 明确恶心 | SNOMED CT `422587007` | `valueBoolean` | true | `effectiveDateTime` |
| 呕吐次数 | LOINC `94070-0` | `valueQuantity` | `/d` | 24 小时 `effectivePeriod` |
| 液体摄入 | LOINC `75301-2` | `valueQuantity` | `mL/(24.h)` | 24 小时 `effectivePeriod` |
| 明确腹痛 | SNOMED CT `21522001` | `valueBoolean` | true | `effectiveDateTime` |

恶心程度的 LOINC Answer 已在 Questionnaire 中定义，但自由文本“有点恶心”不会被自动换算为 Mild。必须来自患者按钮选择或人工确认。

## 4. 已通过的工程验收

### 4.1 FHIR 结构

- 固定使用 FHIR R4 4.0.1；
- Questionnaire、QuestionnaireResponse、Observation、PlanDefinition 通过严格 R4 模型；
- 未知字段和缺少必填字段会被拒绝；
- Observation 使用标准 `value[x]`，不保存自定义临床属性；
- `subject`、`performer`、`issued`、`effective[x]` 和 `derivedFrom` 已建立。

### 4.2 引用与追溯

- QuestionnaireResponse canonical 与 version 必须匹配发布问卷；
- QuestionnaireResponse `linkId` 和回答类型必须存在于 Questionnaire；
- Observation `derivedFrom` 必须引用产生它的 QuestionnaireResponse；
- QuestionnaireResponse subject 必须与来源消息患者一致；
- 原文证据位置和抽取置信度保存在独立应用元数据，不伪装成 FHIR 字段。

### 4.3 持久化

- 数据库存储完整 FHIR JSON；
- 搜索字段只作为数据库投影；
- 保存前和读取时重新验证；
- 资源被构造后再篡改，持久化边界仍会拒绝；
- 数据库外键保证 Observation 来源回答存在。

### 4.4 治理

- Pathway 包含版本、状态、FHIR 版本、来源、资源 canonical 和审批对象；
- Active 状态要求取消 synthetic 标记并具有临床、术语审批；
- ClinicalRule 必须引用已登记信源；
- 当前 `clinical_rules=[]`，所有输入保持 `not_assessed`；
- 旧版 `level_1_3`、`times`、饮水减少布尔值和无来源 L2/L4 规则已停用。

### 4.5 自动化结果

当前验收结果：

```text
37 passed
Questionnaire: valid
PlanDefinition: valid
QuestionnaireResponse: valid
Observation: valid
三个合成场景连续彩排三次通过
```

验证命令见 [evaluation.md](evaluation.md)。测试结果只能证明当前工程契约和合成场景，不代表临床性能。

## 5. 第一层尚未封闭的工程项

这些工作应在第二层患者端正式接入前完成，或与第二层一起完成：

### 5.1 通用结构化 QuestionnaireResponse Builder

当前运行时主要保存 `free-text-report`。需要新增通用 Builder，接受 boolean、choice、integer、quantity 和 text，并保持 Questionnaire 的 item 层级。

### 5.2 更完整的问卷语义校验

当前已校验 canonical、linkId 和回答数据类型；还需补：

- choice 是否属于 `answerOption` 或绑定 ValueSet；
- `required`；
- `repeats`；
- `enableWhen` 与被禁用问题的答案；
- quantity 允许单位；
- 嵌套 group/item；
- QuestionnaireResponse 修改和撤回。

### 5.3 Questionnaire 到 Observation 的通用映射

需要从结构化答案确定性生成 Observation，而不是把所有内容再次交给 LLM：

```text
Questionnaire item.code
+ QuestionnaireResponse answer.value[x]
+ Pathway mapping policy
→ FHIR Observation
```

自由文本抽取仍然保留，但只作为按钮和结构化输入之外的补充路径。

### 5.4 Observation 生命周期与 Provenance

当前明确、完整的合成患者报告使用 `final`。接入模型后必须区分：

- `preliminary`：信息不完整或未经所需确认；
- `final`：已完成且不再需要后续动作；
- `amended/corrected`：患者或医护后续修订；
- `entered-in-error`：错误记录撤回。

同时需要 FHIR Provenance 记录模型、版本、患者确认和医护审核。

## 6. 真实患者上线前的阻断项

以下内容不阻止开发第二层，但阻止真实患者使用：

1. 明确具体药物、剂型、适应证、人群和地区说明书；
2. 扩展并临床审核完整指标范围，例如腹泻、便秘、症状持续时间、体重和需要人工确认的上下文；
3. 锁定 SNOMED CT 地区版本、许可证和术语服务器；
4. 为目标医院发布 HTTPS canonical、StructureDefinition、ValueSet 和 Implementation Guide；
5. 在生产 CI 使用官方 FHIR Validator 和术语 `$validate-code`；
6. 完成临床、药学、术语、信息安全和法务审批；
7. 建立身份、授权、同意撤回、隐私、审计、备份和事件响应；
8. 任何临床规则必须单独提供证据、阈值、审批人、测试集和回退方案。

## 7. 验收判定

### Layer 1A：工程基线

**通过。** 当前第一层已经能为第二层提供可信、版本化、可校验的 Questionnaire 与 Observation 契约。

### Layer 1B：患者交互封板

**部分通过。** 还需完成通用 QuestionnaireResponse Builder、完整问卷语义校验和结构化答案映射。

### Layer 1C：医院实施基线

**未通过，且当前不应尝试伪装通过。** 必须等待目标医院、地区术语环境和临床审批。

## 8. 下一步

第二层应从“Questionnaire renderer + 通用 QuestionnaireResponse Builder”开始。完成后，患者端按钮、文字和语音会统一进入同一 FHIR 回答模型，第三层 Agent 只处理确实需要语义理解的内容。
