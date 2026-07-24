# Rhospital Riven Gate 1Panel 安装报告

## 目标

在生产节点 `Rhospital-Riven-Gate` 安装 1Panel，并将面板地址、用户名和加密密码保存到 Server Desk 节点 ID `13`。

## 官方依据

1. 1Panel 官方在线安装文档：<https://1panel.cn/docs/v1/installation/online_installation/>
2. 1Panel 官方命令行文档：<https://1panel.cn/docs/installation/cli/>
3. Docker 官方 Ubuntu 安装文档：<https://docs.docker.com/engine/install/ubuntu/>

Docker 官方文档在 2026 年 7 月列出 Ubuntu 26.04 Resolute 为支持版本。1Panel 官方稳定通道在执行时返回 `v2.2.3`。

## 安装前状态

| 项目 | 状态 |
|---|---|
| 操作系统 | Ubuntu 26.04 LTS x86_64 |
| 可用磁盘 | 36 GiB |
| 可用内存 | 3.2 GiB |
| 1Panel | 未安装 |
| Docker | 未安装 |
| 已监听端口 | 22、80、443、9108 |
| Nginx | active |
| 心跳 Agent | active |

## 安装内容

| 组件 | 版本或状态 |
|---|---|
| 1Panel | `v2.2.3 stable` |
| Docker Engine | `29.6.2` |
| Docker Compose | `5.3.1` |
| Buildx | 通过 Docker 官方仓库安装 |
| containerd | active |
| 面板端口 | `44177/tcp` |
| 面板安全入口 | 已随机生成并保存到 Server Desk URL |
| 面板用户名 | 已随机生成并保存 |
| 面板密码 | 28 位随机复杂密码，加密保存 |

1Panel 安装包 `1panel-v2.2.3-linux-amd64.tar.gz` 的 SHA256 为 `0607a71654aab677d67fcdab8be58daa81bf3de5d022cba455e304d10c1a5f7f`，与官方校验文件一致。

## Server Desk 变更

节点 ID `13` 已写入 `panel_url`、`panel_username` 和 `panel_password_encrypted`。最终校验确认资料库解密结果与目标机 `1pctl user-info` 一致。页面显示面板地址、用户名和遮蔽密码。

环境检查刷新为 `40 个应用，27 个服务，CPU 2 核，内存 3.8Gi`。心跳配置已重新部署，服务监控列表包含 `1panel-agent.service`、`1panel-core.service`、`containerd.service`、`docker.service` 和 `nginx.service`。

## 安装过程中的恢复记录

| 阶段 | 现象 | 处理 |
|---|---|---|
| 第一次安装 | 远程非交互会话缺少 `TERM` | 设置 `TERM=xterm` 后重试 |
| 第二次安装 | 在线安装包没有内置 Docker 目录 | 按 Docker 官方 APT 仓库方式安装 Docker |
| 第三次安装 | 36 位面板密码超过官方 30 位上限 | 删除遗留 UFW 规则，改用 28 位复杂密码 |
| 安装后检查 | 安全入口地址返回 HTTP 301 | 跟随规范化跳转后返回 HTTP 200 |
| 地址解析 | `1pctl user-info` 主机字段显示 `$LOCAL_IP` | 保留安全入口和端口，使用资产公网 IPv4 重建 URL |

## 验收结果

1. 1Panel Core、Agent、Docker、containerd、Nginx 和心跳 Agent 均为 active。
2. 面板在目标机本地和生产资料库主机外部访问均返回 HTTP 200。
3. UFW 已开放 `44177/tcp`。
4. 1Panel 登录页显示用户名、密码和许可协议控件。
5. 登录页控制台日志为空。
6. Docker `hello-world` 验证通过，测试容器与镜像已删除。
7. Docker 当前容器数为 0。
8. 安装包、预检目录和快速安装脚本已删除。

## 安全与后续事项

面板当前使用 HTTP。首次登录涉及许可协议勾选，由使用者在浏览器中完成。建议首次登录后配置面板 HTTPS、授权 IP、MFA 和密码过期策略。

Docker 官方文档提示，容器发布端口可能绕过 UFW。后续部署容器时需要同步检查 `DOCKER-USER` 链和云侧安全组。

## 回滚

1. 执行 `1pctl uninstall` 卸载 1Panel。
2. 删除 UFW 的 `44177/tcp` 放行规则。
3. 确认 Docker 无业务容器后，卸载 `docker-ce`、`docker-ce-cli`、`containerd.io`、`docker-buildx-plugin` 和 `docker-compose-plugin`。
4. 在 Server Desk 清空面板地址、用户名和密码字段，或使用变更前备份 `ops.sqlite3.20260724T103403Z.enc` 恢复。
