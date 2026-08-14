# 操作摘要

本文记录操作类别，账号密码、API Key、API Hash 和会话信息均不落盘。

## 只读调查

- 登录 CloudCone 客户区并打开目标 Budget VPS。
- 读取客户区实例标识、节点标签、IP、月度已用量和额度。
- 查阅 CloudCone 官方 API 文档和产品反馈。
- 使用临时专用 API 凭据调用 compute 实例列表。
- 对 Budget VPS 只执行 GET 或无副作用的只读接口探测。

## 外部配置

- 将 CCNODE-MAIN 的供应商更新为 CloudCone。
- 保存客户区地址、管理用户、加密密码、服务编号和服务器标识。
- 录入已核验的当月流量读数。
- 保持自动同步关闭。

## 清理

- 撤销无法覆盖 Budget VPS 的临时 API 凭据。
- 关闭任务创建的浏览器页签。
