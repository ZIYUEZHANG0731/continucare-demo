# ContinuCare Demo

用版本化 FHIR R4 `Questionnaire` 动态驱动合成患者随访，并通过受控 Care Agent 把患者自由表达整理成待确认候选；患者确认后，第二层将答案保存为完整 `QuestionnaireResponse`，把明确事实确定性沉淀为可追溯的 `Observation`。

> **安全边界：仅使用合成数据。系统不是医疗急救通道，不诊断、不治疗、不分诊，也不生成用药建议。**

当前是仅使用合成数据的本地 Web 原型，不是原生 App、临床试点或生产系统。患者端、护士端和医生端均已提供独立 React 界面：患者与护士由同源 Starlette 服务承载，医生端由独立的受控服务承载；三端通过同一个 SQLite 事实库和版本化记录链协作。医生确认方案会原子地开启患者随访，患者最终确认会生成护士任务，护士人工上报会进入医生端“护理协作”待办。Streamlit 仅保留为综合演示壳、追溯和资料库页面。主故事仍按五步展开：医生开启 → 患者表达与确认 → 护士核对 → 医生查看上报 → 记录追溯。进度从 SQLite 事实恢复，不依赖浏览器 session state。Knowledge 是独立资料库，不属于五步完成度。完整设计见 [M5-D 稳定的一键比赛 Demo](docs/28_m5_d_competition_demo.md)。

当前版本默认接入火山方舟豆包 OpenAI-compatible 接口，使用 `doubao-seed-2-0-lite-260215` JSON mode，分别承担受控抽取、Safety Critic 和患者语言改写；原小米 MiMo 配置仍兼容。主抽取不可用时回退本地语义 Mock，辅助模型不可用时回退确定性硬规则或固定语言模板。无论哪种模式，Safety Agent 和患者确认门都不能绕过。

M5-E 增加了可选飞书 Bot、Aily 和 Bitable 协议适配器、统一配置工厂与 FakeTransport 合同测试。默认配置为飞书/Aily `mock`、Bitable `disabled`，不读取 Token、不创建真实 transport、不认证、不探活、不发送或写入；运行时 `SEND_ENABLED=False`，没有真实外部发送。代码已实现且 FakeTransport 合同已验证；真实租户验证和生产可用性均为否。详见 [飞书 / Aily 集成状态](docs/feishu_integration.md) 与 [M5-E 设计验收](docs/29_m5_e_optional_feishu_aily_adapters.md)。

Knowledge v2 alias readiness 已合入代码主线，但 alias UI consumer integration 尚未实施；当前 Knowledge 页面仍展示既有四主题离线 bundle。Knowledge 保持 `knowledge_effect=informational_only`、`runtime_authority=none`，不授权运行时动作。

## 本地运行

要求 Python 3.11+ 和 Node.js 20+。

先构建两个前端：

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
cd patient-web
npm install
npm run build
cd ..
cd doctor-web
npm install
npm run build
cd ..
```

在两个终端中使用同一个数据库路径启动服务：

```bash
CONTINUCARE_DB_PATH=data/continucare.db .venv/bin/python -m continucare.patient_web
CONTINUCARE_DB_PATH=data/continucare.db .venv/bin/python -m continucare.doctor_web
```

三个入口分别为：患者端 `http://127.0.0.1:8510/`、护士端 `http://127.0.0.1:8510/nurse`、医生端 `http://127.0.0.1:8520/`。演示时先在医生端确认方案，再到患者端提交，护士上报后可直接从护士端打开医生协作待办。浏览器只接收面向相应角色的中文内容；所有写操作由服务端重新校验当前状态和角色边界。

如需运行仍保留的综合 Streamlit 演示壳：

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q
.venv/bin/streamlit run app.py
```

运行数据默认保存在 `data/continucare.db`，该目录已被 Git 忽略。

普通离线测试不下载外部文件；未设置 `FHIR_R4_SCHEMA_ZIP` 时，依赖 HL7 官方 JSON Schema 的 3 项测试会明确标记为 skipped。比赛提交或发布验收不能把这些 skip 当作通过，必须按下方“验收命令”下载并核对固定哈希后重新运行全量测试。

## 火山方舟豆包配置

复制 `.env.example` 为被 Git 忽略的 `.env`，只在本机填入轮换后的密钥：

```dotenv
CONTINUCARE_LLM_PROVIDER=volcengine_doubao
CONTINUCARE_LLM_MODEL=doubao-seed-2-0-lite-260215
CONTINUCARE_LLM_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
CONTINUCARE_LLM_API_KEY_ENV=ARK_API_KEY
ARK_API_KEY=your-rotated-key
CONTINUCARE_LLM_PROMPT_VERSION=doubao-semantic-extraction-v1
CONTINUCARE_USE_SAFETY_LLM=true
CONTINUCARE_SAFETY_PROMPT_VERSION=doubao-safety-critic-v1
CONTINUCARE_USE_LANGUAGE_LLM=true
CONTINUCARE_LANGUAGE_PROMPT_VERSION=doubao-language-rewrite-v1
CONTINUCARE_USE_SUMMARY_LLM=false
CONTINUCARE_SUMMARY_PROMPT_VERSION=doubao-summary-outline-v1
CONTINUCARE_LLM_TIMEOUT_SECONDS=60
```

最小联通测试：

```bash
.venv/bin/python scripts/mimo_smoke_test.py
```

原 MiMo 冻结基线的 10 例合成文本回归（仅用于兼容性复验，不适用于当前豆包配置）：

```bash
.venv/bin/python scripts/evaluate_mimo_live.py --fail-on-mismatch
```

评测报告默认写入 `/tmp/continucare-mimo-live-evaluation.json`。该结果属于工程验证，不能替代临床验证。

同一受支持模型配置用于 Care 抽取、Safety Critic 与患者语言改写，但三者使用独立 Prompt 和严格 JSON 合同。Safety LLM 只能降级候选，不能推翻确定性硬规则；它发现有逐字证据的遗漏时，只能触发已发布 linkId 的定向补抽取。第三层以每日随访 occurrence 保存本次全部对话，并把已完成 Observation 作为只读跨日上下文；历史记录不能成为今天候选的证据。“今天/昨天/过去24小时”按患者时区解析并贯通到 Observation `effective[x]`。语言改写会本地锁定数字、单位、症状、程度、肯否和时间，不满足事实完整性时自动回退固定模板。

第四层另提供默认关闭的受控 Summary LLM：它面对任意数量的动态事实清单，只能返回已有 `fact_id` 的分组和顺序，不能生成正文、风险或建议。事实遗漏、伪造 ID、跨栏目移动、模型故障或超过容量门时，系统使用完整事实清单确定性回退。

受控 Summary 已使用官方 MiMo API 完成固定合成数据验收：5/5 用例、64/64 条事实恰好覆盖一次，包括未知医生自定义指标、事实文本提示注入、40 指标容量场景和完整服务/存储/Provenance 链路。该结果是工程验收，不是临床验证；提交配置仍默认关闭真实 Summary 调用。

MiMo 不生成医学代码。中国 GLP1-14D 的定时随访只加载由 L1 Release 编译的 5 个固定 Questionnaire `linkId` 白名单，并锁定 knowledge release 与白名单 SHA-256。完成定时随访后的“随时补充上报”使用一个显式的合成演示复合边界：固定 5 项仍委托中国 L1 白名单；Pathway 外患者原话只可检索 `continucare/terminology/data/glp1_symptom_catalog_v1.json` 的原型概念。后者必须经患者选定/确认，写入独立补充 QuestionnaireResponse 和 Observation，并保留目录版本、SHA-256、`draft-prototype-verified` 与“目标医院待验证”状态；它不继承中国 knowledge release 或 Observation Mapping，也不能被宣称为中国产品证据。两套目录都未安全命中时只保存患者确认原话，不伪造 Observation。

2026-08-02 的冻结配置 10 例单轮合成数据评测达到业务结果 10/10、完整三 Prompt 链路 10/10、原始模型输出 10/10。Layer 3 工程发布清单、原始报告和 Git 回滚标签固定为 `continucare-layer3-v1.0.0` / `layer3-v1.0.0`。该结果是工程回归，不是临床验证。

密钥不会进入 AgentRun、审计日志或 Git。当前只允许官方 `*.xiaomimimo.com` HTTPS 地址，并且只发送合成演示文本。[MiMo 官方快速接入](https://mimo.mi.com/docs/en-US/quick-start/summary/first-api-call) · [JSON mode](https://mimo.mi.com/docs/en-US/quick-start/usage-guide/text-generation/structured-output)

## 可选飞书 / Aily / Bitable 配置

`.env.example` 的安全默认值是：

```dotenv
CONTINUCARE_FEISHU_MODE=mock
CONTINUCARE_AILY_MODE=mock
CONTINUCARE_BITABLE_MODE=disabled
CONTINUCARE_EXTERNAL_EGRESS_ENABLED=false
```

仅将 mode 改为 `test_tenant` 不足以创建外部 client；还必须同时设置对应 capability flag、全局 egress flag 和完整配置。偶然存在凭据不会自动启用。`test_tenant` 配置缺失时 fail-closed。当前仓库没有 `production` 模式；本轮也未用真实凭据或调用任何外部 API。

## 核心价值与工程保障

- 原始回答与 FHIR Observation 通过 `derivedFrom` 可追溯；
- 患者端由 Questionnaire 动态渲染，结构化答案不依赖 LLM；
- 对话式自由表达只生成候选，患者确认前不写入第二层；
- Agent 输出通过 linkId/code、enableWhen、证据跨度、候选值、主语、否定、时间和单位安全检查；
- 随访会话锁定 Pathway/Questionnaire 版本，支持草稿恢复和幂等提交；
- LOINC 2.82 与 UCUM 映射已锁定；SNOMED CT 固定代码仅限合成工程测试，Edition、中国适用性和许可仍待核验；
- 定时随访只使用中国固定白名单；合成补充上报可使用明确标记为原型、待医院验证的动态症状目录，未安全命中则不生成 Observation；
- 没有获批临床规则时采取 fail-closed，不输出风险等级或 Alert；
- Summary 审阅和 Mock 通知进入本地审计链。

## 页面

- 合成演示导览：展示五步故事的当前角色、当前步骤和下一步；
- 我的随访：展示患者原话、系统记法和明确的确认选择；
- 护士安全复核台（独立网页 `/nurse`）：人工检查中文随访答案，标记异常并决定继续观察或上报医生；软件不设未经批准的临床阈值、不自动预警、不代替护士判断；
- 复诊速览：分开呈现患者确认的事实、护理动作和尚未提供临床评估的边界；
- 记录追溯：用人类可读的中文说明记录如何形成或停止，技术详情按需展开；
- Knowledge 资料库 / 症状采集参考：独立只读，不读取患者故事，也不参与五步完成判定。

## 临床与标准依据

下载资料已接入版本化的中国 GLP-1 L1 知识层 `cn-glp1-l1-v1.0.3`：运行时读取结构化 JSON 和编译后的 FHIR 契约，不直接解析 PDF/ZIP。当前按批准文号登记 15 条中国产品记录：6 条 `verified`，9 条 `incomplete`。穆峰达 8 条和度易达 2 条已绑定现行中文说明书，但穆峰达一次性预填充笔 4 条还缺文号—规格逐项原子证据；诺和盈 5 条仍缺最新完整说明书。GLP1-14D 问卷和 Observation Mapping 已绑定 `metric_id`、`evidence_claim_id` 与知识版本；PRO-CTCAE 只保留来源和 11 个非运行指标元数据，许可范围确认前公开版本不包含原文或衍生 Questionnaire。CTCAE、FDA 标签和 FAERS 不进入自动临床判断。

- [中国 GLP-1 L1 知识版本与运行边界](docs/clinical/cn_glp1/README.md)

- [整体六层方案与端到端工作流](docs/14_layered_solution_blueprint.md)
- [第一层验收报告](docs/15_layer_1_acceptance.md)
- [第二层验收报告](docs/16_layer_2_acceptance.md)
- [第三层验收报告](docs/17_layer_3_acceptance.md)
- [第三层 v1.0.0 发布基线与回滚说明](docs/releases/layer3_v1.0.0.md)
- [第四层第 1 步：合同与存储](docs/18_layer_4_contract_and_storage.md)
- [第四层第 2 步：Clinical Memory 与 Timeline](docs/19_layer_4_clinical_memory.md)
- [第四层第 3 步：双审批规则与 Task 责任闭环](docs/20_layer_4_approved_rules_and_tasks.md)
- [第四层第 4 步：证据化 Summary 与医生审阅](docs/21_layer_4_evidence_summary_and_doctor_review.md)
- [第四层第 5 步：状态快照与原始数值趋势](docs/22_layer_4_state_snapshot_and_numeric_trends.md)
- [第四层第 6 步：Doctor Workbench 只读组合查询与整体回放](docs/23_layer_4_doctor_workbench_read_model.md)
- [第四层增强项：指标数量无关的受控 LLM Summary](docs/24_layer_4_controlled_llm_summary.md)
- [FHIR R4 合规策略与上线门槛](docs/13_fhir_conformance_policy.md)
- [GLP-1 指标、术语映射与权威临床信源](docs/clinical/glp1_14d_observation_evidence.md)
- [GLP-1 患者可报告症状术语目录与检索流程](docs/clinical/glp1_symptom_terminology_catalog.md)
- [当前 FHIR 数据模型](docs/03_data_model_fhir.md)

`assets/screenshots` 中旧截图属于早期工作流原型，包含已停用的合成 L2/L4 规则，不作为当前实现或临床依据。

## 当前实施范围

- 第一层工程基线：FHIR R4 契约、术语、证据、追溯和 fail-closed 治理
- 第二层比赛工程基线：Care Session、动态 Questionnaire renderer、通用回答 Builder 和确定性 Observation 映射
- 第三层比赛工程基线：Agent Runtime、MiMo/Care Agent 语义候选、Safety Agent v4 混合复核、受控连续对话、患者本地时间、事实锁定语言改写、患者确认与本地回退
- 第四层第 1 步工程基线：最终 Observation 状态门禁、Memory/Timeline/Revision/规则/Summary 合同、FHIR Task/Communication/Provenance 和版本化完整 JSON 存储；规则执行仍保持关闭
- 第四层第 2 步工程基线：最终资源确定性摄取、证据化 Clinical Memory、按临床有效时间组织的 Timeline、迟到数据重排、冲突/缺失表达和可追溯修订；规则执行仍保持关闭
- 第四层第 3 步工程基线：双审批规则执行门、逐条件证据解释、Task 去重、版本化责任状态机及 Clinical Memory 历史；仓库无 active 临床规则，产品路径仍为 not_assessed
- 第四层第 4 步工程基线：当前 Timeline 的确定性证据简报、生成时点门、Summary 版本链及医生接受/修改/拒绝；LLM 摘要仍未启用
- 第四层第 5 步工程基线：版本化指标定义、current/stale/unknown/conflict 状态、单位一致的端点数值方向、快照版本链及 Provenance；不输出好转/恶化或风险解释
- 第四层第 6 步工程基线：Timeline/State/Summary/Task 只读组合查询、patient/pathway 权限隔离、历史 as-of 回放、版本化证据图及组件级故障降级；真实 IAM/EMR 仍未接入
- 旧 M0–M5 自由文本链继续作为兼容测试夹具，不是患者端主流程
- M5-E：可选飞书/Aily/Bitable 合同、FakeTransport 与零 Token Mock fallback；真实租户联调仍未进行
- M6：真实租户验收、回调与医院集成，仍不在本轮范围内

## 演示

- [60–90 秒与 2–3 分钟脚本](docs/demo_scripts.md)
- [安全边界](docs/implementation_safety.md)
- [实施架构](docs/implementation_architecture.md)
- [飞书 / Aily 集成状态](docs/feishu_integration.md)
- 连续三次无外部服务彩排：`.venv/bin/python scripts/rehearse_demo.py`

## 验收命令

以下命令均从仓库根目录运行。FHIR R4 Schema 是固定版本的外部验收材料，不提交进仓库；先验证 SHA-256，成功后才运行依赖它的测试：

```bash
FHIR_R4_SCHEMA_PATH=/tmp/fhir-r4-schema.zip
FHIR_R4_SCHEMA_SHA256=75e5560da3cf503895a44c8ca7af17a83b4cca6c2cb5ba1883d2aec0d1cb5ac6

curl --fail --location --retry 3 \
  https://hl7.org/fhir/R4/fhir.schema.json.zip \
  --output "$FHIR_R4_SCHEMA_PATH"
printf '%s  %s\n' "$FHIR_R4_SCHEMA_SHA256" "$FHIR_R4_SCHEMA_PATH" \
  | shasum -a 256 --check

FHIR_R4_SCHEMA_ZIP="$FHIR_R4_SCHEMA_PATH" \
  .venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/python -m scripts.validate_fhir_r4 \
  --schema "$FHIR_R4_SCHEMA_PATH"

# 公开 checkout：先从受控 JSON 重建，再核验
.venv/bin/python -m scripts.validate_cn_glp1_knowledge --skip-source-files
.venv/bin/python -m scripts.build_cn_glp1_knowledge
.venv/bin/python -m scripts.build_cn_glp1_knowledge --check

# 持有受控本地 source pack 时，再做原件哈希核验
.venv/bin/python -m scripts.check_cn_glp1_sources

.venv/bin/python -m scripts.evaluate_semantic_layer
npm --prefix patient-web run build
.venv/bin/python -m continucare.patient_web
.venv/bin/streamlit run app.py
```

哈希检查必须输出 `/tmp/fhir-r4-schema.zip: OK`。全量 pytest 必须以退出码 0 完成，且官方 Schema 可用时不应再出现上述 3 个 skip；独立校验器应逐项输出 `valid`。任一步失败都应停止验收，不得仅根据一次新下载结果修改仓库中的固定哈希。

`/tmp` 可能在重启或系统清理后被删除；发生这种情况时重新执行下载和哈希检查即可。

所有演示身份、消息和结果均为合成数据。禁止把运行数据库、密钥或真实患者信息提交到仓库。比赛包必须从已审查的 Git 文件集合用 `git archive` 或显式 allowlist 生成，不能对当前工作区执行递归 `zip`，因为本地 `output/` 可能包含受限核验原件和历史候选包。
