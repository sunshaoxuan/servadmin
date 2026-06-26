# OrangeVPS Second Replica Setup 2026-06-26

## Summary

`RHospital.OrangeVPS` was converted from an independent prewarmed environment into a read-only second replica that continuously follows `CLAW-JP-PROD`.

No production service on `CLAW-JP-PROD` was stopped or restarted.

The public gateway still points to `CLAW-JP-PROD`. No cutover was performed.

## Target Topology

- PostgreSQL:
  - `CLAW-JP-PROD` to `SAKURA-HOSP-DBBACK`
  - `CLAW-JP-PROD` to `RHospital.OrangeVPS`
- MySQL:
  - `CLAW-JP-PROD` to `SAKURA-HOSP-DBBACK`
  - `CLAW-JP-PROD` to `RHospital.OrangeVPS`

`RHospital.OrangeVPS` is intentionally not accepting application writes in this state.

## Changes

- Stopped write-capable services on `RHospital.OrangeVPS`:
  - scaled `hospital_stack_hospital-backend` to `0/0`
  - stopped `snail-job`
  - stopped `flarum`
- Created a dedicated PostgreSQL replication slot on `CLAW-JP-PROD`:
  - `rhospital_backup_178_239_117_99`
- Added PostgreSQL replication access for `178.239.117.99` on `CLAW-JP-PROD`.
- Used the caught-up Sakura PostgreSQL standby as the baseline for `RHospital.OrangeVPS`.
- Reconfigured the copied PostgreSQL standby baseline on `RHospital.OrangeVPS`:
  - application name: `rhospital_standby_178_239_117_99`
  - slot: `rhospital_backup_178_239_117_99`
- Removed the previous independent OrangeVPS PostgreSQL data directory after the new standby validated successfully.
- Created a dedicated MySQL replication user for `RHospital.OrangeVPS` on `CLAW-JP-PROD`.
- Exported the Flarum MySQL baseline from `SAKURA-HOSP-DBBACK` at a stable replica execution point.
- Imported the Flarum MySQL baseline into `RHospital.OrangeVPS`.
- Configured `RHospital.OrangeVPS` MySQL as a replica of `CLAW-JP-PROD`.
- Set `RHospital.OrangeVPS` MySQL read-only controls:
  - `read_only=ON`
  - `super_read_only=ON`
- Changed `RHospital.OrangeVPS` MySQL identity to avoid clashing with Sakura:
  - server id: `178239117`
  - server UUID regenerated on OrangeVPS

## Validation

`RHospital.OrangeVPS` write-capable application services:

- `hospital_stack_hospital-backend`: `0/0`
- `snail-job`: stopped
- `flarum`: stopped

`RHospital.OrangeVPS` PostgreSQL:

- container: `postgresql`
- image: `pgvector/pgvector:pg18-trixie`
- `pg_is_in_recovery() = true`
- receive LSN equaled replay LSN
- LSN diff: `0`
- WAL receiver: `streaming`
- collation mismatch count: `0`

`RHospital.OrangeVPS` MySQL:

- container: `mysql`
- image: `mysql:8.4.9`
- source host: `47.79.38.216`
- `Replica_IO_Running = Yes`
- `Replica_SQL_Running = Yes`
- `Seconds_Behind_Source = 0`
- `Last_IO_Error` was empty
- `Last_SQL_Error` was empty
- `read_only = ON`
- `super_read_only = ON`
- Flarum table count: `33`

`CLAW-JP-PROD` PostgreSQL source confirmed:

- `rhospital_standby_160_16_91_200` from `160.16.91.200`: `streaming`, replay lag bytes `0`
- `rhospital_standby_178_239_117_99` from `178.239.117.99`: `streaming`, replay lag bytes `0`
- `rhospital_backup_160_16_91_200`: active, retained WAL bytes `0`
- `rhospital_backup_178_239_117_99`: active, retained WAL bytes `0`

`CLAW-JP-PROD` MySQL source confirmed:

- Sakura replica:
  - server id: `1601691200`
  - binlog dump connection from `160.16.91.200`
- OrangeVPS replica:
  - server id: `178239117`
  - binlog dump connection from `178.239.117.99`

## Notes

- This is not a cutover.
- `RHospital.OrangeVPS` is now a read-only candidate production environment.
- Before cutover, perform one final PostgreSQL and MySQL lag check, then promote OrangeVPS databases and start the application services.
- The current OrangeVPS backend service remains on image `hospital-backend:20260625`; production currently uses `hospital-backend:20260626`.
