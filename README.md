# Server Desk

Server Desk 是一个网页形式的服务器管理 APP，用于维护多台服务器的资产信息、登录方式、SSH 检查结果、备注和操作审计。

## 功能

- 登录保护的运维工作台
- 新监控首页采用 One人事官网当前的橙色、青色、浅灰背景与参考面板的白色侧栏、圆角卡片设计语言。首页为每台有效服务器生成独立监视块，统一显示分布式心跳、CPU、内存、根分区空间、网络上下行、磁盘读写和月度套餐流量
- 心跳 Agent 2.1 采集 Linux CPU 累计时间、默认路由网卡收发字节、物理磁盘读写字节及根分区容量。管理端使用相邻样本计算实时 IO 速率，计数器回绕或样本时间无效时显示等待样本
- 月度套餐流量只使用供应商管理画面或正式 API 的权威读数。每台服务器独立保存供应商账期、精确重置时刻、IANA 时区、已用量和套餐限额。首页把供应商重置时刻换算为东京时间，同时保留供应商原始时区。Agent 网卡累计计数只用于实时 IO
- 服务器新增、编辑、删除和搜索
- 主机名、IPv4、IPv6、供应商、区域、服务代码、标签和备注维护
- SSH 22 端口连通性检查
- 登录凭据加密存储
- 详情面板固定显示密码框，默认遮蔽，支持显示和复制
- 密码读取、显示和复制使用加密凭据接口，并记录凭据查看审计
- 设置页签提供异步应用和服务状态检测，覆盖 `server-desk`、`nginx`、`frps`、`xray` 等 systemd 服务，并解析 Nginx 代理应用做本机端口连通性检测；服务器列表、详情、详情服务页签和服务卡片使用绿、红、黄状态点辅助快速判断
- Ubuntu 节点可部署分布式心跳 Agent。V2 每分钟调度一轮，每轮最多向三个到期邻居主动上报，同一目标至少间隔 5 分钟；稳定邻居维持连续路径，随机邻居完成扩散。节点使用成员摘要、心跳版本和邻居水位做双向增量同步，并持有最近 5 分钟内至少一个邻居的成功确认。self-only 状态不计为在线。主服务每分钟合并三个随机报告节点的视角，网络与应用数据均由对应节点采集，CCNODE 只读取报告。采集器无法取得任何报告源时记录独立采集异常，不把所有节点误判为离线；三小时曲线用灰色断点标记采集缺口，网络趋势同时反映心跳年龄和同伴可见率
- 服务器列表和详情页支持对单台节点手工执行“全面体检”。体检采集操作系统、主板 BIOS、虚拟化、运行时间、负载、CPU、GPU、内存、磁盘、inode、时间同步、失败服务、网络、公网 IP、出站质量、TCP 策略、SSH 策略、防火墙、应用和服务端口，并读取心跳 Agent 直连结果、主动上报定时器状态及最近 30 个分布式样本。新报告按访问链路、系统资源、网络质量、服务状态、访问安全、心跳传播六个维度给出透明评分、原始证据和逐项建议。心跳定时器显示为 `active / elapsed` 或缺少下一次触发时将明确列为异常
- 传统“检查配置”入口继续用于快速刷新资产清单；“全面体检”会刷新清单并生成第二版环境质量体检报告。一次仅执行一项检查，体检期间对应节点的行内操作和详情操作进入加载态
- SSH 检查和配置检查执行期间，详情按钮和行内按钮进入禁用加载态，并显示旋转图标，防止重复触发
- Debian/Ubuntu 应用清单优先读取 dpkg 状态文件，并限制采样数量，避免慢速包管理命令阻塞整次配置检查
- 密码认证节点优先通过目标机 `python3` 执行环境采集脚本，缺少 `python3` 时回退 shell 采集脚本
- 服务器支持标记为已失效，列表默认隐藏已失效节点，可通过过滤开关显示；已失效节点保留历史报告和编辑能力，禁止 SSH 检查和环境检测
- 最近操作审计
- 基于 Tabler 的运维控制台界面，使用图标化 KPI 总览、资产表格、行内操作、详情面板、审计动作标签和移动端卡片式行

## 安全边界

- 代码仓库不保存真实服务器密码。
- `OPS_CREDENTIAL_KEY` 用于 Fernet 加密，部署后需要长期保存。
- `OPS_APP_SECRET` 用于登录 Cookie 签名，部署后需要长期保存。
- `OPS_MESH_SECRET` 用于节点报告和主服务读取报告时的 HMAC 签名，所有 Agent 与主服务必须保持一致，长度至少为 32 个字符。
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

### 分布式心跳矩阵

当前协议和状态模型详见 [docs/heartbeat-mesh-protocol-v2.md](docs/heartbeat-mesh-protocol-v2.md)。

主服务环境变量：

```bash
OPS_MESH_ENABLED=1
OPS_MESH_SECRET=<至少32字符的共享密钥>
OPS_MESH_INTERVAL_SECONDS=60
```

部署脚本从主服务环境文件读取数据库路径、凭据密钥和矩阵密钥。首次执行先检查目标列表，再部署所有环境检测结果为 Ubuntu 的有效节点：

```bash
/opt/server-desk/.venv/bin/python /opt/server-desk/scripts/deploy_heartbeat_mesh.py \
  --env-file /etc/server-desk/server-desk.env \
  --all-ubuntu --configure-firewall --dry-run

/opt/server-desk/.venv/bin/python /opt/server-desk/scripts/deploy_heartbeat_mesh.py \
  --env-file /etc/server-desk/server-desk.env \
  --all-ubuntu --configure-firewall
```

新增 Ubuntu 节点时可使用 `--server-id <ID>`。脚本会把已有心跳节点作为启动种子，并写入本轮统一的成员部署纪元。新节点随机尝试最多五个可用种子，收到一个有效确认后完成注册。节点标记失效后需要执行一次 `--all-ubuntu` 部署，使活动节点生成并传播成员墓碑。部署会重启 Agent、重启定时器并立即报告一次。Agent 使用 `9108/tcp`；`--configure-firewall` 只向当前矩阵节点 IP 添加 UFW 来源白名单，云厂商安全组需要单独配置相同来源。

Agent V2 依据邻居版本水位发送最多 64 条增量记录，正常周期最多连接三个目标，失败后执行指数退避。`seen_by` 固定为八个节点 ID，`seen_count` 保留见证数量。管理端每 60 秒从最多六个候选中取得三个报告并合并；有外部邻居确认时，300 到 660 秒显示同步延迟，超过 660 秒判定离线；self-only 状态显示为“未被邻居确认”。零个成功报告源时只写入 `mesh_poll_cycles` 采集失败记录，节点沿用最后一个可信样本。页面网络趋势使用 75% 心跳新鲜度和 25% 同伴可见率计算，原始 100、70、0 状态继续作为在线边界。

### 月度订阅流量

首页的套餐已用量、套餐限额和计费周期只接受供应商管理画面或官方 API 的读数。Agent 网卡计数保留在网络 IO 区域，用于实时速率和异常诊断，不参与套餐用量计算。

点击卡片的“录入后台读数”后，可按服务器保存独立的计费周期、已用流量、套餐限额、来源名称和管理画面地址。同一服务器与同一计费周期再次保存会更新读数并刷新采集时间。尚未接入供应商数据时，卡片显示等待状态，不显示由机器网卡推算的套餐数值。

供应商提供精确重置时刻时，同时保存当地时间和 IANA 时区。首页以东京时间显示下一次重置，并在下方保留供应商当地时间。VMISS 总部位于多伦多，其管理画面时间使用 `America/Toronto` 解释，夏令时偏移由时区数据库计算。

每台服务器可以单独保存 VPS 供应商后台地址、管理用户、加密密码、供应商服务编号、外部服务器标识、连接器类型和同步开关。供应商密码由 `OPS_CREDENTIAL_KEY` 加密，列表与普通详情接口只返回是否已保存密码。连接页按需读取、显示或复制密码，并记录审计事件。供应商后台资料与 SSH、1Panel 资料分开保存。

Riven Cloud 连接器使用客户区登录、VirtFusion 单点登录和供应商流量接口读取实际周期、接收、发送与总量。连接器使用最近一次供应商限额作为当前套餐额度，同周期读数执行更新。连接页可以手工立即同步，后台默认在启动 30 秒后执行一次，之后每 6 小时同步。`OPS_PROVIDER_SYNC_ENABLED=0` 可停用后台同步，`OPS_PROVIDER_SYNC_INTERVAL_SECONDS` 可调整周期，最短为 900 秒。

数据库迁移由 `schema_migrations` 记录。版本 1 创建供应商读数表。版本 2 曾创建 Agent 套餐账本，版本 3 根据数据边界纠正清空错误口径数据。版本 4 创建供应商后台加密档案。版本 5 增加供应商精确重置时刻和 IANA 时区。运行代码不再读写 Agent 套餐账本。该表结构在本次发布观察期内保留，用于上一版本的短期回滚安全，稳定后由独立迁移删除。迁移均可重复检查。发布回滚前应先备份 SQLite 数据库。

当前部署路径为 `https://ccnode.briconbric.com/server-desk/`。Nginx 使用 `/etc/letsencrypt/live/briconbric.com/fullchain.pem` 和 `/etc/letsencrypt/live/briconbric.com/privkey.pem` 的通用证书。

首页和本地静态资源会返回 `Cache-Control: no-cache, no-store, must-revalidate`，前端 CSS/JS 使用版本号查询参数，避免部署后浏览器继续展示旧版界面。

证书验收需要同时确认两层：TLS 客户端能验证到 `*.briconbric.com` 的 Let's Encrypt 证书链，浏览器安全状态为 `secure` 且没有 `http://` 混合资源。当前线上证书有效期为 2026-05-06 到 2026-08-04，部署验证使用 Chrome 和 Edge 的安全事件确认。线上环境应设置 `OPS_COOKIE_SECURE=1`，Nginx 片段会返回 HSTS、`X-Content-Type-Options` 和 `Referrer-Policy` 响应头。

### 生产 Git 同步

生产目录应保持为 Git 工作树。`server-desk-git-sync.timer` 每 5 分钟检查 `origin/main`，发现新提交后会先执行数据库加密备份，再停止服务、快进更新、安装依赖、运行测试并启动服务。

服务启动后，发布脚本会在最多 30 秒内逐秒检查 `/api/health`。应用完成启动即判定发布成功，超时才记录失败，避免固定等待时间短于实际启动耗时造成假告警。

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
