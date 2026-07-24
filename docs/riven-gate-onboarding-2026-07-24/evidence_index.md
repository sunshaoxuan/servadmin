# 证据索引

| 结论 | 证据 | 可信度 | 限制 |
|---|---|---|---|
| PEM 与 root 授权密钥一致 | 本机 PEM 指纹 `SHA256:uKoRl6Mcy+TwQqYVm4lXFXTOCBkpX+MinXhThhAFV7c` 与远端 `authorized_keys` 指纹一致 | 高 | 仅覆盖当前 RSA PEM |
| 新节点主机指纹一致 | 本机 known hosts 与资料库主机扫描均返回 ED25519 `SHA256:v9tFc10PKtoWeeQFxTUSiONLeXxBbytlX53xRV8hMiY` | 高 | 云控制台侧指纹未单独显示 |
| root 密码登录生效 | 两次密码轮换后均从资料库主机建立真实 Paramiko 会话，最终回执为 `password_login: ok` | 高 | 密码内容未写入证据文件 |
| SSH 策略生效 | `sshd -T` 返回 `permitrootlogin yes`、`pubkeyauthentication yes`、`passwordauthentication yes` | 高 | 仅针对当前全局配置 |
| 节点资料已落库 | 生产 SQLite 查询返回 ID `13` 及完整非敏感字段 | 高 | 加密凭据内容未输出 |
| SSH 与环境检查通过 | Server Desk 运行身份执行检查，结果 `online 109ms` 与 `40 个应用，20 个服务，CPU 2 核，内存 3.8Gi` | 高 | 公网 IP 子项未由采集器返回 |
| 心跳部署通过 | 7 个目标逐台返回 `ok: true`，汇总 `deployed: 7, failed: 0` | 高 | 旧节点 `5` 未进入部署目标 |
| 新节点心跳可见 | 主服务样本返回直接读取成功、应用 100%、网络 100%、外部可见 2 个及后续页面传播 3/7 | 高 | 传播比例会随时间变化 |
| 生产 UI 展示正确 | `docs/assets/riven-node-production-verified-20260724.png` | 高 | 截图时间为 2026-07-24 18:35 JST |
| 站点控制台无自身错误 | Chrome 控制台仅出现浏览器扩展主题依赖警告 | 高 | 只覆盖本次页面会话 |
| 变更前备份成功 | `ops.sqlite3.20260724T092754Z.enc` 与 SHA256 文件，服务结果 success | 高 | 备份只在资料库主机本地 |
| 变更后备份成功 | `ops.sqlite3.20260724T093751Z.enc` 与 SHA256 文件，服务结果 success | 高 | 备份只在资料库主机本地 |
