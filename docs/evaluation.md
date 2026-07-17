# 本地验证说明

验证只覆盖合成固定场景和工作流正确性，不代表临床性能或真实世界效果。

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/rehearse_demo.py
.venv/bin/streamlit run app.py
```

自动化断言包含：正常路径、否定呕吐、既往胸痛、L2 双条件、当前 L4、关闭必填记录、摘要证据引用、完整 L2 故事和数据库重开持久化。

截至 2026-07-17 的最后一次 M5 验证结果会以终端实际输出为准；不要把这里的测试范围解释为诊断准确率、治疗效果或真实飞书可用性。

