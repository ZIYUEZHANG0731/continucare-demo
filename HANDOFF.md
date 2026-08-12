# HANDOFF

> 给新会话的最新项目快照：每次更新都整体覆盖本文，不在末尾追加聊天记录或历史流水。

## 最终目标

- 交付一个仅使用合成数据、无需外部 API Key、可在本地运行的 AI 医疗随访闭环 Demo：患者提交随访信息，系统完成结构化抽取与证据定位，使用确定性规则生成 L0/L2/L4 风险等级，支持护士处理留痕，并为医生生成可追溯 Summary 与审计记录。
- 让项目能够由两位开发者和多个 AI 会话安全接力：Claude 负责方案制定、策略建议和交叉审查，Codex 负责独立审核、协调、唯一执行和最终验收，用户是最终决策者。

## 当前仓库状态（2026-08-12）

- 当前文档分支：`codex/docs-collaboration-init`；`main` 保持在基线 `7768638d635c1b90e4962cbe1775046bbec0ee07`，本次不直接修改或推送 `main`。
- GitHub 远端：`origin = https://github.com/xli561980-ship-it/continucare-demo.git`。
- 当前 GitHub 账号：`ZIYUEZHANG0731`。
- Write 权限已通过 GitHub API 实际验证：仓库权限字段 `permissions.push=true`。
- 已成功执行 `git fetch origin`，退出码为 0。
- 本地 `main`：`7768638d635c1b90e4962cbe1775046bbec0ee07`。
- 最新 `origin/main`：`7768638d635c1b90e4962cbe1775046bbec0ee07`。
- `git rev-list --left-right --count main...origin/main` 结果为 `0 0`：本地与远端一致，没有领先、落后或分叉。
- 主工作区仍检出 `main` 且没有已跟踪文件改动；两份协作文档已在独立分支 `codex/docs-collaboration-init` 纳入版本控制。本次仅有文档变更，Pull Request 待审核。

## 已完成内容

- 仓库已经包含 M0–M5 对应的应用代码、页面、数据模型、规则引擎、Mock 适配器和测试文件；现有文档将这些里程碑标记为完成。当前机器尚未重新运行测试或 UI，因此这里只表示“实现已存在”，不表示“本机已验证通过”。
- 产品当前采用 Python、Streamlit、SQLite、Pydantic 和 pytest，主要入口及目录包括 `app.py`、`pages/`、`continucare/` 和 `tests/`。M6 真实飞书/Aily 接入尚未开始，第一版本继续使用 Mock 边界。
- 根目录 `AGENTS.md` 已定义 Claude、Codex 和用户的角色边界，以及方案、双向审核、执行、执行后交叉审核和最终验收流程；该文件已在分支 `codex/docs-collaboration-init` 纳入版本控制，并完成机械去重，规则语义未改变。
- GitHub 协作者邀请已接受，账号 `ZIYUEZHANG0731` 对仓库的 Write 权限已经验证。
- 远端引用已于 2026-08-12 实时同步；本地 `main` 与最新 `origin/main` 均为 `7768638d635c1b90e4962cbe1775046bbec0ee07`，ahead/behind 为 `0/0`。
- 当前没有项目 `.venv`；系统 Python 为 3.13.3，且尚未安装 Streamlit。依赖、pytest 和 Streamlit UI 均尚未在本机验证。状态是“尚未验证”，不是“测试失败”。

## 关键决策

- 所有会修改项目的任务遵循 `AGENTS.md`：Claude 先读取环境并提出方案；Codex 独立审核并把意见交回 Claude；双方解决关键问题并确认可执行后，才由 Codex 修改；完成后再由 Claude 审查差异和验证证据，Codex 独立复核并最终验收。
- Claude 只做方案、策略建议、只读检查和交叉审查，不直接修改用户工作区、Git 状态或外部系统；Codex 是唯一执行者。用户已明确授权 Claude 在方案和审查环节只读访问项目全部文件，并将必要上下文发送给 Claude 服务，但不得向其提供密钥、令牌、真实患者数据或无关敏感信息。
- 团队协作使用独立分支和 Pull Request，不直接修改或推送 `main`；开始工作前先执行 `git fetch` 并比较远端状态，同时保护队友和用户已有改动。
- 本次协作初始化已获用户明确授权；`AGENTS.md` 与 `HANDOFF.md` 已在独立分支 `codex/docs-collaboration-init` 提交，Pull Request 待审核；不直接修改或推送 `main`。
- 医疗安全边界保持不变：只使用合成数据；L0/L2/L4 由确定性规则产生而不是交给模型自由判断；证据链必须可追溯；不得提交真实患者信息、Token、密钥或其他凭据。
- `HANDOFF.md` 始终只保留最新项目状态，更新时整体覆盖旧内容；详细的长期决策或实验记录如以后确有需要，再分别建立 `DECISIONS.md` 或 `WORKLOG.md`。
- 2026-08-12 新增模型分层规则，条文写入 `AGENTS.md` 的“模型分层”一节：档位按是否涉及实质性取舍划分而非按流程阶段——涉及架构、范围与非目标、验收标准、依赖或外部系统、安全隐私数据完整性医疗风险、关键分歧的工作使用 `opus`（Opus 5 系列）；目标与验收标准已冻结且本次不变的机械性计划与验证可用 `sonnet`（Sonnet 5 系列）以节省 token；判据不明确时默认 `opus`。`sonnet` 遇到需要主观判断的阻断项须升级到 `opus`；`opus` 档工作不得因容量不足自动降级，用户允许的临时降级结论须由 `opus` 在执行前补充确认。命令行只使用 `opus`/`sonnet` 别名，精确模型 ID 仅在平台可验证时记录。本文件只记摘要，`AGENTS.md` 为唯一规范来源。
- 2026-08-12 新增上下文供给规则：Codex 交给 Claude 的材料默认最小必要，但不得省略会改变结论的关键事实与决定性原文片段；Claude 缺少关键上下文时须指出并由 Codex 补充。条文见 `AGENTS.md` 的“上下文供给：最小必要上下文”一节，该节为唯一规范来源。

## 踩过的坑

- `git status` 显示与 `origin/main` 一致，只代表与本地缓存的远端引用一致；必须先成功执行 `git fetch`，才能声称已与 GitHub 实时同步。
- GitHub Write 权限应以当前账号和仓库 API 返回的 `permissions.push=true` 等真实证据判断；不要依赖旧记录，也不要把可能为空的 `viewer_permission` 当作唯一判据。
- 本地没有安装依赖时，无法运行 pytest 或启动 Streamlit。此时应写“尚未验证”，不能写成“测试失败”，也不能把文档中的完成标记当成新的测试证据。
- Claude Code 模型可能临时容量不足；只读审查可以显式选择其他可用模型并配置 fallback。模型切换不改变 Claude 的只读职责边界，也不能成为跳过实际双向审核的理由。分层判据、升级与降级规则见 `AGENTS.md` 的“模型分层”一节，该节为唯一规范来源。
- Claude Code 的可变参数（例如 `--allowedTools`）应放在命令末尾，避免误吞后续 prompt。若 Claude 没有返回有效审查，必须先排查登录、模型容量和调用参数，不能虚构意见或跳过审核。

## 下一步动作

1. 审核并合并 `codex/docs-collaboration-init` 对 `main` 的 Pull Request；本次只创建 PR，不由 Codex 合并，也不直接推送 `main`。
2. PR 合并后，在当前主工作区同步前先处理同名的未跟踪 `AGENTS.md` 和 `HANDOFF.md`，避免 `git pull` 被阻塞；任何清理需另行授权。
3. 经用户另行确认后建立隔离的项目环境，安装已声明依赖，运行 pytest 并启动 Streamlit 做基础验证；用真实结果整体覆盖更新本文。
4. 后续新会话先读取 `AGENTS.md` 和 `HANDOFF.md`。任何产品功能任务，包括 M6 飞书/Aily 接入，都重新从 Claude 方案和双向审核开始，不能直接执行。
