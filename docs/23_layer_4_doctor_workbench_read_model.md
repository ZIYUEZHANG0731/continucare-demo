# 第四层第 6 步验收：Doctor Workbench 只读组合查询与整体回放

- 版本：0.1.0（2026-08-02）
- 范围：Timeline/State/Summary/Task 组合读取、角色与患者/路径隔离、历史 as-of 回放、证据图和组件级故障降级
- 结论：**第 6 步工程基线通过；第四层现在具备独立、只读、可降级和可追溯的医生工作台读取边界，但尚未替换旧演示页面，也未接入真实身份系统或 EMR 写回。**

> 后续进展：第四层已增加默认关闭的受控 LLM Summary 增强项，见 [24_layer_4_controlled_llm_summary.md](24_layer_4_controlled_llm_summary.md)。本报告的 159 项结果仍是第 6 步当时的验收快照。

## 1. 本步目标

```text
已验证的访问断言 + patient/pathway + as_of
→ Timeline 历史投影
→ 当时可见的 State Snapshot / Summary / Task 版本
→ 组件状态 available / empty / degraded
→ 版本化 evidence roots
→ FHIR / 合同 / 指标定义 / Provenance 证据图
```

`DoctorWorkbenchService` 只组合已经持久化的第四层结果。查询不会重建 Clinical Memory、生成 Snapshot/Summary、执行规则、转换 Task、创建新版本或写入审计/病历。

## 2. 只读组合视图

`DoctorWorkbenchView` 同时返回：

- 当前查询时点可见的 Timeline；
- 与 patient、pathway code/version 完全一致的 ClinicalStateSnapshot；
- 与 patient、pathway code/version 完全一致的 Layer4SummaryDraft；
- 明确带有对应 pathway URN 的 FHIR Task；
- 每个组件的读取状态；
- 可供证据查看器继续展开的版本化 evidence roots。

为阻止同一患者的多个 Care Pathway 串读，新生成的 Summary 现在显式保存 `pathway_code / pathway_version`。兼容读取旧 Summary 合同，但缺少路径的旧版本不能进入新 Workbench 组合视图，必须由确定性 Summary 服务产生带路径的新版本。

Task 必须在 `basedOn` 中包含：

```text
urn:continucare:pathway:{pathway_code}|{pathway_version}
```

没有路径标签或标签不一致的 Task 不会被猜测归类。

## 3. 权限边界

访问合同 `WorkbenchAccessContext` 要求：

- actor FHIR reference；
- role 与 purpose-of-use 一致；
- 明确的 permitted patient ID 集合；
- `identity_verified=true` 的上游身份断言。

当前 Doctor Workbench 只允许：

| 角色 | purpose | 结果 |
|---|---|---|
| `doctor` | `treatment` | 允许只读访问授权患者 |
| `clinical_auditor` | `audit` | 允许只读回放授权患者 |
| `nurse` | `operations` | 拒绝进入 Doctor Workbench；护士使用独立任务视图 |

未验证身份、角色/purpose 混淆、患者不在许可集合时在任何数据查询前 fail-closed。证据解析始终以本次 `patient_id` 查询存储；即使同一调用者同时被授权访问两名患者，也不能在患者 A 的 trace 中解析患者 B 的 Task。

`identity_verified` 当前是受信适配器传入的工程合同，不是真实 SSO、SMART on FHIR 或医院 IAM。本步没有宣称完成生产身份认证、Break Glass、同意管理、行级策略或访问日志写入；这些仍属于第六层医院集成。

## 4. 历史 as-of 回放

Workbench 不使用今天的 current 结果覆盖过去：

### 4.1 Timeline

- 只读取 `recorded_at <= as_of` 的事件；
- 只应用 `created_at <= as_of` 的 RevisionLink；
- 因此 Task v2 尚未产生的历史时点仍显示 Task v1；
- 修订发生后，predecessor 才退出默认历史视图；
- Audit、superseded 和 entered-in-error 不进入医生默认视图。

### 4.2 State Snapshot

- `snapshot.as_of <= query as_of`；
- `snapshot.created_at <= query as_of`；
- 同一临床时点出现迟到数据新版本时，早期查询仍返回旧版本，之后查询返回新版本；
- 完整 StateMetricDefinition 现在嵌入 Snapshot，保证算法参数可回放。

### 4.3 Summary

- `period_end` 和 `created_at` 均不得晚于查询时点；
- 医生审阅前返回 safety-reviewed 版本；
- 审阅完成后才返回 doctor-reviewed/rejected 新版本；
- patient 和 pathway 必须完全一致。

### 4.4 Task

- 按 `meta.lastUpdated <= as_of` 选择每个 Task 当时最新的版本；
- entered-in-error 的当时 current 版本不进入默认任务列表；
- Task 必须带目标 pathway URN。

当前回放仍受第四层输入端口限制：上游只暴露 completed QuestionnaireResponse 和 final Observation。若历史源版本已不在输入适配器中，证据图会诚实标记 unresolved，而不会补造内容。医院版本应由 FHIR history API 或等效不可变事件存储提供完整双时间源查询。

## 5. 证据图

`trace_evidence()` 从一个版本化 root 开始，受最大深度和节点数限制地展开：

- FHIR Observation、QuestionnaireResponse、Communication、Task、Provenance；
- MemoryEvent、TimelineEvent、Summary、DoctorReview、State Snapshot、Clinical Rule；
- Snapshot 内嵌的完整 StateMetricDefinition；
- Summary → Timeline → Memory → 原始 FHIR；
- Task → trigger/input Observation，并反向查找以该 Task 版本为 target 的 Provenance；
- Snapshot/Summary → 其生成 Provenance 和全部来源。

每个 resolved artifact 返回存储中的完整 JSON/合同 payload，而不是查询投影。无法解析的引用进入 `unresolved_references`。来源服务异常时返回 `degraded=true + evidence_source_unavailable`；缺少单个资源但服务正常时只是 unresolved，不伪装成系统故障。

达到 `max_depth` 或 `max_nodes` 时显式设置 `truncated=true`。循环引用通过 visited set 截断，不会无限展开。

## 6. 组件级故障降级

Timeline、State、Summary 和 Tasks 独立读取，每个组件状态为：

| 状态 | 含义 |
|---|---|
| `available` | 查询成功且存在数据 |
| `empty` | 查询成功但该时点没有对应数据 |
| `degraded` | 来源读取失败 |

一个组件失败时：

- 其他组件继续返回；
- 失败组件返回空值并带稳定 reason code；
- 不回显底层异常、路径或敏感数据；
- 不用 Timeline 推测 State，不用 Task 拼接 Summary；
- 整体视图标记 `degraded=true`。

权限失败不属于可降级场景，必须直接拒绝访问。

## 7. 当前安全边界

第 6 步没有实现：

- 将新服务接入当前 Streamlit 医生页面；
- 自动生成或刷新 Summary/State；
- 医生端写操作、Task 转换或 EMR 写回；
- 真实登录、RBAC/ABAC、Break Glass、患者同意和访问审计；
- 医院 FHIR history、目标 IG/Profile 或跨机构患者主索引；
- 将 increasing/decreasing 解释为好转、恶化或风险；
- 启用真实临床规则；受控 Summary LLM 已完成固定合成数据的真实 MiMo 验收，但仍未接入旧页面，也未获真实患者或医院上线批准。

旧 `pages/3_doctor_summary.py` 仍是比赛演示兼容页面，不应被描述为已经使用本步读取边界。

## 8. 验收结果

启用 HL7 官方 R4 Schema 校验时：

```text
159 passed
0 failed
0 skipped
```

第 6 步新增 13 个专项场景，覆盖：

- Timeline/State/Summary/Task 完整组合视图且查询零写入；
- Task 转换前后按历史时点分别选择 v1/v2；
- RevisionLink 只在产生后影响历史视图；
- 迟到 Observation 产生的 Snapshot v2 不覆盖此前查询；
- Summary 医生审阅版本按创建时间进入视图；
- doctor/auditor 允许，未验证身份、nurse 和未授权患者拒绝；
- role 与 purpose 混淆在合同层阻断；
- patient 与 pathway 的 Summary/Task 严格隔离；
- Timeline/State/Summary/Task 组件独立降级且不补造数据；
- Snapshot、Summary 和 Task 均可展开到 Observation 与 Provenance；
- 指标定义 payload 可从 Snapshot 证据图还原；
- 缺失来源与来源服务故障被明确区分；
- 深度上限显式标记 truncated。

同时继续通过：

- 7 类资源统一 HL7 R4 Schema 验证；
- 第三层离线语义回归 8/8；
- 无外部服务演示彩排 3/3；
- Python 编译检查和差异格式检查。

## 9. 下一步

第四层六个实施步骤已经形成完整工程基线。进入第五层前应进行一次第四层封板审计：

1. 汇总第 1–6 步合同、服务、数据迁移和负向安全测试；
2. 固定 Layer 4 release manifest、官方 FHIR Schema hash 和回滚说明；
3. 确认仓库仍无真实 active 临床规则和 EMR 写回，并确认受控 Summary LLM 保持默认关闭或具有正式发布证据；
4. 对默认数据库执行旧版本迁移与只读回放烟测；
5. 经确认后创建第四层冻结标签，再进入第五层角色应用接入。
