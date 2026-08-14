# 测试结果

## 生产环境验收

| 测试 | 期望 | 实际 | 状态 |
| --- | --- | --- | --- |
| 主机身份 | 新主机名和新 IP | `RHospital.OrangeVPS.Prd2` / `92.113.124.185` | 通过 |
| 1Panel | v2.1.13 且可访问 | core/agent active，HTTP 200 | 通过 |
| PostgreSQL | recovery + streaming | true + streaming | 通过 |
| MySQL | 双线程运行且 lag=0 | Yes / Yes / 0 | 通过 |
| MySQL 写保护 | 两个只读变量为 1 | 1 / 1 | 通过 |
| Flarum 数据 | 34 张表 | 34 | 通过 |
| 后端服务 | 今日保持停用 | 0/0 | 通过 |
| 业务入口 | 今日保持关闭 | 8190、40020、38084 关闭 | 通过 |
| OpenResty | 配置有效 | syntax ok，HTTP 200 | 通过 |
| 心跳 | 生产页面在线 | 8/8 在线，新节点在线 | 通过 |
| 流量读数 | 新节点有基线 | 10.0 GB / 8.6 TB，0.1% | 通过 |
| failed units | 空 | 空 | 通过 |
| 临时授权 | 已删除 | 0 行 | 通过 |

## 受控失败与修正

1. PostgreSQL 首次 base backup 使用的 pgpass 仅 root 可读，容器内 UID 999 无法读取。修正文件属主和权限后重试成功。
2. MySQL 只读变量最初写入了错误配置段。调整到 mysqld 配置段后生效。
3. MySQL 首次初始化提前启用 super_read_only，阻止入口脚本设置 root 密码。清空未使用的目标数据目录后按初始化顺序重建，再启用只读保护。
4. 首个复制密码长度超过 MySQL 32 字符限制。改用满足限制的高强度随机密码后成功。
5. 恢复后复制到 `ALTER USER` 时，目标缺少对应用户。短时受控解除只读，建立占位用户，恢复只读后继续 SQL thread，最终 lag=0。
6. Docker scale 客户端等待超时，服务实际状态保持 0/0。终止等待客户端后复核实际状态。
7. 综合应用检查中的无匹配 grep 在 pipefail 下返回非零。逐项复核后确认当前生效配置没有旧 IP 引用。
8. `systemd-networkd-wait-online` 在首次启动期间超时。网络达到 routable/configured 后重试成功并清空 failed 状态。

## 仓库测试

- 命令：`.venv\\Scripts\\python.exe -m pytest -q --basetemp=.task\\orangevps-prd2-pytest`
- 结果：`57 passed in 15.48s`
- 状态：通过
