# Rhospital Riven Gate 生产接入调查报告

## 目标

将新节点 `Rhospital-Riven-Gate` 接入 Server Desk 生产资料库，支持 `sunsxaws.pem` 登录，启用 root 密码登录，完成 SSH 检查、环境检查、心跳矩阵部署与生产页面验收。

## 已验证资产

| 字段 | 值 |
|---|---|
| Server Desk ID | `13` |
| 主机名 | `Rhospital-Riven-Gate` |
| IPv4 | `45.94.40.77` |
| IPv6 | `2a12:a303:11a:47::a` |
| 供应商 | `Riven Cloud` |
| 区域 | `Tokyo / JP-RYZEN` |
| 系统 | `Ubuntu 26.04 LTS` |
| CPU | `2 vCPU, AMD Ryzen 9 9950X` |
| 内存 | `3.8 GiB` |
| 系统盘 | `40 GiB` |
| SSH | `root@45.94.40.77:22` |
| 生产密钥 | `/etc/server-desk/ssh/sunsxaws.pem` |
| Windows 密钥 | `C:\workspace\Secure\sunsxaws.pem` |
| 心跳端口 | `9108/tcp` |

## 完成的生产变更

1. 使用本机已记录指纹与生产资料库主机独立扫描结果核对新节点 ED25519 指纹，两端均为 `SHA256:v9tFc10PKtoWeeQFxTUSiONLeXxBbytlX53xRV8hMiY`。
2. 在新节点写入 `/etc/ssh/sshd_config.d/00-server-desk-password-auth.conf`，启用公钥认证、密码认证与 root 密码认证。
3. 生成高强度 root 密码，完成真实密码会话验证，并将凭据通过生产 `OPS_CREDENTIAL_KEY` 加密后保存。
4. 在生产资料库创建 Server Desk 节点 ID `13`，记录 IPv4、IPv6、区域、标签、PEM 路径与连接参数。
5. 执行 Server Desk SSH 检查与环境检查，结果为在线和正常。
6. 对 7 个有效 Ubuntu 节点执行心跳矩阵部署，全部成功，失败数为 0。
7. 新节点 Agent、报告定时器、9108 监听与 UFW 来源白名单均已验证。
8. 使用生产 Chrome 会话验收节点列表与详情页，保存截图 `docs/assets/riven-node-production-verified-20260724.png`。
9. 页面结构检查接触密码框底层值后立即轮换 root 密码，旧值失效，新值完成实际登录验证并更新加密资料库。

## 结果

生产页面显示节点在线，配置状态正常，应用健康为 100%。验收时心跳传播达到 `3/7`，网络趋势约为 `85%` 至 `86%`。新节点报告由主服务直接读取，Agent 记录了对节点 `12` 与节点 `7` 的成功上报。

## 已知风险

| 风险 | 当前证据 | 影响 |
|---|---|---|
| 旧节点报告端口超时 | 全量轮询时节点 `1、3、4、5` 的 9108 读取超时 | 主服务仍从节点 `2、7、12、13` 取得报告，新节点状态可见 |
| RHOSPITAL-GATE 离线 | Server Desk 显示 `64.83.37.55:3022` 连接超时 | 该旧网关未参与本轮 Agent 重部署目标 |
| 备份未配置远端推送 | 备份服务记录 `BACKUP_GIT_REMOTE is not configured` | 变更前后加密备份只存放于资料库主机本地 |
| root 密码认证扩大攻击面 | `PasswordAuthentication yes` 与 `PermitRootLogin yes` 生效 | 依赖高强度随机密码、UFW 与后续登录审计控制风险 |

## 回滚方式

1. 从 Server Desk 将节点 ID `13` 标记失效，再对有效 Ubuntu 节点执行一次全量心跳部署以传播成员墓碑。
2. 删除 `/etc/ssh/sshd_config.d/00-server-desk-password-auth.conf`，执行 `sshd -t`，重载 SSH，然后使用 `passwd -l root` 锁定 root 密码。
3. 需要恢复资料库时，使用变更前加密备份 `ops.sqlite3.20260724T092754Z.enc` 和对应 SHA256 文件。
4. 最终状态备份为 `ops.sqlite3.20260724T093751Z.enc`，可用于恢复完成接入后的资料库状态。
