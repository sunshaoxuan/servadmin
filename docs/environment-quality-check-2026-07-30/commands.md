# 命令记录

本文仅记录可公开的命令结构，不包含凭据、签名密钥或数据库敏感字段。

## 生产证据读取

```powershell
ssh -i <本机密钥> root@<Server Desk 主机> <只读数据库查询>
```

## 真实节点采集

```powershell
<项目 Python> 调用 run_server_inspection(<CCNODE 非敏感连接字段>)
```

## 自动化测试

```powershell
python -m pytest --basetemp .task-test\quality-check\all -q
```

## 浏览器验证

```text
启动本地 Server Desk，登录测试账号，选择样例服务器，打开环境检测页签，检查控制台并保存截图。
```
