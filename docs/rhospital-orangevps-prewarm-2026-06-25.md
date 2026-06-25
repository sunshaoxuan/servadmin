# RHospital OrangeVPS Prewarm Record 2026-06-25

## Summary

This record documents the pre-cutover migration work from `CLAW-JP-PROD` to `RHospital.OrangeVPS`.

No public gateway cutover was performed. No write fence was enabled. No production service on `CLAW-JP-PROD` was stopped or restarted.

## Verified Source State

- `CLAW-JP-PROD` backend container was healthy on image `hospital-backend:20260625`.
- Docker Swarm hot release settings were present: `start-first`, `parallelism=1`, `failure_action=rollback`, `monitor=5m`.
- PostgreSQL source had streaming async replicas, including `rhospital_standby_160_16_91_200`.
- `SAKURA-HOSP-DBBACK` MySQL replica reported `Replica_IO_Running=Yes`, `Replica_SQL_Running=Yes`, and `Seconds_Behind_Source=0`.
- `RHOSPITAL-GATE` still pointed public traffic at `47.79.38.216`.

## OrangeVPS Prewarm Changes

- Installed Docker, Docker Compose v2, rsync, tar, and gzip on `RHospital.OrangeVPS`.
- Initialized Docker Swarm on `RHospital.OrangeVPS`.
- Pulled public images:
  - `opensnail/snail-job:1.10.0`
  - `crazymax/flarum:1.8.10`
  - `mysql:8.4.9`
  - `pgvector/pgvector:pg18-trixie`
  - `1panel/openresty:1.29.2.4-0-noble`
- Copied private image `hospital-backend:20260625`; target image ID matched source image ID.
- Hot-copied 1Panel app and compose assets, excluding live production PostgreSQL and MySQL data directories.
- Restored PostgreSQL prewarm data from the Sakura standby using logical dump and restore.
- Restored Flarum MySQL data from the Sakura replica using a stopped-replica physical snapshot.
- Started target prewarm services on OrangeVPS:
  - `postgresql`
  - `mysql`
  - `flarum`
  - `snail-job`
  - `openresty`
  - `hospital_stack_hospital-backend`
- Installed 1Panel `v2.1.13` on OrangeVPS after backing up the prewarm `/opt/1panel` directory.
- Restored the prewarmed 1Panel app, compose, and website directories after installing 1Panel.
- Stored OrangeVPS 1Panel login URL, username, and encrypted panel password in Server Desk server id `7`.

## Validation

- Backend service on OrangeVPS reported healthy in Docker Swarm.
- Local backend check returned page title `英雄荣光医院:立刻开玩`.
- Local Flarum check returned page title `英雄荣光医院论坛`.
- PostgreSQL target databases: `hospital`, `postgres`, `snailjob`, and the default root-user database.
- MySQL target `flarum_rtt3ns` database had 33 tables.
- Server Desk inspection for OrangeVPS was refreshed and reported `80 个应用，29 个服务`.
- OrangeVPS disk after prewarm: about `9.2G` used, `68G` available on `/`.
- OrangeVPS 1Panel responded with `HTTP 200` at `http://178.239.117.99:38428/rhospital`.

## Cutover Status

Pending. A separate scheduled cutover window is still required.

Actions not yet performed:

- Publish maintenance notice.
- Enable write fence on `RHOSPITAL-GATE`.
- Perform final database catch-up.
- Promote OrangeVPS as production writer.
- Change `RHOSPITAL-GATE` proxy targets from `47.79.38.216` to `178.239.117.99`.
- Run post-cutover production write validation.

## Known Follow-up

- The Flarum MySQL prewarm snapshot required `lower_case_table_names=0` on OrangeVPS because the Sakura replica data dictionary was created with that setting.
- PostgreSQL logical dumps from Sakura reported collation-version warnings caused by OS library version differences. The restore completed successfully, but this should be reviewed before final cutover.
- The final cutover should repeat database catch-up immediately before the write fence is released.
