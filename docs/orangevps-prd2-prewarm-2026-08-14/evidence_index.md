# 证据索引

| 编号 | 证据 | 结果 |
| --- | --- | --- |
| E01 | 目标系统资源检查 | Ubuntu 24.04.3，6 vCPU，7.8 GiB，77 GiB 根盘 |
| E02 | SSH 主机指纹三点复核 | ED25519 指纹一致：`SHA256:2CStbt0xMhmM3TmW0dzgtPrxkAzPCAzZQyAD5HdOmVk` |
| E03 | Server Desk 资产记录 | 节点 14，`RHospital.OrangeVPS.Prd2`，`92.113.124.185` |
| E04 | 1Panel 状态 | v2.1.13，core/agent active，本机与外部 HTTP 200 |
| E05 | Docker 运行清单 | PostgreSQL、MySQL、OpenResty、New Relic 运行 |
| E06 | Swarm 运行清单 | `hospital_stack_hospital-backend` 为 `0/0` |
| E07 | 镜像清单 | 9 个迁移所需镜像齐全 |
| E08 | PostgreSQL receiver | recovery=true，streaming，sender=`178.239.117.99` |
| E09 | MySQL replica status | IO=Yes，SQL=Yes，lag=0，错误为空 |
| E10 | MySQL 只读变量 | read_only=1，super_read_only=1 |
| E11 | 外部端口门禁 | 8190、40020、38084 均未开放 |
| E12 | OpenResty 验证 | 配置语法通过，本机 HTTP 200 |
| E13 | systemd 验证 | 目标跟踪服务 active，failed units 为空 |
| E14 | 生产监控页面 | 8/8 心跳在线，新节点在线并显示 0.1% 流量 |
| E15 | 浏览器控制台 | Server Desk 页面未发现本站脚本错误，观察到的告警来自浏览器扩展 |
| E16 | 临时敏感文件清理 | MySQL 转储、源 agent 临时副本、临时 pgpass 已删除 |
| E17 | 临时 SSH 授权清理 | `codex-production-migration-target14` 行数为 0 |

生产监控页面在 2026-08-14 通过已登录的实际生产页面完成截图核验。截图中可见新节点 `RHospital.OrangeVPS.Prd2`、IPv4 `92.113.124.185`、在线状态、当前资源读数和 `10.0 GB / 8.6 TB` 流量读数。
