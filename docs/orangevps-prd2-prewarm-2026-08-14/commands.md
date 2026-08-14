# 操作摘要

本文只记录可审计的操作类别，凭据、数据库密码和 Swarm secret 明文均不落盘。

## Server Desk

- 在变更前执行生产数据库加密备份。
- 新增节点 14 并保存 SSH 与 1Panel 凭据的加密值。
- 部署分布式心跳并执行配置检查。
- 绑定 OrangeVPS 服务 12143，录入已核验的本月流量读数。

## 主机预热

- 设置新主机名并安装与源生产一致的 Docker、Compose 和 1Panel 版本。
- 复制应用目录、站点文件、Compose 配置、镜像和自定义 systemd 单元。
- 初始化独立 Swarm，创建所需 secret，将后端服务缩放为 0。
- 启动基础设施容器，保留业务容器停用状态。

## 数据库

- PostgreSQL 使用专用角色和物理复制槽执行 base backup，持续接收 WAL。
- MySQL 使用专用复制用户恢复全量快照并启动 GTID/坐标复制。
- 目标 MySQL 强制 read_only 与 super_read_only。

## 清理

- 删除包含生产数据或认证信息的临时转储。
- 删除临时迁移 SSH 授权。
- 保留明确命名的覆盖前回滚快照和审计日志。
