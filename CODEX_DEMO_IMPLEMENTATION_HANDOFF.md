# ContinuCare 比赛 Demo：Codex 实施交接文档

> 用途：将本文件复制到一个新的 Codex 工作区，作为实现任务的唯一主说明。
>
> 当前阶段：开题提交前。飞行社/Aily 专属账号尚未开通，因此先实现平台无关的可运行闭环；入围后仅替换外部适配器，不重写业务逻辑。

## 1. 项目目标

实现一个使用**合成患者数据**的医疗连续照护 Demo，完整展示：

```text
医生启用随访路径
→ 患者提交院外状态
→ 系统抽取结构化 Observation 和原文证据
→ 确定性规则判断工作流优先级
→ 护士收到 Alert 并处理留痕
→ 复诊前生成可追溯 Summary
→ 医生点击结论查看原始证据
```

Demo 的目的不是诊断或给出治疗建议，而是证明“院外随访数据如何进入医院工作流，并转化为复诊前可审阅证据”。

## 2. 成功标准

第一版完成时必须满足：

1. 本地一条命令启动，无外部 API Key 也能完整演示。
2. 提供患者端、护士端、医生端三个可操作页面。
3. 患者输入“今天吐了一次，喝水也不太想喝”后：
   - 生成结构化 Observation；
   - 标出“吐了一次”“喝水也不太想喝”两个原文证据；
   - 确定性规则创建 L2 Alert；
   - 护士可确认、升级或关闭并填写处理记录；
   - 医生 Summary 展示 Alert 及处理结果。
4. 患者输入急症红旗表达后触发 L4，并显示固定急救提示。
5. Summary 的每条关键结论都包含 `evidence_refs`。
6. 所有操作写入审计日志，刷新或重启后数据不丢失。
7. 所有测试数据明确标注为合成数据。
8. 自动化测试覆盖正常、L2、L4、否定表达和摘要证据链。

## 3. 非目标

第一版不要实现：

- 真实患者接入；
- HIS/EMR 正式集成；
- 自动诊断、治疗建议或用药建议；
- 全病种 Pathway Studio；
- 复杂多 Agent 运行平台；
- 真实短信、电话或患者微信推送；
- 依赖尚未获得的飞书/Aily Token；
- 大规模模型评测平台。

## 4. 固定技术栈

为了让 Codex 可快速、稳定地实现，第一版固定使用：

- Python 3.11+
- Streamlit：单体 Demo UI
- SQLite：本地持久化
- Pydantic：输入输出 Schema
- pytest：测试
- 标准库 `sqlite3`：避免引入不必要 ORM
- 可选 HTTP 客户端：`httpx`

除非遇到无法解决的技术阻塞，不要自行切换到 Next.js、React、FastAPI 或其他框架。

## 5. 推荐目录结构

```text
continucare-demo/
├── README.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── app.py
├── pages/
│   ├── 1_patient_followup.py
│   ├── 2_nurse_risk_center.py
│   ├── 3_doctor_summary.py
│   └── 4_audit_log.py
├── continucare/
│   ├── __init__.py
│   ├── config.py
│   ├── db.py
│   ├── models.py
│   ├── repositories.py
│   ├── services/
│   │   ├── enrollment.py
│   │   ├── followup.py
│   │   ├── extraction.py
│   │   ├── risk_rules.py
│   │   ├── alerts.py
│   │   ├── summaries.py
│   │   └── audit.py
│   ├── adapters/
│   │   ├── base.py
│   │   ├── mock_extractor.py
│   │   ├── mock_notifier.py
│   │   ├── sqlite_store.py
│   │   └── feishu/
│   │       ├── README.md
│   │       ├── aily_extractor.py
│   │       ├── bitable_store.py
│   │       └── bot_notifier.py
│   └── demo_data.py
├── tests/
│   ├── test_extraction.py
│   ├── test_risk_rules.py
│   ├── test_alert_workflow.py
│   ├── test_summary_evidence.py
│   └── test_end_to_end.py
├── docs/
│   ├── architecture.md
│   ├── safety.md
│   ├── feishu_integration.md
│   ├── evaluation.md
│   └── submission_brief.md
└── assets/
    ├── screenshots/
    └── demo/
```

第一阶段可以创建 `continucare/adapters/feishu/` 的接口和说明，但不得伪造已经完成的飞书联调。

## 6. 核心数据模型

所有模型使用字符串 ID 和 ISO 8601 时间。至少实现以下实体。

### 6.1 Patient

```text
patient_id
display_name
synthetic: bool
pathway_code
enrollment_date
next_visit_date
status
created_at
```

### 6.2 FollowUpMessage

```text
message_id
patient_id
message_text
submitted_at
source = "patient_demo_web"
processing_status
```

### 6.3 Observation

```text
observation_id
patient_id
message_id
code
value
unit
effective_time
source = "patient_reported"
confidence_tier
evidence_text
evidence_start
evidence_end
created_at
```

`confidence_tier` 只允许：

```text
patient_confirmed
verbatim_explicit
model_inferred
needs_human_review
```

不要使用“模型自报 0-1 置信度”决定任何治理动作。

### 6.4 Alert

```text
alert_id
patient_id
severity
title
trigger_rule_id
trigger_reason
evidence_refs
owner_role
status
sla_due_at
created_at
resolved_at
resolution_reason
```

`status` 只允许：

```text
open
acknowledged
escalated
resolved
```

### 6.5 AlertAction

```text
action_id
alert_id
action_type
actor_role
note
created_at
```

### 6.6 Summary

```text
summary_id
patient_id
period_start
period_end
status
summary_json
created_at
reviewed_at
```

`summary_json` 至少包含：

```json
{
  "overview": [],
  "key_changes": [],
  "alerts_and_actions": [],
  "patient_questions": [],
  "missing_data": [],
  "doctor_to_confirm": []
}
```

每个数组元素必须是：

```json
{
  "text": "...",
  "evidence_refs": ["message_x", "observation_y", "alert_z"]
}
```

### 6.7 AuditEvent

```text
event_id
patient_id
entity_type
entity_id
event_type
actor_type
details_json
created_at
```

## 7. 适配器边界

业务服务不得直接调用 Streamlit、飞书或特定模型 SDK。定义三个核心接口。

### 7.1 AIExtractor

```python
class AIExtractor(Protocol):
    def extract(self, message: FollowUpMessage) -> ExtractionResult: ...
    def generate_summary(self, context: SummaryContext) -> SummaryDraft: ...
```

第一版实现 `MockExtractor`。它必须稳定处理固定 Demo 场景，并支持：

- “吐了一次” → `vomiting_count = 1`
- “喝水也不太想喝” → `fluid_intake_reduced = true`
- “没有吐”不得抽取为呕吐阳性
- “上个月胸痛过，现在没有”不得直接判定当前急症
- 保留证据文本和字符位置

`MockExtractor` 可以是规则与模板组合，但 UI 中必须标注“本地稳定演示模式”。不得把规则抽取伪装成真实大模型结果。

### 7.2 DataStore

```python
class DataStore(Protocol):
    def save_message(...): ...
    def save_observations(...): ...
    def save_alert(...): ...
    def update_alert(...): ...
    def save_summary(...): ...
    def append_audit_event(...): ...
```

第一版使用 `SQLiteStore`；入围后增加 `BitableStore`，但 SQLite 继续保留为本地开发模式。

### 7.3 NotificationChannel

```python
class NotificationChannel(Protocol):
    def notify_nurse(self, alert: Alert) -> DeliveryResult: ...
    def notify_doctor(self, summary: Summary) -> DeliveryResult: ...
```

第一版使用 `MockNotifier`，在 UI 中显示“模拟飞书告警卡片”；入围后增加 `FeishuBotNotifier`。

## 8. 固定规则引擎

风险分级必须是确定性纯函数，不能由 LLM 直接输出最终等级。

### Rule EMERGENCY-001

当患者当前表达包含红旗症状且不被否定/既往描述排除时：

```text
胸痛
喘不过气 / 呼吸困难 / 上不来气
意识不清 / 晕厥
大量出血
```

输出：

```text
severity = L4
owner_role = on_call_clinician
```

患者端固定显示：

> 系统不是急救通道。你的描述包含需要尽快获得医疗帮助的信号，请立即联系当地急救或前往急诊，同时系统会通知医护团队。

不得输出疾病判断。

### Rule GLP1-002

条件：

```text
vomiting_count >= 1 AND fluid_intake_reduced == true
```

输出：

```text
severity = L2
owner_role = nurse
SLA = 24 hours
```

### Default

没有规则命中时：

```text
severity = L0
不创建需要处理的 Alert
Observation 正常进入 Timeline
```

## 9. 页面要求

### 9.1 首页

必须展示：

- 项目一句话定位；
- “仅使用合成数据”的醒目标记；
- 三个角色入口；
- Demo重置按钮；
- 当前模式：本地稳定演示 / 外部AI / 飞书集成；
- 预置场景快速入口：正常、L2、L4。

### 9.2 患者随访页

必须包含：

- 手机尺寸的聊天/随访区域；
- 患者基本信息和当前 Pathway；
- 文本输入框；
- 三个预置示例按钮；
- 提交后的结构化抽取结果；
- 原文证据高亮；
- L4 时固定急救提示；
- 不得显示诊断或改药建议。

### 9.3 护士风险中心

必须包含：

- Open Alert 列表；
- L2/L4、责任人、SLA倒计时；
- 触发规则和证据；
- 模拟飞书通知卡片；
- “确认收到”“升级医生”“关闭”操作；
- 关闭时强制填写处理记录；
- 全部操作写入 AlertAction 和 AuditEvent。

### 9.4 医生复诊简报

必须包含：

- 14天概览；
- Observation趋势；
- Alert及处理结果；
- 患者主要问题；
- 缺失数据；
- 医生待确认事项；
- 每条内容的 Evidence 展开区；
- “已审阅”按钮；
- 禁止默认写入EMR。

### 9.5 审计页

按时间倒序显示所有事件，至少包括：

- 患者提交；
- 抽取完成；
- 规则命中；
- Alert 创建；
- 通知模拟发送；
- 护士处理；
- Summary 生成；
- 医生审阅。

## 10. 固定 Demo 数据

初始化一个合成患者：

```text
patient_id: P-DEMO-001
display_name: 陈女士（合成）
pathway_code: GLP1-14D
next_visit_date: 当前日期 + 14天
```

至少准备三组输入：

### 正常路径

```text
今天有点恶心，但是能正常喝水，没有吐。
```

预期：记录恶心；不得抽取呕吐阳性；不创建 L2。

### L2路径

```text
今天吐了一次，喝水也不太想喝。
```

预期：创建 L2。

### L4路径

```text
我现在胸口很痛，还有点喘不过气。
```

预期：创建 L4、显示固定急救提示、通知值班角色。

## 11. Summary生成原则

默认使用模板生成，保证演示稳定：

1. 只允许使用数据库中已经存在的事实。
2. 每条 bullet 必须有 `evidence_refs`。
3. 无证据内容不得进入 Summary。
4. `model_inferred` 内容进入“医生待确认”，不进入确定事实区。
5. 患者问用药时只能记录“患者希望医生确认是否需要调整”，不得生成调整建议。
6. 缺失随访日期必须明确列出。
7. Alert 必须同时展示触发原因和最终处理结果。

## 12. 测试要求

Codex 完成每个里程碑后必须运行测试，不得只做 UI。

至少实现：

```text
test_normal_message_does_not_trigger_l2
test_negated_vomiting_is_not_positive
test_historical_chest_pain_is_not_current_l4
test_l2_rule_requires_both_conditions
test_current_emergency_phrase_triggers_l4
test_alert_resolution_requires_note
test_summary_bullets_have_evidence_refs
test_end_to_end_l2_workflow
test_database_persists_after_reopen
```

验收命令应写入 README，例如：

```bash
python -m pytest -q
streamlit run app.py
```

## 13. 实施里程碑

### M0：仓库与可运行骨架

- 创建目录、依赖、README、`.env.example`、`.gitignore`；
- Streamlit 首页可启动；
- SQLite初始化和Demo重置可用；
- 测试框架运行成功。

完成条件：新环境按照README可以启动。

### M1：数据模型与患者提交

- 实现所有核心表；
- Seed合成患者；
- 患者页面可提交消息；
- 消息和审计事件持久化。

完成条件：重启后消息仍存在。

### M2：抽取与证据

- 实现 `AIExtractor` 和 `MockExtractor`；
- 处理正常、L2、L4及否定表达；
- UI展示结构化字段和证据高亮；
- 完成抽取测试。

完成条件：固定输入产生确定结果。

### M3：规则、Alert与护士处理

- 实现纯函数规则引擎；
- 创建Alert和SLA；
- 实现护士操作和强制备注；
- 实现模拟飞书卡片；
- 完成规则与工作流测试。

完成条件：L2和L4从患者端贯通到护士端。

### M4：Summary与医生端

- 生成有证据链的Summary；
- 展示趋势、Alert处理、缺失和待确认；
- 支持医生审阅；
- 完成证据链测试。

完成条件：完整L2故事可从头演示到尾。

### M5：演示打磨

- 三个预置场景一键重置；
- 修复布局、长文本裁切和移动端展示；
- 增加醒目的合成数据/非诊断声明；
- 添加截图；
- 编写60–90秒和2–3分钟两个演示脚本。

完成条件：连续演示三次无错误。

### M6：入围后的飞书适配

获得飞行社账号和权限后再做：

- `AilyExtractor`：输出与 `MockExtractor` 相同Schema；
- `BitableStore`：映射患者、随访、Alert、Summary；
- `FeishuBotNotifier`：护士告警和医生摘要卡片；
- Webhook回调：护士操作写回业务服务；
- 添加 `FEISHU_ENABLED=false` 功能开关；
- 保留Mock模式，保证无Token仍可演示。

完成条件：同一端到端测试在Mock模式和飞书模式下都能通过核心业务断言。

## 14. Codex执行规则

将本文件放入新工作区后，给Codex以下要求：

1. 开始前完整阅读本文件。
2. 严格按M0→M5顺序实施，一次只推进一个里程碑。
3. 每个里程碑先检查现有文件，避免覆盖用户改动。
4. 所有文件修改使用小而可审查的补丁。
5. 每个里程碑结束必须运行相关测试和启动检查。
6. 不得因缺少飞书Token阻塞M0–M5。
7. 不得使用真实患者数据。
8. 不得声称Mock结果来自真实大模型或真实飞书集成。
9. 不得加入诊断、治疗或用药建议。
10. 如果需求与本文件冲突，以用户最新明确指令为准，并记录变更。

## 15. 可复制给新Codex工作区的启动提示词

```text
请完整阅读根目录的 CODEX_DEMO_IMPLEMENTATION_HANDOFF.md，并以它作为本项目的实施规范。

现在从 M0 开始，依次实现 M0-M5。不要等待或依赖飞书/Aily账号；第一版必须在没有任何外部API Key时完整运行。核心医疗工作流、规则、证据链和持久化必须是真实实现，飞书通知使用明确标注的Mock适配器。

每完成一个里程碑：
1. 运行相关pytest；
2. 启动Streamlit做最小烟雾检查；
3. 汇报完成内容、测试结果和剩余风险；
4. 然后继续下一里程碑，直到M5完成或遇到必须由用户提供权限/信息的真实阻塞。

不要使用真实患者数据，不要生成诊断、治疗或用药建议，不要伪造评测数字或飞书联调状态。
```

## 16. GitHub策略

### 16.1 是否需要GitHub

建议使用。原因不是“代码越多越好”，而是GitHub可以提供：

- 一个稳定、免登录的评委访问入口；
- 可验证的开发过程和提交时间；
- README、截图、Demo视频、短版PDF的统一索引；
- 入围后继续迭代的基础；
- 附件链接失效时的备用入口。

但GitHub必须整洁。一个充满临时文件、密钥和未完成代码的仓库会降低可信度。

### 16.2 推荐的公开/私有拆分

优先方案：

```text
公开 showcase 仓库
├── README
├── 合成数据Demo
├── 截图和视频链接
├── 短版PDF
├── 架构与安全说明
└── 可运行的基础代码

私有开发仓库（如需要）
├── 未完成实验
├── 模型提示词迭代
├── 可能涉及商业细节的材料
└── 后续真实集成代码
```

如果团队不介意开源原型，可以只使用一个公开仓库。若在意知识产权，可以公开文档、截图和精简Demo，核心实现保留私有。

### 16.3 GitHub必须上传

- 清晰的 `README.md`；
- 30秒内能理解的一句话定位；
- Demo GIF或视频链接；
- 3–5张关键截图；
- 架构图；
- 快速启动命令；
- 合成数据声明；
- 医疗安全边界；
- 当前完成/未完成能力清单；
- `.env.example`；
- 测试命令与最新真实测试结果；
- 短版评审PDF；
- 技术附录链接。

### 16.4 GitHub禁止上传

- 任何真实患者数据；
- `.env`、API Key、飞书App Secret、Token；
- 本地数据库文件；
- 聊天日志中的个人信息；
- 临时渲染文件、缓存、虚拟环境；
- 未经核实的性能数字；
- 写着“已完成飞书集成”但实际仍为Mock的内容；
- 大量无说明的生成文件。

`.gitignore` 至少包含：

```gitignore
.env
.venv/
__pycache__/
.pytest_cache/
*.pyc
*.db
*.sqlite
data/
tmp/
.DS_Store
```

如果需要保留合成样本，单独放在 `fixtures/`，不得依赖被忽略的运行数据库。

### 16.5 README首屏顺序

README开头建议固定为：

1. 项目名与一句话定位；
2. Demo视频/GIF；
3. 三个核心价值点；
4. 三步业务闭环图；
5. 本地运行；
6. 截图；
7. 安全边界；
8. 当前状态与Roadmap；
9. 文档索引。

不要让评委先阅读长篇背景。

### 16.6 提交与版本

建议在开题提交前创建一个可追溯版本：

```text
tag: v0.1-opening-report
```

入围并接入飞书后创建：

```text
tag: v0.2-feishu-demo
```

提交信息应描述真实变化，例如：

```text
feat: add deterministic L2 alert workflow
test: cover negated symptom extraction
docs: add synthetic-data and safety boundaries
```

不要为了制造活跃度拆出大量无意义提交。

### 16.7 开源许可证

不要默认替团队选择许可证：

- 如果愿意开放原型代码，可选择MIT或Apache-2.0；
- 如果仅希望评委查看但暂不授权复用，可以公开仓库但暂不添加开源许可证，并在README说明版权；
- 如果竞赛规则对成果授权有要求，以赛事规则为准。

## 17. 开题前的最小交付清单

即使完整Demo尚未完成，开题提交前至少准备：

- 两个必填文本框定稿；
- 8–12页短版PDF；
- 本文件中的业务闭环图；
- 三个关键页面线框或截图；
- 公开GitHub/落地页链接；
- 明确的“当前实现”和“入围后飞书接入”边界；
- 如能完成，附60–90秒平台无关Demo视频。

## 18. 最终验收脚本

交付前按以下顺序人工演示：

1. 点击“重置Demo”。
2. 进入患者页，提交正常路径，确认无L2。
3. 提交L2路径，确认Observation、证据和L2 Alert。
4. 进入护士页，确认收到，填写处理记录并关闭。
5. 进入医生页，生成Summary，展开证据。
6. 标记医生已审阅。
7. 进入审计页，确认完整事件链。
8. 重置后提交L4路径，确认固定急救提示和L4 Alert。
9. 重启应用，确认数据持久化。
10. 运行全部pytest并保存真实结果。

只有以上十步全部通过，才将Demo标记为“可提交”。
