# 第四层第 1 步验收：契约与存储基线

- 版本：0.1.0（2026-08-02）
- 范围：Clinical Memory 与工作流的合同、FHIR R4 资源边界和 SQLite 持久化
- 结论：**第 1 步工程基线通过；规则执行、Timeline 构建和任务状态机尚未启用。**

## 1. 本步目标

本步只建立第四层后续服务共同依赖的稳定边界：

```text
Layers 1–3 最终资源
→ Layer4InputReader 状态门禁
→ 版本化业务合同
→ FHIR Task / Communication / Provenance Builder
→ 完整 JSON 持久化 + 检索投影
```

当前 `clinical_rules=[]` 保持不变，Risk Engine 继续返回 `not_assessed`，不会因为本步新增了 Task Builder 就自动创建临床任务。

## 2. Observation 状态门禁

第四层输入端口只接受：

- `status=completed` 的 QuestionnaireResponse；
- `status=final` 的 Observation；
- 已持久化 AuditEvent。

Observation 即使由 completed CareSession 关联返回，只要自身状态不是 `final`，仍会被输入端口 fail-closed 拒绝。后续引入 `amended / corrected / entered-in-error` 时必须发布新的状态策略，不能静默扩大白名单。

## 3. 业务合同

`continucare/layer4/contracts.py` 建立了以下 `extra=forbid` 合同：

| 合同 | 用途 |
|---|---|
| `EvidenceReference` | 资源、版本、证据角色、有效时间和原文证据 |
| `MemoryEvent` | 从最终资源归一化得到的可去重记忆事件 |
| `TimelineEvent` | 面向时间线读取的确定性投影 |
| `RevisionLink` | 修订、纠正、撤回和 entered-in-error 的前后版本关系 |
| `ClinicalRuleDefinition` | 规则适用范围、证据、输入、条件、Task 动作、测试、审批和回退 |
| `Layer4SummaryDraft` | 逐条证据绑定的复诊简报草稿 |
| `DoctorReview` | 医生接受、修改或拒绝的结构化结果 |

规则处于 `approved` 或 `active` 时，必须同时具有临床与术语批准、审批人和时间。条件只能引用同一规则已登记的 Observation 输入。该合同只证明治理字段齐全，不代替临床人员审批。

## 4. FHIR R4 工作流资源

`continucare/layer4/fhir.py` 新增：

- `Communication`：患者或医护沟通，保留 sender、recipient、subject、payload 和时间；
- `Task`：包含患者、责任人、规则 ID/版本、任务编码、触发 Observation、优先级和截止时间；
- `Provenance`：记录目标资源、产生或审核者、活动和来源资源；
- `validate_layer4_fhir_resource`：只允许上述三类资源进入第四层存储边界。

三类资源已加入统一 `validate_r4_resource` 白名单，同时通过 `fhirclient` 严格模型和 HL7 官方 R4 JSON Schema。

## 5. 持久化策略

`Layer4SQLiteStore` 使用两个新表：

### 5.1 `layer4_fhir_resources`

- 保存完整、规范化 FHIR JSON；
- 主键为 `resource_type + resource_id + version_id`；
- 同一版本重复写入完全相同 JSON 时幂等；
- 同一版本写入不同 JSON 时拒绝；
- 新版本不会删除历史版本，只更新 `is_current` 投影；
- patient、status 和 clinical_time 仅用于查询，不能替代资源正文。

### 5.2 `layer4_contract_records`

- 保存规则、MemoryEvent、TimelineEvent、RevisionLink、SummaryDraft 和 DoctorReview 的完整合同 JSON；
- 保留 record type、version、patient、pathway、status 和 effective time 投影；
- 同一合同版本不可覆盖；
- 读取时重新执行 Pydantic 严格校验。

## 6. 当前验收结果

```text
100 passed
0 failed
0 skipped
```

HL7 官方 R4 Schema 独立验证资源：

- Questionnaire；
- PlanDefinition；
- QuestionnaireResponse；
- Observation；
- Communication；
- Task；
- Provenance。

新增测试覆盖：

- preliminary Observation 被第四层拒绝；
- 未双审批规则不能进入 active；
- 规则条件不能引用未知输入；
- Memory、Timeline、Revision、Summary 和 DoctorReview 合同约束；
- FHIR 工作流资源患者引用一致性；
- 同版本幂等与篡改拒绝；
- FHIR 历史版本保留和 current 投影；
- SQLite 重开后的完整 JSON 恢复。

## 7. 后续进展

以下第 2 步内容已完成，验收结果见 [19_layer_4_clinical_memory.md](19_layer_4_clinical_memory.md)：

1. 从 `Layer4InputReader` 读取最终 QR、Observation 和 AuditEvent；
2. 确定性生成 EvidenceReference 与 MemoryEvent；
3. 按临床有效时间生成 TimelineEvent；
4. 实现幂等去重、迟到数据重排、冲突和缺失表达；
5. 创建 Provenance，并用 RevisionLink 处理更正和撤回；
6. 保持规则执行关闭，直到规则包获得所需审批。
