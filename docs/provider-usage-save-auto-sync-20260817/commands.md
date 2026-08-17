# 命令记录

```powershell
rg -n "保存读数|已用流量|step=|provider_sync" app tests docs README.md -S
.\.venv\Scripts\python.exe -m pytest tests/test_app.py -k "version_3 or version_6 or provider_sync_loop or orange_provider_authentication or dashboard_combines or static_and_index" -q --basetemp=.tmp/pytest
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.tmp/pytest-final
git diff --check
```

浏览器验收使用本地 `http://127.0.0.1:8092/`，完成登录、创建测试服务器、自动连接器联动、多位小数表单校验、空 URL 提交、结果卡片和控制台检查。
