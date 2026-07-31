# GLP-1 治疗后 14 天患者报告：Observation 与临床信源包

- 版本：1.0.0（2026-07-31）
- 状态：临床审核草案；仅限合成数据
- FHIR 基线：R4 4.0.1
- 术语基线：LOINC 2.82；SNOMED CT 版本须在目标地区与医院确定后锁定

## 1. 适用范围与不可外推边界

本信源包支持“GLP-1 治疗后患者报告采集”的第一版数据结构。当前药品安全证据主要来自司美格鲁肽 Wegovy 的 EMA/FDA 监管资料，并辅以 GLP-1 受体激动剂胃肠道不良事件多学科专家共识。

因此，本版本不得被解释为：

- 适用于所有 GLP-1 或 GLP-1/GIP 产品的统一临床路径；
- 已验证的分诊、诊断或用药决策系统；
- 可直接替代具体国家说明书、处方医生判断或医院急诊流程；
- 已获得目标医院临床、药学、术语和信息部门批准。

在真实试点前必须锁定：具体药物与剂型、适应证、患者人群、地区说明书版本、医院实施指南和责任科室。

## 2. 信源层级

本路径按以下优先级管理证据：

1. 监管机构批准的产品说明书与风险资料；
2. HL7 FHIR、LOINC、SNOMED CT、UCUM 官方标准；
3. 正式临床指南或多学科专家共识；
4. 经临床负责人批准的院内规范；
5. 其他研究仅作为补充，不可单独产生自动升级阈值。

## 3. 数据结构依据

### 3.1 FHIR 资源职责

| 内容 | FHIR R4 资源 | 依据 |
|---|---|---|
| 问题、选项、启用条件 | `Questionnaire` | [HL7 FHIR R4 Questionnaire](https://hl7.org/fhir/R4/questionnaire.html) |
| 患者某次原始回答 | `QuestionnaireResponse` | [HL7 FHIR R4 QuestionnaireResponse](https://hl7.org/fhir/R4/questionnaireresponse.html) |
| 由患者回答形成的点时临床事实 | `Observation` | [HL7 FHIR R4 Observation](https://hl7.org/fhir/R4/observation.html) |
| 可复用的路径动作定义 | `PlanDefinition` | [HL7 FHIR R4 PlanDefinition](https://hl7.org/fhir/R4/plandefinition.html) |
| Observation 来源关系 | `Observation.derivedFrom` 指向 `QuestionnaireResponse` | [Observation 详细定义](https://hl7.org/fhir/R4/observation-definitions.html) |

FHIR Observation 用于测量和点时主观评估，不应用来替代诊断。当前患者原话保存在 QuestionnaireResponse；标准化事实保存在 Observation；抽取置信度和字符位置属于应用证据元数据，不伪装成 FHIR 原生属性。

### 3.2 术语和单位职责

| 标准 | 用途 | 官方资料 |
|---|---|---|
| LOINC | 标识观察项目、测量项目和标准问卷项目 | [About LOINC](https://loinc.org/about/) |
| SNOMED CT | 表示症状和临床发现等临床语义 | [Clinical Finding and Disorder](https://docs.snomed.org/snomed-ct-specifications/snomed-ct-editorial-guide/readme/authoring/domain-specific-modeling/clinical-finding-and-disorder) |
| UCUM | 机器可计算的数量单位 | [UCUM Specification](https://ucum.org/ucum) |

## 4. 当前允许产生的 Observation

### 4.1 指标与编码矩阵

| 指标 | `Observation.code` | FHIR 值元素 | 时间语义 | 术语依据 | 当前状态 |
|---|---|---|---|---|---|
| 患者明确报告恶心 | SNOMED CT `422587007` Nausea | `valueBoolean=true` | `effectiveDateTime` | [NLM MedGen: Nausea](https://www.ncbi.nlm.nih.gov/medgen/10196) | 已实现；仅在患者原文明确出现时生成 |
| 恶心程度 | LOINC `81660-3` Nausea [Presence] | `valueCodeableConcept`，采用该词条列出的 LOINC Answer | 点时 | [LOINC 81660-3](https://loinc.org/81660-3) | Questionnaire 已定义；自由文本不能推断程度 |
| 过去 24 小时呕吐次数 | LOINC `94070-0` Emesis count 24 hour | `valueQuantity`；UCUM `/d` | `effectivePeriod` 24 小时 | [LOINC 94070-0](https://loinc.org/94070-0) | 已实现；必须有明确次数 |
| 过去 24 小时估计液体摄入 | LOINC `75301-2` Fluid intake 24 hour Estimated | `valueQuantity`；UCUM `mL/(24.h)` | `effectivePeriod` 24 小时 | [LOINC 75301-2](https://loinc.org/75301-2) | 已实现；必须有明确数值和单位 |
| 患者明确报告腹痛 | SNOMED CT `21522001` Abdominal pain (finding) | `valueBoolean=true` | `effectiveDateTime` | [SNOMED International 示例](https://docs.snomed.org/implementation-guides/allergy-implementation-guide/4-information-model-and-terminology-binding/4.3-examples) | 已实现；不自动推断病因或严重度 |
| 体重 | LOINC `29463-7` Body weight | `valueQuantity`；UCUM `kg`/`g`/`[lb_av]` | 测量时间 | [FHIR R4 Body Weight Profile](https://hl7.org/fhir/R4/bodyweight.html) | 术语已登记；尚未进入本轮患者问卷 |

### 4.2 恶心程度答案

LOINC `81660-3` 的官方页面给出示例答案列表。当前 Questionnaire 使用：

| 含义 | LOINC Answer code |
|---|---|
| Mild | `LA6752-5` |
| Moderate | `LA6751-7` |
| Severe | `LA6750-9` |

旧版自定义 `1/2/3` 和 `level_1_3` 已删除。系统不得把自由文本“有点恶心”自动换算成这些答案；需要患者选择或人工确认。

## 5. 为什么采集这些指标

| 指标 | 临床依据 | 能支持的结论 | 不能支持的结论 |
|---|---|---|---|
| 恶心、呕吐 | EMA 将恶心、呕吐列为 Wegovy 常见不良反应；FDA 说明书记录相关胃肠道不良反应 | 作为患者报告症状持续收集 | 不能仅凭出现一次确定治疗调整或风险等级 |
| 液体摄入 | FDA 说明书指出恶心、呕吐、腹泻相关脱水可伴急性肾损伤风险；患者咨询部分要求关注液体丢失和持续症状 | 记录 24 小时估计摄入量，为医护复核提供上下文 | 不存在由说明书直接给出的统一“低于多少毫升自动报警”阈值 |
| 腹痛 | FDA 警示持续或严重腹痛可能与急性胰腺炎有关；可伴或不伴恶心、呕吐 | 采集腹痛是否存在，并保留原文供人工判断 | 不能仅凭“腹痛=true”诊断胰腺炎或自动给出处置 |
| 症状是否持续或严重 | FDA 患者咨询信息要求严重或持续胃肠道症状联系医护；专家共识也建议持续症状及时告知医护 | 支持设计人工复核问题 | “持续”“严重”的操作化定义仍须目标医院审批 |

## 6. 权威临床资料

### 6.1 监管资料

1. **European Medicines Agency. Wegovy EPAR and Product Information.**
   https://www.ema.europa.eu/en/medicines/human/EPAR/wegovy
   用途：确认监管认可的常见不良反应和产品安全资料。EMA 页面列出的常见反应包括恶心、呕吐、腹泻、便秘和腹痛。

2. **U.S. Food and Drug Administration. WEGOVY Prescribing Information, 2026, Reference ID 5766092.**
   https://www.accessdata.fda.gov/drugsatfda_docs/label/2026/218316s005lbl.pdf
   重点章节：

   - 5.2 Acute Pancreatitis：持续或严重腹痛及相关症状；
   - 5.5 Acute Kidney Injury Due to Volume Depletion：胃肠道不良反应、脱水与肾损伤风险；
   - 5.6 Severe Gastrointestinal Adverse Reactions；
   - 6.1 Clinical Trials Experience：恶心、呕吐、腹泻、腹痛等不良反应；
   - Patient Counseling Information：持续症状、补液和联系医护的提示。

监管资料支持“采集”和“人工关注”，但没有支持当前项目旧版的 `vomiting_count >= 1 AND fluid_intake_reduced = true → L2/24h` 规则。

### 6.2 同行评议共识

Gorgojo-Martínez JJ, et al. **Clinical Recommendations to Manage Gastrointestinal Adverse Events in Patients Treated with GLP-1 Receptor Agonists: A Multidisciplinary Expert Consensus.** *Journal of Clinical Medicine.* 2022;12(1):145.

DOI: https://doi.org/10.3390/jcm12010145

PMID: 36614945

全文：https://pmc.ncbi.nlm.nih.gov/articles/9821052/

用途：支持胃肠道不良事件的常见性、补液重要性以及持续症状需要联系医护。该文属于专家共识，不是所有药物、地区和人群通用的强制报警标准。

## 7. 已删除或禁止启用的旧内容

| 旧内容 | 处理 | 原因 |
|---|---|---|
| `nausea_severity = 1/2/3`、`level_1_3` | 删除 | 自定义量表，无验证来源 |
| `fluid_intake_reduced` / `fluid_intake_normal` 布尔 Observation | 删除 | 含义、时间窗和量化标准不明确；保留原始回答但不沉淀为标准 Observation |
| `times` 单位 | 删除 | 不是已证明的 UCUM 表达 |
| `GLP1-002`：呕吐一次且喝水减少触发 L2/24h | 删除 | 无监管资料、指南或院内批准阈值支持 |
| `EMERGENCY-001` 固定关键词触发 L4 | 从本 Pathway 删除 | 未经过分诊模型验证，且并非 GLP-1 特异规则 |
| L0–L4 自动临床分级 | 暂停 | 属于内部工作流等级，不是 FHIR 或统一临床标准 |

## 8. 上线前仍需补齐的证据

- 目标药物及当地现行说明书，而不是仅依赖 Wegovy 美欧资料；
- 适用年龄、适应证、合并用药和糖尿病状态；
- 腹痛、持续呕吐、脱水等人工复核问题的明确操作定义；
- 任何报警条件、责任角色和 SLA 的院内批准文件；
- SNOMED CT 目标地区版本、许可证和术语服务器验证结果；
- 患者可读文案、语音识别和中文同义词表的临床审核；
- 真实世界试点中的漏报率、误报率、人工升级率和安全事件复盘。

在这些事项完成前，本路径必须保持 `draft`、`synthetic_only=true`、`not_reviewed`，且 `clinical_rules=[]`。
