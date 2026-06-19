# Server Desk

Server Desk 是一个网页形式的服务器管理 APP，用于维护多台服务器的资产信息、登录方式、SSH 检查结果、备注和操作审计。

## 功能

- 登录保护的运维工作台
- 服务器新增、编辑、删除和搜索
- 主机名、IPv4、IPv6、供应商、区域、服务代码、标签和备注维护
- SSH 22 端口连通性检查
- 登录凭据加密存储
- 凭据查看审计
- 最近操作审计

## 安全边界

- 代码仓库不保存真实服务器密码。
- `OPS_CREDENTIAL_KEY` 用于 Fernet 加密，部署后需要长期保存。
- `OPS_APP_SECRET` 用于登录 Cookie 签名，部署后需要长期保存。
- 凭据查看会写入审计记录。

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

如果暂时没有域名，可以先通过 `http://ccnode.briconbric.com/server-desk/` 访问。生产使用建议解析独立域名并启用 HTTPS。

## 首台服务器导入

使用 `scripts/seed_server.py` 通过环境变量导入，真实登录凭据只进入运行时数据库。
