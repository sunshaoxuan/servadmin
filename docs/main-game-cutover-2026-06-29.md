# Main Game Cutover Execution Report 2026-06-29

## Scope

This report records the production cutover for the main game only.

Included:

- Public game domains: `rhospital.cc`, `hero-hospital.icu`, `rhospital-api-services.com`
- Public entry: `RHOSPITAL-GATE` at `64.83.37.55`
- Old production host: `CLAW-JP-PROD` at `47.79.38.216`
- New production host: `RHospital.OrangeVPS` at `178.239.117.99`
- Game backend image: `hospital-backend:2026062801`
- PostgreSQL databases: `hospital` and `snailjob`
- Downstream PostgreSQL mirror: `SAKURA-HOSP-DBBACK`

Excluded:

- BBS traffic and Flarum runtime
- BBS MySQL and Sakura BBS MySQL replica
- Cloudflare DNS changes

## Final State

| Item | Final State |
|---|---|
| Main game gateway target | `178.239.117.99:8190` |
| New backend service | `hospital_stack_hospital-backend`, `1/1`, image `hospital-backend:2026062801` |
| New PostgreSQL | OrangeVPS container `postgresql`, primary, `pg_is_in_recovery() = false` |
| New `snail-job` | OrangeVPS container `snail-job`, running |
| Sakura PostgreSQL mirror | follows OrangeVPS, sender `178.239.117.99:35432`, lag `0` |
| Old backend service | `0/0` on `CLAW-JP-PROD` |
| Old `snail-job` | stopped on `CLAW-JP-PROD` |
| Old PostgreSQL | stopped on `CLAW-JP-PROD` |
| Old MySQL | still running, because BBS MySQL is outside this cutover |
| BBS | `https://bbs.rhospital.cc/` remained served by OrangeVPS |

## Gateway Maintenance Fence

The main game site was fenced at the gateway before database promotion.

Maintenance response:

```text
HTTP status: 503
Body: 服务器维护中，请稍候重试。
Content-Type: text/plain
Retry-After: 300
```

Gateway files backed up and replaced during the maintenance stage:

```text
/opt/1panel/www/sites/rhospital.cc/proxy/root.conf
/opt/1panel/www/sites/rhospital.cc/proxy/10-api-bypass.conf
/opt/1panel/www/sites/rhospital.cc/proxy/20-static-cache.conf
```

Observed public validation:

```text
https://rhospital.cc/ status=503 contains_maintenance=True
https://hero-hospital.icu/ status=503 contains_maintenance=True
https://rhospital-api-services.com/ status=503 contains_maintenance=True
https://bbs.rhospital.cc/ status=200 title_present=True
```

OpenResty `nginx -t` passed before each reload.

## Execution Adjustment

After gateway maintenance was confirmed, old production backend and old `snail-job` were stopped before OrangeVPS PostgreSQL promotion.

Reason:

```text
The public gateway fence blocks user traffic, but old backend tasks and old snail-job could still create PostgreSQL writes.
Stopping these writers before the final LSN check prevents old and new PostgreSQL timelines from diverging after OrangeVPS promotion.
```

Rollback before promotion remained possible by restarting old backend, restarting old `snail-job`, and restoring the gateway proxy.

Observed freeze state:

```text
old_backend_replicas=0/0
old_snail_job_status=snail-job|Exited
```

Old PostgreSQL was kept running until final validation and Sakura repoint completed.

## OrangeVPS Preparation

The backend image was streamed from old production to OrangeVPS:

```text
Loaded image: hospital-backend:2026062801
```

OrangeVPS compose and database configuration files were backed up before edits.

Backup timestamp:

```text
20260629T122504Z
```

OrangeVPS official backend compose was updated to:

```text
image: hospital-backend:2026062801
SPRING_DATASOURCE_URL=jdbc:postgresql://178.239.117.99:35432/hospital
```

Test runtime cleanup:

```text
snail-job-test: removed
rhospital-test-postgres: removed
/opt/rhospital-test-baseline/postgres: removed
```

Official stack was redeployed and kept stopped before promotion:

```text
service_image=hospital-backend:2026062801
service_replicas=0/0
datasource_url=jdbc:postgresql://178.239.117.99:35432/hospital
```

## Final Catch-up

Before promotion, old production showed zero WAL lag to OrangeVPS and Sakura.

Observed old production sender state:

```text
rhospital_standby_160_16_91_200|160.16.91.200|streaming|async|0
rhospital_standby_178_239_117_99|178.239.117.99|streaming|async|0
```

OrangeVPS standby state:

```text
pg_is_in_recovery=t
receive_lsn=11/3F0825D8
replay_lsn=11/3F0825D8
receive_replay_lag_bytes=0
```

Sakura direct standby state before repoint:

```text
pg_is_in_recovery=t
receive_lsn=11/3F05D908
replay_lsn=11/3F05D908
receive_replay_lag_bytes=0
```

## OrangeVPS PostgreSQL Promotion

Promotion command:

```text
select pg_promote(true, 60);
```

Validation after promotion:

```text
pg_is_in_recovery=f
write_probe_hospital=1
write_probe_snailjob=1
```

The write probes used transaction rollback and did not leave persistent probe tables.

## OrangeVPS Service Startup

Official `snail-job` was started from the 1Panel compose directory.

Official backend was scaled to one replica.

Observed startup:

```text
orange_backend=1/1 hospital-backend:2026062801
orange_snail=snail-job|Up
Tomcat started on port 8090
Started HospitalBackendApplication
```

Direct OrangeVPS validation:

```text
http://178.239.117.99:8190/ status=200 title=英雄荣光医院:立刻开玩
http://178.239.117.99:8190/login status=200 title=登录
```

The path `/run/game` still returned the pre-existing template error. The actual login redirect target found in the current frontend is `/run/newGame`, which returned `200`.

## Gateway Cutover

The gateway proxy files were restored from the maintenance-prep backups and changed from:

```text
http://47.79.38.216:8190
```

to:

```text
http://178.239.117.99:8190
```

Switch timestamp:

```text
2026-06-29T12:32:33Z
```

OpenResty configuration test passed and reload succeeded.

No Cloudflare DNS records were changed.

## Public Validation

Public validation after cutover:

```text
https://rhospital.cc/ status=200 title=英雄荣光医院:立刻开玩
https://rhospital.cc/login status=200 title=登录
https://hero-hospital.icu/ status=200 title=英雄荣光医院:立刻开玩
https://rhospital-api-services.com/ status=200 title=英雄荣光医院:立刻开玩
https://bbs.rhospital.cc/ status=200 title=英雄荣光医院论坛
```

Test account validation:

```text
account: sunshaoxuan@gmail.com
password handling: queried at runtime only, not printed, not stored in this repository
login_status=200
token_present=True
auth_get /run/newGame status=200 title=英雄荣光医院
auth_get /homeApi/wishlist/count status=200 body_prefix={"count":283}
```

## Sakura PostgreSQL Mirror Repoint

OrangeVPS was configured as a PostgreSQL replication source for Sakura.

Created or updated source-side replication resources:

```text
role: rhospital_sakura_standby
slot: rhospital_backup_orange_160_16_91_200
pg_hba: host replication rhospital_sakura_standby 160.16.91.200/32 scram-sha-256
```

The replication password is stored only in root-owned server files and is not part of this repository.

Sakura standby was stopped, rebuilt by `pg_basebackup` from OrangeVPS, started, and validated.

Observed Sakura state:

```text
remaining_old_backup_dirs=0
pg_is_in_recovery=t
receive_lsn=11/412366B8
replay_lsn=11/412366B8
receive_replay_lag_bytes=0
wal_receiver_status=streaming
wal_receiver_host=178.239.117.99
```

Observed OrangeVPS sender state:

```text
rhospital_backup_orange_160_16_91_200|active=True
walreceiver|160.16.91.200|streaming|0
```

The old Sakura standby baseline directory was removed after the new stream was verified.

## Old Production Stop

Old production final state:

```text
old_backend 0/0 hospital-backend:2026062801
old_snail snail-job|Exited
old_pg postgresql|Exited
old_mysql mysql|Up
```

Old MySQL remained running because BBS MySQL was excluded from this main game migration.

## Interruption Window

No independent one-second synthetic monitor was running. The interruption window is estimated from gateway maintenance and first successful public-domain cutover evidence.

Observed timeline in UTC:

| Time | Evidence | Meaning |
|---|---|---|
| `2026-06-29T12:16:51Z` | gateway maintenance config applied and reloaded | main game started returning 503 maintenance response |
| `2026-06-29T12:25:04Z` | OrangeVPS official stack prepared with backend image `2026062801` and kept `0/0` | new production runtime prepared |
| `2026-06-29T12:31:51Z` | OrangeVPS backend log, Tomcat started on `8090` | new backend ready internally |
| `2026-06-29T12:32:33Z` | gateway proxy changed to `178.239.117.99:8190` and OpenResty reloaded | public cutover executed |
| `2026-06-29T12:32:33Z` to same minute | public checks returned `200` for all game domains | original domains reached OrangeVPS |

Best estimate for user-visible main game maintenance:

```text
2026-06-29T12:16:51Z to 2026-06-29T12:32:33Z
= 15 minutes 42 seconds
```

Conservative validation window extends to the post-stop validation pass, which included login and Sakura mirror checks.

## Rollback Position

After OrangeVPS accepted login traffic and Sakura was repointed, clean rollback requires a data decision.

If a severe issue appears:

1. Re-enable the gateway maintenance response for the main game domains.
2. Stop OrangeVPS backend and `snail-job` to freeze new writes.
3. Decide whether OrangeVPS writes are authoritative.
4. If rolling back, restart old PostgreSQL, old `snail-job`, and old backend.
5. Point gateway proxy back to `47.79.38.216:8190`.
6. Validate original domains.
7. Rebuild or repoint Sakura according to the chosen authoritative source.

Do not expose both old and new PostgreSQL-backed game stacks as writable public targets at the same time.

## Remaining Work

- Continue monitoring OrangeVPS backend health, OrangeVPS PostgreSQL disk usage, and Sakura PostgreSQL lag.
- Keep old production stopped unless a rollback decision is made.
- Remove old production resources only after the agreed stability period.
- Investigate the pre-existing `/run/game` template error separately. The current frontend login path uses `/run/newGame`, which validated successfully.
