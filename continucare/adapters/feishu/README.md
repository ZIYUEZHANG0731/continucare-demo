# 飞书 / Aily 适配器状态

此目录仅保留 M6 的技术边界。当前 M0–M5 **未配置账号、未调用飞书 API、未完成联调**。

- `AilyExtractor` 将来必须输出与 `MockExtractor` 相同的 Schema；
- `BitableStore` 将来映射 Patient、FollowUp、Observation、Alert、Summary；
- `FeishuBotNotifier` 将来发送护士 Alert 和医生 Summary 卡片；
- 业务服务不得直接依赖飞书 SDK；
- `FEISHU_ENABLED=false` 是默认且可完整演示的安全模式。

在获得账号、权限和测试租户之前，任何类实例化都会明确失败，避免把占位代码误称为已联调能力。

