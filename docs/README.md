# AI Native Doctor Copilot 文档包

本文档包是项目的 v0.1 source of truth，用于比赛提交、团队开发、医院沟通和后续融资材料的基础沉淀。

项目定位：

> AI Native Doctor Copilot 是面向医院的连续照护操作系统。它不替代医生诊断和治疗，而是在患者离院后持续收集、理解、整理和总结健康状态，并在复诊前向医生提供可审阅、可解释、可追溯的患者连续健康记忆。

## 文档结构

| 文件 | 用途 |
|---|---|
| [00_product_overview.md](00_product_overview.md) | 产品定位、使命愿景、核心边界、竞品差异 |
| [01_prd.md](01_prd.md) | 完整产品需求文档、用户、场景、MVP范围、验收标准 |
| [02_system_architecture.md](02_system_architecture.md) | 系统架构、模块职责、数据流、部署和集成方式 |
| [03_data_model_fhir.md](03_data_model_fhir.md) | FHIR风格数据模型和核心实体关系 |
| [04_pathway_engine.md](04_pathway_engine.md) | Clinical Pathway Engine、规则、模板、审批和版本管理 |
| [05_ai_agents.md](05_ai_agents.md) | Guideline/Care/Memory/Risk/Summary/Safety Agent设计 |
| [06_safety_and_governance.md](06_safety_and_governance.md) | 医疗安全、证据链、急症流程、人工审批、审计 |
| [07_workbench_and_patient_app.md](07_workbench_and_patient_app.md) | 医生工作台、护士风险中心、患者端体验设计 |
| [08_demo_script.md](08_demo_script.md) | 比赛Demo故事线、页面、演示数据、讲解词 |
| [09_pitch_and_defense.md](09_pitch_and_defense.md) | 开题、创新点、商业价值、答辩PPT和评委问答 |
| [10_roadmap.md](10_roadmap.md) | MVP、比赛版、医院部署版、商业版路线图 |
| [11_wireframes_and_visuals.md](11_wireframes_and_visuals.md) | 系统图表清单、医生端/护士端/患者端低保真线框图 |

## 关键原则

1. AI不诊断、不改药、不决定治疗方案。
2. 医生负责照护路径确认、最终审批和临床决策。
3. 高风险判断由确定性规则和人工流程兜底，LLM只辅助理解和整理。
4. 所有临床相关输出必须有证据链、置信度和审计记录。
5. 系统围绕 Care Pathway 设计，而不是围绕单个疾病设计。

## 比赛命题对齐

比赛命题强调“贯穿诊前诊后的医生AI Copilot”，具体包括：

- 诊后替医护主动随访。
- 监测异常并进行分级。
- 起草人性化医患沟通。
- 院外数据沉淀后，在复诊前生成医生简报。
- 形成院外连续照护与院内精准决策互相喂养的闭环。

本项目以此为第一版落地方向，但将产品边界进一步收敛为“连续照护记忆与工作流系统”，避免被误解为问诊、诊断或自动治疗系统。

## 当前已有图表

文档包已包含 Figma/Figwright 风格的系统架构图、数据模型图、Pathway引擎图、Agent关系图、安全工作流图、时序图、页面信息架构图和中保真线框图。图表资产集中在 [assets](assets)，图表索引和页面规格集中放在 [11_wireframes_and_visuals.md](11_wireframes_and_visuals.md)。
