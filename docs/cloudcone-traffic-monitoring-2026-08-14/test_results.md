# 测试结果

## 外部验证

| 测试 | 结果 | 状态 |
| --- | --- | --- |
| CloudCone 客户区登录 | 成功 | 通过 |
| 目标实例识别 | 66588 / HS113 / 203.24.89.50 | 通过 |
| 客户区流量读取 | 0 GB / 8192 GB | 通过 |
| CloudCone API 凭据 | 可成功访问 compute API | 通过 |
| Budget VPS 出现在 compute API | 未出现 | 不支持 |
| Budget VPS API 路径 | HTTP 404 | 不支持 |
| 无会话客户区 AJAX | session expired | 不支持 |
| 临时 API 凭据撤销 | 剩余匹配数 0 | 通过 |

## Server Desk 验证

| 测试 | 结果 | 状态 |
| --- | --- | --- |
| 供应商名称 | CloudCone | 通过 |
| 服务编号 | 66588 | 通过 |
| 服务器标识 | HS113 | 通过 |
| 密码显示 | 默认遮蔽 | 通过 |
| 自动同步按钮 | disabled | 通过 |
| 首页读数覆盖率 | 5/8 | 通过 |
| CCNode 流量卡 | 0 B / 8.2 TB，0.0% | 通过 |

## 仓库测试

- 命令：`.venv\\Scripts\\python.exe -m pytest -q --basetemp=.task\\cloudcone-monitoring-pytest`
- 结果：`57 passed in 15.50s`
- 状态：通过
