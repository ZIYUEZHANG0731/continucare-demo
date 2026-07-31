# 12. 第一版 Pathway 配置层

当前第一版将 Care Pathway 改为“治理清单 + FHIR R4 临床资源”。治理清单只管理版本、审批、信源和资源引用；Questionnaire、QuestionnaireResponse、Observation 与 PlanDefinition 使用 FHIR R4 4.0.1 原生结构。

内置 `GLP1-14D v1.0.0` 明确定义：

- FHIR 版本、Questionnaire 和 PlanDefinition canonical；
- LOINC、SNOMED CT 与 UCUM 术语边界；
- 权威标准、监管资料和同行评议共识；
- 合成数据、审批和版本状态；
- `clinical_rules=[]` 的 fail-closed 临床规则状态。

该配置仍是 `draft`、`synthetic_only` 和 `not_reviewed`，不能解释为经过临床审批的真实路径。

## 查看方式

```bash
.venv/bin/python -m continucare.pathways GLP1-14D
```

治理清单位于 `continucare/pathways/data/glp1_14d_v1.json`；FHIR 资源位于 `continucare/pathways/data/fhir/`；完整信源包位于 `docs/clinical/glp1_14d_observation_evidence.md`。

## 当前边界

本层只定义“标准上允许收集什么、问题与答案如何表示、证据在哪里以及能否启用规则”。下一层 Care Engine 必须直接消费 Questionnaire 并输出 QuestionnaireResponse；任何 Observation 都必须通过标准校验并可追溯到原始回答。
