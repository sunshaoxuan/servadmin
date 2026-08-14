# 命令记录

## 本地验证

```powershell
python -m pytest -q --basetemp=.test-output\traffic-meter
python -m compileall -q app scripts tests
node --check app\static\app.js
git diff --check
```

## 生产验证

```text
systemctl start server-desk-git-sync.service
systemctl show server-desk-git-sync.service -p Result --value
git rev-parse --short HEAD
curl -fsS http://127.0.0.1:8090/api/health
```

生产数据库验证使用只读查询检查迁移版本、七台账本、周期、计量增量和更新时间。命令输出不记录凭据或应用密钥。
