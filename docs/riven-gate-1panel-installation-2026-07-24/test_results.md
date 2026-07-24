# 测试结果

| 测试 | 结果 |
|---|---|
| 官方安装包 SHA256 | 通过 |
| Docker 服务 | active |
| Docker hello-world | 通过 |
| Docker Compose | 5.3.1 |
| 1Panel Core | active |
| 1Panel Agent | active |
| 面板端口监听 | 44177/tcp |
| UFW 面板规则 | 存在 |
| 本机面板 HTTP | 200 |
| 外部面板 HTTP | 200 |
| 资料库 URL 一致性 | 通过 |
| 资料库用户名一致性 | 通过 |
| 资料库密码一致性 | 通过 |
| Server Desk 环境检查 | 正常，40 个应用、27 个服务 |
| 心跳单节点部署 | 1 成功、0 失败 |
| 1Panel 登录页 | 可见 |
| 登录页控制台 | 0 条警告或错误 |
| 临时安装文件 | 已清除 |
| 变更前后加密备份 | 通过 |

## 项目单元测试

```powershell
.\.venv\Scripts\python.exe -m pytest --basetemp .task-test\riven-1panel-pytest
```

结果为 `46 passed in 15.92s`。测试临时目录在完成后删除。
