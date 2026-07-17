# ContinuCare 本地 Demo 实施架构

```text
Streamlit 角色页面
  ├─ 患者随访 ── FollowUpWorkflow
  │                  ├─ FollowUpService
  │                  ├─ ExtractionService ── MockExtractor（规则/模板）
  │                  └─ AlertService ── evaluate_risk（确定性纯函数）
  ├─ 护士风险中心 ── AlertService ── MockNotifier（明确未联调）
  ├─ 医生复诊简报 ── SummaryService ── 本地证据模板
  └─ 审计日志
                         │
                     SQLiteStore
                         │
     Patient / Message / Observation / Alert / AlertAction / Summary / AuditEvent
```

## 关键边界

- `AIExtractor`：首版由 `MockExtractor` 实现；抽取结果包含原文证据和字符位置。
- `DataStore`：首版由标准库 `sqlite3` 驱动的 `SQLiteStore` 实现。
- `NotificationChannel`：首版由 `MockNotifier` 实现，返回并审计明确的 `mock_feishu` 投递结果。
- `evaluate_risk`：纯函数，只根据已结构化且未被否定/既往语境排除的 Observation 计算 L0/L2/L4 工作流优先级。

业务服务不导入 Streamlit、飞书 SDK 或特定外部模型 SDK。M6 替换适配器时不需要重写核心规则、证据链和状态流转。

## 持久化与证据

- 每个核心实体使用字符串 ID 和 ISO 8601 时间；
- Alert 保存消息和 Observation 引用；
- Summary 每条内容由 Pydantic 强制至少一个 `evidence_ref`；
- 护士动作和医生审阅进入 append-only `audit_events`；
- 重启应用后 SQLite 记录仍保留，显式重置会重建合成患者并记录 `demo_reset`。

