# HANDOFF

> 给完全没有上下文的新会话使用。先完整阅读根目录 `AGENTS.md`，再阅读本文件。本文记录的是 2026-08-13 M5-A 收口时的真实状态；不要把它当成长期路线图。

## 1. Git 基线与已完成提交

工作目录：

```text
/Users/zhangziyue/Documents/Codex/continucare-demo
```

当前分支：

```text
codex/docs-collaboration-init
```

upstream 仍为：

```text
43012df9582a346524b26ba0cdd7a5318e510966
```

upstream 之后已有两个本地 commit：

1. `8151161d527f717ad47a78cf145a6722e4268ece`
   `docs: define collaboration workflow and handoff`
2. `cd9bf456b0793b66fe73cd72862c93316dcb6733`
   `feat: add pathway-agnostic knowledge evidence foundation`

两者都没有 push。

Knowledge Evidence Foundation 已经正式提交，不再是未跟踪成果。它仍保持 Pathway-agnostic；GLP1-14D 只是 fixture。没有 `target_number`、固定分母或人工目标序号；20/11/9/0/0 只是当前 GLP 数据快照。Knowledge 只解释采集/展示依据，不能授权 Task、ClinicalRule 或其他运行时行为。

本文件更新后，M5-A 将以单独 commit：

```text
feat: add confirmed manual review task flow
```

收口。未经用户后续明确授权，不得 push。

## 2. M5-A 目标与完成结论

M5-A 已完成以下最小闭环：

```text
一键合成随访表达
→ Layer 3 受控候选
→ 患者人工确认
→ 完成 QuestionnaireResponse / final Observation
→ 创建常规护士人工复核 Task
```

这不是诊断、风险等级、临床结论、自动分诊或治疗建议。

### 2.1 首页一键入口

- 首页增加“患者确认后创建护士人工复核任务”合成场景；
- 合成原话固定为 `我今天拉肚子。`，只用于 Demo；
- 一键入口显式注入 `UnconfiguredModelAdapter(SemanticModelConfig())`，不读取环境中可能配置的真实 provider，也不发网络请求；
- 一键阶段只创建 CareSession、AgentRun 和分析审计；
- 不创建 QuestionnaireResponse、Observation、Task、Provenance 或 Alert；
- Streamlit session state 会把该 AgentRun 接到患者页待确认卡片，不再绕过 Layer 3。

### 2.2 Layer 3 人工确认边界

- 候选仍使用现有严格 candidate/clarification/resolution 合同；
- Demo 候选是 patient-reported symptom 的受控术语匹配，不是诊断或临床判断；
- 专用 M5-A 路径必须一次处理该轮完整候选集；服务端和 UI 均有完整集检查；
- `rejected`、`unsure` 和 `cancelled` 只保留允许的决策/停止审计，不发布临床资源或 Task；
- `unsure` 之后只有新的明确接受动作才能进入发布事务；
- 会话完成后不能再取消或追加第二次发布。

### 2.3 纯准备与原子发布

新增纯准备边界：

- `CareAgentService.prepare_confirmed_candidates(...)`：验证并物化答案上下文/患者自述症状，不写库；
- `CareEngine.prepare_completion(...)`：构建并校验 completed QuestionnaireResponse 和 final Observations，不写库；
- `ConfirmedReviewService.accept_all(...)`：计算稳定 receipt、组装 Task/Provenance，并调用唯一原子落库方法。

`SQLiteStore.persist_confirmed_review_bundle(...)` 使用同一个 SQLite 连接和显式 `BEGIN IMMEDIATE`，在一个事务中写入：

- confirmed answer contexts / symptom reports；
- FollowUpMessage；
- completed QuestionnaireResponse；
- final Observations 与 observation evidence；
- CareSession `completed` 转换；
- conversation action resolutions；
- 患者确认、问卷完成、Task 创建审计；
- FHIR Task 与 Provenance。

任何校验失败或事务中故障都会整体回滚。故障注入测试覆盖了 Task/Provenance 已尝试插入后的回滚，证明不会留下部分副作用。

### 2.4 幂等、并发和证据链

- receipt 是患者、会话、精确 Pathway 版本、AgentRun 和完整候选内容的 canonical SHA-256；
- Task identifier 只保存 64 位 opaque digest，不暴露 raw run/candidate ID；
- QuestionnaireResponse、Task、Provenance 和患者自述 Observation ID 均由稳定 receipt/response/report 身份派生；
- 顺序重试返回同一资源；
- 两线程并发接受测试只产生一套 QR、Observation、Task、Provenance；
- Provenance target 包含 QuestionnaireResponse、Observation 和 Task；
- Provenance 明确区分 Patient author 与 deterministic assembler；
- Task 只引用 completed QR 和 final Observation，不读取 AgentRun/candidate 作为 Layer 4 临床输入。

### 2.5 护士队列与 M6 隔离

手工复核 Task 使用独立 identifier system：

```text
urn:continucare:patient-confirmed-review
```

任务固定：

- `priority=routine`；
- 通用描述“人工复核患者已确认报告”；
- 不含 severity、risk、threshold、diagnosis、ClinicalRule 或治疗建议；
- 原话保留在 FollowUpMessage / QuestionnaireResponse，护士页从最终证据读取，不把模型标签当临床原因。

护士页新增独立只读队列：

- 只正向选择 manual-review identifier；
- 不导入或暴露 `TaskWorkflowService`；
- 与旧 ClinicalRule/Alert 队列明确分开；
- 展示患者原话、最终 Observation、`routine` 和 `not_assessed`。

Doctor Workbench 的 Task 读取改为只正向选择：

```text
urn:continucare:clinical-rule
```

因此相同 Pathway 下的 M5-A 手工复核 Task 不会进入现有 M6 医生任务视图；已有 M6 接口未修改。

### 2.6 审计与可点击 Demo

审计页增加 M5-A 四步链：

```text
受控候选 → 患者确认 → 最终证据 → 护士任务
```

可点击验收路径已通过：

1. 首页点击“一键生成待患者确认的合成候选”；
2. 确认尚无 QR、Observation 或护士 Task；
3. 进入患者页，看到 SNOMED CT `62315008` 的未确认候选；
4. 点击“确认全部并创建护士人工复核任务”；
5. 患者页显示 completed QR/final Observation，临床评估仍为 `not_assessed`；
6. 护士页显示独立只读 `requested/routine` Task、患者原话和最终证据；
7. Alert 队列仍为 0、获批临床规则仍为 0；
8. 审计页四步链全部完成。

桌面和 390×844 移动端均已验收；干净浏览器会话无相关 console error/warn。

## 3. 冻结安全边界

M5-A 保持以下边界，后续不得悄悄放宽：

- 只使用合成患者；
- 候选不是诊断、风险等级或临床结论；
- 必须患者明确确认后才能创建护士人工复核 Task；
- rejected、cancelled、unsure 或校验失败不得创建 Task 或留下部分临床副作用；
- 不新增或启用临床阈值、Alert、L0–L4 或 ClinicalRule；
- GLP1-14D 继续保持 `clinical_rules=[]`；
- 运行时临床评估继续为 `not_assessed`；
- 不输出治疗、改药或个体化患者建议；
- 不接真实飞书/Aily、外部 API、真实患者或生产权限；
- 不做数据库迁移；
- 不修改 M6 对外接口；
- Knowledge Evidence 只能解释采集依据，不能授权任务或规则执行。

## 4. 验证结果

最终验证：

```text
.venv/bin/python -m pytest -q
283 passed, 3 skipped

.venv/bin/python -m compileall -q continucare app.py pages
通过

git diff --check
通过
```

三个 skip 都是既有条件测试缺少官方 `FHIR_R4_SCHEMA_ZIP`：

- `tests/test_fhir_conformance.py:114`
- `tests/test_layer4_rules_tasks.py:575`
- `tests/test_layer4_summaries.py:428`

不是 M5-A 回归失败。

新增测试覆盖：

- analyze-only 零临床发布；
- happy path 完整证据链；
- 顺序幂等；
- raw run/candidate ID 不进入 Task/Provenance；
- rejected/unsure/cancelled 零发布；
- unsure 后重新明确接受；
- Task 插入后故障的全事务回滚；
- 两线程并发仅一套资源；
- Layer 4 completed/final/patient/derivedFrom admission；
- manual Task 严格 FHIR、无 rule/severity；
- 环境中配置 provider 时仍不联网；
- Doctor Workbench 排除同 Pathway manual-review Task。

## 5. Claude 审查记录

按 `AGENTS.md` 将本切片判断为 Level 4，因为它涉及医疗工作流边界和跨表原子性。

### 5.1 Opus 策略审查

实施前完成一次 Claude Opus 策略/高风险审查。其 blocker 已在冻结方案中处理，包括：

- M6 Doctor Workbench 必须正向筛选 clinical-rule Task；
- 幂等 receipt 不得直接暴露 raw run/candidate ID；
- 原子写入必须使用同连接 `BEGIN IMMEDIATE`；
- Layer 4 使用共享 completed/final admission predicate；
- Provenance 必须包含 Task，并区分 Patient confirmer 与 software assembler；
- 必须一次处理完整候选集；
- Task 不把模型标签当临床原因；
- Demo 必须显式注入本地 unconfigured adapter；
- 完成后取消不得改变 Task。

没有遗留 Opus blocker。

### 5.2 Sonnet 最终审查

实现、测试和浏览器验收后完成一次聚焦 Sonnet final review。初次只因普通 `git diff` 不包含 3 个 untracked 新文件而返回 `NEED_CONTEXT`，不是代码 blocker。补充这 3 个完整文件后，同一审核阶段最终结论：

```text
CLEAN PASS
```

Sonnet 确认：

- 服务端双重强制完整候选集；
- manual/clinical-rule identifier 为精确正向分类；
- ManualReviewQueue 只读；
- receipt/Task ID 稳定且不泄露 raw ID；
- reject/unsure/cancel、故障回滚和并发测试覆盖冻结边界；
- 无剩余 blocker 或 NEED_CONTEXT。

采纳的非阻断小建议：护士页复用 `DEMO_PATIENT_ID` 常量；UI 对 `LookupError` 也做友好提示。没有因建议扩大功能范围。

## 6. 当前剩余限制

以下是明确限制，不是 M5-A blocker：

- 手工护士队列当前只读，不支持护士领取、完成或记录结果；
- Task 状态保持 `requested`，本切片不启用 Task transition；
- 没有把手工任务处理结果写入医生简报；
- 没有真实通知、飞书/Aily、外部 API 或生产身份/权限；
- 没有真实患者数据，也没有真实 provider 网络调用；
- 没有临床规则、风险等级、阈值、Alert 或治疗建议；
- 没有正式官方 FHIR R4 schema archive，因此 3 项条件 schema 测试仍 skip；
- demo synthetic security metadata 沿用仓库现状，没有只为新资源引入不一致的安全标签；
- `SQLiteStore._confirmed_review_fault` 是用于证明最终写入后回滚的显式测试 seam，生产默认 no-op；可在未来持久化重构时移除，但不影响当前正确性；
- Knowledge wheel resource inclusion 和外部来源联网核验仍是 Knowledge 后续事项，与 M5-A 无关。

## 7. 下一步 M5-B（尚未开始）

M5-B 只应在用户明确授权后开始。建议最小目标是：

```text
护士对 M5-A 人工复核 Task 进行受控处理
→ 记录复核结果和审计
→ 将处理结果作为可追溯事实提供给后续医生简报
```

开始前必须重新冻结范围和状态机。建议重点确认：

- 护士可执行的最小 Task transition（例如 received/in-progress/completed）；
- 每个 transition 的角色、必填 note、幂等和并发规则；
- completed/rejected/cancelled/failed 的零部分副作用；
- 护士结果如何引用 Task、QR、Observation 和 Provenance；
- 哪些结果允许进入医生简报，如何保持事实与临床结论分离；
- 是否需要新的应用合同但不修改既有 M6 对外接口；
- 是否继续使用同一原子事务边界；
- Demo 的回退方式和可点击验收步骤。

M5-B 仍不得自动引入：

- ClinicalRule、阈值、Alert、L0–L4 或风险分级；
- 诊断、治疗或改药建议；
- 真实飞书/Aily、外部 API、真实患者或生产权限；
- 数据库迁移，除非用户以后单独授权并完成新的高风险方案审查；
- Knowledge 对 Task/规则执行的授权。

## 8. 接管与回退

新会话接管时先运行：

```bash
git branch --show-current
git rev-parse HEAD
git rev-parse @{upstream}
git status --short --untracked-files=all
```

预期在 M5-A commit 成功后：

- 工作区干净；
- 暂存区为空；
- 分支仍为 `codex/docs-collaboration-init`；
- 本地分支领先 upstream 3 commits；
- 未 push。

回退不需要数据库迁移或外部系统操作：回退 M5-A commit 后重置本地 Demo 数据即可；两个较早的协作文档和 Knowledge Foundation commits 不受影响。未经用户授权不得 reset、clean、checkout、revert、push 或开始 M5-B。

## 9. 一句话接管结论

M5-A 已完成“合成表达 → Layer 3 受控候选 → 患者确认 → 原子生成最终证据与常规护士人工复核 Task”的可点击闭环；全量测试为 283 passed、3 skipped，Opus blocker 已纳入冻结方案，Sonnet 最终为 CLEAN PASS。当前只需保留已提交结果并等待用户决定是否启动 M5-B，绝不能自行 push 或扩展临床能力。
