# 第四层第 4 步验收：证据化 Summary 与医生审阅

- 版本：0.1.0（2026-08-02）
- 范围：当前 Timeline 的确定性简报、逐条证据绑定、Summary 版本链和医生接受/修改/拒绝
- 结论：**第 4 步工程基线通过；当前只启用不调用 LLM 的确定性摘要，未审阅内容不会成为患者指令或正式临床结论。**

> 后续进展：本步的确定性服务和医生审阅合同继续保留；第四层后来增加了默认关闭、只允许编排 fact ID 的受控 LLM 增强项，见 [24_layer_4_controlled_llm_summary.md](24_layer_4_controlled_llm_summary.md)。

## 1. 本步目标

```text
当前 Clinical Memory / Timeline
→ 生成时点与复诊时间窗过滤
→ 确定性 SummaryEvidenceItem
→ 结构与证据安全校验
→ safety_reviewed 草稿
→ 医生 accept / modify / reject
→ 新 Summary 版本 + DoctorReview + Provenance
```

新的 `EvidenceSummaryService` 与旧演示 `continucare.services.summaries.SummaryService` 相互独立。本步没有替换旧链路，也没有把新摘要自动发送给患者或写入病历。

## 2. 唯一输入边界

摘要只读取 `ClinicalMemoryService.list_timeline()` 的默认当前视图：

- 不读取聊天、AgentRun、候选或模型原始输出；
- 不读取 superseded 或 entered-in-error 事件；
- 默认不读取 AuditEvent；
- 只取与 `period_start / period_end` 相交的事件；
- `recorded_at` 晚于 `generated_at` 的事件不能进入历史时点摘要；
- 迟到但在生成前已记录的事件可以按其临床有效时间进入正确位置。

当前摘要不是完整的双时间数据库回放；它使用调用时的当前 Timeline，再以 `generated_at` 排除未来记录。医院版本若需严格历史快照，应增加独立的 as-of 查询索引。

## 3. 确定性摘要

每个当前 TimelineEvent 产生一个 SummaryEvidenceItem：

| Timeline 类型 | Summary section |
|---|---|
| QuestionnaireResponse、Communication | `overview` |
| Observation | `key_changes` |
| Task | `tasks_and_actions` |
| MissingData | `missing_data` |
| Conflict | `conflicts` |
| Review | `doctor_to_confirm` |

这里的 `key_changes` 只表示进入复诊简报的 Observation 事件，不声称已经计算趋势或临床显著变化。

条目文字由固定模板直接组合事件有效时间、标题和 Timeline 摘要；不调用 LLM，不生成风险等级、诊断、治疗或用药内容。Task 条目包含当前状态和最近一条处理记录，完整处理历史仍通过 Task 版本与 Provenance 查看。

每个条目必须至少包含一个 EvidenceReference。冲突条目标记 `requires_doctor_confirmation=true`；无事件时生成空 items，不编造“正常”“稳定”或“无异常”。

## 4. 摘要版本与 Provenance

Summary ID 由患者和摘要时间窗稳定计算：

- 相同 Timeline 来源集合重复生成时直接返回当前版本；
- 即使 `generated_at` 改变，也不会制造内容相同的新版本；
- 新 Timeline 事件出现时创建递增版本；
- 医生已审阅且 Timeline 未变化时，重复生成不会把状态退回草稿；
- 生成器版本变化时可以触发新的安全审阅版本。

每个生成版本保存：

- `source_timeline_event_ids`；
- 全部条目的 EvidenceReference；
- `generation_mode=deterministic`；
- `generator_version=deterministic-summary-v1`；
- 指向 Timeline URN 和原始资源版本的 Provenance。

确定性摘要不能伪装成模型输出；`model_name / prompt_version` 必须为空。未来 `llm_assisted` 模式必须同时声明模型和 Prompt 版本。

## 5. 医生审阅

`DoctorReviewService` 只允许审阅当前 `safety_reviewed` 版本，并要求审阅时间晚于摘要创建时间。

### 5.1 Accept

- 保留全部条目和证据；
- 创建 `doctor_reviewed` 新版本；
- 保存医生身份、时间、可选说明和 Provenance。

### 5.2 Modify

- 必须提供非空修改说明；
- 必须至少修改一个条目；
- 每个修改后条目仍必须带证据；
- 证据必须与源摘要中的 EvidenceReference 完全一致；
- 创建 `doctor_reviewed` 新版本，并记录 amended summary id/version。

医生可以调整措辞、删除或重组已有证据条目，但不能通过该接口加入源摘要不存在的证据引用。系统不会自动判断医生新措辞是否蕴含额外临床结论；这属于明确记录的人工责任，医院版本仍需权限控制和审阅规范。

### 5.3 Reject

- 必须填写拒绝说明；
- 创建 `rejected` 新版本；
- rejected 版本不能再次直接接受或修改；
- 原 safety-reviewed 版本和所有证据仍保留历史。

完全相同的审阅重试是幂等的。同一审阅身份下更换说明或修改内容会被拒绝；非当前 Summary 版本不能被新审阅覆盖。

## 6. Safety 状态含义

本步的 `safety_reviewed` 表示确定性结构门已经通过：

- 内容来自当前 Timeline 固定模板；
- 每条有证据；
- 没有 LLM 生成；
- 没有风险、诊断或处置推断；
- 时间窗和生成时点有效。

它不表示医生已经确认临床准确性，也不替代医院临床质量审核。只有 `doctor_reviewed` 才表示该具体版本经过记录在案的医生操作。

## 7. 验收结果

启用 HL7 官方 R4 Schema 校验时：

```text
136 passed
0 failed
0 skipped
```

第 4 步新增 9 个专项场景，覆盖：

- Observation、QuestionnaireResponse、Task、Conflict 和 MissingData 分区；
- 所有条目强制证据绑定，冲突强制医生确认；
- Task 当前状态和最近处理记录进入摘要；
- 空 Timeline 不编造事实；
- 生成时点之后记录的事件被排除；
- 重复生成幂等，新 Timeline 产生新版本；
- 医生接受、修改、拒绝和完全相同重试；
- 无来源证据、无实际修改、旧版本审阅和无说明拒绝被阻断；
- 生成和医生审阅 Provenance 通过 HL7 官方 R4 Schema。

同时继续通过：

- 7 类资源统一 HL7 R4 Schema 验证；
- 第三层离线语义回归 8/8；
- 无外部服务演示彩排 3/3；
- Python 编译检查和差异格式检查。

## 8. 后续进展

以下第 5 步内容已完成，验收结果见 [22_layer_4_state_snapshot_and_numeric_trends.md](22_layer_4_state_snapshot_and_numeric_trends.md)：

1. 明确每个指标的 current state、last known、unknown 和 stale；
2. 只对单位一致、时间窗明确、版本有效的序列计算趋势；
3. 将原始数值变化与临床意义判断分开；
4. 趋势必须保留 Observation 证据和算法版本；
5. 冲突、缺失或 entered-in-error 数据不能被静默纳入趋势；
6. Summary 只能引用趋势结果，不能自行计算或解释临床意义。
