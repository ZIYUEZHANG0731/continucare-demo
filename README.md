# ContinuCare Copilot

ContinuCare 是一个使用合成数据的连续照护 Web 原型：医生先确认随访方案，患者用自然语言持续反馈，系统在患者确认后形成可追溯的 FHIR 记录，护士人工复核并决定是否上报医生，医生最终在协作待办和复诊视图中查看完整上下文。

> **安全边界：** 本项目不是医疗急救通道，不诊断、不治疗、不分诊、不生成用药建议；当前仅用于本地工程演示，不代表临床验证或医院生产部署。

## 最快启动

要求：Python 3.11+、Node.js 20+。所有命令都从仓库根目录执行。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
npm --prefix patient-web ci
npm --prefix patient-web run build
npm --prefix doctor-web ci
npm --prefix doctor-web run build
cp .env.example .env
# 编辑 .env，填写本机 ARK_API_KEY 后再启动完整交互演示
.venv/bin/python scripts/start_demo.py --open
```

`start_demo.py` 会让两个后端共享 `.env` 中的同一数据库，等待服务就绪，并在使用 `--open` 时打开三个页面：

| 角色 | 地址 | 用途 |
|---|---|---|
| 医生端 | <http://127.0.0.1:8520/> | 创建/更新随访方案、查看趋势和护士上报 |
| 患者端 | <http://127.0.0.1:8510/> | 按医生方案完成自然语言随访并确认事实 |
| 护士端 | <http://127.0.0.1:8510/nurse> | 人工复核患者记录、记录结果或上报医生 |

本地回环地址默认无需登录。停止时在启动终端按 `Ctrl+C`，两个服务会一起退出。更详细的配置、手动启动和故障排查见 [本地运行手册](docs/quickstart.md)。

## 完整使用顺序

1. 先打开医生端，在“创建随访方案”中确认记录内容、频率和周期。确认后才会激活患者随访。
2. 打开患者端，按页面提示输入合成随访信息，检查系统整理的候选内容，并由患者明确确认。
3. 打开护士端，接收任务、开始复核、勾选核对项并记录人工处理结果；需要时选择上报医生。
4. 返回医生端“护理协作”查看护士上报，在“患者随访”查看结构化记录和趋势。
5. 刷新任一页面，状态会从共享 SQLite 事实恢复，不依赖浏览器 session state。

演示必须使用合成数据。标准讲解顺序和失败恢复见 [三端演示脚本](docs/demo_scripts.md)。

## 配置模式

复制 `.env.example` 后，运行数据默认写入被 Git 忽略的 `data/continucare.db`。当前最新患者网页要求真实配置豆包后才允许自然语言整理；缺少 Key 时会明确停止该操作，不会把离线 Mock 冒充成在线模型结果。

### 1. 无 Key 工程检查

不填写 `ARK_API_KEY` 时，可以安装、构建、启动页面、查看等待状态并运行离线自动化测试；不能完成患者自然语言输入后的三端业务闭环。患者页会显示“豆包当前未配置”，并禁用发送操作。

```dotenv
CONTINUCARE_DB_PATH=data/continucare.db
CONTINUCARE_MODE=local_stable_demo
CONTINUCARE_PATIENT_TIMEZONE=Asia/Shanghai
ARK_API_KEY=
CONTINUCARE_USE_SUMMARY_LLM=false
CONTINUCARE_EXTERNAL_EGRESS_ENABLED=false
```

### 2. 火山方舟豆包完整演示（推荐使用路径）

只在本机被忽略的 `.env` 中填写轮换后的 Key：

```dotenv
CONTINUCARE_LLM_PROVIDER=volcengine_doubao
CONTINUCARE_LLM_MODEL=doubao-seed-2-0-lite-260215
CONTINUCARE_LLM_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
CONTINUCARE_LLM_API_KEY_ENV=ARK_API_KEY
ARK_API_KEY=replace-with-a-local-secret
CONTINUCARE_LLM_PROMPT_VERSION=doubao-semantic-extraction-v1
CONTINUCARE_USE_SAFETY_LLM=true
CONTINUCARE_SAFETY_PROMPT_VERSION=doubao-safety-critic-v1
CONTINUCARE_USE_LANGUAGE_LLM=true
CONTINUCARE_LANGUAGE_PROMPT_VERSION=doubao-language-rewrite-v1
CONTINUCARE_USE_SUMMARY_LLM=false
CONTINUCARE_SUMMARY_PROMPT_VERSION=doubao-summary-outline-v1
CONTINUCARE_LLM_TIMEOUT_SECONDS=60
```

最小联通检查：

```bash
.venv/bin/python scripts/mimo_smoke_test.py
```

脚本名为历史兼容名称，会读取当前 `CONTINUCARE_LLM_*` 配置。当前三端主流程只调用豆包做受控语义抽取，随后由本地硬规则、术语映射和患者确认门接管；仓库中的 Safety LLM、语言 LLM 和 Summary LLM 属于独立可选能力，当前三端适配器不会调用它们。模型不生成或决定医学代码，未配置或结果未通过安全校验时主流程 fail-closed，保留已有记录并要求重试。禁止发送真实患者数据。

### 3. 飞书 / Aily / Bitable

默认全部处于安全的 Mock/disabled 状态：

```dotenv
CONTINUCARE_FEISHU_MODE=mock
CONTINUCARE_AILY_MODE=mock
CONTINUCARE_BITABLE_MODE=disabled
CONTINUCARE_EXTERNAL_EGRESS_ENABLED=false
```

当前仓库只完成协议适配器和 FakeTransport 合同验证，没有完成真实租户或生产验证。仅修改 mode 不会启用外部访问；还必须同时显式启用对应 capability、全局 egress 并提供完整配置。详见 [飞书 / Aily 集成边界](docs/feishu_integration.md)。

## 当前主线架构

```text
医生 React (:8520) ─┐
                    ├─ Starlette 服务 ─ SQLite 事实库 ─ FHIR/审计/版本链
患者 React (:8510) ─┤
护士 React (:8510/nurse) ┘
```

- 医生确认方案会原子地保存版本并激活患者随访。
- 患者自由表达只产生待确认候选；确认后才写入完整 `QuestionnaireResponse` 和可追溯 `Observation`。
- 护士复核是人工工作流，系统没有未经批准的临床阈值，不自动分诊。
- 医生端只显示与角色相关的最小必要投影；所有写操作由服务端重新校验状态和角色边界。
- Knowledge 是独立只读资料库，`knowledge_effect=informational_only`、`runtime_authority=none`，不授权运行时动作。
- Streamlit `app.py` 仅保留为历史综合演示壳和技术资料入口，不是当前推荐主流程。

实施架构见 [整体六层方案](docs/14_layered_solution_blueprint.md) 和 [当前实现架构](docs/implementation_architecture.md)。

## 验证

日常本地验收：

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/rehearse_demo.py
npm --prefix patient-web run build
npm --prefix doctor-web run build
.venv/bin/python scripts/start_demo.py --check
```

普通离线测试不下载外部文件。未设置 `FHIR_R4_SCHEMA_ZIP` 时，依赖 HL7 官方 Schema 的 3 项测试会明确显示 skipped；比赛发布验收不能把这些 skip 视为通过。完整 FHIR Schema 哈希校验、知识包重建和发布门见 [验收说明](docs/evaluation.md)。

## 目录导航

| 路径 | 内容 |
|---|---|
| `continucare/` | Python 业务服务、FHIR、Care Agent、知识治理和角色边界 |
| `patient-web/` | 患者端与护士端 React/Vite 前端 |
| `doctor-web/` | 医生端 React/Vite 前端 |
| `scripts/start_demo.py` | 推荐的本地三端启动器 |
| `tests/` | 单元、集成、安全边界和三端闭环测试 |
| `docs/` | 产品、架构、数据、验证与演示文档 |
| `submission/` | 比赛提交材料；不属于运行时依赖 |

`data/`、`.env`、`output/`、`movie/`、前端 `dist/` 和 `node_modules/` 均为本地产物，不进入 Git。原始录屏和本地数据库不能作为主线运行依赖。

## 工程与临床边界

- 原始回答与 FHIR `Observation` 通过 `derivedFrom` 可追溯。
- 定时随访只使用发布包固定的 Questionnaire linkId 白名单；模型不能提供医学代码。
- 没有获批临床规则时保持 fail-closed，不输出风险等级或 Alert。
- 当前中国 GLP-1 L1 知识版本为 `cn-glp1-l1-v1.0.3`；部分产品证据仍标记 incomplete，不能夸大为完整临床覆盖。
- 真实 IAM/EMR、医院 Profile、临床规则审批、真实飞书/Aily 联调、消息实际发送和生产隐私合规均不在当前完成范围。

临床知识版本、证据覆盖与运行边界见 [中国 GLP-1 L1 文档](docs/clinical/cn_glp1/README.md)。所有演示身份、消息和结果均为合成数据；禁止提交密钥、运行数据库或真实患者信息。
