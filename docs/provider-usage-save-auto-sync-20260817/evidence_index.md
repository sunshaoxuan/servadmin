# 证据索引

| 结论 | 证据 | 置信度 | 限制 |
|---|---|---|---|
| 多位小数受到前端步进限制 | `app/static/index.html` 的流量输入原值为 `step="0.001"`，浏览器复测的新值为 `step="any"` 且 validity.valid 为 true | 高 | 浏览器复测使用本地环境 |
| 空管理地址触发 422 | 修改前本地浏览器提交返回 422，全部表单字段的原生 validity 均为 true，控制台显示 API 错误 | 高 | 该证据来自本地复现日志 |
| 空管理地址现可保存 | `SubscriptionUsagePayload.normalize_empty_source_url` 与前端 null 规范化，浏览器真实提交成功 | 高 | 真实供应商站点未参与 |
| 自动任务原先存在 | `app/main.py` 的 `provider_sync_loop` 与 lifespan 任务创建 | 高 | 生产环境变量仍需发布后核验 |
| 已认证档案会自动启用 | `app/db.py` 版本 6 migration 与 `infer_provider_connector` | 高 | 只覆盖 OrangeVPS 与 Riven Cloud |
| 启动立即同步且默认每 15 分钟运行 | `app/main.py` 的默认间隔和无启动等待循环，单元测试验证首次循环先调用同步 | 高 | 外部网络失败会记录单机失败状态 |
