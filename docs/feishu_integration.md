# 飞书 / Aily 集成状态

## 当前真实状态（M0–M5）

- `FEISHU_ENABLED=false`；
- 未获得或使用飞书/Aily Token；
- 未调用飞书开放平台、Aily 或多维表格 API；
- 护士卡片和医生通知来自 `MockNotifier`，UI 与审计均标注 `Mock / 未联调`；
- 核心工作流不依赖外部 API Key，可离线运行。

`continucare/adapters/feishu/` 中的类只是 M6 边界占位；实例化会明确报错，避免把未完成能力误认为已联调。

## M6 前置条件

只有在用户提供测试租户、应用权限、回调地址与合规边界后才执行：

1. 让 `AilyExtractor` 输出与 `MockExtractor` 相同 Schema；
2. 建立 Bitable 字段映射和幂等写入；
3. 用测试租户验证 Bot 卡片投递及护士回调；
4. 保留 SQLite + Mock 模式并复跑同一套核心断言；
5. 分开报告 Mock 测试状态与真实联调状态。

