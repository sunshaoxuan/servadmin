# 证据索引

| 结论 | 证据 | 可信度 | 限制 |
|---|---|---|---|
| 1Panel 安装包来自官方稳定通道 | 官方 latest 接口返回 `v2.2.3`，包哈希与 checksums.txt 一致 | 高 | 下载时间为 2026-07-24 |
| Ubuntu 26.04 支持 Docker | Docker 官方 Ubuntu 文档列出 Resolute 26.04 | 高 | 后续支持列表可能变化 |
| Docker 可用 | Engine 29.6.2、Compose 5.3.1、hello-world 通过 | 高 | 当前没有业务容器 |
| 1Panel 服务正常 | 1panel-core 与 1panel-agent 均为 active | 高 | 未执行许可协议后的登录 |
| 面板外部可达 | 生产资料库主机访问安全入口返回 HTTP 200 | 高 | 当前协议为 HTTP |
| 访问信息已保存 | URL、用户名和解密密码与 `1pctl user-info` 一致 | 高 | 证据未输出明文密码 |
| Server Desk 页面正确 | `docs/assets/riven-1panel-server-desk-verified-20260724.png` | 高 | 密码保持遮蔽 |
| 1Panel 登录页正确 | `docs/assets/riven-1panel-login-verified-20260724.png` | 高 | 未提交登录表单 |
| 环境检查已刷新 | Server Desk 返回 40 个应用、27 个服务 | 高 | 应用数为采集器统计口径 |
| 心跳服务列表已刷新 | 单节点部署返回 1 成功、0 失败 | 高 | 只重部署节点 13 |
| 变更前备份成功 | `ops.sqlite3.20260724T103403Z.enc` 与 SHA256 文件 | 高 | 备份只在资料库主机本地 |
| 变更后备份成功 | `ops.sqlite3.20260724T104536Z.enc` 与 SHA256 文件 | 高 | 备份只在资料库主机本地 |
