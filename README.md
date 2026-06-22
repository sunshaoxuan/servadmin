# Server Desk

Server Desk 是一个网页形式的服务器管理 APP，用于维护多台服务器的资产信息、登录方式、SSH 检查结果、备注和操作审计。

## 功能

- 登录保护的运维工作台
- 服务器新增、编辑、删除和搜索
- 主机名、IPv4、IPv6、供应商、区域、服务代码、标签和备注维护
- SSH 22 端口连通性检查
- 登录凭据加密存储
- 详情面板固定显示密码框，默认遮蔽，支持显示和复制
- 密码读取、显示和复制使用加密凭据接口，并记录凭据查看审计
- 设置页签提供异步应用和服务状态检测，覆盖 `server-desk`、`nginx`、`frps`、`xray` 等 systemd 服务，并解析 Nginx 代理应用做本机端口连通性检测；服务器列表、详情和服务卡片使用绿、红、黄状态点辅助快速判断
- 最近操作审计
- 基于 Tabler 的运维控制台界面，使用图标化 KPI 总览、资产表格、行内操作、详情面板、审计动作标签和移动端卡片式行

## 安全边界

- 代码仓库不保存真实服务器密码。
- `OPS_CREDENTIAL_KEY` 用于 Fernet 加密，部署后需要长期保存。
- `OPS_APP_SECRET` 用于登录 Cookie 签名，部署后需要长期保存。
- 自用运维场景下，选中服务器后详情面板会固定展示密码输入框。密码默认以遮蔽形式显示，点击显示或复制时使用相同的加密凭据接口，并写入审计记录。

## 本地运行

```powershell
cd C:\workspace\server-admin-app
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
$env:OPS_ADMIN_PASSWORD='admin-pass'
$env:OPS_APP_SECRET='change-me'
$env:OPS_CREDENTIAL_KEY=(.\.venv\Scripts\python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
.\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8090
```

访问 `http://127.0.0.1:8090`，账号为 `admin`。

## 测试

```powershell
cd C:\workspace\server-admin-app
.\.venv\Scripts\python -m pytest
```

## 部署说明

部署目标路径建议为 `/opt/server-desk`，systemd 服务名为 `server-desk`，Nginx 反代到本机 `127.0.0.1:8090`。

当前部署路径为 `https://ccnode.briconbric.com/server-desk/`。Nginx 使用 `/etc/letsencrypt/live/briconbric.com/fullchain.pem` 和 `/etc/letsencrypt/live/briconbric.com/privkey.pem` 的通用证书。

首页和本地静态资源会返回 `Cache-Control: no-cache, no-store, must-revalidate`，前端 CSS/JS 使用版本号查询参数，避免部署后浏览器继续展示旧版界面。

证书验收需要同时确认两层：TLS 客户端能验证到 `*.briconbric.com` 的 Let's Encrypt 证书链，浏览器安全状态为 `secure` 且没有 `http://` 混合资源。当前线上证书有效期为 2026-05-06 到 2026-08-04，部署验证使用 Chrome 和 Edge 的安全事件确认。线上环境应设置 `OPS_COOKIE_SECURE=1`，Nginx 片段会返回 HSTS、`X-Content-Type-Options` 和 `Referrer-Policy` 响应头。

### 生产 Git 同步

生产目录应保持为 Git 工作树。`server-desk-git-sync.timer` 每 5 分钟检查 `origin/main`，发现新提交后会先执行数据库加密备份，再停止服务、快进更新、安装依赖、运行测试并启动服务。

```bash
install -m 0755 scripts/server_desk_git_sync.sh /opt/server-desk/scripts/server_desk_git_sync.sh
install -m 0755 scripts/server_desk_backup.sh /opt/server-desk/scripts/server_desk_backup.sh
install -m 0644 deploy/server-desk-git-sync.service /etc/systemd/system/server-desk-git-sync.service
install -m 0644 deploy/server-desk-git-sync.timer /etc/systemd/system/server-desk-git-sync.timer
systemctl daemon-reload
systemctl enable --now server-desk-git-sync.timer
```

如果生产目录存在本地改动，同步脚本会拒绝更新，避免覆盖未入库文件。

### 加密备份

`server-desk-backup.timer` 每天执行一次 SQLite 一致性备份。备份文件会使用 `/etc/server-desk/backup.key` 加密，提交到 `/var/lib/server-desk-backups`，并在配置 `BACKUP_GIT_REMOTE` 后推送到指定 Git 仓库。

```bash
install -m 0600 deploy/backup.env.example /etc/server-desk/backup.env
openssl rand -base64 48 > /etc/server-desk/backup.key
chmod 600 /etc/server-desk/backup.key
install -m 0644 deploy/server-desk-backup.service /etc/systemd/system/server-desk-backup.service
install -m 0644 deploy/server-desk-backup.timer /etc/systemd/system/server-desk-backup.timer
systemctl daemon-reload
systemctl enable --now server-desk-backup.timer
```

`BACKUP_GIT_REMOTE` 建议使用只存放加密备份的私有仓库。不要把 `/etc/server-desk/backup.key` 提交到 Git。

## 首台服务器导入

使用 `scripts/seed_server.py` 通过环境变量导入，真实登录凭据只进入运行时数据库。
