# 命令记录

本文仅记录可公开的命令结构。密码在远端内存中生成与传递，未写入命令记录。

## 本机与新节点核对

```powershell
ssh-keygen -lf C:\workspace\Secure\sunsxaws.pem
ssh -i C:\workspace\Secure\sunsxaws.pem root@45.94.40.77 <只读主机检查>
```

## 生产资料库核对

```powershell
ssh -i C:\workspace\Secure\sunsxaws.pem root@203.24.89.50 <Server Desk 非敏感字段查询>
```

## 备份

```bash
systemctl start server-desk-backup.service
systemctl show server-desk-backup.service -p Result --value
```

## 心跳预演与部署

```bash
/opt/server-desk/.venv/bin/python /opt/server-desk/scripts/deploy_heartbeat_mesh.py \
  --env-file /etc/server-desk/server-desk.env \
  --all-ubuntu --configure-firewall --dry-run

/opt/server-desk/.venv/bin/python /opt/server-desk/scripts/deploy_heartbeat_mesh.py \
  --env-file /etc/server-desk/server-desk.env \
  --all-ubuntu --configure-firewall
```

## 新节点验收

```bash
systemctl is-active server-desk-heartbeat.service
systemctl is-active server-desk-heartbeat-report.timer
ss -lnt
ufw status
journalctl -u server-desk-heartbeat.service -u server-desk-heartbeat-report.service
```
