# RHospital Production Cutover Plan 2026-06-27

## Purpose

This document is the production cutover plan for moving public traffic from `CLAW-JP-PROD` to `RHospital.OrangeVPS`.

The BBS first-version cutover was completed on 2026-06-29. The game backend cutover is still pending.

The cutover uses `RHOSPITAL-GATE` as the single public entry point. DNS does not need to change. The actual public switch is an OpenResty proxy target change on `RHOSPITAL-GATE`.

## Current Topology

| Role | Host | IP | Current Purpose |
|---|---|---:|---|
| Current production | `CLAW-JP-PROD` | `47.79.38.216` | Current writer and public backend target |
| Candidate production | `RHospital.OrangeVPS` | `178.239.117.99` | Read-only second replica and future writer |
| Backup mirror | `SAKURA-HOSP-DBBACK` | `160.16.91.200` | PostgreSQL and MySQL mirror |
| Public gateway | `RHOSPITAL-GATE` | `64.83.37.55` | OpenResty front proxy and TLS endpoint |

Current replication direction for game PostgreSQL:

```text
CLAW-JP-PROD PostgreSQL -> SAKURA-HOSP-DBBACK
CLAW-JP-PROD PostgreSQL -> RHospital.OrangeVPS
```

Current replication direction for BBS MySQL:

```text
RHospital.OrangeVPS MySQL -> SAKURA-HOSP-DBBACK MySQL
```

Current public traffic direction for game services:

```text
Internet -> RHOSPITAL-GATE -> CLAW-JP-PROD
```

Current public traffic direction for BBS:

```text
Internet -> RHOSPITAL-GATE -> RHospital.OrangeVPS
```

Target game public traffic direction after cutover:

```text
Internet -> RHOSPITAL-GATE -> RHospital.OrangeVPS
```

Target post-cutover replication direction:

```text
RHospital.OrangeVPS -> SAKURA-HOSP-DBBACK -> CLAW-JP-PROD
```

BBS MySQL has already moved to this first leg:

```text
RHospital.OrangeVPS MySQL -> SAKURA-HOSP-DBBACK MySQL
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
- On 2026-06-29, promoted OrangeVPS MySQL for BBS and repointed Sakura MySQL to follow OrangeVPS.

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
| Official MySQL | `mysql` | `33306` | BBS writer after 2026-06-29 first-version cutover |

Credentials for the writable test baseline are stored only on OrangeVPS:

```text
/opt/rhospital-test-baseline/credentials.env
```

No test credentials are stored in this repository.

### Application Test Runtime

On 2026-06-28, a temporary application test runtime was started on OrangeVPS so the candidate host can run the old production style services before public cutover.

The test runtime uses production-like external ports on OrangeVPS, while `RHOSPITAL-GATE` still points public traffic to `CLAW-JP-PROD`.

| Component | Runtime | External Port | Database Target |
|---|---|---:|---|
| Game backend | `hospital_stack_hospital-backend` | `8190`, `9996`, `17889` | Test PostgreSQL `45432` |
| snail-job | `snail-job-test` | `38084`, `17888` | Test PostgreSQL `45432` |
| BBS | `flarum-test` | `40020` | Test MySQL `rhospital-test-mysql:3306` |

Validation from 2026-06-28:

- Backend service reached `1/1` and was healthy.
- Backend runtime environment pointed at `jdbc:postgresql://178.239.117.99:45432/hospital`.
- `http://178.239.117.99:8190/` returned HTTP `200`.
- `http://178.239.117.99:40020/` returned HTTP `200` and loaded the BBS homepage.
- Test PostgreSQL and test MySQL accepted controlled write probes.
- Official PostgreSQL remained a read-only replica. Official MySQL was later promoted for BBS during the 2026-06-29 first-version cutover.

This was a test runtime for the pre-cutover stage. The BBS test runtime and BBS test MySQL were removed during the 2026-06-29 BBS first-version cutover. The game test PostgreSQL and game test runtime were preserved.

### BBS First-version Cutover

Completed on 2026-06-29.

Current BBS state:

| Component | Current State |
|---|---|
| Public domain | `https://bbs.rhospital.cc/` |
| Public DNS | `bbs.rhospital.cc CNAME rhospital.cc`, `rhospital.cc A 64.83.37.55` |
| Gateway target | `178.239.117.99:40020` |
| OrangeVPS Flarum | `flarum` running, image `crazymax/flarum:1.8.10` |
| OrangeVPS MySQL | writable, binlog enabled, `server_id=178239117` |
| Sakura MySQL mirror | follows OrangeVPS, lag `0` |
| Old production Flarum | stopped |
| Old production MySQL | retained for inspection and fallback data decisions |

See `docs/bbs-first-cutover-2026-06-29.md` for the execution report and evidence.

## Current Known Gaps Before Cutover

| Gap | Current State | Required Action |
|---|---|---|
| Backend image version | Old production uses `hospital-backend:20260626`; OrangeVPS service record still references `hospital-backend:20260625` | Upload or build `hospital-backend:20260626` on OrangeVPS and update stack compose before cutover |
| OrangeVPS game app services | A temporary game test runtime may still be active on production-like ports and points to writable test PostgreSQL | Stop the game test runtime, then start official game services only after official PostgreSQL is promoted |
| Official OrangeVPS PostgreSQL | Read-only replica | Promote during the game cutover window |
| Official OrangeVPS MySQL | Already promoted and serving BBS | Keep it running, do not reset it during game cutover |
| Public gateway for game domains | Still targets `47.79.38.216` | Replace game proxy targets with `178.239.117.99` during game cutover |
| Public gateway for `bbs.rhospital.cc` | Already targets `178.239.117.99:40020` | Monitor, do not switch again during game cutover |
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
| `bbs.rhospital.cc` | `178.239.117.99:40020` | completed on 2026-06-29 |
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
- Old production Flarum remains stopped.
- Old production MySQL remains available for inspection and fallback data decisions.

### Sakura Mirror Health

- PostgreSQL:
  - `rhospital-postgres-standby-trixie` running
  - `pg_is_in_recovery() = true`
  - WAL receiver `streaming`
  - receive LSN equals replay LSN
  - collation mismatch count `0`
- MySQL:
  - `rhospital-bbs-mysql-replica` running
  - source host is `178.239.117.99`
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
  - BBS writer after 2026-06-29 first-version cutover
  - `read_only=OFF`
  - `super_read_only=OFF`
  - binlog enabled for Sakura downstream replication
- Game application services remain controlled:
  - if the temporary game test runtime is active, stop it before the game cutover checkpoint
  - backend official runtime should be `0/0`
  - official game `snail-job` should be stopped until game database promotion
  - official `flarum` should remain running for BBS
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
- Verify BBS only through the already public OrangeVPS runtime.
- Verify static assets and uploaded files.
- Verify no test traffic reaches official ports `35432` and `33306`.
- Remove temporary backend test service after testing.

Do not use ports `35432` and `33306` for game write tests before cutover. Port `33306` is now the BBS MySQL writer and must not be reset for game testing.

If the 2026-06-28 game test runtime is still active, the direct backend check is:

```text
Backend test URL: http://178.239.117.99:8190/
```

`http://178.239.117.99:40020/` is now official BBS runtime, not a test URL.

Before moving into the formal game cutover window, stop or replace the game test runtime:

```bash
docker service scale hospital_stack_hospital-backend=0
docker rm -f snail-job-test
```

Then restore the official backend service specification from the official compose:

```bash
docker stack deploy -c /opt/1panel/docker/compose/hospital-stack/docker-compose.yml hospital_stack
docker service scale hospital_stack_hospital-backend=0
```

## Cutover Window Plan

Target operational impact: keep write freeze within 30 minutes.

### T minus 24 hours

- Freeze nonessential deployments.
- Confirm backend image `hospital-backend:20260626` is available on OrangeVPS.
- Update OrangeVPS stack compose image tag to the production target image.
- Confirm the compose environment still points to official OrangeVPS database ports:
  - PostgreSQL `178.239.117.99:35432`
  - snail-job `178.239.117.99`
- Confirm Flarum is still running against the official MySQL container.
- Confirm writable test baseline is not referenced by production compose files.
- Prepare gateway config backups.
- Prepare rollback command snippets, but do not run them.

### T minus 60 minutes

- Announce maintenance window to players and BBS users.
- Confirm no manual deployment is in progress.
- Confirm all application write-capable services on OrangeVPS remain stopped.
- Confirm `hospital_stack_hospital-backend` does not point to `45432`.
- Confirm `flarum-test` is not running.
- Confirm `snail-job-test` is not running before game cutover.
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

- BBS remains healthy through `https://bbs.rhospital.cc/`
- `read_only=OFF`
- `super_read_only=OFF`
- Sakura downstream replica lag remains `0`

Check old production:

- OrangeVPS PostgreSQL standby replay lag bytes `0`
- OrangeVPS PostgreSQL slot retained WAL bytes `0`
- old production Flarum remains stopped

If any final catch-up check fails, keep gateway pointing to old production and stop the cutover.

### T plus 5 to 10 minutes, promote OrangeVPS databases

Promote PostgreSQL on OrangeVPS:

- run PostgreSQL promote on official container `postgresql`
- verify `pg_is_in_recovery() = false`
- verify `hospital`, `snailjob`, and `postgres` are accessible
- verify collation mismatch count `0`
- perform a controlled write probe if required, then remove the probe object

Keep MySQL on OrangeVPS as the current BBS writer:

- do not stop or reset OrangeVPS MySQL during the game cutover
- verify Flarum database table count
- verify Sakura still follows OrangeVPS with lag `0`
- perform no BBS write probe during the game cutover unless a BBS validation owner approves it

Do not start game applications until PostgreSQL is writable and verified.

### T plus 10 to 15 minutes, start OrangeVPS application services

Start database-dependent services in this order:

1. PostgreSQL official container is already running and writable
2. MySQL official container remains running for BBS
3. `snail-job`
4. backend Swarm service

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
- BBS `http://127.0.0.1:40020/`, monitor only because it is already public
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
- `bbs.rhospital.cc`, already on OrangeVPS after the BBS first-version cutover
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

The BBS MySQL first leg was completed on 2026-06-29:

```text
RHospital.OrangeVPS MySQL -> SAKURA-HOSP-DBBACK MySQL
```

Current Sakura BBS MySQL mirror state:

- container `rhospital-bbs-mysql-replica` running
- `Source_Host=178.239.117.99`
- `Source_Port=33306`
- `Replica_IO_Running=Yes`
- `Replica_SQL_Running=Yes`
- `Seconds_Behind_Source=0`
- `read_only=ON`
- `super_read_only=ON`

Old production MySQL remains available for inspection. Repointing old production MySQL downstream of Sakura is still a separate decision.

Recommended sequence:

1. Ensure OrangeVPS MySQL remains binlog enabled with unique server id.
2. Confirm Sakura continues to follow OrangeVPS:
   - `Replica_IO_Running=Yes`
   - `Replica_SQL_Running=Yes`
   - `Seconds_Behind_Source=0`
3. Configure old production MySQL to follow Sakura if old production remains in lifecycle.
4. Confirm old production is read-only downstream.
5. Remove old production source users and obsolete replication credentials after retirement.

## Monitoring After Cutover

Monitor continuously for at least 24 hours:

- gateway OpenResty access and error logs
- OrangeVPS backend logs
- OrangeVPS `docker service ps hospital_stack_hospital-backend`
- OrangeVPS PostgreSQL logs
- OrangeVPS MySQL logs for BBS
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
- OrangeVPS MySQL is writable for BBS and Sakura MySQL mirror delay is `0` or explicitly accepted.
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
- Sakura MySQL downstream delay from OrangeVPS does not converge.
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
- `docs/bbs-first-cutover-2026-06-29.md`
