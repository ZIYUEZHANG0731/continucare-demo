# ContinuCare 独立医生端

该目录是独立的 React/Vite 医生工作台。页面通过同源 Starlette API 读取现有 ContinuCare 结构化数据，不依赖 Streamlit。

## 本地启动

推荐从仓库根目录按 [统一运行手册](../docs/quickstart.md) 构建两个前端，并用一个命令同时启动三角色网页：

```bash
.venv/bin/python scripts/start_demo.py --open
```

如需单独调试医生服务：

```bash
npm --prefix doctor-web ci
npm --prefix doctor-web run build
CONTINUCARE_DB_PATH=data/continucare.db .venv/bin/python -m continucare.doctor_web
```

打开 `http://127.0.0.1:8520`。

## 随访方案创建

医生端首页会完成以下受控流程：

1. 读取当前患者的最小必要电子档案摘要；
2. 从版本化规则中匹配疾病/治疗目标对应的核心结局指标；
3. 按产品、适应证和人群叠加用药安全知识，并用电子档案做个体化排序；
4. 由医生选择系统候选项、添加自定义指标，并设置数据类型、单位、频率和整体周期；
5. 服务端重新生成规则候选、校验核心必选项和医生自定义项，将方案版本与患者随访激活作为一个原子更新写入共享数据库；
6. 护士人工选择“上报医生评估”后，同一任务会出现在医生端“护理协作”待办中，且保持“未评估”状态，不自动生成临床结论。

核心指标规则位于 `continucare/doctor_data/followup_goal_rules_v1.json`。新增病种时通过增加新的规则匹配条件和指标定义扩展，不在页面组件中写病种判断。规则与用药知识采用独立版本，避免修改已经锁定的知识发布包。

合成演示档案位于 `continucare/doctor_data/synthetic_ehr_profiles_v1.json`。
真实医院部署应通过院内适配器提供相同的最小化档案投影，不应把完整病历或无关身份字段直接发送到医生端浏览器。

相关同源接口：

- `GET /api/doctor/planning?patientId=...`
- `POST /api/doctor/plans`

当前 `cn-glp1-l1-v1.0.3` 知识版本仅允许合成演示方案确认；真实患者激活会保持关闭，直到对应知识版本完成临床、药学、术语和机构审批。

本机回环地址默认允许无登录预览。非本机部署必须配置以下环境变量，否则 API 会拒绝访问：

- `CONTINUCARE_DOCTOR_ALLOWED_HOSTS`：允许的域名，多个域名用逗号分隔。
- `CONTINUCARE_DOCTOR_ACCESS_KEY`：医生端登录密钥。
- `CONTINUCARE_DOCTOR_SESSION_SECRET`：用于签名会话 Cookie 的高强度随机值。
- `CONTINUCARE_DOCTOR_SECURE_COOKIE=true`：HTTPS 部署时启用 Secure Cookie。
- `CONTINUCARE_DOCTOR_PATIENT_IDS`：当前部署允许访问的患者 ID 白名单。

## Docker

```bash
docker build -f Dockerfile.doctor -t continucare-doctor .
docker run --rm -p 8080:8080 \
  -v "$PWD/data:/data" \
  -e CONTINUCARE_DOCTOR_ALLOWED_HOSTS=doctor.example.org \
  -e CONTINUCARE_DOCTOR_ACCESS_KEY='replace-with-a-secret' \
  -e CONTINUCARE_DOCTOR_SESSION_SECRET='replace-with-a-long-random-secret' \
  -e CONTINUCARE_DOCTOR_SECURE_COOKIE=true \
  continucare-doctor
```

生产环境应由反向代理或云负载均衡器终止 HTTPS，并为数据库配置持久化卷。当前访问控制是单一医生门户密钥与患者白名单；医院级上线仍需接入机构 SSO、细粒度授权和正式隐私合规流程。
