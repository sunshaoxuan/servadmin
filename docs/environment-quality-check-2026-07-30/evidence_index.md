# 证据索引

| 证据 | 路径或来源 | 支持的结论 |
| --- | --- | --- |
| 原环境采集与报告构建 | `app/main.py` 中 `INSPECTION_SCRIPT`、`PYTHON_INSPECTION_SCRIPT`、`build_config_report` | 原能力以资产清单为主 |
| 心跳采集与邻居确认 | `app/mesh.py` 中 `fetch_peer_report`、`mesh_health_history` | 可复用签名直连与分布式样本 |
| 手工体检接口 | `app/main.py` 中 `quality_check_server` | 单服务器体检入口与持久化 |
| 六维评分 | `app/main.py` 中 `build_quality_report` | 分值、证据与建议的生成规则 |
| 页面入口与报告 | `app/static/index.html`、`app/static/app.js`、`app/static/styles.css` | 行内入口、详情入口和报告呈现 |
| 自动化验证 | `tests/test_app.py` | 脚本可编译、评分、接口、审计与静态资源 |
| 生产样本 | 生产 Server Desk 数据库的 CCNODE-MAIN 最近心跳样本，只读查询 | 0/7 与 3/7 相邻出现，应用分保持 100 |
| 真实节点采集 | CCNODE-MAIN SSH 只读体检 | 新增诊断字段可在真实 Linux 主机解析 |
| 三小时矩阵对比 | 生产 `mesh_health_samples` 与 `mesh_poll_cycles` 只读聚合 | CCNODE 独有 86 次未确认和 76 次翻转，主采集周期均成功 |
| CCNODE systemd 状态 | Agent、上报 timer 与 journal 只读查询 | Agent 无重启，timer 为 active / elapsed 且最近触发停在重启前 |
| 正常节点对照 | Riven Gate 与 AWS 节点 timer 只读查询 | 正常 timer 为 active / waiting 且最近触发持续更新 |
| boot 证据 | `uptime`、`last reboot`、`journalctl --list-boots` | 2026-07-27 出现新 boot，上一轮日志缺少正常关机序列 |
