# Layer 3 v1.0.0 发布基线

## 发布标识

- Release：`continucare-layer3-v1.0.0`
- Git 回滚标签：`layer3-v1.0.0`
- 范围：受控自然语言理解、Safety、语言改写、术语检索、连续对话、时间解析和患者确认门
- 边界：合成数据工程基线，不是临床验证或医院上线批准

运行时的不可变清单位于 `continucare/care_agent/release.py`。测试会逐项核对实际 Agent、模型适配器、Prompt、FHIR 和术语目录版本，防止文档与代码漂移。真实 MiMo 评测脚本在配置版本与发布清单不一致时直接失败。

## 冻结版本

| 项目 | 版本 |
|---|---|
| Care Agent | `care-agent-v2` |
| Safety Agent | `safety-agent-hybrid-v4` |
| MiMo Adapter | `xiaomi-mimo-openai-v4` |
| Safety Critic | `mimo-safety-critic-v2` |
| Language Rewriter | `mimo-language-rewriter-v1` |
| 模型 | `mimo-v2.5` |
| 抽取 Prompt | `mimo-semantic-extraction-v4` |
| Safety Prompt | `mimo-safety-critic-v2` |
| Language Prompt | `mimo-language-rewrite-v1` |
| GLP-1 术语目录 | `1.0.0` |
| FHIR | `R4 4.0.1` |

## 冻结评测

- 官方 FHIR R4 Schema：`89 passed`，0 skipped；四类独立样例资源全部通过；
- 离线语义契约：8/8；
- 真实 MiMo 合成评测：业务结果 10/10，完整三 Prompt 链路 10/10，原始输出 10/10；
- MiMo 用量：34,228 tokens；平均端到端延迟 8,468 ms；
- 真实评测曾检出并修复两个编排问题：带单位的中文数字答案归一化，以及他人主语被 Safety Critic 误报为患者遗漏项。

原始机器可读报告：

- `docs/evaluations/layer3_v1.0.0_fhir_r4_schema.json`
- `docs/evaluations/layer3_v1.0.0_offline.json`
- `docs/evaluations/layer3_v1.0.0_mimo_live.json`

## 第四层输入边界

第四层必须通过 `continucare.layer4.Layer4InputReader` 获取数据，只能收到：

1. 与 `completed CareSession` 关联且状态为 `completed` 的最终 QuestionnaireResponse；
2. 从上述最终 QuestionnaireResponse 派生的最终 Observation；
3. 持久化 AuditEvent。

该端口不暴露聊天轮次、SemanticCandidate、CandidateIssue、AgentRun、模型原始输出或 Mock 正则中间结果。边界测试会把 `list_agent_runs` 和普通 `list_observations` 设置为失败调用，以证明第四层读取过程不依赖它们。

## 回滚方式

标签 `layer3-v1.0.0` 指向完整通过上述验收的提交。需要复现或回滚时，应部署该标签，或从标签创建恢复分支：

```bash
git fetch --tags origin
git switch -c restore/layer3-v1.0.0 layer3-v1.0.0
```

不要通过复制旧 Prompt 或切回 Mock 内部实现来“逻辑回滚”；发布单元是标签所指向的完整代码、目录、Prompt 和评测报告。
