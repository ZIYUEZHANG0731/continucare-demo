# 今晚任务：官方 FHIR R4 Schema ZIP 的 SHA-256 默认 fail-closed 校验

> 这是一份可以直接交给 PR #2 负责人 AI 的启动 Prompt。它只描述一个晚上内的单一任务，不规划后续路线。
>
> 生成本任务卡的阶段只做 Markdown 静态验收，**没有授权运行产品测试或修改产品代码**。测试基线属于收到明确开工授权后的执行阶段动作。

## 0. 指令优先级

1. 平台安全规则与法律合规要求；
2. 你方分支 owner 当前、明确的指令；
3. 本任务卡；
4. 仓库中的历史归档与模糊背景材料（优先级最低）。

本卡不高于你方 owner 的当前明确指令。若 owner 的新指令与本卡在细节上不同，按 owner 指令执行并在交付包中记录差异；若新指令会改变任务范围、允许文件清单或验收标准，则停止并请 owner 明确确认后再继续，不要自行扩大范围。

## 0.5 授权模型（三级，缺一不可）

1. 读取本卡、收到附件、被要求“先看看”——**都不是授权**，此时不得写入任何文件。
2. 只有你方 owner 明确表达“开始执行本卡”，才授权在本卡允许文件清单内写入代码。
3. `git add` / `commit` / `push` / `merge` / `rebase` / `tag` / PR 元数据变更**一律需另行明确授权**；即使已获得第 2 级授权也**不自动**获得第 3 级。未获第 3 级授权时，交付 working diff + 当前 HEAD SHA，**不得虚构 new head**。

## 1. 本文件的性质与使用方式

**这份文件是自包含任务书。** 你收到的是一份独立文件，它本身包含本任务所需的当前事实、范围、安全边界、实施语义、测试和交付标准。

本任务不要求你采用任何多 Agent 审核流程；验收以本任务卡为准。你仍须服从平台规则和你方 owner 的当前明确指令。

## 2. 不要寻找或依赖不存在的协作文档

- 分支 `codex/fhir-foundation-docs@dd666a6dcbe72647e05abddc338615dfb4fbe928` 的整棵树中**没有**根目录 `AGENTS.md`，也**没有**根目录 `HANDOFF.md`。这是预期状态，不是仓库损坏。
- **不要**等待、索取、补建或假设上述两个文件的内容，也不要把找不到它们当作阻断项。
- 仓库中存在 `CODEX_DEMO_IMPLEMENTATION_HANDOFF.md`，但其开头已明确标记为**历史实现归档**，包含已经停用的 L2/L4 风险演示。它不是当前规范；本任务不得读取它作为依据、引用它、修改它或恢复其中的旧医疗风险逻辑。
- PR #3 的私人协作文档和另一张任务卡不属于本任务上下文，不得依赖。
- 若收到会实质改变本卡范围、允许文件或验收标准的新材料，停止并请你方 owner 明确裁决；不要用历史归档自行填补冲突。

## 3. 开工必读清单（只读）

收到明确“开始执行本卡”的授权后，先按符号名完整阅读：

- `continucare/fhir/r4.py`
  - `FHIRValidationError`
  - `FHIR_R4_VERSION`
  - `validate_official_json_schema`
  - `validate_r4_resource`（只为确认不修改的边界）
- `tests/test_fhir_conformance.py`
  - `example_resources`
  - `test_all_clinical_resources_pass_official_hl7_json_schema`
  - 现有 `FHIR_R4_SCHEMA_ZIP` 门控 skip 的写法
- `continucare/care_agent/release.py`
  - `LAYER3_RELEASE.fhir_schema_sha256`，**只读参考，禁止修改**
- `tests/test_layer3_release_boundary.py`
  - 只读了解既有 release 边界测试；本任务禁止修改此文件

行号会随改动漂移，一律以文件路径和符号名定位。

## 4. 今晚唯一任务

### 目标

让 `validate_official_json_schema` 在默认调用时，先对整个官方 HL7 FHIR R4 Schema ZIP 的原始字节执行固定 SHA-256 校验；摘要不匹配必须在打开 ZIP 或解析 Schema 前 fail closed。

当前固定摘要为：

```text
75e5560da3cf503895a44c8ca7af17a83b4cca6c2cb5ba1883d2aec0d1cb5ac6
```

它应同时与只读的 `LAYER3_RELEASE.fhir_schema_sha256` 保持一致。

### 当前缺口

基线实现只检查路径、打开 ZIP、读取 `fhir.schema.json`、解析 JSON，再用 `Draft6Validator` 校验资源；它没有在运行时验证 ZIP 摘要。因此，一个结构合法但并非官方发行物的替代 ZIP 可能被接受，这与该函数“不静默替换官方 artifact”的边界承诺不一致。

### 非目标

- 不做 Summary、pathway 过滤、Doctor Workbench 或其它 Layer 4 功能。
- 不生成 Layer 4 release manifest、rollback instructions 或 freeze tag。
- 不修改 Layer 3 release manifest、其 SHA/URL、`docs/evaluations/*.json` 或 release 测试。
- 不修改 `validate_r4_resource`、调用方、数据库、迁移、UI、脚本或依赖。
- 不下载官方 Schema，不新增命令行下载器或缓存机制。
- 不规划第二晚或仓库整体路线图。

## 5. 起始提交与只读接管核验

只在获得开工授权后执行本节；任何写入之前完成全部步骤。

```bash
git rev-parse HEAD
git status --short
git fetch --prune origin
git rev-parse origin/codex/fhir-foundation-docs
```

必须同时满足：

- 当前分支是 `codex/fhir-foundation-docs`；
- 本地 `HEAD` 为 `dd666a6dcbe72647e05abddc338615dfb4fbe928`；
- `origin/codex/fhir-foundation-docs` 也是 `dd666a6dcbe72647e05abddc338615dfb4fbe928`；
- `git status --short` 为空。

任何一项不满足都立即停止并报告；不得自行 reset、checkout、stash、rebase、merge、覆盖或清理 owner 的改动。

只读 `git fetch --prune origin` 是允许的。除此之外，不得下载依赖、下载 FHIR Schema、调用外部模型或访问其它外部服务。

## 6. 写入前基线

使用仓库现有环境；不要安装、升级或降级任何依赖。先运行并记录：

```bash
python -m pytest -q tests/test_fhir_conformance.py tests/test_layer3_release_boundary.py
python -m pytest -q
python -m compileall continucare scripts
```

对每条命令记录退出码，以及适用时的 `passed / skipped / failed` 计数。

- 缺依赖：停止并报告，不安装依赖、不改 `pyproject.toml`。
- 基线已有失败：停止并报告，不通过修改测试、增加 skip 或放宽断言掩盖。
- 本机若没有已存在的官方 Schema ZIP，不下载；env-gated 测试保持原有 skip 即可。

## 7. 允许修改的文件

**只能修改以下两个已跟踪文件：**

1. `continucare/fhir/r4.py`
2. `tests/test_fhir_conformance.py`

需要第三个文件时立即停止并报告，不要先改后解释。

## 8. 明确禁止触碰的范围

包括但不限于：

- `continucare/care_agent/release.py`
- `tests/test_layer3_release_boundary.py`
- `continucare/care_agent/**`
- `continucare/adapters/**`
- `continucare/layer4/**`
- `continucare/care_engine/**`
- `continucare/db.py`
- `scripts/**`
- `pages/**`、`app.py`
- `CODEX_DEMO_IMPLEMENTATION_HANDOFF.md`
- `docs/**`、`evaluations/**`
- `pyproject.toml`、依赖和配置文件

禁止联网下载依赖或 Schema，禁止 live LLM/MiMo，禁止新增迁移、标签或发布产物。

## 9. 冻结实施语义

严格执行以下 6 步，不另创替代设计：

1. 在 `continucare/fhir/r4.py` 增加模块内常量：

   ```python
   OFFICIAL_R4_SCHEMA_SHA256 = (
       "75e5560da3cf503895a44c8ca7af17a83b4cca6c2cb5ba1883d2aec0d1cb5ac6"
   )
   ```

2. 将公开函数保持向后兼容地扩展为：

   ```python
   def validate_official_json_schema(
       resource: dict[str, Any],
       schema_zip_path: str | Path,
       *,
       expected_sha256: str = OFFICIAL_R4_SCHEMA_SHA256,
   ) -> None:
   ```

   现有二参数调用必须继续工作，并自动执行默认 pinning。

3. 先读取 ZIP 的原始字节并计算 SHA-256。实际摘要不等于 `expected_sha256` 时立即抛出项目自己的 `FHIRValidationError`；错误消息要明确包含 `sha256`，并只暴露 expected/actual 各自前 16 个字符，避免冗长输出。

4. 摘要校验必须发生在 `zipfile.ZipFile(...)` 打开和 `json.loads(...)` 解析之前。通过校验后，从**同一份已经完成哈希校验的字节**读取 ZIP/Schema，不要对路径进行“先哈希、后重新打开”的二次读取。

5. 保持既有 fail-closed 行为：文件不存在、无法读取、摘要不符、坏 ZIP、缺少 `fhir.schema.json`、坏 JSON、资源不符合 Schema，均抛 `FHIRValidationError`。不得 warning 后继续，不得 fallback，不得静默跳过。

6. 生产分层边界必须保持：
   - `continucare/fhir/r4.py` **绝不能**导入 `continucare.care_agent.release` 或 `care_agent` 下任何模块；
   - `tests/test_fhir_conformance.py` 可以仅在测试中只读导入 `LAYER3_RELEASE`，用于断言两个固定摘要相等；
   - 不允许 `expected_sha256=None`、环境变量开关、warning 降级或其它绕过默认 pinning 的通道。

## 10. 必须新增的离线测试

在 `tests/test_fhir_conformance.py` 至少新增以下 4 个测试；全部使用 `tmp_path`、标准库和合成 ZIP，不联网、不依赖 `FHIR_R4_SCHEMA_ZIP`：

1. **显式实际摘要可通过**：创建含可解析 `fhir.schema.json` 的合成 ZIP，计算它的实际 SHA-256，通过 `expected_sha256=<actual>` 调用并成功。
2. **显式错误摘要 fail closed**：同一类合法合成 ZIP，给出错误摘要，断言 `FHIRValidationError` 且错误语义包含 `sha256`。
3. **默认官方摘要拒绝替代 ZIP**：不传 `expected_sha256`，断言结构合法、内部 Schema 可解析且旧实现会接受的合成 ZIP 仍被默认 pinning 拒绝。
4. **跨边界一致性**：断言 `OFFICIAL_R4_SCHEMA_SHA256 == LAYER3_RELEASE.fhir_schema_sha256`；这个跨层导入只能存在于测试文件。

保持现有 `test_all_clinical_resources_pass_official_hl7_json_schema` 的 env gate 和二参数调用不变，不得为了新实现修改或删除它。

测试还应证明摘要不匹配时没有进入 ZIP/JSON Schema 解析路径；不要只用随机坏字节把失败归因于 `BadZipFile`。

## 11. 执行后验证命令

```bash
python -m pytest -q tests/test_fhir_conformance.py tests/test_layer3_release_boundary.py
python -m pytest -q
python -m compileall continucare scripts
grep -n "care_agent" continucare/fhir/r4.py
```

最后一条应无输出；其退出码因“无匹配”通常为 1，交付时按“无输出即满足静态边界”解释，不把它误报成产品测试失败。

仅当本机在开工前已经存在官方 ZIP 时，额外运行：

```bash
FHIR_R4_SCHEMA_ZIP=/existing/path/fhir.schema.json.zip python -m pytest -q
```

不得为执行这条可选验证而联网下载文件。

## 12. 客观验收标准

- 定向测试 `0 failed`，至少新增 4 个测试且全部通过。
- 完整 pytest `0 failed`；`passed` 数等于写入前实测基线加新增测试数；`skipped` 不高于基线。
- 若开工前已有本地官方 ZIP，带 `FHIR_R4_SCHEMA_ZIP` 的完整 pytest 为 `0 failed, 0 skipped`。
- `python -m compileall continucare scripts` 退出码为 0。
- 默认二参数调用执行固定官方摘要校验；不存在 `None`、环境变量或 warning 绕过。
- 摘要不匹配在 ZIP 打开和 JSON 解析前被拒绝，并使用同一份已校验字节完成后续解析。
- 生产代码没有反向导入 `care_agent`；测试中的 release 常量比较通过。
- `git status --short` 只出现两条产品修改：
  - ` M continucare/fhir/r4.py`
  - ` M tests/test_fhir_conformance.py`
- 没有下载、依赖修改、迁移、文档、脚本、tag 或 PR 元数据变化。

## 13. 立即停止条件

出现任一情况即停止并报告，不扩大范围：

- 本地或远端 HEAD 不再是冻结 SHA，或开工时工作区不干净；
- 需要修改第三个文件、公开调用方、依赖、数据库或迁移；
- 需要让 `continucare/fhir/r4.py` 导入 `care_agent`；
- 本地所谓官方 ZIP 的摘要与固定值不同；不得自行更新 pin、release manifest 或 URL；
- 只能通过联网下载才能继续正向集成验证；
- 无法从同一份已哈希字节完成 ZIP/JSON 解析；
- 基线或执行后出现无关测试失败；
- 需要放宽测试、增加 skip 或改变冻结验收标准；
- 需要提交、推送、合并、打 tag 或改变 PR 状态但尚未得到 owner 的独立明确授权。

## 14. 医疗与数据安全红线

- 只使用合成数据；不得接触或发送真实患者数据。
- 保持 `clinical_rules=[]`、`not_assessed` 和 Summary LLM 默认关闭。
- 不生成或宣称 L0–L4 风险等级、临床 Alert、SLA、治疗建议或处置建议。
- 不得把 `not_assessed` 表述为“无风险”。
- 不调用 live LLM/MiMo 或其它外部服务。
- 不得恢复 `CODEX_DEMO_IMPLEMENTATION_HANDOFF.md` 中已停用的旧 L2/L4 风险演示。

## 15. 交付包格式

完成实现但未获 Git 写入授权时，只交付 working tree 证据：

1. 分支名、起始 SHA、当前 HEAD SHA；
2. 开工前与收工前两次 `git fetch --prune origin` 的远端 head 结论；
3. `git status --short` 完整输出；
4. working diff 与 `git diff --stat`；
5. 实际修改文件清单；
6. 写入前基线的每条命令、退出码和测试计数；
7. 执行后每条命令、退出码和测试计数；
8. 是否使用了开工前已存在的官方 ZIP；若没有，明确记录该正向集成验证未运行；
9. 已知限制和未完成项；
10. 明确说明没有修改共享合同、依赖、release manifest、tag 或 PR 元数据。

若没有 commit 授权，不得提供或暗示“new commit SHA”。若 owner 后续单独授权 commit/push，再按该授权范围行动。

## 16. 时间盒

- 预计用时：1.5–2.5 小时。
- 2.5 小时为硬停止线；到时仍未满足验收标准，就停止并提交现有证据和阻断项，不扩大范围、不降低标准。

收工前再执行一次只读 `git fetch --prune origin`。远端自身分支 head 若在执行期间改变，立即停止并报告，不自行 rebase、merge、reset 或覆盖。
