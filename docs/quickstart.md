# ContinuCare 本地运行手册

这份手册对应当前主线的三角色 React 网页。推荐入口是 `scripts/start_demo.py`；`app.py` 的 Streamlit 页面只保留为历史综合演示壳，不再作为完整业务闭环入口。

## 1. 环境要求

- macOS 或 Linux；Windows 建议使用 WSL。
- Python 3.11 或更高版本。
- Node.js 20 或更高版本，包含 npm。
- 约 1 GB 可用磁盘空间用于 Python/Node 依赖和前端构建。

确认版本：

```bash
python3 --version
node --version
npm --version
```

## 2. 首次安装

在仓库根目录运行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
npm --prefix patient-web ci
npm --prefix patient-web run build
npm --prefix doctor-web ci
npm --prefix doctor-web run build
cp .env.example .env
```

`npm ci` 会严格使用两个前端目录内已提交的 lockfile。`dist/`、`node_modules/`、`.venv/` 和 `.env` 都是本地产物，不应提交。

## 3. 选择配置

### 无 Key 工程检查

保持 `.env` 中 `ARK_API_KEY` 为空、外部 egress 为 `false` 时，可以构建、启动页面、查看等待状态并运行离线测试，但不能完成患者自然语言整理后的三端闭环。最新患者网页会明确提示豆包未配置并禁用发送，不会用离线 Mock 冒充在线成功。

```dotenv
CONTINUCARE_DB_PATH=data/continucare.db
CONTINUCARE_MODE=local_stable_demo
CONTINUCARE_PATIENT_TIMEZONE=Asia/Shanghai
ARK_API_KEY=
CONTINUCARE_EXTERNAL_EGRESS_ENABLED=false
```

### 豆包完整合成演示（推荐使用路径）

在 `.env` 中填写本机 Key，并保持真实患者数据禁止进入系统。至少确认：

```dotenv
CONTINUCARE_LLM_PROVIDER=volcengine_doubao
CONTINUCARE_LLM_MODEL=doubao-seed-2-0-lite-260215
CONTINUCARE_LLM_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
CONTINUCARE_LLM_API_KEY_ENV=ARK_API_KEY
ARK_API_KEY=replace-with-a-local-secret
CONTINUCARE_USE_SAFETY_LLM=true
CONTINUCARE_USE_LANGUAGE_LLM=true
CONTINUCARE_USE_SUMMARY_LLM=false
```

这三个 LLM 开关属于仓库其他受控能力的兼容配置。当前三端患者主流程会强制只使用主语义抽取调用，并由本地安全门、术语映射和患者确认完成后续处理，不会调用 Safety、语言或 Summary LLM。

保存 `.env` 后先执行联通检查：

```bash
.venv/bin/python scripts/mimo_smoke_test.py
```

不要把 Key 粘贴到终端日志、截图、Issue、PR 或提交文件中。

## 4. 启动三端网页

推荐命令：

```bash
.venv/bin/python scripts/start_demo.py --open
```

启动器会：

1. 读取仓库根目录被忽略的 `.env`；
2. 检查两个前端的 `dist/index.html`；
3. 用同一 Python 环境和同一 `CONTINUCARE_DB_PATH` 启动患者/护士服务与医生服务；
4. 等待 API 就绪；
5. 打印三个 URL，并在指定 `--open` 时打开浏览器。

只检查构建和配置、不启动：

```bash
.venv/bin/python scripts/start_demo.py --check
```

如不希望自动打开浏览器，省略 `--open`。服务地址始终是：

- 医生：<http://127.0.0.1:8520/>
- 患者：<http://127.0.0.1:8510/>
- 护士：<http://127.0.0.1:8510/nurse>

在启动终端按 `Ctrl+C` 会同时停止两个服务。

## 5. 手动启动

需要分别观察日志时，可以开两个终端，并显式给两个进程传入同一个数据库路径：

终端 A：

```bash
CONTINUCARE_DB_PATH=data/continucare.db .venv/bin/python -m continucare.patient_web
```

终端 B：

```bash
CONTINUCARE_DB_PATH=data/continucare.db .venv/bin/python -m continucare.doctor_web
```

使用自定义 `.env` 中的其他变量时，优先使用启动器，避免两个终端加载到不同配置。

## 6. 完成一次业务闭环

1. **医生端**：在“创建随访方案”确认系统候选、频率与起止日期。保存成功后，患者随访被激活。
2. **患者端**：输入页面要求的合成信息；逐项处理候选、澄清或未知项，最后明确确认并提交。
3. **护士端**：选择任务，依次接收、开始复核、完成检查清单，并记录“继续观察”或“上报医生”等人工结果。
4. **医生端**：进入“护理协作”查看护士明确上报的事项；进入“患者随访”查看记录和趋势。
5. 刷新页面验证状态仍存在。三端读取的是同一 SQLite 事实，而不是浏览器临时状态。

若要开始一轮独立演示，最安全的方法是先停止服务，再在 `.env` 中改用一个新的合成数据库文件名，例如 `data/continucare-demo-2.db`；不要覆盖需要保留的本地数据库。

## 7. 本地登录与非本机部署

`127.0.0.1` / `localhost` 默认允许无登录预览。医生端如暴露到非本机地址，必须配置：

```dotenv
CONTINUCARE_DOCTOR_ALLOWED_HOSTS=doctor.example.org
CONTINUCARE_DOCTOR_ACCESS_KEY=replace-with-an-access-key
CONTINUCARE_DOCTOR_SESSION_SECRET=replace-with-a-long-random-secret
CONTINUCARE_DOCTOR_SECURE_COOKIE=true
CONTINUCARE_DOCTOR_PATIENT_IDS=P-DEMO-001
```

这仍只是演示级访问控制。医院部署必须接入机构 SSO、细粒度授权、HTTPS、正式数据库、审计和隐私合规流程。

## 8. 验证与故障排查

### 页面提示缺少 build

重新构建对应前端：

```bash
npm --prefix patient-web run build
npm --prefix doctor-web run build
```

### `8510` 或 `8520` 已被占用

先停止上一次启动器或占用端口的本地进程，再重新运行。当前患者服务固定使用 `8510`，推荐不要在演示前临时改端口。

### 医生、患者状态不一致

确认两个服务使用完全相同的 `CONTINUCARE_DB_PATH`。使用 `scripts/start_demo.py` 可自动保证这一点。

### 豆包不可用

先运行联通脚本并检查 `.env` 的 provider、base URL、model 和 Key 环境变量名。模型故障不会绕过人工门禁；当前三端主流程会保留已有记录并 fail-closed，待配置修复后重试。

### 回归检查

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/rehearse_demo.py
npm --prefix patient-web run build
npm --prefix doctor-web run build
```

FHIR 官方 Schema 和知识发布的完整验收要求见 [evaluation.md](evaluation.md)。
