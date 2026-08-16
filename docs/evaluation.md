# 本地验证说明

验证覆盖 FHIR R4 基础资源结构、资源间追溯关系、Questionnaire 动态回答语义、Care Session、确定性 Observation 映射、合成固定场景和应用工作流，不代表临床性能或医院接入认证。

发布验收必须固定并核对官方 FHIR R4 Schema 哈希，不能把未提供 Schema 时的 3 个 skip 视为通过：

```bash
curl --fail --location --retry 3 \
  https://hl7.org/fhir/R4/fhir.schema.json.zip \
  --output /tmp/fhir-r4-schema.zip
printf '%s  %s\n' \
  '75e5560da3cf503895a44c8ca7af17a83b4cca6c2cb5ba1883d2aec0d1cb5ac6' \
  '/tmp/fhir-r4-schema.zip' \
  | shasum -a 256 --check
FHIR_R4_SCHEMA_ZIP=/tmp/fhir-r4-schema.zip \
  .venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/python -m scripts.validate_fhir_r4 \
  --schema /tmp/fhir-r4-schema.zip

# 公开 checkout：从受控 JSON 重建并核验知识发布包
.venv/bin/python -m scripts.validate_cn_glp1_knowledge --skip-source-files
.venv/bin/python -m scripts.build_cn_glp1_knowledge
.venv/bin/python -m scripts.build_cn_glp1_knowledge --check

# 只有持有受控本地 source pack 时才运行原件哈希核验
.venv/bin/python -m scripts.check_cn_glp1_sources

.venv/bin/python scripts/evaluate_semantic_layer.py --output docs/evaluations/layer3_v1.0.0_offline.json
.venv/bin/python scripts/rehearse_demo.py
npm --prefix patient-web run build
npm --prefix doctor-web run build
.venv/bin/python scripts/start_demo.py --check
```

哈希命令必须输出 `/tmp/fhir-r4-schema.zip: OK`；随后全量 pytest 应退出 0，且不再出现上述 3 个 Schema skip。`check_cn_glp1_sources` 依赖不公开的受控源文件包，公开 checkout 缺少该包时必须如实记录，不能用跳过结果替代原件核验。

Layer 3 v1.0.0 的真实 MiMo 评测还会校验模型及三个 Prompt 是否与发布清单完全一致，不一致时拒绝运行：

```bash
CONTINUCARE_LLM_PROMPT_VERSION=mimo-semantic-extraction-v4 \
CONTINUCARE_SAFETY_PROMPT_VERSION=mimo-safety-critic-v2 \
CONTINUCARE_LANGUAGE_PROMPT_VERSION=mimo-language-rewrite-v1 \
.venv/bin/python scripts/evaluate_mimo_live.py \
  --output docs/evaluations/layer3_v1.0.0_mimo_live.json \
  --fail-on-mismatch
```

自动化断言包括：

- Questionnaire、QuestionnaireResponse、Observation、PlanDefinition 通过严格 R4 模型；
- 所有受控样例通过 HL7 官方 R4 JSON Schema；
- 未知 FHIR 字段和缺少必填字段会被拒绝；
- QuestionnaireResponse canonical、linkId 和回答类型与 Questionnaire 一致；
- Observation 使用 LOINC/SNOMED CT、UCUM、value[x]、有效时间和 derivedFrom；
- 否定、既往语境与“过去 24 小时”测量窗口不会混淆；
- 没有获批规则时所有文本均保持 `not_assessed` 且不创建 Alert；
- 资源经 SQLite 重开后保持完整 FHIR JSON，摘要证据引用和医生审阅可持久化。
- 随访草稿可恢复，Pathway/Questionnaire 版本保持锁定；
- 非法 choice、被禁用问题的残留答案、必填缺失和不同答案重复覆盖会被拒绝；
- 结构化答案直接生成患者确认的 Observation，自由文本不会在第二层被推断。

JSON Schema 不能完成术语绑定、Profile、全部不变量和业务验证。上线前仍须执行官方 FHIR Validator、目标医院 Implementation Guide 和术语服务器校验。
