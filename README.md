# ContinuCare Demo

把合成患者的院外随访输入保存为 FHIR R4 `QuestionnaireResponse`，将有明确标准语义的内容沉淀为可追溯的 `Observation`，再形成医生复诊前可审阅的证据简报。

> **安全边界：仅使用合成数据。系统不是医疗急救通道，不生成诊断、治疗或用药建议。**

当前版本默认运行在“本地稳定演示模式”：数据写入本地 SQLite，抽取使用明确标注的本地 Mock，飞书通知使用明确标注的 Mock 适配器。无需任何外部 API Key。

## 本地运行

要求 Python 3.11+。

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q
.venv/bin/streamlit run app.py
```

打开 Streamlit 输出的本地地址即可进入首页。运行数据默认保存在 `data/continucare.db`，该目录已被 Git 忽略。

## 三个核心价值

- 原始回答与 FHIR Observation 通过 `derivedFrom` 可追溯；
- LOINC、SNOMED CT 与 UCUM 映射有独立权威信源包；
- 没有获批临床规则时采取 fail-closed，不输出风险等级或 Alert；
- Summary 审阅和 Mock 通知进入本地审计链。

## 页面

- 首页：先展示最终交付物，以及患者 → 护士 → 医生的闭环；
- 患者随访：先展示本次结果、患者原话、记录事实和明确下一步；
- 护士任务中心：先展示今天要处理什么、为什么进入队列以及任务最终结果；
- 医生复诊简报：首屏用 30 秒呈现“患者报告、团队处理、复诊待确认”；
- 工作流证据链：用人类可读的六阶段时间线还原结果形成过程，技术记录按需展开。

## 临床与标准依据

- [整体六层方案与端到端工作流](docs/14_layered_solution_blueprint.md)
- [第一层验收报告](docs/15_layer_1_acceptance.md)
- [FHIR R4 合规策略与上线门槛](docs/13_fhir_conformance_policy.md)
- [GLP-1 指标、术语映射与权威临床信源](docs/clinical/glp1_14d_observation_evidence.md)
- [当前 FHIR 数据模型](docs/03_data_model_fhir.md)

`assets/screenshots` 中旧截图属于早期工作流原型，包含已停用的合成 L2/L4 规则，不作为当前实现或临床依据。

## 当前实施范围

- M0–M5：本地无 Key 闭环，按 [实施交接规范](CODEX_DEMO_IMPLEMENTATION_HANDOFF.md) 实现
- M6：真实飞书/Aily 接入，明确不在第一版范围内

## 演示

- [60–90 秒与 2–3 分钟脚本](docs/demo_scripts.md)
- [安全边界](docs/implementation_safety.md)
- [实施架构](docs/implementation_architecture.md)
- [飞书 / Aily 集成状态](docs/feishu_integration.md)
- 连续三次无外部服务彩排：`.venv/bin/python scripts/rehearse_demo.py`

## 验收命令

```bash
curl -L https://hl7.org/fhir/R4/fhir.schema.json.zip -o /tmp/fhir-r4-schema.zip
FHIR_R4_SCHEMA_ZIP=/tmp/fhir-r4-schema.zip .venv/bin/python -m pytest -q
.venv/bin/python scripts/validate_fhir_r4.py --schema /tmp/fhir-r4-schema.zip
.venv/bin/streamlit run app.py
```

所有演示身份、消息和结果均为合成数据。禁止把运行数据库、密钥或真实患者信息提交到仓库。
