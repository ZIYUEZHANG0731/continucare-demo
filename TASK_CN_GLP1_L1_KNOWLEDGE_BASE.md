# ContinuCare 中国 GLP-1 L1 知识与 FHIR 契约层任务书

- 文档版本：1.0.0
- 适用地区：中国大陆（CN）
- 目标执行者：Codex / 工程协作者
- 项目阶段：比赛原型与合成数据工程验证
- 临床状态：未完成临床审核，不得用于真实患者诊疗

## 1. 任务目标

在 ContinuCare 仓库中建设“中国大陆 GLP-1 药物 L1 知识与 FHIR 契约层”。

最终形成一套：

- 以 NMPA、国家卫生健康委、CDE 等中国权威来源为主；
- 按具体药品、品牌、剂型、适应证和批准人群隔离；
- 可版本化、可审计、可回溯到原始文件及具体章节；
- 可通过代码自动校验；
- 可编译为 FHIR R4 Questionnaire、PlanDefinition 和 Observation Mapping；
- 可被 L2、L3、L4、L5 确定性消费；
- 不依赖模型自身医学记忆或运行时互联网搜索；
- 不包含未经临床批准的风险分级、报警、诊断或治疗建议。

当前没有医生参与，本任务能够达到的最高状态只能是：

~~~text
engineering_validated
~~~

不得标记为：

~~~text
clinically_validated
approved
active
production_ready
~~~

所有路径必须继续保持 fail-closed：

~~~json
{
  "status": "draft",
  "synthetic_only": true,
  "clinical_rules": [],
  "approval": {
    "status": "not_reviewed",
    "clinical_approver": null,
    "terminology_approver": null,
    "approved_at": null
  }
}
~~~

## 2. 中国版适用范围

### 2.1 地区与语言

~~~text
jurisdiction = CN
region = 中国大陆
language = zh-CN
~~~

港澳台地区的药品批准、说明书和临床文件不得自动视为适用于中国大陆。

### 2.2 药物范围

建立中国大陆 GLP-1 相关药品登记目录，包括：

1. 单一 GLP-1 受体激动剂；
2. 含 GLP-1 作用机制的双靶点或多靶点药物；
3. 不同商品名；
4. 不同剂型和规格；
5. 不同批准文号；
6. 不同中国获批适应证；
7. 不同批准人群。

多靶点药物必须单独分类：

~~~text
single_glp1_ra
dual_gip_glp1_agonist
other_multi_agonist
~~~

不得把多靶点产品和单一 GLP-1 受体激动剂当作同一种产品或共享未验证的临床规则。

### 2.3 适应证隔离

至少区分：

~~~text
type_2_diabetes
chronic_weight_management
other_cn_approved_indication
~~~

禁止：

- 把糖尿病适应证自动外推到体重管理；
- 把体重管理产品说明书套用到糖尿病产品；
- 因为活性成分相同就合并品牌、剂型、适应证或批准人群；
- 收录中国未批准适应证作为正式运行知识；
- 把境外说明书当作中国说明书使用；
- 把超说明书用药写入正式 Pathway。

EMA、FDA 等境外资料只能登记为：

~~~json
{
  "jurisdiction": "non-CN",
  "usage": "background_comparison_only",
  "runtime_eligible": false
}
~~~

## 3. 权威来源优先级

### 3.1 A 级：可以支持中国运行时数据契约的来源

1. NMPA 药品注册和批准信息；
2. 中国现行药品说明书；
3. NMPA 药品安全公告和说明书修订公告；
4. 国家药品不良反应监测相关正式文件；
5. 国家卫生健康委正式发布的诊疗指南。

官方入口：

- [NMPA 政务服务窗口](https://www.nmpa.gov.cn/zwfwqjd/index.html?type=pc)
- [国家卫生健康委《肥胖症诊疗指南（2024年版）》通知](https://www.nhc.gov.cn/wjw/c100378/202410/bcf804e19e0c4246b5aea6cd338b55e1.shtml)
- [《肥胖症诊疗指南（2024年版）》PDF](https://www.nhc.gov.cn/yzygj/c100068/202410/18966b78087d44429f934a2ef028b027/files/1732873189749_61795.pdf)

国家卫生健康委指南可以支持理解中国诊疗场景和候选采集内容，但不能直接编译成自动报警、治疗或处置规则。

### 3.2 B 级：规范和技术背景

1. CDE 药品审评技术指导原则；
2. 中华医学会等正式发布的中国指南；
3. 国家级药学、内分泌、糖尿病和肥胖相关指南；
4. 正式发表的中国专家共识。

这些材料可以支持候选采集指标、数据背景、人群和场景说明、Evidence Claim 草案与人工审阅内容。

不能单独产生：

- 自动风险等级；
- 自动报警阈值；
- 医护处置时限；
- 停药、换药或调整剂量建议；
- 个体化就医建议。

### 3.3 C 级：数据和互操作标准

- HL7 FHIR R4 4.0.1；
- LOINC；
- SNOMED CT；
- UCUM。

SNOMED CT 必须记录具体 Edition、版本、地区适用性和许可状态。许可未确认时，不得在项目中批量分发完整术语内容。

### 3.4 D 级：仅用于研究和测试

- Synthea 合成患者数据；
- 公开临床试验数据；
- 药品不良反应报告数据；
- 海外标签和指南；
- 同行评议论文。

这些资料不能成为自动规则的唯一依据，也不能用于计算项目中的真实风险发生率。

### 3.5 禁止作为权威来源

- 百度百科；
- 搜索结果摘要；
- 医药营销页面；
- 健康科普自媒体；
- 电商药品页面；
- 无法确认版本的网络说明书；
- 模型自行生成或仅凭记忆给出的医学结论。

如果中国正式说明书无法获取，必须记录：

~~~text
source_unavailable
label_unverified
coverage_incomplete
~~~

不得使用境外说明书静默补齐中国产品事实。

## 4. 建议文件结构

新增以下结构：

~~~text
continucare/
  knowledge/
    __init__.py
    models.py
    registry.py
    validator.py
    compiler.py
    data/
      __init__.py
      cn_glp1/
        __init__.py
        v1/
          source_registry.json
          product_registry.json
          evidence_claims.json
          metric_definitions.json
          terminology_manifest.json
          patient_content.zh-CN.json
          data_quality_rules.json
          clinical_rules.json
          release_manifest.json
          coverage_report.json

docs/
  clinical/
    cn_glp1/
      README.md
      source_methodology.md
      product_coverage.md
      evidence_coverage.md
      unresolved_sources.md
      runtime_boundaries.md
      sources/
        允许保存和再分发的官方文件

scripts/
  build_cn_glp1_knowledge.py
  validate_cn_glp1_knowledge.py
  check_cn_glp1_sources.py
~~~

原始文件只有在允许保存和再分发时才加入仓库。否则只登记官方 URL、来源元数据和下载内容哈希，不提交受版权限制的全文。

## 5. 数据模型要求

所有知识模型使用严格 Pydantic 模型：

~~~python
model_config = ConfigDict(extra="forbid")
~~~

### 5.1 Source Registry

每个来源至少包含：

~~~json
{
  "source_id": "nhc-obesity-guideline-2024",
  "authority": "中华人民共和国国家卫生健康委员会",
  "jurisdiction": "CN",
  "source_type": "national_guideline",
  "title": "肥胖症诊疗指南（2024年版）",
  "document_number": "国卫办医政函〔2024〕382号",
  "language": "zh-CN",
  "publication_date": "2024-10-12",
  "effective_date": null,
  "retrieved_at": "实际下载日期",
  "canonical_url": "官方URL",
  "local_path": "本地文件或null",
  "sha256": "原始文件SHA-256",
  "license_status": "verified|restricted|unknown",
  "verification_status": "verified|partially_verified|unverified",
  "supersedes": [],
  "superseded_by": null,
  "runtime_eligible": true
}
~~~

校验要求：

- source_id 唯一；
- URL 指向原始官方来源；
- 下载文件计算 SHA-256；
- 动态网页记录抓取时间；
- 找不到原始资料时不得标记为 verified；
- 被新版替代的来源不得进入新的发布版本；
- 不能下载时记录失败原因，不得制造空哈希或虚构版本。

### 5.2 Product Registry

每个中国批准产品分别登记：

~~~json
{
  "product_id": "稳定内部ID",
  "jurisdiction": "CN",
  "active_ingredient": "通用名",
  "brand_name_zh": "商品名",
  "agonist_type": "single_glp1_ra",
  "marketing_authorization_holder": "上市许可持有人",
  "dosage_form": "剂型",
  "strengths": [],
  "administration_route": "给药途径",
  "approval_numbers": [],
  "approval_status": "approved|withdrawn|uncertain",
  "approved_indications": [],
  "approved_populations": [],
  "label_source_id": "对应中国说明书来源",
  "label_version": "版本或修订日期",
  "verified_at": "核验日期",
  "verification_status": "verified|incomplete|unverified"
}
~~~

不得只建立一个通用活性成分记录。不同商品、剂型、批准文号和适应证必须可以拆分。

### 5.3 Evidence Claims

不得让运行时代码直接解析整份 PDF。将可以使用的信息整理为结构化证据：

~~~json
{
  "claim_id": "cn-product-x-label-gi-reporting-001",
  "source_id": "对应source_id",
  "product_ids": ["适用产品"],
  "indications": ["适用适应证"],
  "populations": ["适用人群"],
  "locator": {
    "section": "不良反应",
    "subsection": "胃肠系统",
    "page": 12
  },
  "normalized_claim": "该资料支持采集患者报告的恶心和呕吐信息",
  "claim_type": "supports_data_collection",
  "allowed_use": [
    "设计患者报告采集问题",
    "形成带来源的患者报告事实"
  ],
  "prohibited_inference": [
    "不得据此诊断",
    "不得据此自动分级",
    "不得据此生成停药建议"
  ],
  "runtime_eligible": true,
  "review_status": "engineering_reviewed"
}
~~~

不要长篇复制来源原文。只保存准确定位、必要的短锚点和规范化表述。

### 5.4 Metric Definitions

每个采集指标建立唯一事实定义：

~~~json
{
  "metric_id": "vomiting_count_24h",
  "display_zh": "过去24小时呕吐次数",
  "clinical_intent": "记录患者自报的24小时呕吐次数",
  "product_scope": [],
  "indication_scope": [],
  "population_scope": [],
  "data_type": "quantity",
  "time_window": "previous_24_hours",
  "allowed_units": [
    {
      "system": "http://unitsofmeasure.org",
      "code": "/d"
    }
  ],
  "observation_code": {
    "system": "http://loinc.org",
    "code": "94070-0",
    "version": "必须明确"
  },
  "evidence_claim_ids": [],
  "missing_behavior": "do_not_create_observation",
  "conflict_behavior": "require_clarification",
  "trend_eligible": true,
  "clinical_interpretation_allowed": false,
  "approval_status": "engineering_validated"
}
~~~

必须区分有值、明确否认、不知道、没回答、数据冲突和单位不明确。禁止用数字 0 代表“未回答”。

### 5.5 Terminology Manifest

至少包括：

- LOINC code、display、version；
- SNOMED CT concept、Edition、version、许可状态；
- UCUM unit；
- 本地中文同义词；
- ValueSet；
- 允许使用的代码范围；
- 术语验证日期。

中文同义词只用于 L3 候选抽取，不得改变标准代码含义。

### 5.6 Patient Content Pack

患者问题和说明独立版本化：

~~~json
{
  "content_id": "vomiting-count-question-zh-cn",
  "locale": "zh-CN",
  "text": "过去24小时内，您呕吐了多少次？",
  "purpose": "data_collection",
  "metric_id": "vomiting_count_24h",
  "evidence_claim_ids": [],
  "medical_advice": false,
  "approval_status": "engineering_reviewed"
}
~~~

当前只允许：

- 采集说明；
- 时间范围说明；
- 单位说明；
- 系统非诊断、非急救通道说明；
- 请患者确认系统记录内容。

不允许：

- 是否停药；
- 如何调整剂量；
- 是否必须去医院；
- 个体化饮食或治疗建议；
- “你属于高风险”等判断。

### 5.7 规则文件

clinical_rules.json 必须为：

~~~json
[]
~~~

技术性数据质量规则放在独立文件：

~~~json
[
  {
    "rule_id": "DQ-MISSING-UNIT",
    "rule_type": "data_quality",
    "condition": "quantity_value_present_and_unit_missing",
    "action": "request_clarification",
    "clinical_risk_level": null
  }
]
~~~

数据质量规则可以检测缺少单位、数字格式错误、回答冲突、时间范围不明确、FHIR 校验失败和必填信息缺失。不得把数据质量规则包装成临床风险规则。

## 6. 知识加工和发布流程

完整流水线：

~~~text
中国官方资料发现
→ 核实发布机构和中国适用范围
→ 下载或登记原始文件
→ 计算 SHA-256
→ 建立 Source Registry
→ 建立中国批准 Product Registry
→ 提取结构化 Evidence Claims
→ 建立 Metric Definitions
→ 校验 LOINC/SNOMED CT/UCUM
→ 生成中文 Patient Content
→ 编译 Questionnaire
→ 编译 PlanDefinition
→ 编译 Observation Mapping
→ 运行跨文件验证
→ 生成 Release Manifest
→ 发布不可变 L1 版本
~~~

任何步骤不完整都不能用猜测或模型生成内容补齐。

## 7. 与现有系统的集成

### 7.1 扩展 PathwayDefinition

以向后兼容方式增加可选字段：

~~~python
jurisdiction: str | None = None
knowledge_release_id: str | None = None
product_scope: list[str] = []
indication_scope: list[str] = []
population_scope: list[str] = []
~~~

不要破坏现有 Pathway JSON 的加载和测试。

中国 GLP-1 路径应包含：

~~~json
{
  "jurisdiction": "CN",
  "knowledge_release_id": "cn-glp1-l1-v1",
  "product_scope": [],
  "status": "draft",
  "synthetic_only": true
}
~~~

如果暂时没有锁定具体产品，product_scope 为空，同时必须声明：

~~~text
generic_data_capture_only
not_product_specific
~~~

不得宣称路径适用于全部 GLP-1 产品。

### 7.2 L1 编译器

实现并测试：

~~~python
compile_knowledge_release()
compile_questionnaire()
compile_plan_definition()
compile_observation_mappings()
validate_release()
~~~

编译器必须检查：

- 问题引用的指标存在；
- 指标引用的证据存在；
- 证据引用的来源存在；
- 指标适用范围与产品、适应证、人群不冲突；
- Observation code、value 类型、单位和时间语义一致；
- 未审批规则没有进入发布包。

### 7.3 L2 Care Engine

L2 只能读取 L1 发布的 Questionnaire：

~~~text
L1 Questionnaire
→ Care Engine
→ 患者页面
~~~

L2 不得硬编码另一套医学问题、自己决定医学选项、自己生成单位或根据患者回答生成风险等级。

### 7.4 L3 语义 Agent

L3 只读取：

- 术语白名单；
- 中文同义词；
- 允许的 linkId；
- Metric Definitions；
- Observation Mapping。

L3 输出只能包含候选概念、候选值、原文证据、时间范围、否定状态、主体、是否需要澄清和允许追问的 linkId。

不得通过检索指南自行生成诊断、风险等级、报警或治疗建议。

### 7.5 L4 Clinical Memory

L4 使用 Metric Definitions 计算 current、stale、unknown、conflict 和原始数值方向。

允许表达：

~~~text
过去两次记录的数值增加
~~~

不允许表达：

~~~text
病情恶化
风险升高
治疗效果不佳
~~~

由于没有临床审批，规则结果必须保持：

~~~text
not_assessed
~~~

不得创建临床 Alert。

### 7.6 L5 应用层

患者、护士、医生页面应展示：

- Pathway 版本；
- 中国知识库 Release 版本；
- 产品和适应证范围；
- 数据来源；
- 原始患者回答；
- 标准化 Observation；
- 当前审核状态。

界面必须明确：

~~~text
仅用于合成数据和工程验证
未完成临床审核
不提供诊断和治疗建议
~~~

## 8. RAG 使用边界

当前版本不建设运行时医学 RAG。

如果实现检索，只允许用于离线 Pathway Studio：

~~~text
资料检索
→ 找到候选段落
→ 生成 Evidence Claim 草案
→ 工程人员确认来源、范围和定位
→ 写入结构化知识库
~~~

RAG 输出不得直接进入患者回复、风险判断、临床规则、Observation 或医疗建议。

运行时只能加载经过验证的 JSON Release，不实时访问互联网，也不直接读取散落 PDF。

## 9. 自动校验要求

### 9.1 来源校验

- source_id 不重复；
- 正式来源存在 URL；
- 本地文件 SHA-256 匹配；
- 被替代来源不能进入当前 Release；
- 非中国资料不能设置 runtime_eligible=true；
- 未验证来源不能支持正式 Metric。

### 9.2 产品校验

- product_id 唯一；
- 正式产品有中国批准依据；
- 每个产品分别记录剂型和适应证；
- 糖尿病和体重管理适应证不得混用；
- 多靶点产品不得伪装为单一 GLP-1 产品。

### 9.3 证据校验

- 每个 Evidence Claim 都能解析到 Source；
- 每个 Claim 都有适用产品、适应证和人群范围；
- 每个 Claim 都有 allowed_use；
- 每个 Claim 都有 prohibited_inference；
- 境外 Claim 不能支持中国运行时指标。

### 9.4 指标校验

- 每个运行时 Metric 至少引用一个有效 Claim；
- Metric 数据类型与 FHIR value 类型一致；
- Quantity 必须有 UCUM 单位；
- 时间窗明确；
- 缺失和冲突行为明确；
- 禁止临床解释的指标不能进入风险规则。

### 9.5 Questionnaire 与 Mapping 校验

- linkId 唯一；
- 每个可标准化问题绑定 metric_id；
- 每个映射能回溯到证据；
- enableWhen 引用合法；
- 每个选项与 ValueSet 一致；
- FHIR R4 结构验证通过。

FHIR JSON Schema 不能覆盖所有术语绑定、Questionnaire 规则和业务约束，因此必须保留项目自身跨文件校验，并使用 FHIR Validator：

- [HL7 FHIR R4 Validation](https://hl7.org/fhir/R4/validation.html)

### 9.6 安全校验

- clinical_rules.json 为空；
- 没有临床风险等级；
- 没有自动报警；
- 没有剂量调整；
- 没有诊断；
- 没有个体化治疗建议；
- 未审批路径不能改成 active；
- 常规运行和测试不需要联网。

## 10. Release Manifest

每次发布生成不可变清单：

~~~json
{
  "release_id": "cn-glp1-l1-v1",
  "jurisdiction": "CN",
  "created_at": "生成时间",
  "status": "engineering_validated",
  "synthetic_only": true,
  "source_registry_sha256": "...",
  "product_registry_sha256": "...",
  "evidence_claims_sha256": "...",
  "metric_definitions_sha256": "...",
  "terminology_manifest_sha256": "...",
  "questionnaire_sha256": "...",
  "observation_mapping_sha256": "...",
  "clinical_rules_sha256": "...",
  "clinical_approval": null,
  "known_limitations": []
}
~~~

已发布版本不得原地修改。来源、指标、映射或文案变化时创建新的 Release。

## 11. Codex 执行顺序

### Phase 0：保护现有工作

1. 检查当前 Git 分支、提交和工作区状态；
2. 不覆盖用户或队友的未提交改动；
3. 从双方确认的最新基础创建干净分支：

~~~text
codex/cn-glp1-l1-knowledge
~~~

4. 运行并记录现有测试基线；
5. 检查仓库内已有 L1、FHIR、Pathway 和 Evidence Pack 实现，不重复创建同义结构。

### Phase 1：建立模型和目录

- 新增 continucare/knowledge/；
- 建立严格 Pydantic 模型；
- 建立 JSON 加载器、Registry、Compiler 和 Validator；
- 保持现有 Pathway 向后兼容。

### Phase 2：中国药品和来源盘点

- 从中国官方来源发现产品；
- 建立 Product Registry；
- 核实批准文号、剂型、适应证和上市许可持有人；
- 无法核实的内容进入 unresolved 清单；
- 输出 product_coverage.md；
- 不使用搜索摘要补充产品事实。

如 NMPA 查询需要人工交互、验证码或无法稳定访问，应保留证据和失败记录，标记为待人工核验，不得改用低质量来源冒充完成。

### Phase 3：结构化证据

- 对可靠来源建立 Evidence Claims；
- 记录具体章节和页码；
- 明确允许用途和禁止推导；
- 输出 evidence_coverage.md；
- 不把说明书中的警示自动转换为报警阈值。

### Phase 4：指标和术语

- 把现有 GLP1-14D 指标迁入 Metric Definitions；
- 逐项审计中国适用证据；
- 没有中国证据的指标标记为 background_only 或 unverified；
- 校验 LOINC、SNOMED CT 和 UCUM；
- 不擅自增加临床指标、量表或阈值。

### Phase 5：FHIR 编译接入

- 从结构化知识生成或验证 Questionnaire；
- 生成 Observation Mapping；
- 给问题、指标和映射绑定 evidence_claim_ids；
- 让 Pathway 引用 knowledge_release_id；
- 保持现有 L2、L3 和 FHIR Builder 接口兼容。

### Phase 6：自动测试

执行：

~~~bash
pytest -q
python scripts/validate_cn_glp1_knowledge.py
python scripts/build_cn_glp1_knowledge.py --check
python scripts/validate_fhir_r4.py
~~~

如果实际脚本参数不同，应在文档中记录真实命令。不得通过删除原测试、降低严格性或移除安全条件让测试通过。

### Phase 7：文档和交付

输出：

- 完成的文件列表；
- 已核实产品列表；
- 未核实产品列表；
- 当前知识覆盖率；
- 每个指标的证据状态；
- 运行时接入方式；
- 已知限制；
- 测试结果；
- 没有完成临床验证的明确声明。

## 12. 最终验收标准

只有同时满足以下条件才算任务完成：

- 中国大陆适用范围明确；
- GLP-1 单靶点和多靶点产品分类明确；
- 产品、剂型、适应证和批准人群没有混用；
- 来源具有 URL、版本、日期和 SHA-256；
- 每个运行时指标具有中国适用证据；
- 每个问题能追溯到 Metric 和 Evidence Claim；
- 每个 Observation Mapping 能追溯到问题和证据；
- L2 能读取发布的 Questionnaire；
- L3 只能使用白名单；
- L4 保持 not_assessed；
- clinical_rules 为空；
- 所有既有测试和新增测试通过；
- 系统离线运行不依赖网页或模型医学记忆；
- 发布状态仅为 engineering_validated；
- 文档明确列出临床审批和机构流程缺口。

最终系统工作链：

~~~text
中国官方资料
→ 中国产品登记
→ 结构化证据
→ 指标和术语定义
→ FHIR 契约编译
→ 自动校验
→ 不可变知识版本
→ L2 采集
→ L3 受控抽取
→ L4 事实记忆
→ L5 证据展示
~~~

## 13. 明确不在本任务范围内的内容

- 激活任何临床风险规则；
- 为患者提供诊断或治疗建议；
- 自动停药、换药或调整剂量；
- 建立未经验证的急症关键词分级；
- 创建医护响应 SLA；
- 宣称适用于所有 GLP-1 产品；
- 连接真实医院生产系统；
- 使用真实患者数据；
- 宣称通过临床验证或医疗器械合规认证。

这些事项必须等待目标医疗机构、临床负责人、药学、术语、法务和信息安全相关人员参与后另行实施。
