# OrangeVPS Writable Test Baseline 2026-06-26

## Summary

A writable test baseline was created on `RHospital.OrangeVPS` so backend and BBS cutover behavior can be tested without writing to the official read-only replica.

No public gateway change was performed.

No `CLAW-JP-PROD` production service was stopped or restarted.

## Purpose

`RHospital.OrangeVPS` official databases are currently read-only replicas of `CLAW-JP-PROD`. They must stay read-only until cutover. The test baseline provides independent writable database instances for application testing.

## Test Baseline Containers

PostgreSQL:

- Container: `rhospital-test-postgres`
- Image: `pgvector/pgvector:pg18-trixie`
- Host port: `45432`
- Data path: `/opt/rhospital-test-baseline/postgres/pgroot`
- Baseline source: current OrangeVPS official PostgreSQL standby data
- Runtime mode: writable standalone PostgreSQL

MySQL:

- Container: `rhospital-test-mysql`
- Image: `mysql:8.4.9`
- Host port: `43306`
- Data path: `/opt/rhospital-test-baseline/mysql/data`
- Baseline source: current OrangeVPS official MySQL replica dump
- Runtime mode: writable standalone MySQL
- Test MySQL server id: `178239118`

Credentials:

- Stored on `RHospital.OrangeVPS` at `/opt/rhospital-test-baseline/credentials.env`
- File permission: `600`
- Credentials are intentionally not stored in this repository.

## Official Replica Safety

The official OrangeVPS replica ports remain unchanged:

- PostgreSQL official replica: `35432`
- MySQL official replica: `33306`

The writable test ports are separate:

- PostgreSQL test baseline: `45432`
- MySQL test baseline: `43306`

## Validation

Test PostgreSQL:

- `pg_is_in_recovery() = false`
- collation mismatch count: `0`
- write probe succeeded with create, insert, select, and drop

Test MySQL:

- `read_only = OFF`
- `super_read_only = OFF`
- Flarum table count: `33`
- write probe succeeded with create, insert, select, and drop

Official PostgreSQL replica:

- `pg_is_in_recovery() = true`
- receive and replay LSN diff: `0`

Official MySQL replica:

- `Replica_IO_Running = Yes`
- `Replica_SQL_Running = Yes`
- `Seconds_Behind_Source = 0`
- `read_only = ON`
- `super_read_only = ON`

Write-capable application services remained stopped:

- `hospital_stack_hospital-backend`: `0/0`
- `snail-job`: stopped
- `flarum`: stopped

## Usage Notes

Use the test baseline only for temporary cutover and application-write testing. It is a snapshot and does not follow production changes.

Backend tests should point to:

- PostgreSQL: `178.239.117.99:45432`
- MySQL or Flarum tests: `178.239.117.99:43306`

The official cutover candidate remains the read-only replica on `35432` and `33306`.

## Application Test Runtime 2026-06-28

A temporary application test runtime was started on `RHospital.OrangeVPS` to prove that the new production host can run the old production style services before public cutover.

No public gateway change was performed. `RHOSPITAL-GATE` still points public traffic at `CLAW-JP-PROD`.

Runtime layout:

| Component | Runtime | Public test port | Database target |
|---|---|---:|---|
| Game backend | Swarm service `hospital_stack_hospital-backend` | `8190`, `9996`, `17889` | PostgreSQL test baseline `178.239.117.99:45432/hospital` |
| snail-job | Container `snail-job-test` | `38084`, `17888` | PostgreSQL test baseline `178.239.117.99:45432/snailjob` |
| BBS | Container `flarum-test` | `40020` | MySQL test baseline container `rhospital-test-mysql:3306` |

Runtime compose files on OrangeVPS:

```text
/opt/rhospital-test-runtime/hospital-stack/docker-compose.yml
/opt/rhospital-test-runtime/snailjob/docker-compose.yml
/opt/rhospital-test-runtime/flarum/docker-compose.yml
```

The original read-only production compose file remains:

```text
/opt/1panel/docker/compose/hospital-stack/docker-compose.yml
```

Current validation evidence from 2026-06-28:

- `hospital_stack_hospital-backend` reached `1/1`.
- Backend container health was `healthy`.
- Backend environment contained `SPRING_DATASOURCE_URL=jdbc:postgresql://178.239.117.99:45432/hospital`.
- `http://178.239.117.99:8190/` returned HTTP `200`.
- `flarum-test` returned the BBS homepage from `http://178.239.117.99:40020/`.
- Flarum test config pointed at `DB_HOST=rhospital-test-mysql`, `DB_PORT=3306`, `DB_NAME=flarum_rtt3ns`, `DB_USER=flarum_test`.
- Test PostgreSQL was writable and `pg_is_in_recovery() = false`.
- Official PostgreSQL stayed a read-only standby with `pg_is_in_recovery() = true`.
- Test MySQL was writable with `read_only=0` and `super_read_only=0`.
- Official MySQL stayed protected with `read_only=1` and `super_read_only=1`.

Important operational note:

- The active Swarm service `hospital_stack_hospital-backend` currently points to the test PostgreSQL baseline.
- Before formal cutover, either stop this test runtime or redeploy the official compose after promoting the official databases.
- Do not switch `RHOSPITAL-GATE` to OrangeVPS while `hospital_stack_hospital-backend` points at `45432`.

Stop test runtime after testing:

```bash
docker service scale hospital_stack_hospital-backend=0
docker rm -f snail-job-test flarum-test
```

Restore official Swarm service specification from the original compose when preparing for cutover:

```bash
docker stack deploy -c /opt/1panel/docker/compose/hospital-stack/docker-compose.yml hospital_stack
docker service scale hospital_stack_hospital-backend=0
```

## Cleanup

When testing is finished, remove:

- container `rhospital-test-postgres`
- container `rhospital-test-mysql`
- directory `/opt/rhospital-test-baseline`

Do not remove the official replica containers `postgresql` and `mysql`.
