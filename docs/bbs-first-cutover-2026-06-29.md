# BBS First Cutover Execution Report 2026-06-29

## Scope

This report records the first production migration step for BBS only.

Included:

- Public validation domain: `bbs.rhospital.cc`
- BBS application: Flarum
- BBS database: MySQL database `flarum_rtt3ns`
- Public entry: `RHOSPITAL-GATE`
- New production host: `RHospital.OrangeVPS`
- Mirror after cutover: `SAKURA-HOSP-DBBACK`

Excluded:

- Game backend traffic
- Game PostgreSQL production promotion
- Game test PostgreSQL cleanup
- Extra BBS hostnames such as `bbs.hero-hospital.icu` and `bbs.rhospital-api-services.com`
- Cloudflare DNS changes

## Final State

| Item | Final State |
|---|---|
| Public BBS domain | `https://bbs.rhospital.cc/` |
| DNS | `bbs.rhospital.cc CNAME rhospital.cc`, `rhospital.cc A 64.83.37.55` |
| Public gateway | `RHOSPITAL-GATE` at `64.83.37.55` |
| Gateway BBS target | `178.239.117.99:40020` |
| New BBS app | `RHospital.OrangeVPS` container `flarum`, image `crazymax/flarum:1.8.10` |
| New BBS MySQL | `RHospital.OrangeVPS` container `mysql`, port `33306`, writable |
| Old BBS app | `CLAW-JP-PROD` container `flarum`, stopped |
| Old BBS MySQL | kept running for short-term inspection and fallback data decisions |
| Sakura BBS mirror | `rhospital-bbs-mysql-replica`, now follows OrangeVPS |

## Pre-cutover Cleanup

The OrangeVPS BBS test runtime was removed.

| Resource | Result |
|---|---|
| `flarum-test` | removed |
| `rhospital-test-mysql` | removed |
| `/opt/rhospital-test-baseline/mysql` | removed |
| `rhospital-test-postgres` | preserved |
| game test runtime | preserved |

OrangeVPS BBS configuration was backed up before changing the runtime state.

Backup directory:

```text
/root/bbs-migration-backup-20260629074822
```

Backed up files:

```text
/opt/1panel/apps/flarum/flarum/.env
/opt/1panel/apps/flarum/flarum/docker-compose.yml
```

The host-side path `/opt/1panel/apps/flarum/flarum/data/config.php` was not present before official Flarum startup.

## OrangeVPS MySQL Promotion

OrangeVPS official MySQL was confirmed caught up with old production before promotion.

Observed before promotion:

```text
Source binlog: binlog.000002 / 9959016
Orange read position: binlog.000002 / 9959016
Orange exec position: binlog.000002 / 9959016
Seconds_Behind_Source: 0
```

Promotion actions:

```text
STOP REPLICA
RESET REPLICA ALL
SET GLOBAL super_read_only=OFF
SET GLOBAL read_only=OFF
```

Persistence changes on OrangeVPS:

```text
/opt/1panel/apps/mysql/mysql/conf/my.cnf
```

The previous read-only settings were disabled, and binlog settings were retained or added for downstream replication:

```text
log-bin=mysql-bin
binlog_format=ROW
server_id=178239117
```

Validation after promotion:

```text
read_only=0
super_read_only=0
log_bin=1
server_id=178239117
flarum_tables=33
```

A controlled MySQL write probe completed successfully and was removed.

## OrangeVPS Flarum Startup

Official Flarum was started from the 1Panel compose file:

```text
/opt/1panel/apps/flarum/flarum/docker-compose.yml
```

Runtime validation:

```text
container: flarum
image: crazymax/flarum:1.8.10
port: 0.0.0.0:40020 -> 8000/tcp
FLARUM_BASE_URL=https://bbs.rhospital.cc
DB_HOST=mysql
DB_PORT=3306
DB_NAME=flarum_rtt3ns
```

Access checks:

```text
http://127.0.0.1:40020/ returned 200 on OrangeVPS
http://178.239.117.99:40020/ returned 200 externally
```

Container logs showed database readiness and no pending migrations:

```text
Database ready
Nothing to migrate
PHP-FPM ready
```

## Gateway Cutover

The gateway BBS proxy file was backed up before maintenance and before the final target switch.

Backup files:

```text
/opt/1panel/www/sites/bbs.rhospital.cc/proxy/root.conf.bbs-migration-freeze-20260629074933.bak
/opt/1panel/www/sites/bbs.rhospital.cc/proxy/root.conf.bbs-migration-to-orange-20260629075251.bak
```

The BBS proxy target was changed from:

```text
http://47.79.38.216:40020
```

to:

```text
http://178.239.117.99:40020
```

OpenResty configuration test passed before reload. The reload succeeded.

No Cloudflare DNS record was changed.

## Public Validation

DNS validation from the local workstation:

```text
bbs.rhospital.cc CNAME rhospital.cc TTL 281
rhospital.cc A 64.83.37.55 TTL 281
```

Public HTTP validation from the local workstation:

```text
URL: https://bbs.rhospital.cc/
HTTP status: 200
Title: 英雄荣光医院论坛
Response length: 44034
```

Static asset validation during cutover:

```text
https://bbs.rhospital.cc/assets/forum.css?v=365c3176 returned 200
```

OrangeVPS Flarum logs showed requests arriving from the gateway IP `64.83.37.55` after the switch.

Login or posting was not exercised in this run because no BBS user login credential was available in the execution context. The login page and homepage loaded correctly through the original domain.

## Interruption Window

No independent one-second external synthetic monitor was running during this cutover. The interruption estimate is based on action timestamps, container logs, and successful HTTP evidence.

Observed timeline in UTC:

| Time | Evidence | Meaning |
|---|---|---|
| `2026-06-29T07:47:22Z` | old production `flarum` access log from gateway IP `64.83.37.55`, HTTP `200` | old BBS was still serving public traffic |
| `2026-06-29T07:49:33Z` | gateway backup `root.conf.bbs-migration-freeze-20260629074933.bak` | BBS maintenance or write fence stage started |
| `2026-06-29T07:52:01Z` | OrangeVPS `flarum` container `StartedAt` | new BBS container started |
| `2026-06-29T07:52:03Z` | OrangeVPS `flarum` log, PHP-FPM ready | new BBS runtime ready internally |
| `2026-06-29T07:52:08Z` | OrangeVPS local access log, HTTP `200` | new BBS passed local check |
| `2026-06-29T07:52:23Z` | OrangeVPS external direct access log, HTTP `200` | new BBS passed direct external check |
| `2026-06-29T07:52:51Z` | gateway backup `root.conf.bbs-migration-to-orange-20260629075251.bak` | gateway target switch stage started |
| `2026-06-29T07:53:23Z` | OrangeVPS `flarum` access log from gateway IP `64.83.37.55`, HTTP `200` | original BBS domain reached OrangeVPS through the gateway |
| `2026-06-29T07:54:00Z` | old production `flarum` shutdown log | old BBS was stopped after new BBS had served via gateway |
| `2026-06-29T07:54:51Z` | OrangeVPS `flarum` access log from gateway IP `64.83.37.55`, HTTP `200` | follow-up public-domain validation remained healthy |

Best estimate for user-visible BBS interruption:

```text
2026-06-29T07:49:33Z to 2026-06-29T07:53:23Z
= 3 minutes 50 seconds
```

Conservative validation window:

```text
2026-06-29T07:49:33Z to 2026-06-29T07:54:51Z
= 5 minutes 18 seconds
```

The second number includes extra operator validation time after the first successful gateway-served `200`.

## Old Production BBS Stop

After public validation through the original domain, old production Flarum was stopped.

Old production status after stop:

```text
flarum | crazymax/flarum:1.8.10 | Exited (137)
mysql  | mysql:8.4.9             | Up
```

Old production port `40020` no longer served BBS traffic after the stop.

Old production MySQL was left running for inspection and fallback data decisions. It was not deleted in this BBS first version.

## Sakura MySQL Mirror Repoint

Sakura direct key SSH was not available, so the Sakura host was accessed using the recorded password login path. No password is stored in this repository.

The Sakura BBS MySQL mirror was rebuilt from an OrangeVPS baseline and then configured to follow OrangeVPS.

Baseline source position:

```text
Source_Log_File: binlog.000004
Source_Log_Pos: 1184439
```

Sakura replica validation:

```text
container: rhospital-bbs-mysql-replica
read_only=1
super_read_only=1
flarum_tables=33
Source_Host: 178.239.117.99
Source_User: repl_bbs_sakura
Source_Port: 33306
Replica_IO_Running: Yes
Replica_SQL_Running: Yes
Seconds_Behind_Source: 0
Read_Source_Log_Pos: 1184439
Exec_Source_Log_Pos: 1184439
Replica_SQL_Running_State: Replica has read all relay log; waiting for more updates
```

OrangeVPS source-side validation:

```text
repl_bbs_sakura from 160.16.91.200:57866
Command: Binlog Dump
State: Source has sent all binlog to replica; waiting for more updates
```

The temporary baseline dump was removed locally and from Sakura after import.

## Current BBS Topology

```text
Internet
  -> Cloudflare DNS, grey-cloud direct DNS
  -> RHOSPITAL-GATE 64.83.37.55
  -> OrangeVPS Flarum 178.239.117.99:40020
  -> OrangeVPS MySQL mysql:3306
  -> Sakura MySQL replica 160.16.91.200
```

## Rollback Position

The clean rollback path changed after old production Flarum was stopped.

If OrangeVPS BBS has a severe issue:

1. Activate the BBS gateway maintenance response.
2. Stop OrangeVPS `flarum` to prevent additional writes.
3. Restart old production `flarum`.
4. Point the gateway BBS proxy back to `47.79.38.216:40020`.
5. Test `https://bbs.rhospital.cc/`.
6. Compare any OrangeVPS writes accepted after cutover before deciding whether to keep or discard them.

Do not run both old and new BBS writable paths behind the public domain at the same time.

## Remaining Work

- Perform a real BBS login and post or edit validation when an operator account is available.
- Decide whether old production MySQL should follow Sakura or remain isolated until retirement.
- Continue monitoring OrangeVPS Flarum logs, OrangeVPS MySQL disk usage, Sakura replica lag, and gateway errors.
- Keep old production MySQL until the agreed BBS stable period ends.

## 2026-06-29 Language Package Sync

After the BBS cutover, the new production Flarum UI showed English strings even though old production had the Simplified Chinese language pack enabled.

Investigation result:

```text
old production package: flarum-lang/chinese-simplified v1.6.0
old production enabled extension: flarum-lang-chinese-simplified
new production package before fix: missing from Composer vendor
new production default_locale: zh-Hans
new production extension assets: present from copied static assets
```

Fix applied on OrangeVPS:

```text
container rebuild: not performed
database import or reset: not performed
installed package: flarum-lang/chinese-simplified:v1.6.0
persistent extension list: /opt/1panel/apps/flarum/flarum/data/extensions/list
enabled extension: flarum-lang-chinese-simplified
cache: cleared
assets: republished
```

Validation after fix:

```text
https://bbs.rhospital.cc/ status=200
title=英雄荣光医院论坛
html lang=zh-Hans
visible Chinese UI: 注册, 登录, 全部主题, 标签
browser console errors: none observed
database tables: 33
users: 120
discussions: 154
posts: 1589
```

Screenshot evidence:

```text
docs/assets/bbs-language-sync-20260629.png
```
