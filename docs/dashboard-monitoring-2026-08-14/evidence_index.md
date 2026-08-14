# 证据索引

| 证据 | 路径 | 用途 |
| --- | --- | --- |
| 首页聚合实现 | `app/dashboard.py` | 状态、资源、IO 与套餐流量聚合 |
| 版本化迁移 | `app/db.py` | 月流量表与迁移记录 |
| 自动月度账本 | `app/traffic.py` | 计费周期、重复观测、计数器重置与供应商基线 |
| API | `app/main.py` | 首页读取与月流量保存 |
| 心跳采集 | `scripts/heartbeat_protocol.py` | Linux 累计计数和空间采集 |
| 首页结构 | `app/static/index.html` | 新默认页签与流量表单 |
| 首页交互 | `app/static/app.js` | 卡片渲染与读数保存 |
| 视觉系统 | `app/static/styles.css` | One人事配色与参考图布局 |
| 单元测试 | `tests/test_app.py`、`tests/test_heartbeat_mesh.py` | 数据链路与解析验证 |
| 浏览器截图 | `docs/assets/dashboard-monitoring-20260814.png` | 桌面页面验证 |
| 生产浏览器截图 | `docs/assets/dashboard-monitoring-production-20260814.png` | 七台生产卡片与在线汇总验证 |
| 自动计量截图 | `docs/assets/dashboard-automatic-traffic-20260814.png` | 自动计量汇总验证 |
| 自动计量完整页 | `docs/assets/dashboard-automatic-traffic-full-20260814.png` | 部分周期与校准周期卡片验证 |
