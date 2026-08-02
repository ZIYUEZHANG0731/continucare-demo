# GLP-1 患者可报告症状术语目录 v1

机器可执行目录位于 `continucare/terminology/data/glp1_symptom_catalog_v1.json`。它是运行时唯一来源，同时可以渲染为 FHIR R4 `ValueSet`；SQLite 只保存确认记录和检索轨迹，不复制成另一份权威术语表。

## 覆盖边界

目录根据当前 DailyMed 的 Ozempic、Wegovy、Mounjaro 与 Zepbound 标签，覆盖常见胃肠道反应、注射部位反应，以及胰腺/胆囊、脱水或肾脏、低血糖、过敏、颈部和视觉相关的患者可报告信号。目录包含 40 余个 SNOMED CT 概念和中文口语别名。

这不是“所有可能症状”的医学全集。任何药物使用期间都可能出现目录外表达；因此系统必须保留原文、禁止强制编码，并进入术语人员或医生复核。目录也不表示症状一定由药物造成，更不产生诊断、风险等级或处置建议。

## 代码状态

- 原型核验版本：SNOMED CT International Edition `20250201`；
- 原型查询：FHIR `$expand/$lookup`；
- 当前审核状态：`draft-prototype-verified`；
- 上线门槛：目标医院确认地区版本、许可、术语服务器、ValueSet 和审批人后再次 `$validate-code`；
- LOINC Questionnaire 指标继续锁定为仓库声明的 `2.82`。

## 运行流程

```text
患者原话
  → MiMo/本地解析器只产生逐字症状检索词，不产生 code
  → RepositoryTerminologyBackend.search
      → 唯一匹配：生成带目录版本和 code 的候选卡
      → 多个匹配：患者使用语义区分按钮选定
      → 无匹配：保留原话，标记待人工复核
  → Safety Agent 校验 catalog/version/target coding/evidence
  → 患者确认
      → Pathway 已有项：CareSession answer
      → Pathway 外新症状：ConfirmedSymptomReport
  → 最终提交统一生成 FHIR Observation + derivedFrom + terminology trace
```

`TerminologySearchBackend` 是替换接口。比赛版本使用仓库 JSON；医院版本可以实现相同的 `search / validate_code` 合同，调用 FHIR `$expand`、`$lookup`、`$validate-code`，也可以在检索前加入 RAG 产生搜索词，但最终 code 仍必须来自术语服务而不是 LLM。
