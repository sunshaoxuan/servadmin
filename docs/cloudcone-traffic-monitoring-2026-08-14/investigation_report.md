# CloudCone 流量监控调查报告

## 任务范围

- 日期：2026-08-14
- Server Desk 节点：`CCNODE-MAIN`
- IPv4：`203.24.89.50`
- 供应商：CloudCone
- 目标：使用供应商权威读数接入流量监控，并评估持续自动同步能力。

## 客户区核验

- 账号登录成功。
- 产品类型为 Budget VPS。
- 客户区实例编号为 `66588`。
- 节点标签为 `HS113`。
- 主机名为 `briconbric.com`。
- 客户区显示月度流量为 `0 GB of 8192 GB/Mo Used`。

## 自动同步调查

CloudCone 官方文档说明 API 使用 API Key 与 API Hash 认证。为验证目标产品是否可读，创建了名称为 `Server Desk traffic monitoring` 的专用 API 凭据，并执行只读查询。

验证结果：

- `GET /api/v2/compute/instances` 返回 HTTP 200，结果不包含 `203.24.89.50` 或 `briconbric.com`。
- 尝试读取 Budget VPS 的 `/api/v2/vps/...` 路径均返回 HTTP 404。
- 客户区 `/ajax/vps` 端点依赖登录会话和 CSRF token，API Key 与 API Hash 无法替代浏览器会话。
- CloudCone 客户区登录要求验证码，用户名密码方式不适合作为无人值守后台同步器。
- CloudCone 官方反馈系统仍将促销或 Budget VPS 的 API 覆盖列为需求项。

因此当前产品没有可验证、可长期运行的官方只读自动接口。未增加验证码识别、浏览器 Cookie 持久化或其他易失效方案。

## 已实施配置

- Server Desk 供应商名称更新为 `CloudCone`。
- 客户区地址保存为 `https://app.cloudcone.com/vps/66588/manage`。
- 管理用户与密码保存到加密供应商凭据档案。
- 服务编号保存为 `66588`。
- 服务器标识保存为 `HS113`。
- 连接器类型保持 `browser`。
- 自动同步开关保持关闭。
- 当前周期记录为 2026-08-01 至 2026-08-31。
- 当前读数记录为 0 GB / 8192 GB。
- 来源标签保留客户区原始文本。

Server Desk 首页的供应商读数覆盖率从 `4/8` 更新为 `5/8`，CCNODE-MAIN 卡片显示 `0 B / 8.2 TB` 和 `0.0%`。

## 安全处理

- 账号密码未写入代码、文档、测试输出或截图。
- 新建的 CloudCone API 凭据在确认无法覆盖 Budget VPS 后立即撤销。
- Server Desk 普通资产响应继续只返回 `has_provider_password` 状态，密码由连接详情按需读取并默认遮蔽。

## 后续条件

CloudCone 为 Budget VPS 提供只读 API 后，可新增正式 `cloudcone` 连接器。验收条件应包括实例 66588 可查询、月度已用量与额度可读取、账期字段明确、API 凭据可撤销，以及后台定时同步成功。
