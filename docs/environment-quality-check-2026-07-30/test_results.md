# 测试结果

## 自动化测试

- `tests/test_app.py`：16 项通过。
- 全量测试：50 项通过。
- Python 模块编译通过。
- 前端 JavaScript 语法检查通过。

## 真实节点

- CCNODE-MAIN SSH 只读采集成功。
- 新增诊断字段完整返回。
- 失败服务、NTP、SSH 策略、防火墙和心跳本机状态均能解析。
- Windows 端 UTF-8 输出读取问题修复后复测成功。
- CCNODE-MAIN 返回 `heartbeat_timer=active`、`heartbeat_timer_substate=elapsed`、最近触发为 2026-07-27 19:23:42 UTC，新增检查项正确判定失败。

## 浏览器验收

- 本地页面成功登录并创建单服务器样例。
- 行内听诊器入口和详情“全面体检”按钮均可见。
- 点击详情按钮后自动切换到环境检测页签，并显示六个评分维度、关注项、心跳证据和审计记录。
- 页面展示 55 分 D 级样例，失败、关注、信息、通过状态样式可辨识。
- 浏览器控制台错误和警告数量为 0。
- 验收截图：`environment-quality-report-detail.png`。
- 使用 CCNODE-MAIN 真实只读输出再次验收，页面明确显示“主动上报定时器 active / elapsed”异常，控制台错误和警告数量为 0。
- 心跳原因截图：`ccnode-heartbeat-timer-diagnosis.png`。

## 生产发布

- 首次发布在健康检查阶段出现假失败，原因是固定等待 2 秒，应用在第 3 秒完成启动。
- 生产服务随后进入 active，健康接口返回成功。
- 发布脚本改为最多 30 秒逐秒探测健康接口，并增加自动化检查。
