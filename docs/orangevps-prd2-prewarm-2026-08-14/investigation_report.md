# OrangeVPS Prd2 生产预热调查报告

## 任务范围

- 日期：2026-08-14
- 源生产：`host1782378673.orangevps`，`178.239.117.99`
- 新生产候选：`RHospital.OrangeVPS.Prd2`，`92.113.124.185`
- Server Desk 资产编号：`14`
- 今日目标：完成新主机建档、环境复制、数据库只读预热、流量监控接入和验收。
- 今日边界：保留旧生产写入职责，保留两个 Gate 的现有上游，业务容器维持停用，不执行生产切换。

## 已完成状态

### 资产与基础环境

- 新主机名已设置为 `RHospital.OrangeVPS.Prd2`。
- 系统为 Ubuntu 24.04.3 LTS，6 vCPU，约 7.8 GiB 内存，77 GiB 根盘。
- 已安装 Docker 29.1.3、Compose 2.40.3、1Panel v2.1.13。
- 1Panel 核心与代理服务均为 active，本机面板和外部面板均返回 HTTP 200。
- Server Desk 配置检查返回 40 个应用、34 个服务、6 核 CPU、7.8 GiB 内存。
- 分布式心跳已跟踪 `1panel-agent`、`1panel-core`、`containerd`、`docker`，生产页面显示节点在线。

### 应用、配置与镜像

- 已复制源生产的当前应用目录、Compose 配置、站点文件、业务备份、资源文件和自定义服务脚本。
- 已载入 9 个所需镜像，包括当前后端版本 `hospital-backend:20260813` 和两个回滚版本。
- 已创建 `forum_sso_secret` 与 `support_mail_password` 两个 Swarm secret。
- 后端 Swarm 服务保持 `0/0`。
- Flarum 与 SnailJob 容器保持未启动。
- PostgreSQL、MySQL、OpenResty、New Relic Infrastructure 已启动。
- 当前生效的 Compose 配置已替换为新主机地址。历史备份配置和 Web 日志仍可能记录旧地址，这些文件不参与当前运行。

### PostgreSQL 预热

- 新建专用复制角色 `rhospital_new_standby`。
- 新建物理复制槽 `rhospital_backup_92_113_124_185`。
- 目标端 `pg_is_in_recovery()` 为 true。
- WAL receiver 状态为 streaming，发送端为 `178.239.117.99`。
- `hospital` 数据库大小为 1,233,420,815 字节，`snailjob` 为 78,488,079 字节。

### MySQL 预热

- 目标端 `read_only=1`，`super_read_only=1`。
- `Replica_IO_Running=Yes`。
- `Replica_SQL_Running=Yes`。
- `Seconds_Behind_Source=0`。
- Last IO Error 与 Last SQL Error 均为空。
- Flarum 数据库 `flarum_rtt3ns` 有 34 张表。

### 流量监控

- Server Desk 已绑定 OrangeVPS 服务编号 `12143` 和服务器标识 `host1786681852.orangevps`。
- 客户区核验原始读数为 `9.3 GiB / 8000 GiB`。
- Server Desk 当前显示约 `10.0 GB / 8.6 TB`，使用率 `0.1%`。
- OrangeVPS 连接器现有账户密码无法完成自动登录，因此自动同步保持关闭，状态为 `credential_required`。
- 当前读数有来源、周期和采集时间，可用于今日监控基线。更新 OrangeVPS 管理密码后再启用自动同步。

## 网络基线

标准 60 包测试结果：

| 来源 | 旧生产平均延迟 | 新生产平均延迟 | 丢包 |
| --- | ---: | ---: | ---: |
| Riven Gate | 3.215 ms | 2.687 ms | 两端 0% |
| RHOSPITAL Gate | 1.579 ms | 1.979 ms | 两端 0% |

CNDRP 到新主机的快速样本平均约 45.471 ms，样本出现 16% 丢包。该结果来自短时 ICMP 观测，只作为当时线路证据。生产入口尚未切换，因此不会影响当前线上业务。

## 运行门禁

- 外部 1Panel 地址返回 HTTP 200。
- `8190`、`40020`、`38084` 三个业务端口从操作端测试均为关闭。
- OpenResty 配置语法检查通过，本机 80 端口返回 HTTP 200。
- Docker、containerd、1Panel、Server Desk 心跳和 MSS 服务均为 active。
- `systemd-networkd-wait-online` 首次启动曾超时。网络已处于 routable/configured，重试成功，failed unit 列表已清空。
- 生产 housekeeping timer 保持 disabled，等待切换日后端达到 `1/1` 再启用。

## 安全与回滚

- 已删除目标端 MySQL 全量转储、源 1Panel 临时数据库副本和临时 pgpass。
- 已删除目标端临时迁移 SSH 授权行。
- 保留目标端应用覆盖前快照 `1panel-before-app-copy-20260814T045455Z.tar.gz`。
- 保留目标端 1Panel agent 数据库覆盖前快照 `target-agent-before-copy.db`。
- 保留 housekeeping 审计日志。

## 切换日前置条件

1. 再执行一次应用文件增量同步并记录完成时间。
2. 复核 PostgreSQL 与 MySQL 复制延迟均为 0，确认只读状态。
3. 在维护窗口内停止旧生产写入，并按正式迁移方案提升新数据库。
4. 启动 Flarum、SnailJob 和后端，将后端验收到 `1/1`。
5. 完成健康检查、登录和关键业务冒烟测试。
6. 更新两个 Gate 的上游并观察错误率、延迟和回源连接。
7. 后端稳定后启用 housekeeping timer。
8. 更新 OrangeVPS 管理凭据并开启流量自动同步。
