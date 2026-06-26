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

## Cleanup

When testing is finished, remove:

- container `rhospital-test-postgres`
- container `rhospital-test-mysql`
- directory `/opt/rhospital-test-baseline`

Do not remove the official replica containers `postgresql` and `mysql`.
