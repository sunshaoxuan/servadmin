# 测试结果

| 测试 | 执行方式 | 结果 |
|---|---|---|
| PEM 登录 | 本机到 `root@45.94.40.77:22` 的真实 SSH 会话 | 通过 |
| root 密码登录 | 资料库主机到新节点的真实 Paramiko 会话 | 通过 |
| SSH 配置语法 | 新节点 `sshd -t` | 通过 |
| SSH 生效策略 | 新节点 `sshd -T` | 公钥、密码、root 密码均启用 |
| Server Desk 端口检查 | 生产运行身份连接新节点 22 端口 | 在线，109 ms |
| Server Desk 环境检查 | 生产运行身份执行完整采集 | 正常，40 个应用，20 个服务 |
| 心跳部署预演 | 7 个 Ubuntu 目标与 8 个注册节点 | 通过 |
| 心跳部署 | 7 个目标实际部署 | 7 成功，0 失败 |
| 新节点 Agent | systemd、定时器、9108 监听 | 通过 |
| UFW 白名单 | 新节点 9108 仅列出矩阵节点来源 | 通过 |
| 主服务合并 | 8 个候选报告源全量轮询 | 4 成功，4 个旧节点超时 |
| 新节点样本 | 主服务直接读取 ID 13 | 网络与应用均为 100%，外部可见 2 个 |
| 生产页面 | Chrome 已登录会话 | 在线、正常、详情字段完整 |
| 浏览器控制台 | Chrome 日志检查 | 站点错误 0，扩展警告 3 |
| 变更前备份 | server-desk-backup.service | 成功 |
| 变更后备份 | server-desk-backup.service | 成功 |

## 项目测试

最终命令：

```powershell
.\.venv\Scripts\python.exe -m pytest --basetemp .task-test\riven-gate-pytest
```

最终结果为 `46 passed in 10.49s`。

首次运行使用系统临时目录，20 项通过，26 项因 `C:\Users\X02851\AppData\Local\Temp\pytest-of-X02851` 权限拒绝而中止。第二次指定任务内路径时父目录尚未创建。创建仓库内 `.task-test` 父目录后，46 项全部通过。测试完成后删除 `.task-test`。
