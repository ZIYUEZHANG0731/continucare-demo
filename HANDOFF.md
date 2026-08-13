# HANDOFF

> 给完全没有上下文的新会话使用。先完整阅读根目录 `AGENTS.md`，再阅读本文件。本文记录的是 2026-08-13 会话结束时的真实工作区快照，不是长期路线图。

## 1. 当前任务是什么

我们正在完成 ContinuCare M5 的“通用知识证据基础设施（Knowledge Evidence Foundation）”子任务。

目标不是做一个 GLP-1 专用知识库，也不是做无边界医学问答或 RAG。目标是提供一个 Pathway-agnostic、静态、版本化、只读、fail-closed 的知识登记层，让未来不同疾病、药物、术后路径和患者教育主题都能说明：

- 为什么采集某个 Questionnaire item 或 Observation；
- 某个术语、展示或候选设计依据来自哪里；
- 来源支持什么、明确不支持什么；
- 内容适用于哪个精确 Pathway 版本和哪些 scope；
- 当前知识审核处于什么状态；
- 为什么知识登记不能自动变成临床行为。

必须持续区分四层：

1. Source Document：公开资料或标准；
2. Knowledge Claim：受控、有限定范围的主张；
3. Executable Artifact：Questionnaire、mapping、rule、education 等由各自治理控制的工件；
4. Patient Evidence：患者原话、QuestionnaireResponse、Observation、Timeline、Summary evidence。

Knowledge Evidence 只解释“系统为什么采集或展示”，绝不证明“这个患者发生了什么”，更不授予 artifact 执行、发布或临床批准权限。

GLP1-14D 只是仓库现有资料最完整的第一个 migration fixture。测试里另有一个只存在于临时目录的 synthetic Pathway，用于证明合同和 loader 没有硬编码 GLP。

## 2. 用户本轮最重要的纠正

用户明确反对固定目标编号和固定数量：

> 有多少信息就展现多少。

因此最终实现遵循：

- schema 和 JSON 中没有 `target_number`；
- renderer 不显示 `target#`、分母或百分比；
- 不把 20、11、9 写成 loader 约束；
- 动态派生以下绝对计数：
  - `unique_artifacts`
  - `registered_relationships`
  - `explicit_gaps`
  - `verified_citation_relationships`
  - `claim_review_approved_relationships`
- 同一个 exact artifact 可以同时有 binding 和 gap，`unique_artifacts` 对两者的 artifact key 并集去重，绝不能简单写成 `bindings + gaps`；
- GLP 当前的 20/11/9/0/0 只是 2026-08-13 数据快照，不是通用合同。

## 3. 已完成的实现

### 3.1 新增通用 Knowledge 包

当前有 14 个尚未跟踪的纯新增路径：

- `continucare/knowledge/__init__.py`
- `continucare/knowledge/__main__.py`
- `continucare/knowledge/models.py`
- `continucare/knowledge/registry.py`
- `continucare/knowledge/render.py`
- `continucare/knowledge/resolvers.py`
- `continucare/knowledge/manifests/__init__.py`
- `continucare/knowledge/manifests/bundle_index_v1.json`
- `continucare/knowledge/manifests/sources_v1.json`
- `continucare/knowledge/manifests/claims_v1.json`
- `continucare/knowledge/manifests/bindings_glp1_14d_v1.json`
- `continucare/knowledge/manifests/governance_v1.json`
- `docs/25_knowledge_evidence_foundation.md`
- `tests/test_knowledge_registry.py`

核心能力：

- strict Pydantic contracts，`extra="forbid"`；
- exact compound refs：Source/Claim/Binding/Gap/Pathway 都锁定版本；
- append-only forward `supersedes`，禁止版本跳跃、分支和断链；
- typed artifact refs，不用松散字符串；
- typed citation locator，并拒绝 `N/A`、`unknown`、`whole document`、`TBD` 等 placeholder；
- clinical scope 使用精确 Pathway version whitelist 和 typed dimensions；
- universal scope 仅限 terminology/unit/interoperability，仍要求每个 artifact 显式 binding；
- `WorkflowDesignDecision` 与 sourced claim 分离；缺真实 owner/decision 的内容记录为 gap，不编造历史决策。

### 3.2 真正的深只读

不仅 Pydantic 顶层 `frozen=True`，内部集合也已封死：

- persisted collections 使用 tuple/frozenset；
- approval policy 使用 `MappingProxyType + frozenset`；
- registry/view 的公开映射复制后包装为 `MappingProxyType`；
- `ReviewSummary.axes` 不可修改；
- 测试证明加载后无法修改 source URL、citation、required approvals、review events、artifact resolution、review summary 或全局 approval policy。

不要退回仅靠 `frozen=True` 的浅冻结；那会允许原地篡改嵌套 list/dict，并让 renderer 消费伪造状态。

### 3.3 原子 bundle loader

`bundle_index_v1.json` 是唯一入口，不扫描目录。每个 payload 固定：

- exact file ID/version；
- canonical relative path；
- optional byte size；
- mandatory raw-byte SHA-256。

loader 顺序是先读 bytes、核对 size/hash，再解析 JSON。任何失败都不返回部分 registry。

Filesystem 路径适配器拒绝：

- absolute path；
- 空段、`.`、`..`；
- 反斜杠；
- symlink；
- root escape。

Path-backed `TraversableBundleSource` 会委托同一安全 filesystem adapter。普通非 Path 的自定义 Traversable 被视为受信 provider；如未来开放给不受信输入，必须重新冻结 trust boundary。

### 3.4 CURRENT / HISTORICAL 双模式

CURRENT：

- current refs 必须存在、唯一、指向逻辑 head；
- 只允许 eligible source/claim/binding/open gap；
- binding 必须闭包到 selected exact claim 和 cited sources；
- Pathway scope 和 catalog ownership 必须匹配；
- selected artifact 必须由 resolver 解析；
- current-related review head 必须通过真实注入的 authority resolver；
- authority resolver 返回 identity 必须与 asserted actor 精确绑定；
- local copy 必须先有可信、approved、允许 `local_copy` 的许可决定，然后才允许读取和 hash；
- manifestation equivalence 必须有可信批准；
- trusted rejected current-related event 会阻断。

HISTORICAL：

- 保留旧记录、旧 review event 和 audit topology；
- unresolved/untrusted 显示为 unresolved 或 `unverified_assertion`；
- 不把历史断言当批准；
- 绝不读取注册的第三方 local blob，状态是 `not_read_in_historical_mode`。

source `manifestation_of` 图禁止自环和任意环。

### 3.5 审核与许可语义

ReviewEvent 是按 domain 判别的 typed union：clinical、pharmacy、terminology、internal consistency、citation verification、license decision、equivalence。

- JSON 只保存 actor assertion，不允许自报 `authority_resolved=true`；
- 无真实 identity provider 时，CURRENT 对 current-related review fail closed；
- built-in bundle 没有 review event，所有审核轴均为 `not_assessed`；
- pharmacy 是独立 advisory axis，不替代 clinician review；
- `claim_review_approved_relationships` 只说明知识 claim 的 review aggregate，绝不等于 artifact/binding approval；
- required approvals 只是“哪些独立治理方需要行动”的要求，不是 approval state；
- 没有 `approved_for_execution`，没有 `activation_gate`。

### 3.6 Artifact resolver 和只读展示

当前真实 repository resolver 支持：

- Questionnaire item；
- Observation mapping item；
- Questionnaire terminology binding；
- terminology concept；
- whole PlanDefinition。

预留的 clinical rule、red flag、education、summary refs 在 CURRENT 没有 resolver 时 fail closed，在 HISTORICAL 可显示 unresolved。

CLI：

```bash
.venv/bin/python -m continucare.knowledge GLP1-14D --version 1.0.0
.venv/bin/python -m continucare.knowledge GLP1-14D --version 1.0.0 --historical
```

renderer 显示 exact refs、scope、supports/does_not_support、claim/binding/gap lifecycle、currentness、review axes（含 pharmacy）、source version/URL/review/integrity 和固定安全声明。它不读取患者数据，不推导 runtime eligibility。

## 4. 当前 GLP fixture 的精确事实

当前 built-in bundle：

- 13 source records；
- 13 个一对一 legacy aliases；
- 7 个 draft claims；
- 11 个 bindings；
- 9 个 open gaps；
- 20 个动态去重后的 exact artifact targets；
- 0 verified citation relationships；
- 0 claim-review-approved relationships；
- 0 review events；
- 所有 source 均为 `link_only / not_content_fixed`；
- 所有 quote 均为 null；
- GLP manifest 仍为 `clinical_rules=[]`。

20 个 target 的当前分类是：6 Questionnaire items、5 mapping items、5 questionnaire terminology bindings、3 selected terminology concepts、1 whole PlanDefinition。这只是 fixture 当前覆盖事实。

四条 DailyMed legacy 记录只有仓库内已有的 title、URL、retrieval date：

- authority/jurisdiction 明确标记 `not_available_in_repository`；
- document version 为 null；
- language 用 `und`；
- set ID 仅从既有 URL 机械提取，仍是未核验候选；
- 不得把 DailyMed host 冒充 issuing authority。

FDA AccessData 与 DailyMed WEGOVY 保持独立 source；没有证据证明是同一文档版本，绝不能按标题合并。

当前 manifest pin：

- `sources_v1.json`: `f1402a5e5511e39eaed2f498da0d2c8bf30c9eec946f9d3eedecc47352e9d8a5`, 18037 bytes
- `claims_v1.json`: `5f47a4d8b2b5b9a747670a30a6a068bb599793f7bb77737a64e3d263516460ef`, 11295 bytes
- `bindings_glp1_14d_v1.json`: `4be160b8d55bdfaaacc929b02372edd93a61680eb8582d6ab97304097841a14d`, 9528 bytes
- `governance_v1.json`: `3ed8eb19d49bd9add3e1d6deac638058d591eb1eddfa4f49700a69ece769cdf0`, 8021 bytes

修改任何 payload 后必须同步 index 的 hash/size，并重新跑 pin 测试。

## 5. 验证和审核证据

最后一次 Codex 最终验收：

```text
.venv/bin/python -m pytest -q tests/test_knowledge_registry.py
71 passed

.venv/bin/python -m pytest -q
271 passed, 3 skipped
```

三个 skip 都是已有条件测试缺少官方 `FHIR_R4_SCHEMA_ZIP`：

- `tests/test_fhir_conformance.py:114`
- `tests/test_layer4_rules_tasks.py:575`
- `tests/test_layer4_summaries.py:428`

不是 Knowledge 回归失败。

CURRENT 和 HISTORICAL CLI 都 exit 0，输出的 coverage 均为：

```text
unique_artifacts=20
registered_relationships=11
explicit_gaps=9
verified_citation_relationships=0
claim_review_approved_relationships=0
```

在旧 `AGENTS.md` 的高风险流程下，实际使用 alias `opus`、平台可观察精确模型 `claude-opus-5` 做了两轮隔离执行后审查；无 fallback/降级。两轮结论均为：

- 阻断项：无；
- 冻结验收全部满足；
- 明确可交付。

已经清理本任务创建的隔离 review worktree。不要为了形式重复整仓审查；只有代码变化、验证可疑或高风险语义变化时才按当前 `AGENTS.md` 决定是否调用 Claude。

## 6. 当前 Git 与工作区状态

会话结束时：

- 工作目录：`/Users/zhangziyue/Documents/Codex/continucare-demo`
- 分支：`codex/docs-collaboration-init`
- HEAD：`43012df9582a346524b26ba0cdd7a5318e510966`
- upstream：`origin/codex/docs-collaboration-init`，同一 HEAD
- 未执行 add、commit、push、merge、rebase、tag 或 PR 修改

预期状态：

- `M AGENTS.md`：用户刚提供的新协作规范，已按附件正文替换；
- `M HANDOFF.md`：本交接文档；
- 上述 14 个 Knowledge 路径全部为 `??` 未跟踪文件；
- 不应有其他改动。

这些 Knowledge 文件不是临时垃圾，绝对不要 clean、reset、checkout 或删除。它们是本轮主要交付，只是尚未获得 staging/commit 授权。

## 7. 当前卡在哪里

没有代码 blocker，也没有未解决的审核 blocker。

当前停点只是会话结束和 Git 决策边界：

- 实现、测试、文档、两轮交叉审核和 Codex 最终验收均已完成；
- 改动尚未暂存或提交；
- wheel 安装包中的 JSON resource inclusion 尚未实际 build/install 验证；文档已明确不声称完成；
- 外部公开来源和 locator 本轮没有联网核验；所有 built-in claim 仍为 draft/not_assessed；
- 没有真实 identity provider、没有 licensed local artifact、没有外部 evaluation corpus；
- legacy pathway/terminology source 字段尚未收敛成新 registry 的生成投影；这是后续迁移，不是本切片 blocker。

## 8. 下一步计划

新会话接手后：

1. 完整读取新的 `AGENTS.md` 和本文件。
2. 只读确认：

   ```bash
   git branch --show-current
   git rev-parse HEAD
   git rev-parse @{upstream}
   git status --short --untracked-files=all
   ```

3. 预期看到 `AGENTS.md`、`HANDOFF.md` 两个 tracked 修改和 14 个 Knowledge untracked 文件；若多出其他文件，先报告，不清理。
4. 如果用户只要求交付当前切片：先审阅最终 diff/status；只有用户明确授权后才能 stage、commit 或 push。
5. 若准备发布 wheel：在干净隔离环境实际 build/install wheel，验证 `continucare.knowledge.manifests` 的 5 个 JSON resource 可通过 `importlib.resources` 读取，再跑 CLI 和专项测试。不得在未验证前宣称打包成功。
6. 若继续扩 Knowledge：优先从明确的新 Pathway/新 artifact binding 需求出发，复用同一 generic loader；不要把 synthetic fixture 加入产品 manifest。
7. 若回到比赛 M5 主线：基于当前已完成 foundation，再冻结一个小的比赛演示切片；不要在本任务里偷偷加入 M6 飞书/Aily、临床阈值、Alert 或真实患者数据。

## 9. 绝对不要再踩的坑

### 需求与通用性

- 不要把 Knowledge 合同写死为 GLP-1；GLP 只是一份 built-in fixture。
- 不要恢复 target 编号、固定总数、分母、百分比或“完成度 11/20”。有多少真实记录就动态显示多少。
- 不要把当前 20/11/9 写进 schema validator；测试里的绝对数只用于发现 fixture 意外漂移。
- 不要把 `unique_artifacts` 写成 `bindings + gaps`；同一 artifact 可以同时存在 relationship 和 gap。
- 不要把 `clinical_rules=[]` 说成所有 Pathway 的全局规则；它只是当前 GLP fixture 的回归事实。

### 医疗与治理安全

- 不要让 KnowledgeClaim 自动激活 ClinicalRule。
- 不要添加 `approved_for_execution` 或 `activation_gate`。
- 不要把 claim review approved 表述成 artifact、binding、发布或执行批准。
- 不要把 `not_assessed` 解释为安全、有效或可临床使用。
- 不要虚构 clinician reviewer、authority、jurisdiction、document version、license 或 exact locator。
- 不要让 pharmacist review 代替 clinician review；pharmacy 是独立 advisory axis。
- 不要把 Patient Evidence 类型复用成 Knowledge Citation，也不要向 Knowledge 包加入 patient ID/store/runtime patient data。
- 不要启用阈值、诊断、治疗、用药建议、红旗流程或临床风险等级。

### 版本与审计

- 不要原地修改旧 record 语义；追加新版本并用 forward exact `supersedes`。
- 不要把 `superseded_by` 存回旧记录；reverse/head 必须派生。
- 不要删除历史 binding/review 以让 CURRENT 通过；CURRENT 选择 eligible head 子集，HISTORICAL 保留旧记录。
- 不要允许 source manifestation 自环或环。
- 不要退回浅冻结；所有公开嵌套集合和派生状态必须保持不可变。

### 来源、许可与路径

- 不要仅凭标题把 FDA 与 DailyMed 合并成同一 source/version。
- 不要给 link-only 外部页面填 content hash；它必须是 `not_content_fixed`。
- 不要在许可核验前保存、读取或引用本地第三方 artifact。
- CURRENT local copy 必须先有可信 approved license，再读取并核对 bytes hash。
- HISTORICAL 绝不能自动读取第三方 local blob。
- 不要弱化 relative-path、symlink 或 root-escape 检查。
- 不要把新 manifests 放到 `continucare/knowledge/data/`：根 `.gitignore` 的 `data/` 会把它们忽略。当前选择 `continucare/knowledge/manifests/` 是有意的。
- 不要用 `git add -f` 掩盖资源被 ignore，也不要为了本切片修改 `.gitignore`。

### 测试、依赖与 Git

- 使用项目 `.venv/bin/python`；系统 Python 曾缺项目依赖。
- 修改 manifest 后必须重新 pin SHA-256/size。
- 不要只测 happy path；保留 CURRENT fail-closed、HISTORICAL readable、authority、license、path traversal、deep immutability、scope isolation 和 cycle 负测。
- Knowledge 包不直接依赖 layer4/db/store/care_engine；但当前经 `terminology.catalog -> continucare.agents` 会传递性加载 `continucare.db`。该模块 import 期没有数据库副作用，这是已知非阻断边界；不要误报成运行时写库，也不要宣称完全没有传递 import。
- `RepositoryArtifactResolver` 默认注入当前唯一的 GLP terminology catalog，但接口支持注入其他 catalogs；不要把这个默认 fixture 误判为 schema 硬编码。
- 未经用户明确授权，不得 stage、commit、push、merge、rebase、tag、改 PR、切分支或删除文件。
- 工作区里 14 个 `??` Knowledge 文件都是有效成果，绝对不要 `git clean`。

## 10. 已知非阻断建议

这些不阻塞当前交付，不要擅自扩大范围：

- 发布前验证 wheel resource inclusion；
- 未来可明确普通非 Path Traversable 的 trusted-provider 边界；
- 可补 pharmacy `changes_requested/rejected` 在 HISTORICAL aggregate 中保持 advisory 的直接测试和更精确文档；
- 可评估 HISTORICAL catalog ownership 缺失是否应降级为 unresolved，而非硬结构错误；当前行为是跨模式硬不变量；
- 可评估 CURRENT 对“选中但未被 binding 引用的 claim”是否也要求 cited-source closure；当前 renderer 只展示 bindings；
- 未来 source registry 与 legacy pathway/terminology source 应收敛成 aliases/生成投影，但不要在没有新方案时修改旧合同。

## 11. 一句话接管结论

通用 Knowledge Evidence 第一实现切片已经完成并通过 71 项专项、271 项全量测试以及两轮 Opus 审查；现在没有技术阻断，只差用户决定是否将 `AGENTS.md`、`HANDOFF.md` 和 14 个 Knowledge 文件纳入 Git，下一会话首先保护这些未提交成果，绝不能清理或把动态 GLP 快照重新写成固定编号合同。
