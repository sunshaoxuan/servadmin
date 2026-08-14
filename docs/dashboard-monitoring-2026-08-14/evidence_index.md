# 证据索引

| 证据 | 路径 | 用途 |
| --- | --- | --- |
| 首页聚合实现 | `app/dashboard.py` | 状态、资源、IO 与套餐流量聚合 |
| 版本化迁移 | `app/db.py` | 月流量表与迁移记录 |
| API | `app/main.py` | 首页读取与月流量保存 |
| 心跳采集 | `scripts/heartbeat_protocol.py` | Linux 累计计数和空间采集 |
| 首页结构 | `app/static/index.html` | 新默认页签与流量表单 |
| 首页交互 | `app/static/app.js` | 卡片渲染与读数保存 |
| 视觉系统 | `app/static/styles.css` | One人事配色与参考图布局 |
| 单元测试 | `tests/test_app.py`、`tests/test_heartbeat_mesh.py` | 数据链路与解析验证 |
| 浏览器截图 | `docs/assets/dashboard-monitoring-20260814.png` | 桌面页面验证 |
| 生产浏览器截图 | `docs/assets/dashboard-monitoring-production-20260814.png` | 七台生产卡片与在线汇总验证 |
| 供应商数据边界截图 | `docs/assets/dashboard-provider-usage-source-20260814.png` | 无供应商读数时不显示网卡推算套餐量，独立周期读数正常展示 |
| 供应商凭据档案截图 | `docs/assets/provider-access-archive-20260814.png` | 供应商入口、用户、服务编号、服务器标识、同步状态和遮蔽密码展示 |
