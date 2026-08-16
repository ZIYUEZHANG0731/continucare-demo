# ContinuCare 患者端与护士端

该目录包含患者端和护士端 React/Vite 前端，由同一个本地 Starlette 服务承载，并与独立医生服务共享 SQLite 事实库。

推荐从仓库根目录按 [统一运行手册](../docs/quickstart.md) 启动全部页面：

```bash
.venv/bin/python scripts/start_demo.py --open
```

单独构建与启动：

```bash
npm --prefix patient-web ci
npm --prefix patient-web run build
CONTINUCARE_DB_PATH=data/continucare.db .venv/bin/python -m continucare.patient_web
```

- 患者端：<http://127.0.0.1:8510/>
- 护士端：<http://127.0.0.1:8510/nurse>

患者自由表达只会产生待确认候选；患者明确确认后才形成最终记录和护士人工复核任务。护士端不自动诊断或分诊，所有处理结果均由护士显式选择并由服务端校验。
