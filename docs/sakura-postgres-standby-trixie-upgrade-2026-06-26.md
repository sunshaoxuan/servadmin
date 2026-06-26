# Sakura PostgreSQL Standby Trixie Upgrade 2026-06-26

## Summary

`SAKURA-HOSP-DBBACK` PostgreSQL standby was moved from the host-installed Ubuntu PostgreSQL cluster to a Docker container using the same runtime family as production:

- Image: `pgvector/pgvector:pg18-trixie`
- Container: `rhospital-postgres-standby-trixie`
- Data root: `/opt/rhospital-postgres-standby-trixie/pgroot`
- Container data path: `/var/lib/postgresql/18/docker`
- Host port: `127.0.0.1:5432`

The purpose was to remove the collation mismatch caused by the host PostgreSQL runtime reporting actual collation version `2.39` while production databases were created with collation version `2.41`.

No production service on `CLAW-JP-PROD` was stopped or restarted.

## Pre-change State

- Old Sakura standby was a host PostgreSQL cluster:
  - Cluster: `18/main`
  - Data directory: `/var/lib/postgresql/18/main`
  - Status: `online,recovery`
- Old standby data size was about `765M`.
- Old standby was caught up before replacement:
  - receive LSN equaled replay LSN
  - LSN diff was `0`
- Old host runtime produced collation mismatch:
  - recorded version: `2.41`
  - actual version: `2.39`
  - affected databases included `hospital`, `postgres`, `snailjob`, `template1`, and `user_CBJxJt`
- Source `CLAW-JP-PROD` had enough WAL retention headroom:
  - `rhospital_backup_160_16_91_200` retained WAL: `0 bytes`
  - source disk free space: about `191G`

## Changes

- Pulled `pgvector/pgvector:pg18-trixie` on `SAKURA-HOSP-DBBACK`.
- Verified the image runtime reports GLIBC `2.41`.
- Stopped the old host PostgreSQL standby.
- Copied the old standby data directory into the new Docker-managed bind mount tree.
- Started `rhospital-postgres-standby-trixie` with restart policy `unless-stopped`.
- Reused the existing upstream connection and replication slot:
  - application name: `rhospital_standby_160_16_91_200`
  - slot: `rhospital_backup_160_16_91_200`
- Disabled and dropped the old host PostgreSQL cluster after the new container standby passed validation.
- Removed dangling Docker volume residue from the temporary container test and first mount attempt.

## Validation

Mirror-side validation on `SAKURA-HOSP-DBBACK`:

- Container image: `pgvector/pgvector:pg18-trixie`
- Container restart policy: `unless-stopped`
- Active mount:
  - `/opt/rhospital-postgres-standby-trixie/pgroot` to `/var/lib/postgresql`
- Runtime GLIBC:
  - `Debian GLIBC 2.41-12+deb13u3`
- PostgreSQL recovery:
  - `pg_is_in_recovery() = true`
  - receive LSN equaled replay LSN
  - LSN diff was `0`
  - WAL receiver status was `streaming`
- Collation:
  - `hospital`: `2.41 / 2.41 / mismatch=false`
  - `postgres`: `2.41 / 2.41 / mismatch=false`
  - `snailjob`: `2.41 / 2.41 / mismatch=false`
  - `template1`: `2.41 / 2.41 / mismatch=false`
  - `user_CBJxJt`: `2.41 / 2.41 / mismatch=false`
  - mismatch count: `0`

Source-side validation on `CLAW-JP-PROD`:

- Sakura standby row:
  - `rhospital_standby_160_16_91_200`
  - client: `160.16.91.200`
  - state: `streaming`
  - sync mode: `async`
  - replay lag bytes: `0`
- Sakura replication slot:
  - `rhospital_backup_160_16_91_200`
  - active: `true`
  - WAL status: `reserved`
  - retained WAL: `0 bytes`

Cleanup validation:

- Old host PostgreSQL cluster list was empty.
- Host `postgresql` service was disabled and inactive.
- `/var/lib/postgresql` was reduced to about `4K`.
- Active PostgreSQL standby data exists only under `/opt/rhospital-postgres-standby-trixie/pgroot`.
- Docker local volumes: `0`.
- MySQL replica container `rhospital-bbs-mysql-replica` remained running.

## Rollback Notes

The old host PostgreSQL standby data was intentionally removed after the new container standby passed validation and source-side replication was confirmed. Rollback should now use one of these paths:

- keep using the new container standby and restart it with Docker if needed
- rebuild Sakura standby from production using the existing replication slot or a fresh base backup
- use `RHospital.OrangeVPS` as the newer prewarmed production-equivalent environment if Sakura standby rebuild is delayed

## Remaining Notes

- The Sakura PostgreSQL mirror is still asynchronous. Final migration work must still perform a last replay-lag check before any cutover decision.
- The source also has another standby at `192.129.191.18`; it is unrelated to this Sakura cleanup.
