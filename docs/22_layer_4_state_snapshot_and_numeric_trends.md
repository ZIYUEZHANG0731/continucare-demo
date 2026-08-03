# 第四层第 5 步验收：状态快照与原始数值趋势

- 版本：0.1.0（2026-08-02）
- 范围：版本化指标定义、current/stale/unknown/conflict 状态、单位一致的端点差值趋势、快照版本链和 Provenance
- 结论：**第 5 步工程基线通过；系统只陈述可验证的数据状态和原始数值方向，不输出“好转/恶化”、风险等级或临床处置。**

## 1. 本步目标

```text
final Observation + 当前 RevisionLink + 版本化指标定义
→ patient / as-of / clinical-time / lookback 门禁
→ current / stale / unknown / conflict
→ 单位与数值类型门禁
→ increasing / decreasing / unchanged 端点差值
→ ClinicalStateSnapshot 版本 + Observation 证据 + Provenance
```

新的 `ClinicalStateService` 与规则执行器、Summary 和旧演示趋势展示相互独立。本步没有把趋势接入风险规则、医生摘要、患者通知或正式病历。

## 2. 指标定义合同

每个 `StateMetricDefinition` 必须显式声明：

- metric ID 和版本；
- pathway code 和 pathway version；
- Observation code system、code 和显示名；
- 需要时声明精确 unit 和 unit system；
- 状态 lookback、freshness/stale 窗口；
- 趋势窗口和最小点数；
- 趋势算法版本，当前为 `endpoint-delta-v1`。

`stale_after_hours` 不得大于 `lookback_hours`。指标定义与服务的路径或版本不一致时整次构建失败，避免把其他路径的定义静默套用到当前患者。

当前仓库没有发布真实临床阈值或“某方向代表改善/恶化”的映射。测试中的指标定义只存在于临时测试数据。

## 3. Observation 进入门禁

状态与趋势只使用满足全部条件的 Observation：

1. 通过 FHIR R4 校验；
2. `status=final`；
3. `subject` 与目标患者完全一致；
4. `issued` 和 `effective[x]` 均不晚于快照 `as_of`；
5. 不是当前 `RevisionLink` 的 predecessor；
6. code system 和 code 与指标定义匹配；
7. 落入对应的状态或趋势窗口。

迟到数据按临床有效时间重新排序，而不是按传入数组顺序排序。晚于 `as_of` 才签发的数据不能回填到历史快照。被 corrected、amended、retracted 或 entered-in-error 修订关系替代的前序版本不会进入当前计算，但仍保留在不可变存储中。

## 4. 状态语义

| 状态 | 严格含义 |
|---|---|
| `current` | 最新可用值仍处于 freshness 窗口内 |
| `stale` | lookback 内存在 last known 值，但已经超过 freshness 窗口 |
| `unknown` | 没有可用值，或最新记录缺值/单位不符合定义；不推断患者状态 |
| `conflict` | 同一精确有效时间段存在不同值；系统不选择其中任何一个 |

`stale` 会保留 last known 的值、时间、年龄、Observation 版本和 EvidenceReference；它不等同于当前值。`unknown` 不携带伪造的 Observation 证据或默认值。`current` 也只表示数据新鲜度，不表示“正常”“稳定”或“安全”。

冲突检测使用相同的 `effective start/end`，避免把相邻但有重叠的滚动 24 小时报告误判为同一次测量。冲突结果至少保存两条 contradicting EvidenceReference。

## 5. 原始数值趋势

当前趋势算法只做确定性的端点差值：

```text
delta = 最后一个数值 - 第一个数值
delta > 0  → increasing
delta < 0  → decreasing
delta = 0  → unchanged
```

只有以下条件全部满足时，状态才是 `calculated`：

- 窗口内点数达到 `minimum_trend_points`；
- 每个点都是数值型；
- value kind、unit 和 unit system 一致；
- 若定义声明单位，每个点必须精确匹配；
- 没有同时间段冲突；
- 所有点都是当前有效版本。

其余结果显式返回：

- `insufficient_data`：点数不足、非数值或缺值；
- `unit_mismatch`：单位或数值类型不一致，且不自动换算；
- `conflict`：存在同时间段不同值。

每个计算结果保留 first/last/delta、点数、时间窗、算法版本及逐点 EvidenceReference。`increasing / decreasing / unchanged` 只描述原始数值方向，不等于改善、恶化、稳定、风险或临床显著性。

## 6. 快照版本与 Provenance

`ClinicalStateSnapshot` 保存：

- patient、pathway 和 `as_of`；
- 指标定义 ID/版本；
- 每个指标的一条 MetricState 和一条 NumericTrend；
- 实际参与投影的 Observation 版本引用；
- 快照算法版本；
- 生成 Provenance。

Snapshot ID 由 patient、pathway、`as_of` 和完整指标定义摘要稳定计算：

- 相同输入重复构建直接返回相同版本；
- 同一历史时点收到此前遗漏但符合门禁的迟到 Observation 时创建递增版本；
- 旧快照版本和旧 Provenance 不删除；
- 新的 `state_snapshot` 类型沿用第四层通用 immutable JSON/current 投影；
- 旧数据库自动扩展 record type CHECK，并在事务内保留已有行和查询索引。

Provenance target 指向具体快照版本，entity 指向实际 Observation 版本和指标定义版本。空数据快照仍以指标定义为来源，不声称观察到患者事实。

## 7. 当前安全边界

第 5 步明确没有实现：

- 好转、恶化、波动或临床显著性判断；
- 风险分级、Alert、诊断、治疗或用药建议；
- UCUM 单位换算、异常值修复或缺失值填补；
- 复杂回归、预测、基线校正或人群模型；
- Summary 自行计算趋势；
- 将状态快照自动发送给患者或写回医院病历；
- 用 LLM 选择数据点、解决冲突或解释趋势。

后续 Summary 若引用趋势，只能读取已持久化的 NumericTrend 及其证据，不能在生成文字时重新计算或赋予临床含义。

## 8. 验收结果

启用 HL7 官方 R4 Schema 校验时：

```text
146 passed
0 failed
0 skipped
```

第 5 步新增 10 个专项场景，覆盖：

- current、stale、unknown 及 last known 保留；
- 同一有效时间段冲突时不选值；
- 迟到数据按 clinical time 重排并计算端点差值；
- 单位不一致不换算、不静默跳过；
- corrected predecessor 不参与当前状态和趋势；
- future effective 和 future issued 数据被历史快照排除；
- 非数值状态可保存，但趋势明确为 insufficient data；
- 相同输入幂等，迟到来源产生新快照版本；
- 快照及逐点来源进入 Provenance；
- 旧 contract record type 表迁移后数据和索引保持完整。

同时继续通过：

- 7 类资源统一 HL7 R4 Schema 验证；
- 第三层离线语义回归 8/8；
- 无外部服务演示彩排 3/3；
- Python 编译检查和差异格式检查。

## 9. 后续进展

以下第 6 步内容已完成，验收结果见 [23_layer_4_doctor_workbench_read_model.md](23_layer_4_doctor_workbench_read_model.md)：

1. Summary 只能引用已持久化快照/趋势结果，禁止自行计算；
2. 医生界面同时展示原始点、单位、时间窗、版本和 EvidenceReference；
3. stale、unknown、conflict、unit mismatch 必须显式可见；
4. 任意 Snapshot/Summary/Task 可回溯到 FHIR/合同版本及 Provenance；
5. 未获临床批准的趋势解释和规则继续保持关闭；
6. 完成第四层整体回放、权限和故障降级验收。
