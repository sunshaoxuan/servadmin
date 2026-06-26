# RHospital Production Cutover Plan 2026-06-27

## Purpose

This document is the production cutover plan for moving public traffic from `CLAW-JP-PROD` to `RHospital.OrangeVPS`.

This is a plan only. No cutover action is performed by this document.

The cutover uses `RHOSPITAL-GATE` as the single public entry point. DNS does not need to change. The actual public switch is an OpenResty proxy target change on `RHOSPITAL-GATE`.

## Current Topology

| Role | Host | IP | Current Purpose |
|---|---|---:|---|
| Current production | `CLAW-JP-PROD` | `47.79.38.216` | Current writer and public backend target |
| Candidate production | `RHospital.OrangeVPS` | `178.239.117.99` | Read-only second replica and future writer |
| Backup mirror | `SAKURA-HOSP-DBBACK` | `160.16.91.200` | PostgreSQL and MySQL mirror |
| Public gateway | `RHOSPITAL-GATE` | `64.83.37.55` | OpenResty front proxy and TLS endpoint |

Current replication direction:

```text
CLAW-JP-PROD PostgreSQL -> SAKURA-HOSP-DBBACK
CLAW-JP-PROD PostgreSQL -> RHospital.OrangeVPS

CLAW-JP-PROD MySQL -> SAKURA-HOSP-DBBACK
CLAW-JP-PROD MySQL -> RHospital.OrangeVPS
```

Current public traffic direction:

```text
Internet -> RHOSPITAL-GATE -> CLAW-JP-PROD
```

Target public traffic direction after cutover:

```text
Internet -> RHOSPITAL-GATE -> RHospital.OrangeVPS
```

Target post-cutover replication direction:

```text
RHospital.OrangeVPS -> SAKURA-HOSP-DBBACK -> CLAW-JP-PROD
```

## Completed Preparation Work

### OrangeVPS Base Environment

- Installed Docker, Docker Compose v2, rsync, tar, and gzip.
- Initialized Docker Swarm.
- Installed 1Panel `v2.1.13`.
- Restored 1Panel application metadata for:
  - `openresty`
  - `postgresql`
  - `mysql`
  - `flarum`
- Restored OpenResty, Flarum, MySQL, PostgreSQL, snail-job, and backend compose assets.
- Configured SSH key access for root.
- Verified OpenResty, MySQL, PostgreSQL, Flarum, snail-job, and Swarm backend runtime availability during prewarm.

### Sakura PostgreSQL Mirror Runtime Alignment

- Replaced Sakura host-installed PostgreSQL standby with container runtime:
  - image: `pgvector/pgvector:pg18-trixie`
  - container: `rhospital-postgres-standby-trixie`
  - data root: `/opt/rhospital-postgres-standby-trixie/pgroot`
- Removed the old host PostgreSQL standby data after the new container standby validated.
- Confirmed:
  - `pg_is_in_recovery() = true`
  - WAL receiver `streaming`
  - receive LSN equals replay LSN
  - collation mismatch count `0`
  - actual collation version `2.41`
- Kept Sakura MySQL replica running.

### OrangeVPS Second Replica

- Stopped OrangeVPS write-capable application services:
  - `hospital_stack_hospital-backend` scaled to `0/0`
  - `snail-job` stopped
  - `flarum` stopped
- Converted OrangeVPS PostgreSQL into a read-only standby following `CLAW-JP-PROD`.
- Created PostgreSQL slot on `CLAW-JP-PROD`:
  - `rhospital_backup_178_239_117_99`
- OrangeVPS PostgreSQL application name:
  - `rhospital_standby_178_239_117_99`
- Converted OrangeVPS MySQL into a read-only replica following `CLAW-JP-PROD`.
- Set OrangeVPS MySQL:
  - `server_id=178239117`
  - unique `server_uuid`
  - `read_only=ON`
  - `super_read_only=ON`
- Confirmed old production sees both Sakura and OrangeVPS as MySQL replicas.

### Writable Test Baseline

Created a separate writable test baseline on OrangeVPS:

| Component | Container | Port | Purpose |
|---|---|---:|---|
| Test PostgreSQL | `rhospital-test-postgres` | `45432` | Backend write testing |
| Test MySQL | `rhospital-test-mysql` | `43306` | Flarum and BBS write testing |

The official cutover candidate remains:

| Component | Container | Port | Purpose |
|---|---|---:|---|
| Official PostgreSQL | `postgresql` | `35432` | Read-only cutover candidate |
| Official MySQL | `mysql` | `33306` | Read-only cutover candidate |

Credentials for the writable test baseline are stored only on OrangeVPS:

```text
/opt/rhospital-test-baseline/credentials.env
```

No test credentials are stored in this repository.

## Current Known Gaps Before Cutover

| Gap | Current State | Required Action |
|---|---|---|
| Backend image version | Old production uses `hospital-backend:20260626`; OrangeVPS service record still references `hospital-backend:20260625` | Upload or build `hospital-backend:20260626` on OrangeVPS and update stack compose before cutover |
| OrangeVPS app services | Backend scaled to `0/0`, `snail-job` and `flarum` stopped | Start only after official databases are promoted |
| Official OrangeVPS databases | Read-only replicas | Promote during cutover window |
| Public gateway | Still targets `47.79.38.216` | Replace proxy target with `178.239.117.99` during cutover |
| Test baseline | Writable snapshot | Use only for testing, do not use as cutover source |

## Public Gateway Configuration

`RHOSPITAL-GATE` runs OpenResty through 1Panel.

Current proxy files:

```text
/opt/1panel/www/sites/rhospital.cc/proxy/root.conf
/opt/1panel/www/sites/rhospital.cc/proxy/10-api-bypass.conf
/opt/1panel/www/sites/bbs.rhospital.cc/proxy/root.conf
```

Current proxy targets:

| Entry | Current Target | Target After Cutover |
|---|---:|---:|
| `rhospital.cc` root | `47.79.38.216:8190` | `178.239.117.99:8190` |
| `hero-hospital.icu` root | `47.79.38.216:8190` | `178.239.117.99:8190` |
| `rhospital-api-services.com` root | `47.79.38.216:8190` | `178.239.117.99:8190` |
| `/api/` | `47.79.38.216:8190` | `178.239.117.99:8190` |
| `bbs.rhospital.cc` | `47.79.38.216:40020` | `178.239.117.99:40020` |
| `bbs.rhospital-api-services.com` | `47.79.38.216:40020` | `178.239.117.99:40020` |
| `bbs.hero-hospital.icu` | `47.79.38.216:40020` | `178.239.117.99:40020` |

TLS remains on `RHOSPITAL-GATE`.

## Pre-cutover Verification Checklist

Run these checks before scheduling the cutover window.

### Old Production Health

- `hospital_stack_hospital-backend` is `1/1`.
- PostgreSQL source sees:
  - `rhospital_standby_160_16_91_200` streaming
  - `rhospital_standby_178_239_117_99` streaming
  - both replay lag bytes acceptable, target is `0`
- PostgreSQL replication slots:
  - `rhospital_backup_160_16_91_200` active
  - `rhospital_backup_178_239_117_99` active
  - retained WAL bytes acceptable, target is `0`
- MySQL source sees:
  - Sakura replica server id `1601691200`
  - OrangeVPS replica server id `178239117`
  - both binlog dump connections active

### Sakura Mirror Health

- PostgreSQL:
  - `rhospital-postgres-standby-trixie` running
  - `pg_is_in_recovery() = true`
  - WAL receiver `streaming`
  - receive LSN equals replay LSN
  - collation mismatch count `0`
- MySQL:
  - `rhospital-bbs-mysql-replica` running
  - `Replica_IO_Running=Yes`
  - `Replica_SQL_Running=Yes`
  - `Seconds_Behind_Source=0`

### OrangeVPS Candidate Health

- PostgreSQL:
  - official container `postgresql` running
  - `pg_is_in_recovery() = true`
  - WAL receiver `streaming`
  - receive LSN equals replay LSN
  - collation mismatch count `0`
- MySQL:
  - official container `mysql` running
  - `Replica_IO_Running=Yes`
  - `Replica_SQL_Running=Yes`
  - `Seconds_Behind_Source=0`
  - `read_only=ON`
  - `super_read_only=ON`
- Application services remain stopped:
  - backend `0/0`
  - `snail-job` stopped
  - `flarum` stopped
- Writable test baseline remains separate:
  - PostgreSQL test port `45432`
  - MySQL test port `43306`

## Pre-cutover Application Testing

Use only the writable test baseline for write tests.

Test database endpoints:

```text
PostgreSQL test: 178.239.117.99:45432
MySQL test:      178.239.117.99:43306
```

Test goals:

- Start a temporary backend stack against test PostgreSQL.
- Verify login or health endpoint.
- Verify a write path against `hospital`.
- Verify Flarum against test MySQL if needed.
- Verify static assets and uploaded files.
- Verify no test traffic reaches official ports `35432` and `33306`.
- Remove temporary backend test service after testing.

Do not use ports `35432` and `33306` for write tests before cutover.

## Cutover Window Plan

Target operational impact: keep write freeze within 30 minutes.

### T minus 24 hours

- Freeze nonessential deployments.
- Confirm backend image `hospital-backend:20260626` is available on OrangeVPS.
- Update OrangeVPS stack compose image tag to the production target image.
- Confirm the compose environment still points to official OrangeVPS database ports:
  - PostgreSQL `178.239.117.99:35432`
  - snail-job `178.239.117.99`
- Confirm Flarum config points to official MySQL container.
- Confirm writable test baseline is not referenced by production compose files.
- Prepare gateway config backups.
- Prepare rollback command snippets, but do not run them.

### T minus 60 minutes

- Announce maintenance window to players and BBS users.
- Confirm no manual deployment is in progress.
- Confirm all application write-capable services on OrangeVPS remain stopped.
- Confirm old production remains healthy.
- Confirm Sakura and OrangeVPS replicas have zero or acceptable lag.
- Confirm old production disk has enough room for WAL and binlog retention.

### T minus 30 minutes

- Re-run final replica checks.
- Put operators on a single communication channel.
- Stop all nonessential admin actions.
- Prepare `RHOSPITAL-GATE` write fence configuration.
- Prepare OpenResty reload validation.

### T plus 0 minutes, start write fence

On `RHOSPITAL-GATE`, activate a write fence for production-facing paths.

Preferred behavior:

- block new write requests
- keep read-only or maintenance response available where possible
- avoid changing DNS
- keep TLS and hostnames unchanged

Write methods to block:

```text
POST
PUT
PATCH
DELETE
```

The write fence must be reversible.

### T plus 1 to 5 minutes, final catch-up check

Check OrangeVPS official PostgreSQL:

- `pg_is_in_recovery() = true`
- receive LSN equals replay LSN
- WAL receiver `streaming`

Check OrangeVPS official MySQL:

- `Replica_IO_Running=Yes`
- `Replica_SQL_Running=Yes`
- `Seconds_Behind_Source=0`
- source file and exec position at latest known source position

Check old production:

- OrangeVPS PostgreSQL standby replay lag bytes `0`
- OrangeVPS PostgreSQL slot retained WAL bytes `0`
- OrangeVPS MySQL binlog dump connection active

If any final catch-up check fails, keep gateway pointing to old production and stop the cutover.

### T plus 5 to 10 minutes, promote OrangeVPS databases

Promote PostgreSQL on OrangeVPS:

- run PostgreSQL promote on official container `postgresql`
- verify `pg_is_in_recovery() = false`
- verify `hospital`, `snailjob`, and `postgres` are accessible
- verify collation mismatch count `0`
- perform a controlled write probe if required, then remove the probe object

Promote MySQL on OrangeVPS:

- stop replica
- reset replica metadata when final position is confirmed
- set `read_only=OFF`
- set `super_read_only=OFF`
- verify Flarum database table count
- perform a controlled write probe if required, then remove the probe table

Do not start applications until both databases are writable and verified.

### T plus 10 to 15 minutes, start OrangeVPS application services

Start database-dependent services in this order:

1. PostgreSQL official container is already running and writable
2. MySQL official container is already running and writable
3. `snail-job`
4. Flarum
5. backend Swarm service

Backend startup requirements:

- target image must match the planned production image
- `docker stack deploy -c docker-compose.yml hospital_stack` should use the planned image tag
- Swarm update strategy remains:
  - `start-first`
  - `parallelism=1`
  - `failure_action=rollback`
  - healthcheck enabled
- service reaches `1/1`

Verify local OrangeVPS endpoints before gateway switch:

- backend `http://127.0.0.1:8190/`
- BBS `http://127.0.0.1:40020/`
- snail-job port and logs
- database writes from application logs

### T plus 15 to 20 minutes, switch front gateway

On `RHOSPITAL-GATE`, back up these files:

```text
/opt/1panel/www/sites/rhospital.cc/proxy/root.conf
/opt/1panel/www/sites/rhospital.cc/proxy/10-api-bypass.conf
/opt/1panel/www/sites/bbs.rhospital.cc/proxy/root.conf
```

Replace proxy targets:

```text
47.79.38.216:8190  ->  178.239.117.99:8190
47.79.38.216:40020 ->  178.239.117.99:40020
```

Validate and reload OpenResty:

- run config test
- reload OpenResty only if config test passes
- keep backup files in place

Do not change DNS.

### T plus 20 to 25 minutes, public validation

Validate through `RHOSPITAL-GATE`:

- `rhospital.cc`
- `hero-hospital.icu`
- `rhospital-api-services.com`
- `bbs.rhospital.cc`
- `bbs.rhospital-api-services.com`
- `bbs.hero-hospital.icu`

Validate behavior:

- HTTPS certificate is valid
- backend main page loads
- key API endpoint responds
- login path works
- one controlled write path works
- Flarum home loads
- Flarum login or post test works if allowed
- logs show traffic reaching OrangeVPS
- old production receives no new gateway traffic except residual keepalive or manual checks

### T plus 25 to 30 minutes, release write fence

If public validation passes:

- remove or relax the write fence
- keep traffic pointed at OrangeVPS
- keep old production services available for observation
- continue monitoring database writes, backend logs, MySQL replication status, PostgreSQL status, and OpenResty errors

If validation fails:

- keep write fence active
- use rollback plan below

## Rollback Plan

Rollback options depend on whether OrangeVPS accepted real writes.

### Rollback before OrangeVPS accepts writes

This is the clean rollback path.

Actions:

- keep or re-enable gateway write fence
- point `RHOSPITAL-GATE` proxy files back to:
  - `47.79.38.216:8190`
  - `47.79.38.216:40020`
- reload OpenResty after config test
- stop OrangeVPS application services
- keep OrangeVPS databases for investigation
- release write fence after old production validation

Expected data loss: none, assuming no writes reached OrangeVPS.

### Rollback after OrangeVPS accepts writes

This path requires a data decision.

Actions:

- immediately activate write fence
- stop OrangeVPS application services
- identify writes that happened on OrangeVPS
- choose one of:
  - keep OrangeVPS as source and fix the service issue there
  - manually reconcile OrangeVPS writes back to old production
  - accept loss of test or limited writes if explicitly approved
- avoid sending traffic back to old production until the data decision is made

Expected data risk: depends on accepted writes.

## Post-cutover Replication Reversal

After OrangeVPS is stable as production writer, reverse the replication direction.

Target:

```text
RHospital.OrangeVPS -> SAKURA-HOSP-DBBACK -> CLAW-JP-PROD
```

### PostgreSQL Post-cutover

Recommended sequence:

1. Keep old production read-only or isolated from public writes.
2. Confirm OrangeVPS is the sole writer.
3. Rebuild Sakura PostgreSQL standby from OrangeVPS using a clean base backup or a validated rewind path.
4. Confirm Sakura follows OrangeVPS:
   - `pg_is_in_recovery() = true`
   - WAL receiver `streaming`
   - receive LSN equals replay LSN
   - collation mismatch count `0`
5. Rebuild or rewind old production from Sakura if it must remain as downstream standby.
6. Drop obsolete old-production replication slots only after new downstream topology validates.

### MySQL Post-cutover

Recommended sequence:

1. Ensure OrangeVPS MySQL has binlog enabled and unique server id.
2. Configure Sakura MySQL replica to follow OrangeVPS.
3. Confirm Sakura:
   - `Replica_IO_Running=Yes`
   - `Replica_SQL_Running=Yes`
   - `Seconds_Behind_Source=0`
4. Configure old production MySQL to follow Sakura if old production remains in lifecycle.
5. Confirm old production is read-only downstream.
6. Remove old production source users and obsolete replication credentials after retirement.

## Monitoring After Cutover

Monitor continuously for at least 24 hours:

- gateway OpenResty access and error logs
- OrangeVPS backend logs
- OrangeVPS `docker service ps hospital_stack_hospital-backend`
- OrangeVPS PostgreSQL logs
- OrangeVPS MySQL logs
- Flarum container logs
- snail-job logs
- database disk usage
- Docker image and build cache usage
- application error rate
- player reconnect complaints
- BBS posting and upload behavior

## Cleanup After Stable Period

Do not clean up immediately after cutover. Wait for the agreed stable period.

Cleanup candidates:

- writable test baseline:
  - `rhospital-test-postgres`
  - `rhospital-test-mysql`
  - `/opt/rhospital-test-baseline`
- obsolete replication users on old production
- obsolete PostgreSQL replication slots on old production
- old production application services
- old production database data after lifecycle end
- Docker build cache and unused images

Cleanup must happen only after:

- OrangeVPS has been stable
- Sakura follows OrangeVPS
- old production is no longer required for rollback
- final data retention decision is recorded

## Go or No-go Checklist

Proceed only if every item below is true:

- OrangeVPS PostgreSQL official replica lag is `0` or explicitly accepted.
- OrangeVPS MySQL official replica delay is `0` or explicitly accepted.
- Sakura PostgreSQL and MySQL mirrors are healthy.
- OrangeVPS backend image matches planned production version.
- OrangeVPS application services are stopped before promotion.
- Test baseline is not referenced by production compose files.
- `RHOSPITAL-GATE` proxy backup is ready.
- Write fence procedure is ready and reversible.
- Rollback owner and decision rule are agreed.
- Maintenance notice is sent.

Stop the cutover if any item below is true:

- PostgreSQL final replay lag does not converge.
- MySQL final `Seconds_Behind_Source` does not converge.
- OrangeVPS database promotion fails.
- OrangeVPS backend fails health checks.
- Flarum cannot access MySQL.
- OpenResty config test fails.
- Gateway switch points to an unexpected target.
- Any real write reaches OrangeVPS before promotion is complete.

## Evidence Documents

This plan is based on these repository records:

- `docs/rhospital-orangevps-prewarm-2026-06-25.md`
- `docs/sakura-postgres-standby-trixie-upgrade-2026-06-26.md`
- `docs/orangevps-second-replica-2026-06-26.md`
- `docs/orangevps-test-baseline-2026-06-26.md`

