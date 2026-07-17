# ContinuCare Demo

把合成患者的院外随访输入转化为可追溯 Observation、确定性工作流优先级和复诊前证据简报。

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

- 原文证据与结构化 Observation 双向可追溯；
- L0/L2/L4 由确定性规则产生，不把治理等级交给模型；
- Alert 处理、Summary 审阅和 Mock 通知全部进入本地审计链。

## 页面

- 患者随访：合成文本、结构化抽取、证据高亮、固定 L4 提示；
- 护士风险中心：Alert、SLA、Mock 飞书卡片和带记录的状态流转；
- 医生复诊简报：14 天证据摘要、逐条 Evidence 和审阅留痕；
- 审计日志：完整工作流事件时间线。

## 关键截图

以下截图由本地 Streamlit Demo 真实渲染生成，内容均为合成数据。

### 患者原文证据与 Observation

![患者 L2 原文证据与结构化 Observation](assets/screenshots/02_patient_l2.jpg)

### 护士 Alert、证据与 Mock 飞书卡片

![护士 L2 Alert 与明确标注的 Mock 飞书卡片](assets/screenshots/03_nurse_l2.jpg)

### 医生 Summary 证据链

![医生复诊简报展开 evidence_refs](assets/screenshots/04_doctor_summary.jpg)

### 审计事件链

![按时间倒序展示的审计事件](assets/screenshots/05_audit_log.jpg)

### L4 固定急救提示

![L4 固定急救提示和原文证据](assets/screenshots/06_patient_l4.jpg)

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
.venv/bin/python -m pytest -q
.venv/bin/streamlit run app.py
```

所有演示身份、消息和结果均为合成数据。禁止把运行数据库、密钥或真实患者信息提交到仓库。
