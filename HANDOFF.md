# HANDOFF

> 给本人后续 Codex/Claude 会话使用的最新快照。本文是个人协作上下文，不要求队友采用同一套多 Agent 流程；更新时整体覆盖，不追加聊天流水。

## 当前目标

- 以队友的最新 PR #2 为产品基线，在自己的 PR #3 上继续后续工作。
- 保持两人分支边界清楚：队友继续维护 PR #2，本人和 Codex 在 PR #3 工作；每个小阶段重新查看 PR #2 的进展，尽早发现重叠修改和冲突。
- 当前这次同步只调整个人协作文档和分支基线，不修改产品代码、依赖或配置。

## Git 与 Pull Request 状态（2026-08-12）

- 远端：`origin = https://github.com/xli561980-ship-it/continucare-demo.git`。
- 共享 `main` 仍为旧基线 `7768638d635c1b90e4962cbe1775046bbec0ee07`；不要从它开始新的产品工作。
- 队友 PR #2：`codex/fhir-foundation-docs`，最新已核验提交为 `dd666a6dcbe72647e05abddc338615dfb4fbe928`，base 为 `main`，当前是 Draft。它包含当前最新产品实现。
- 本人 PR #3：`codex/docs-collaboration-init`。本分支已把原先唯一的文档提交精确重放到 `PR2@dd666a6` 顶部，本地工作区最终应检出并跟踪这个分支。
- 本次同步完成后，PR #3 的 GitHub base 为 `codex/fhir-foundation-docs`。在尚未开始新产品任务时，相对 PR #2 的差异只能是 `AGENTS.md` 与 `HANDOFF.md`；以后本人的产品改动也会显示在这个差异中。
- `AGENTS.md` 使用 PR #3 的干净 185 行版本，SHA-256 为 `dd623b60f6bb0d9a81362603e54efc000f771d73ba4b6262cd86c4ec6ab888f4`。
- 分支切换前发现的两份未跟踪文档已原样备份到 `/private/tmp/continucare-docs.pgDfVM/`；其中 372 行的重复 `AGENTS.md` 仅作本次恢复备份，不得重新覆盖当前 185 行版本。系统临时目录不是长期归档。
- 是否最终把个人 `AGENTS.md` / `HANDOFF.md` 合入共享 `main` 尚未决定；未经用户确认不要合并 PR #3。

## PR #2 当前实现基线

- **Layer 1：FHIR 与路径契约。** 已有版本化 Pathway、FHIR R4 Questionnaire/PlanDefinition、QuestionnaireResponse/Observation 构造、术语目录、证据和来源追溯。当前路径仍为 `draft / synthetic_only / not_reviewed`，且 `clinical_rules=[]`。
- **Layer 2：Care Engine。** 已有版本锁定的随访会话、Questionnaire 动态渲染、`enableWhen`、草稿/幂等提交、结构化答案校验，以及患者确认后确定性写入 QuestionnaireResponse 和 Observation 的闭环。
- **Layer 3：受控 Care Agent。** 已有 MiMo OpenAI-compatible 适配器、确定性 Mock 回退、抽取/Safety Critic/患者语言改写、证据跨度与术语校验、澄清和患者确认门。模型只提出受限候选，不能直接写任意 FHIR 或生成临床风险。
- **Layer 4：服务与存储基线。** 已有合同与 SQLite 版本化存储、Clinical Memory/Timeline/Revision、状态快照与原始数值方向、双审批规则执行基础设施、FHIR Task 状态机、证据 Summary/医生审阅、受控 Summary Agent，以及 Doctor Workbench 的只读组合查询、历史回放、证据图和组件级降级。
- **尚未产品接线的部分。** 新 Layer 4 Doctor Workbench 尚未替换旧 Streamlit 医生页面，也未接真实 IAM/EMR；自动编排、真实医院 FHIR history、病历写回和真实 active 临床规则仍未完成。
- **当前医疗安全行为。** 仓库没有获批 active 临床规则，产品路径必须保持 `not_assessed`，不得生成 L0–L4 风险等级、临床 Alert、SLA 或处置建议。所有演示数据必须是合成数据。

## 验证证据与未验证项

- 本次已只读检查 PR #2 的 README、分层设计、Layer 1–4 代码、测试与验收文档，并确认产品实现确实存在；本次没有修改或运行产品代码。
- 仓库文档记录的历史证据包括：Layer 4 第 6 步 `159 passed`；受控 Summary 后完整套件 `169 passed`；官方 MiMo 固定合成验收 `5/5 cases`、`64/64 facts`。这些是仓库已有验收记录，不是本次机器重新复现的结果。
- 当前机器没有项目 `.venv`，依赖未完整安装；独立 pytest 曾在 collection 阶段因缺少 `fhirclient` 无法执行断言。官方 FHIR Schema 测试还需要显式提供 `FHIR_R4_SCHEMA_ZIP`，否则会 skip。
- 因本次只改文档和 Git 拓扑，不安装依赖、不调用模型 API、不启动 Streamlit；不能把“代码存在”或历史报告写成“本机测试通过”。
- `assets/screenshots`、旧 PDF、早期 M0–M5 文档和页面中可能保留已停用的 L2/L4 风险演示，不是当前临床依据。优先参考 `README.md`、`docs/14_layered_solution_blueprint.md` 和 `docs/18`–`docs/24`。

## 已知审查风险（尚未修复）

以下是对 PR #2 的静态审查发现，尚未在本地依赖齐全的环境中全部复现。开始相关功能前要先做任务级验证和方案，不要把它们误写成已修复：

- Layer 3 的 clarification/candidate 在 `unsure` 或 `rejected` 后仍可能再次确认并写入答案，而数据库 resolution 保留第一次决定，存在状态不一致风险。
- Layer 2 的 Questionnaire/手工结构化 Quantity 入口只检查 `unit/system/code` 非空，液体 Observation 映射只严格检查允许的 `code`；绕过 Layer 3 Safety 时，错误 UCUM system/unit 可能没有 fail-closed。Layer 3 Safety 本身已要求 `system=UCUM` 且 `unit/code=mL`。
- 术语 `validate_code`、医院 terminology backend、release manifest 的 Schema SHA/版本边界尚未形成完整的运行时验证闭环。
- Layer 4 输入读取按患者取数，未在入口严格按 pathway 过滤；同一患者多路径时可能造成 Timeline/State/Summary 串读。
- Summary 稳定 ID 未包含 pathway，同一患者和时间窗的不同路径可能共用 current/version 链。
- 多资源业务写入由多次独立 SQLite 提交组成；中途失败可能留下 Task、Summary、Review 或 Provenance 的半完成状态，且重试不一定可恢复。
- RevisionLink 在缺失判断、规则执行、状态历史和多分支冲突中的生效语义不完全一致；真实 SQLite 上游也没有完整 Observation 版本历史。
- 规则合同包含 population，但执行器尚未真正评估人口适用性。当前因 `clinical_rules=[]` 而保持 dormant；未来启用真实规则前必须补齐。
- 上述风险并不等于当前 synthetic demo 已失效，但会阻止把 PR #2 描述为多路径、生产授权或临床封板完成。

## 与队友同步方式

1. 每次开始和每个小阶段结束时执行 `git fetch origin`，记录 PR #2 的新 head，并查看 `origin/codex/fhir-foundation-docs` 相对上次 head 的文件与提交变化。
2. 不直接修改或推送队友的 PR #2 分支。先按模块明确分工；如果双方将修改同一文件或同一合同，立即协调负责人和合并顺序。
3. 本地 PR #3 工作树必须干净后，才能把新的 PR #2 提交 rebase/merge 进来；任何冲突、远端 head 变化或未保存改动都先停止并报告。
4. 完成本人一个小阶段后，Codex同时检查 PR #3 差异和队友 PR #2 最新差异，重点找同文件修改、API/Schema 变化、测试假设变化和重复实现。
5. PR #2 若最终 squash merge 到 `main`，需要以新的 `main` 提交重新整理 PR #3 基线，不能假设原 PR #2 commit SHA 仍在共享历史中。
6. 若个人协作文档最终不进入共享 `main`，交付产品代码时应从最新共享产品基线建立干净分支，只挑选产品提交，避免把个人文档误带进队友的交付。

## Agent 协作约定

- `AGENTS.md` 是唯一完整规范：Claude 做方案决策和只读审查，Codex 做独立交叉审核、协调、唯一执行和最终验收，用户是最终决策者。
- Claude 决策默认使用 `opus`，冻结目标后的机械验证可用 `sonnet`；只发送最小必要且足以改变结论的上下文，不发送密钥、凭据、真实患者数据或无关敏感信息。
- 用户可以在具体任务中明确跳过 Claude。此次分支/文档机械同步的 Claude 调用未返回审查结果，用户随后明确要求无需 Claude 审核；不得把本次描述为 Claude 已通过。
- 后续产品功能任务仍按用户当时指令和 `AGENTS.md` 重新确定方案、范围、验收标准与审查强度。

## 下一步

1. 等待用户给出本人和队友的具体功能分工；开始前先 fetch 并重新核验 PR #2 是否已前进。
2. 优先把新任务映射到明确模块、文件和验收标准，再决定是否直接在 PR #3 编码以及何时与 PR #2 同步。
3. 在涉及 Layer 3/4 已知风险的工作前，先补最小复现测试；不要顺手扩大范围或宣称生产/临床就绪。
4. PR #3 暂不合并，除非用户明确决定个人协作文档和后续产品改动的最终归属。
