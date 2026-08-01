# 16. 第二层验收报告：Care Engine 与 Questionnaire 驱动患者端

- 版本：0.2.0（2026-08-01）
- 验收结论：**第二层核心工程基线通过，可开始第三层受控语义理解；生产交互封板尚未通过。**

## 1. 第二层目标

第二层把第一层发布的静态 Pathway 契约变成一次可执行、可恢复、可验证的患者随访：

```text
Enrollment 中的 Pathway code
→ 锁定 Pathway / Questionnaire 版本
→ Care Engine 计算可见问题
→ 前端按 Questionnaire 动态渲染
→ 构造并校验 QuestionnaireResponse
→ 结构化答案确定性映射 Observation
→ 自由文本原样保留，供第三层后续处理
```

第二层不调用 LLM，不生成诊断、风险等级、报警或治疗建议。

## 2. 当前交付物

| 交付物 | 文件或目录 | 状态 |
|---|---|---:|
| 通用 QuestionnaireResponse Builder | `continucare/fhir/questionnaires.py` | 已实现当前题型 |
| Questionnaire 语义与跨资源校验 | `continucare/fhir/references.py` | 已扩展 |
| Care Session 模型与生命周期 | `continucare/models.py` | 已实现进行中、完成、停止 |
| Care Engine 编排 | `continucare/care_engine/service.py` | 已实现 |
| 确定性 Observation Mapper | `continucare/care_engine/mapping.py` | 已实现 |
| 版本化映射策略 | `continucare/pathways/mapping_artifacts/` | 已实现 GLP1-14D v1 |
| 会话与回答持久化 | `continucare/db.py`、`continucare/adapters/sqlite_store.py` | 已实现 |
| 动态患者端 | `pages/1_patient_followup.py` | 已实现 |
| 第二层自动化测试 | `tests/test_care_engine.py` | 已实现 |

## 3. 已完成能力

### 3.1 版本锁定与草稿恢复

- 从患者 Enrollment 的 `pathway_code` 选择治理清单；
- 会话开始时锁定 Pathway version、Questionnaire canonical 与 version；
- 同一患者刷新或重开数据库后恢复当前进行中会话；
- 草稿答案持久化，不产生临床 Observation；
- 已完成会话的重复提交采用幂等读取；不同答案不能覆盖已完成提交；
- 放弃草稿会进入 `stopped`，不形成临床事实。

### 3.2 Questionnaire Renderer

当前患者端直接读取 FHIR Questionnaire，不复制临床问题和选项，已支持：

- `boolean`：是、否和暂不回答；
- `choice`：按 `answerOption` 渲染；
- `integer`、`decimal`：数值输入；
- `quantity`：数值与当前路径锁定单位；
- `text` / `string`：原始补充说明；
- `enableWhen`：恶心为真时显示程度问题，为假时隐藏并清除残留答案；
- 填写进度、草稿保存、版本提示、提交确认和移动端响应式布局。

“动态”只表示依据 Questionnaire 和现有答案确定性显示或隐藏，不表示 RAG/LLM 在患者填写时临时创造问题。

### 3.3 通用回答构造与校验

- Builder 根据 Questionnaire item type 选择 FHIR `answer.value[x]`；
- 支持 boolean、choice、integer、decimal、quantity 和 text；
- 校验 canonical、version、linkId、答案类型、`answerOption`、`required`、`repeats` 和 `enableWhen`；
- 禁止给当前被禁用的问题提交答案；
- 非法选项、未知 linkId 和非法 Quantity 被 fail-closed 拒绝；
- 患者、会话、消息和 QuestionnaireResponse 的 subject 必须一致。

### 3.4 确定性 Observation 映射

映射策略与前端分离并随 Pathway 版本发布。当前结构化答案可以形成：

- 恶心存在：SNOMED CT `422587007`；
- 恶心程度：LOINC `81660-3` 与受控 LOINC Answer；
- 过去 24 小时呕吐次数：LOINC `94070-0`、UCUM `/d`；
- 过去 24 小时液体摄入：LOINC `75301-2`、UCUM `mL/(24.h)`；
- 腹痛存在：SNOMED CT `21522001`。

结构化答案不经过 Mock 抽取或 LLM。阴性症状在当前 `positive_only` 映射中不形成“存在”Observation；自由文本中的模糊数量也不会被自动编造。

### 3.5 持久化与来源链

- `care_sessions` 保存版本锁定、草稿和生命周期；
- 完成提交时，Message、QuestionnaireResponse、Observation 和会话状态在一个 SQLite 事务中写入；
- Observation 使用确定性 ID，防止同一回答重复生成；
- 每条 Observation 的 `derivedFrom` 指向来源 QuestionnaireResponse；
- 结构化按钮和数值答案标记为 `patient_confirmed`；
- 审计记录会话开始、草稿保存、停止和问卷完成事件。

## 4. 当前验收结果

```text
46 passed
Questionnaire: official HL7 R4 JSON Schema valid
PlanDefinition: official HL7 R4 JSON Schema valid
QuestionnaireResponse: official HL7 R4 JSON Schema valid
Observation: official HL7 R4 JSON Schema valid
三个合成场景连续彩排三次通过
```

浏览器验收已覆盖：

- 桌面端患者页面正常渲染；
- 选择“有恶心”后程度卡片出现；
- 切换到无恶心场景后程度卡片隐藏；
- 结构化“呕吐 1 次 + 液体摄入 800 mL”形成 2 条 Observation；
- 提交后明确显示 `not_assessed`；
- 390 px 移动端布局无横向溢出，卡片和操作按钮可用。

## 5. 安全边界

- 只接受已登记的合成患者；
- 当前 Pathway 保持 `draft / synthetic_only / not_reviewed`；
- `clinical_rules=[]`，所有输入保持 `not_assessed`；
- 第二层没有 LLM 依赖；
- 自由文本只原样保存，不在本层自动结构化；
- 外部模型、飞书和医院接口不可用时，结构化问卷仍可独立运行。

## 6. 尚未封闭的第二层事项

以下内容不阻止第三层开发，但阻止第二层生产封板：

1. 提交后的正式修改、撤回以及 QuestionnaireResponse/Observation 修订链；
2. Questionnaire 重复 group/item 的完整 UI 与回答构造；
3. 语音文件、转写来源和同意元数据持久化；
4. 正式随访排期、超时、提醒和多设备并发冲突处理；
5. 多患者身份验证、授权、隐私和真实 Enrollment；
6. 目标医院 Questionnaire Profile、单位约束和术语服务器；
7. 无障碍、浏览器矩阵、弱网、性能和正式用户可用性测试。

## 7. 验收判定

### Layer 2A：执行内核

**通过。** 版本锁定、草稿、Builder、校验、持久化和幂等提交已建立。

### Layer 2B：比赛患者端

**通过。** 当前 GLP1-14D 可以由 Questionnaire 动态驱动，并完成桌面和移动端合成演示。

### Layer 2C：生产患者交互

**未通过。** 仍需修订/撤回、语音、真实排期、多设备并发、身份授权和医院 Profile。

## 8. 下一步

第三层应直接消费第二层保存的 `free-text-report`，只输出受限结构化候选、证据位置和 `needs_clarification`。澄清只能返回第一层 Questionnaire 中已有的 `linkId`；最终 FHIR 构造仍由第二层已经建立的确定性 Builder 和映射边界完成。
