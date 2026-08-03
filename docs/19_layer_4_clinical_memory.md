# 第四层第 2 步验收：Clinical Memory 与 Timeline

- 版本：0.1.0（2026-08-02）
- 范围：最终资源的确定性摄取、证据绑定、临床时间线、冲突/缺失表达和修订链
- 结论：**第 2 步工程基线通过；临床规则执行、风险分级和自动 Task 仍保持关闭。**

## 1. 本步目标

本步把第 1 步的合同与存储真正接入第三层冻结输出，但不引入新的临床判断：

```text
Layer4InputReader 最终资源 + 第四层 Communication / Task
→ 确定性归一化
→ EvidenceReference + MemoryEvent
→ 按临床有效时间投影 TimelineEvent
→ Provenance + RevisionLink
```

整个构建过程不调用 LLM。相同输入、路径版本和事件版本产生相同 ID 与 JSON；重复执行是幂等的。

## 2. 摄取边界

`ClinicalMemoryService` 当前读取：

- `status=completed` 的 `QuestionnaireResponse`；
- `status=final` 的 `Observation`；
- 第三层冻结边界提供的 `AuditEvent`；
- 已进入第四层存储的当前 `Communication` 和 `Task`。

它不读取聊天轮次、候选、AgentRun、模型原始输出或正则中间状态。`Provenance` 不会再次作为输入摄取，避免形成自引用循环。审计事件可进入完整记忆，但默认不展示在面向临床用户的 Timeline 中。

## 3. 事件、证据和幂等

每个资源版本会产生：

- 一个 `MemoryEvent`：保存患者、路径、来源版本、有效时间、记录时间、去重键和证据；
- 一个 `TimelineEvent`：保存面向读取的标题、摘要、状态和同一组证据；
- 一个 `Provenance`：把两个派生事件绑定回输入资源版本和固定的 Memory Agent 身份。

ID 由患者、路径代码/版本、事件类型、来源资源版本和必要的冲突/缺失身份稳定计算。完整合同仍采用第 1 步的不可变版本存储；同版本不同 JSON 会被拒绝。

## 4. 临床时间与迟到数据

Timeline 使用资源的临床有效时间排序，而不是数据库写入顺序：

- `Observation` 使用 `effectiveDateTime` 或 `effectivePeriod`；
- `QuestionnaireResponse` 使用 `authored`；
- `Communication` 使用 `sent / received`；
- `Task` 优先使用 `executionPeriod`，否则使用 `authoredOn`；
- `AuditEvent` 使用其记录时间。

迟到写入的旧事件会自动回到正确的历史位置，不会被误当作最新临床状态。有效时间相同时再以记录时间和稳定事件 ID 排序，保证重放结果稳定。

## 5. 冲突与缺失数据

### 5.1 冲突

同一编码在重叠临床时间窗中出现不同值时，系统：

- 保留每条原始 Observation；
- 另建一个 `state=conflict` 的 TimelineEvent；
- 把全部不一致 Observation 标为 `CONTRADICTING` 证据；
- 明确说明系统没有选择任何一个值，等待人工确认。

如果相关原始事实随后被正式修订或撤回，派生冲突会从当前视图退出，但继续保留在历史中。

### 5.2 缺失

缺失只能由显式 `MissingDataExpectation` 触发，不能从“没有记录”推断患者正常或异常。每个缺失项使用独立 `expectation_id`，即使共享同一 PlanDefinition 来源也不会互相覆盖。

期望时间窗尚未结束时不会提前标记缺失；只有 `period_end` 不晚于本次输入快照的 `assembled_at`，系统才会评估该缺失项。

当匹配的最终 Observation 后续到达时，旧缺失事件会通过 `RevisionLink(SUPERSEDES)` 转为历史状态；当前 Timeline 不再展示，原事件和 Provenance 仍可审计。

## 6. 修订和当前视图

`record_revision` 支持 `corrects`、`amends`、`supersedes`、`retracts` 和 `entered-in-error` 关系，并为每条关系创建 FHIR `Provenance`：

- predecessor 保留具体版本或派生事件 URN；
- successor 保留新的具体资源版本；
- 关系、原因、执行者和时间进入不可变 `RevisionLink`；
- 默认查询隐藏 `superseded / entered-in-error`，`include_history=True` 可恢复完整历史。

该设计没有物理删除旧事实，也没有用新值覆盖旧版本。

## 7. 安全边界

本步只整理已确认事实和工作流记录：

- 不诊断、不推荐治疗或用药；
- 不从数据缺失推断临床状态；
- 不自动解决冲突；
- 不计算风险等级；
- 不自动创建 Alert 或 Task；
- `clinical_rules=[]` 和 Risk Engine `not_assessed` 行为保持不变。

虽然 `Task` 可以作为已批准外部流程的输入被摄取，但本步不会自行生成 Task。

## 8. 验收结果

在 HL7 官方 R4 Schema 归档校验开启时：

```text
112 passed
0 failed
0 skipped
```

其中 12 个 Clinical Memory 专项场景覆盖：

- 重建幂等、证据绑定和默认隐藏审计事件；
- 迟到数据按临床有效时间重排；
- 不一致值形成独立冲突且不自动选值；
- 显式缺失、开放时间窗不提前报缺失、已有数据满足期望、后到数据关闭缺失；
- 共享路径来源的多个缺失项保持独立；
- 修订幂等、不可变、Provenance 和历史保留；
- 修订后的原事实及派生冲突退出当前视图；
- 真实第三层边界可构建 QR、Observation 和 Communication 记忆；
- 在没有获批规则时不会创建 Task。

同时通过：

- 7 类资源的 HL7 官方 R4 JSON Schema 验证；
- 第三层离线语义回归 8/8；
- 无外部服务演示彩排 3/3；
- Python 编译检查和差异格式检查。

## 9. 后续进展

以下第 3 步内容已完成，验收结果见 [20_layer_4_approved_rules_and_tasks.md](20_layer_4_approved_rules_and_tasks.md)：

1. 只装载同时具备临床和术语批准的版本化规则；
2. 对 Observation 状态、单位、时间窗和适用人群 fail-closed；
3. 产生逐条件证据解释和 `not_assessed` 原因；
4. 仅由获批规则创建版本化 FHIR Task；
5. 实现 Task 的分派、接受、升级、完成和取消状态机；
6. 保留规则版本、触发证据、人工动作和回滚链。

在真实临床规则包、审批人和测试集进入仓库前，不应打开这一执行路径。
