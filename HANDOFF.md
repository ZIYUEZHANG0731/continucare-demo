# HANDOFF

> 给完全没有上下文的新会话使用。先完整阅读根目录 `AGENTS.md`，再阅读本文件。本文记录的是 2026-08-13 M5-C 已完成实现、尚未提交时的真实状态；不要把它当成长期路线图。

## 1. Git 基线与已完成提交

工作目录：

```text
/Users/zhangziyue/Documents/Codex/continucare-demo
```

当前分支：

```text
codex/docs-collaboration-init
```

upstream 为：

```text
20d2521bf7bacaebe7d980c4013819d37de7fffb
```

`43012df` 之后的四个 M5 基础提交都已位于 upstream：

1. `8151161d527f717ad47a78cf145a6722e4268ece`
   `docs: define collaboration workflow and handoff`
2. `cd9bf456b0793b66fe73cd72862c93316dcb6733`
   `feat: add pathway-agnostic knowledge evidence foundation`

3. `3fa2e2a812dbf29d228cef95badb64bc894c8b3e`
   `feat: add confirmed manual review task flow`
4. `20d2521bf7bacaebe7d980c4013819d37de7fffb`
   `feat: add controlled nurse review workflow`

Knowledge Evidence Foundation 已经正式提交，不再是未跟踪成果。它仍保持 Pathway-agnostic；GLP1-14D 只是 fixture。没有 `target_number`、固定分母或人工目标序号；20/11/9/0/0 只是当前 GLP 数据快照。Knowledge 只解释采集/展示依据，不能授权 Task、ClinicalRule 或其他运行时行为。

M5-A 已以单独 commit：

```text
feat: add confirmed manual review task flow
```

收口。

M5-B 已以单独 commit 收口：

```text
20d2521bf7bacaebe7d980c4013819d37de7fffb
feat: add controlled nurse review workflow
```

当前 HEAD 为：

```text
20d2521bf7bacaebe7d980c4013819d37de7fffb
feat: add controlled nurse review workflow
```

HEAD 与 `origin/codex/docs-collaboration-init` 相同。M5-C 已完成但未暂存、未提交；本轮不得 commit 或 push。

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

M5-A 当时为护士页新增独立只读队列；M5-B 已在相同正向分类边界上加入受控处理：

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
6. M5-A 当时的护士页显示独立只读 `requested/routine` Task、患者原话和最终证据；M5-B 已在此队列上加入受控领取、处理与完成；
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
- M5-A 审查时的 ManualReviewQueue 只读边界正确；M5-B 已通过独立受控服务扩展处理能力；
- receipt/Task ID 稳定且不泄露 raw ID；
- reject/unsure/cancel、故障回滚和并发测试覆盖冻结边界；
- 无剩余 blocker 或 NEED_CONTEXT。

采纳的非阻断小建议：护士页复用 `DEMO_PATIENT_ID` 常量；UI 对 `LookupError` 也做友好提示。没有因建议扩大功能范围。

## 6. 当前剩余限制

M5-B 已解除 M5-A “护士队列只读、Task 固定 requested”的历史限制。当前明确限制是：

- 3 项官方 FHIR R4 schema 条件测试仍因缺少 `FHIR_R4_SCHEMA_ZIP` 而 skip；
- 合成护士身份没有真实鉴权或职责分离；
- Communication 批准暂不可撤销；
- 当前完全没有发送能力；
- 仍无真实患者、真实外部系统、临床规则、风险等级、Alert、诊断或治疗建议。

## 7. M5-B（已完成，准备提交）

M5-B 已完成以下受控闭环：

```text
requested Task
→ 护士确认收到（received）
→ 护士一次明确“接受并开始”（accepted → in-progress，同一原子动作）
→ 记录受控处理结果（completed）
→ 原子生成 Communication preparation / pending-approval
→ 护士另一次明确批准
→ Communication preparation / ready-to-send
```

`ready-to-send` 只由 ContinuCare 自定义 readiness extension 表达；Communication 所有版本均没有 `sent` 或 `received`，模块级 `SEND_ENABLED=False`，本切片不存在发送适配器、发送方法或发送按钮。

### 7.1 冻结安全与证据边界

- 专用服务只正向接收 `urn:continucare:patient-confirmed-review` Task；
- 每个动作重新校验 patient、completed QuestionnaireResponse、final Observation 与 `derivedFrom`；
- 规范化患者原话、QR 与 Observation 集合计算 SHA-256 evidence digest，写入 Task output、Provenance extension 与 audit；
- 证据完整性失败时处理动作 fail-closed、零写入，只允许用受控 `evidence-integrity-failure` 原因取消 Task；
- 两种受控处理结果不会改变患者可见模板，护士自由 note 不进入 Communication；
- 患者可见草稿是固定中性模板，不包含诊断、风险、阈值、治疗或用药建议；
- 拒绝/取消精确只写 Task 新版本、一个 Provenance 和一个 audit event，不创建 Communication/readiness；
- completed、rejected、cancelled 为本切片终态，不提供反向处理；批准当前不可撤销，但本切片没有发送能力。

### 7.2 原子性、幂等与审计

`Layer4SQLiteStore.persist_manual_review_action(...)` 使用 `BEGIN IMMEDIATE` 和 current-resource compare-and-swap，在一个事务中写入本次动作的完整 Task/Communication/Provenance 版本集合与单条 audit event。稳定 action digest 包含 Task、动作、from-version、合成 actor 与 canonical payload hash，不包含墙钟时间；顺序重试和并发重试返回原资源，不同 payload 或陈旧版本拒绝且不留下部分写入。

护士页可查看：

- 患者原话；
- completed QuestionnaireResponse；
- final Observation 与 `derivedFrom`；
- Task 全版本历史；
- 受控处理结果和护士 note；
- Communication 草稿与 pending/ready readiness。

审计页新增并验证：确认收到、接受并开始、结果与草稿、人工批准。M6 Doctor Workbench 仍只正向选择 `urn:continucare:clinical-rule` Task；回归测试覆盖 completed manual Task 也不会进入 M6。

### 7.3 M5-B 验证

```text
.venv/bin/python -m pytest -q
290 passed, 3 skipped

.venv/bin/python -m compileall -q continucare app.py pages
通过

git diff --check
通过
```

3 个 skip 仍只因缺少官方 `FHIR_R4_SCHEMA_ZIP`，与 M5-B 无关。

浏览器完整点击路径已通过，桌面与 390×844 移动视口均无相关 console error/warn；待批草稿明确显示“尚不可发送”，批准后显示“可发送（本切片未实际发送）”，Alert 与获批 ClinicalRule 均为 0。

### 7.4 M5-B Claude 审查

按 `AGENTS.md` 将 M5-B 判断为 Level 4。实施前完成一次 Opus 策略/高风险审查，冻结方案已吸收其 blocker：原子写路径、患者可见模板对称、单一发送资格合取谓词、批准并发终态守卫、证据失败 fail-closed 与 evidence digest。

实现、测试和浏览器验收后完成一次 Sonnet final review，结论：

```text
CLEAN PASS
BLOCKER: 无
NEED_CONTEXT: 无
```

采纳其一项非阻断纵深防御：原子 bundle 在存储边界显式要求恰好一个 Provenance。其余两项为已冻结的已知设计边界：QR/Observation 依赖现有不可变写入合同；批准 note 只进入内部 `Communication.note`，绝不进入患者 payload。

## 8. 接管与回退

新会话接管时先运行：

```bash
git branch --show-current
git rev-parse HEAD
git rev-parse @{upstream}
git status --short --untracked-files=all
```

当前 M5-C 交接状态：

- 工作区包含 M5-C 未提交改动；
- 暂存区为空；
- 分支仍为 `codex/docs-collaboration-init`；
- HEAD 与 upstream 相同；
- 未 push。

M5-C 没有数据库迁移或外部系统操作。若用户决定放弃本切片，只能在用户明确授权后回退未提交代码并重置本地 Demo 数据；未经授权不得 reset、clean、checkout、revert、commit 或 push。

## 9. 一句话接管结论

M5-B 已提交并位于 upstream。M5-C 已完成“逐字原话 + 最终 Observation + 受控护士结果 + Communication readiness → 确定性、版本化、可追溯医生简报”的可点击闭环，仍保持 synthetic-only、clinical_rules=[]、not_assessed、无外发；M5-C 当前未暂存、未提交，且不得开始 M5-K/D/E。

## 10. 后续顺序

1. M5-C：本轮已完成，尚未提交；
2. M5-K：比赛最小版 Knowledge Expansion，尚未开始；
3. M5-D：把 M5-A/B/C/K 串成稳定的一键比赛 Demo，尚未开始；
4. M5-E：接入 M6 飞书/Aily，并保留无 Token 的 Mock fallback，尚未开始。

## 11. M5-C（已完成，未提交）

M5-C 新增 `ManualReviewBriefService`，只从 completed manual-review Task、completed QuestionnaireResponse、final Observation、Communication preparation、精确 Provenance 与必要 AuditEvent 形成固定模板 Summary。患者原话逐字显示；护士自由 note 不进入正文；pending 与 ready 两态分别明确为“尚不可发送；未发送”和“已人工批准；尚未发送”。

核心边界：

- `summary_kind=manual_review_brief` 隔离旧 Timeline Summary；
- 正文只由本地固定模板与受控来源事实生成；护士自由 note、AuditEvent 和模型候选不作为临床事实正文，Controlled Summary / controlled LLM 未调用；
- 同来源重复生成返回当前版本；来源变化生成新的不可变版本，旧医生审阅决定不迁移；
- 原子写入在 `BEGIN IMMEDIATE` 内重查精确来源并一次提交 Summary、Provenance 与 audit；
- 页面普通查询和陈旧性判断只读，只有明确生成/刷新按钮写入；
- Workbench as-of、精确/资源级 Provenance 关系和应用审计记录均可追踪；
- Timeline 明确标注可能为空或过时，不作为本简报事实来源；
- Alert、获批 ClinicalRule、M6 clinical-rule Task 都保持 0，发送能力关闭，临床评估保持 `not_assessed`。
- 不生成诊断、风险分级、阈值、治疗或改药建议，不发送消息，也不写回 EMR。

浏览器已完成从首页候选、患者确认、护士确认收到/接受/记录结果、医生查看 pending、护士批准、医生查看陈旧提示并明确刷新到 ready、审计全链的点击验收。桌面和 390×844 移动端通过，console 无 error/warn。

实现细节见 `docs/26_m5_c_deterministic_doctor_brief.md`。本轮还修复了批准动作的一个既有并发重试竞态：第二个同 payload 请求在看到 ready 版本后会再次执行精确幂等查找，而不是误报状态错误。

最终本地验证：

```text
.venv/bin/python -m pytest -q
299 passed, 3 skipped

.venv/bin/python -m compileall -q continucare app.py pages
通过

git diff --check
通过
```

3 个 skip 仍只因没有设置官方 `FHIR_R4_SCHEMA_ZIP`。实施前已完成一次 Opus Level 4 策略审查，并吸收其关于事务内精确来源重查、不可变版本/审阅绑定、Summary 生产者隔离、as-of 总排序、Timeline 陈旧标记以及精确/资源级 Provenance 区分的 blocker；没有遗留 Opus blocker。

实现冻结后完成一次 Sonnet final diff review。初次结论无 blocker，但要求补充 3 个最小上下文：`_replay` 是否精确匹配 actor/note、Workbench artifact version 是否来自真实 FHIR `meta.versionId`、共享准入是否拒绝非 completed/final。补充对应源码并加强 Communication v2 精确 Provenance / QR 资源级 Provenance 测试后，Sonnet 逐项关闭 NEED_CONTEXT，最终结论：

```text
CLEAN PASS
BLOCKER: 无
NON-BLOCKING: 无
NEED_CONTEXT: 无
```

当前仍未执行 commit、push、M5-K、M5-D 或 M5-E。
