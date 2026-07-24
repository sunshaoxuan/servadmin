# 命令记录

本文省略面板安全入口、用户名和密码。真实密码只在内存中传递，并以 Fernet 密文保存。

## 官方安装包预检

```bash
curl -fsSL https://resource.fit2cloud.com/1panel/package/v2/stable/latest
curl -fsSL https://resource.fit2cloud.com/1panel/package/v2/stable/v2.2.3/release/checksums.txt
sha256sum 1panel-v2.2.3-linux-amd64.tar.gz
```

## Docker 安装与检查

```bash
apt-get install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
docker version
docker compose version
docker run --rm hello-world
```

## 1Panel 检查

```bash
1pctl version
1pctl user-info
systemctl is-active 1panel-core.service
systemctl is-active 1panel-agent.service
```

## 心跳服务刷新

```bash
/opt/server-desk/.venv/bin/python /opt/server-desk/scripts/deploy_heartbeat_mesh.py \
  --env-file /etc/server-desk/server-desk.env \
  --server-id 13 --configure-firewall
```
