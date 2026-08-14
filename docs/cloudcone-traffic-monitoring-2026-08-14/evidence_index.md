# 证据索引

| 编号 | 证据 | 结果 |
| --- | --- | --- |
| E01 | CloudCone 客户区登录 | 登录成功，目标 Budget VPS 可见 |
| E02 | 客户区节点详情 | 实例 66588，HS113，203.24.89.50 |
| E03 | 客户区流量卡 | 原始读数 `0 GB of 8192 GB/Mo Used` |
| E04 | CloudCone 官方 API 文档 | API Key 与 API Hash 认证 |
| E05 | `GET /api/v2/compute/instances` | HTTP 200，目标 Budget VPS 不在结果中 |
| E06 | Budget VPS API 路径探测 | `/api/v2/vps/...` 返回 HTTP 404 |
| E07 | 客户区 AJAX 只读探测 | 无登录会话时返回 session expired |
| E08 | API 凭据清理 | 专用 API 凭据已撤销，页面剩余匹配数为 0 |
| E09 | Server Desk 供应商档案 | 地址、管理用户、服务编号、服务器标识已保存，密码默认遮蔽 |
| E10 | Server Desk 生产首页 | 供应商读数覆盖率 5/8，CCNODE-MAIN 为 0 B / 8.2 TB |

外部依据：

- CloudCone API 文档：`https://cloudcone-api.readme.io/reference/welcome`
- CloudCone API 使用说明：`https://help.cloudcone.com/en-us/article/how-to-use-the-api-hevq9l/`
- CloudCone Budget VPS API 需求：`https://voice.cloudcone.com/396`
