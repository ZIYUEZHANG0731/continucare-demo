# HANDOFF

> 给完全没有上下文的新会话使用的最新项目快照。它不是聊天记录；每次更新应覆盖旧状态，只保留接手所需事实。

## 最终目标

完成 ContinuCare 的比赛展示版本，对齐根目录的 `2026 AI 先锋未来人才大赛.pdf`：

- 用合成数据展示诊后主动随访；
- 展示受控 AI 对患者表达的理解、异常候选与人工复核；
- 展示护士任务、沟通草稿和人工确认；
- 把院外数据沉淀为医生复诊前简报；
- 每个结果都能回到原始证据和审计记录；
- 至少展示一个真实飞书 AI/Aily 环节，并保留无 Token 时的 Mock 降级。

当前只做比赛展示，不做医院试点、生产部署、真实患者接入、IAM、EMR 写回或 HA。

项目实施编号统一使用原计划的 `M0–M6`，不要再把它和架构 `Layer 1–6` 混用。当前：

- `M0–M4`：核心工程基础已存在；
- `M5`：下一阶段，比赛 Demo 接线与打磨；
- `M6`：由队友从当前新基线独立实现飞书/Aily 适配。

## 已完成内容

### Git 状态

- 当前分支：`codex/docs-collaboration-init`。
- A1 产品基线提交：`19507a1`。本文件随后作为独立文档提交推送，因此接手时以 `origin/codex/docs-collaboration-init` 的最新 HEAD 为准，并确认其历史包含 `19507a1`。
- 队友旧分支最后核验：`codex/fhir-foundation-docs@dd666a6dcbe72647e05abddc338615dfb4fbe928`。
- 队友接下来不再继续旧 A1 分工；应从本分支包含 `19507a1` 的最新远端 HEAD 创建新的 M6 分支。
- 未经用户另行授权，不修改 PR 元数据，不 merge/rebase/tag。

### 已提交并推送的产品工作

1. `7f05f69 fix: make conversation action resolution terminal`
   - Layer 3 candidate/clarification 的硬终态不可重复或反向执行；
   - `unsure` 只允许一次转为 `accepted/rejected`；
   - 批处理预检失败时业务状态、resolution 和审计均无增量。

2. `3671008 fix: govern quantity observation inputs`
   - Layer 2 完成提交前严格校验 Quantity 形状；
   - 数值必须有限且非负；
   - system/unit/code 必须逐字匹配 `UCUM / mL / mL` 及 mapping allowlist；
   - 失败不产生完成副作用，并可用正确值重试。

3. `19507a1 fix: pin official FHIR schema archive`
   - 默认二参数 Schema 校验绑定官方 SHA-256；
   - 先读取一次 ZIP 原始字节并校验摘要，不匹配时在打开 ZIP/解析 JSON 前 fail closed；
   - 通过后从同一份已校验字节解析；
   - 坏路径、读取失败、坏 ZIP、缺 entry、坏 JSON/编码均统一为项目 `FHIRValidationError`；
   - 生产 `r4.py` 没有反向导入 `care_agent`；
   - 新增 5 个完全离线回归测试。

### A1 最终验证证据

- 写入前定向：`9 passed, 1 skipped`。
- 写入前完整：`195 passed, 3 skipped`。
- 写入后定向：`14 passed, 1 skipped`。
- 写入后完整：`200 passed, 3 skipped`。
- `compileall continucare scripts`：通过。
- `git diff --check`：通过。
- Sonnet 执行后机械复核实际模型为 `claude-sonnet-5`：无阻断项，无需升级 Opus。
- 开工前机器没有官方 Schema ZIP，因此没有联网下载，也没有运行可选的官方 ZIP 正向集成测试；三个 skip 均来自缺少 `FHIR_R4_SCHEMA_ZIP`。
- 用户授权后，A1 已单独 commit 并 push；两份任务卡未混入该 commit。本文件随后单独提交，避免与产品差异混在一起。

### 当前比赛能力

已经可以展示：

- Questionnaire 驱动的患者随访；
- Care Agent 候选、患者确认和 Safety 边界；
- FHIR QuestionnaireResponse / Observation；
- 原文、编码、单位和 `derivedFrom` 证据链；
- 医生复诊简报、审阅和审计；
- 无外部服务时的 Mock 回退。

尚未形成一条页面上完整串通的比赛故事：

- 首页一键场景主要直接载入 Layer 2，绕过 Layer 3；
- `clinical_rules=[]`，正常场景始终 `not_assessed`，护士任务中心为空；
- 尚无完整的“护士处理 → 人性化沟通草稿 → 人工批准”展示；
- 飞书/Aily/Bitable 仍是 placeholder/Mock；
- 现有医生页面能展示旧比赛简报，但新 Layer 4 Workbench 服务尚未替换页面。

## 关键决策

- 正式比赛 PDF 高于内部长期 roadmap。判断下一步时先看比赛要求，不把医院生产化缺口算入当前范围。
- 比赛使用合成数据，不能宣称诊断、治疗、改药或临床验证完成。
- 当前没有获批临床规则，必须保持默认 `clinical_rules=[]` 和 `not_assessed`。
- 比赛如展示异常分级，只能使用清楚标注的“合成演示规则 / 人工复核优先级”，不能包装为已获批临床风险结论。
- 我方下一阶段是 `M5`，目标是把现有能力接成稳定的可点击故事，不继续扩建医院级底座。
- 队友下一阶段是 `M6`，从包含 `19507a1` 的本分支最新远端 HEAD 新开分支，独立实现：
  - `AilyExtractor`；
  - `FeishuBotNotifier`；
  - `BitableStore`；
  - 卡片/回调；
  - `FEISHU_ENABLED` 开关；
  - 无 Token 的 Mock fallback。
- M6 只做比赛飞书适配，不是架构“第六层”的医院 SSO、EMR 和生产运维。
- M5 与 M6 要用稳定的消息/任务 payload 解耦并行；任一方失败不阻塞另一方的本地路径。
- 所有 Token/密钥只放被 Git 忽略的本地环境变量，不进入聊天、代码、日志或仓库。
- 产品写入继续遵守 `AGENTS.md`。未经用户明确授权，不执行 add、commit、push、merge、rebase、tag 或 PR 元数据变更。

## 踩过的坑

- 不要把“完成比赛展示”解释成医院试点或生产部署；IAM、EMR、HA、生产数据库不属于当前比赛任务。
- 不要只读内部 roadmap 而忽略根目录正式比赛 PDF。
- 不要混用两套编号：
  - `M0–M6` 是当前实施里程碑；
  - `Layer 1–6` 是技术架构分层。
- 不要再次把 action resolution、Quantity 完成期治理或 A1 Schema pin 列为未修复；它们已经分别在 `7f05f69`、`3671008`、`19507a1` 完成。
- 不要把 Layer 4 后端模块存在说成页面已接线；比赛 UI 仍主要走旧 Streamlit 服务。
- 不要把 Mock 飞书说成真实联调。
- 不要为了让护士页出现内容而恢复历史 L2/L4 风险文案或未经审批的临床规则。
- 不要让队友继续从 `dd666a6` 做 M6；那会缺少本分支后续三项修复。必须从包含 `19507a1` 的本分支最新远端 HEAD 创建 M6 分支。
- A1 开始时系统 Python 缺 `fhirclient`；经用户授权后已创建被 Git 忽略的项目 `.venv` 并安装 `pyproject.toml` 已声明依赖。后续使用 `.venv/bin/python`，不要再用缺依赖的系统 `python`。
- 更新 HANDOFF 要覆盖当前快照，不追加聊天流水、长期路线图或工时估算。

## 当前卡点

没有产品代码阻断。

当前只等待下一轮明确分工和授权：

- 队友从包含 `19507a1` 的本分支最新远端 HEAD 创建 M6 分支；
- 我方为 M5 冻结第一个小切片。

本文件提交后，工作区预期只剩两份未跟踪历史任务卡：

- `?? TASK_PROMPT_PR2_fhir_schema_sha256.md`；
- `?? TASK_PROMPT_PR3_layer3_action_resolution.md`。

这两项均为协调文件，应原样保护。出现任何其他未授权改动时立即停止报告。

## 下一步计划

新会话接手后的第一个动作：

1. 完整读取 `AGENTS.md`、本文件和正式比赛 PDF。
2. 运行只读核验：
   - `git branch --show-current`
   - `git rev-parse HEAD`
   - `git rev-parse @{upstream}`
   - `git status --short`
   - `git fetch --prune origin`
3. 确认本地和远端 `codex/docs-collaboration-init` 指向同一最新 HEAD、其历史包含 `19507a1`，且工作区只有上述两份任务卡。
4. 确认队友已从本分支最新远端 HEAD 创建 M6 分支，记录其精确分支名和 HEAD；不要代替用户自行创建或切换队友分支。
5. 我方进入 M5 的第一小切片：设计“一键合成异常候选 → 人工复核任务 → 护士处理”的比赛安全闭环。
6. 在写代码前冻结：
   - 合成规则和非临床标签；
   - Task/Communication payload；
   - 允许修改文件；
   - 页面演示步骤；
   - 零副作用和回归验收。
7. M5 后续再接沟通草稿、人工批准、医生简报和 M6 飞书适配；最后运行完整 pytest、三次 Demo 彩排、页面测试、录屏和答辩脚本。
