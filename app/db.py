from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Union


SCHEMA = """
create table if not exists users (
  id integer primary key autoincrement,
  username text not null unique,
  password_hash text not null,
  password_salt text not null,
  created_at text not null default current_timestamp
);

create table if not exists servers (
  id integer primary key autoincrement,
  name text not null,
  hostname text not null,
  ipv4 text,
  ipv6 text,
  provider text,
  region text,
  login_user text not null,
  auth_type text not null default 'password',
  ssh_host text,
  ssh_port integer not null default 22,
  ssh_key_path text,
  ssh_local_key_path text,
  ssh_windows_key_path text,
  ssh_options text,
  panel_url text,
  panel_username text,
  panel_password_encrypted text,
  service_code text,
  is_starred integer not null default 0,
  is_retired integer not null default 0,
  heartbeat_enabled integer not null default 0,
  heartbeat_port integer not null default 9108,
  tags_json text not null default '[]',
  notes text,
  credential_encrypted text,
  last_status text not null default 'unknown',
  last_latency_ms integer,
  last_checked_at text,
  config_status text not null default 'unknown',
  config_summary text,
  config_report_json text not null default '{}',
  installed_apps_json text not null default '[]',
  services_json text not null default '[]',
  last_config_check_at text,
  created_at text not null default current_timestamp,
  updated_at text not null default current_timestamp
);

create table if not exists audit_events (
  id integer primary key autoincrement,
  actor text not null,
  action text not null,
  target_type text not null,
  target_id integer,
  detail text,
  created_at text not null default current_timestamp
);

create table if not exists mesh_health_samples (
  id integer primary key autoincrement,
  server_id integer not null references servers(id) on delete cascade,
  sampled_at integer not null,
  network_score real not null,
  app_score real,
  direct_ok integer not null default 0,
  direct_latency_ms integer,
  peer_visible integer not null default 0,
  peer_expected integer not null default 0,
  details_json text not null default '{}',
  unique(server_id, sampled_at)
);

create index if not exists idx_mesh_health_server_time
on mesh_health_samples(server_id, sampled_at);

create table if not exists mesh_poll_cycles (
  sampled_at integer primary key,
  status text not null,
  attempted_sources integer not null default 0,
  successful_sources integer not null default 0,
  source_server_ids_json text not null default '[]',
  errors_json text not null default '{}'
);

create table if not exists schema_migrations (
  version integer primary key,
  name text not null,
  applied_at text not null default current_timestamp
);
"""

MIGRATIONS = [
    (
        1,
        "server subscription usage",
        """
        create table if not exists server_subscription_usage (
          id integer primary key autoincrement,
          server_id integer not null references servers(id) on delete cascade,
          period_start text not null,
          period_end text not null,
          used_bytes integer not null check(used_bytes >= 0),
          quota_bytes integer not null check(quota_bytes > 0),
          source_label text not null,
          source_url text,
          collected_at text not null default current_timestamp,
          created_by text not null,
          unique(server_id, period_start, period_end)
        );
        create index if not exists idx_subscription_usage_server_collected
        on server_subscription_usage(server_id, collected_at desc);
        """,
    ),
    (
        2,
        "automatic monthly traffic meter",
        """
        create table if not exists server_traffic_meter (
          id integer primary key autoincrement,
          server_id integer not null references servers(id) on delete cascade,
          period_start text not null,
          period_end text not null,
          base_used_bytes integer not null default 0 check(base_used_bytes >= 0),
          quota_bytes integer check(quota_bytes > 0),
          measured_rx_bytes integer not null default 0 check(measured_rx_bytes >= 0),
          measured_tx_bytes integer not null default 0 check(measured_tx_bytes >= 0),
          last_rx_counter integer,
          last_tx_counter integer,
          last_observed_at integer,
          source_label text not null,
          count_mode text not null default 'both' check(count_mode in ('both', 'outbound')),
          baseline_collected_at text,
          is_partial integer not null default 1,
          initialized_at text not null default current_timestamp,
          updated_at text not null default current_timestamp,
          unique(server_id, period_start, period_end)
        );
        create index if not exists idx_traffic_meter_server_period
        on server_traffic_meter(server_id, period_end desc);
        """,
    ),
    (
        3,
        "disable agent billing traffic meter",
        """
        delete from server_traffic_meter;
        """,
    ),
    (
        4,
        "server provider access archive",
        """
        create table if not exists server_provider_access (
          server_id integer primary key references servers(id) on delete cascade,
          portal_url text,
          login_username text,
          password_encrypted text,
          service_reference text,
          external_server_id text,
          connector_type text not null default 'browser',
          sync_enabled integer not null default 1,
          last_sync_status text not null default 'pending',
          last_sync_message text,
          last_synced_at text,
          created_at text not null default current_timestamp,
          updated_at text not null default current_timestamp
        );
        """,
    ),
    (
        5,
        "provider traffic reset timezone",
        """
        alter table server_subscription_usage add column next_reset_at text;
        alter table server_subscription_usage add column reset_timezone text;
        """,
    ),
    (
        6,
        "enable configured provider sync",
        """
        update server_provider_access
        set connector_type = case
              when lower(coalesce(portal_url, '')) = 'https://portal.orangevps.com'
                or lower(coalesce(portal_url, '')) like 'https://portal.orangevps.com/%' then 'orangevps'
              when lower(coalesce(portal_url, '')) = 'https://portal.sa.net'
                or lower(coalesce(portal_url, '')) like 'https://portal.sa.net/%' then 'riven_cloud'
              else connector_type
            end,
            updated_at = current_timestamp
        where connector_type in ('', 'browser')
          and (
            lower(coalesce(portal_url, '')) = 'https://portal.orangevps.com'
            or lower(coalesce(portal_url, '')) like 'https://portal.orangevps.com/%'
            or lower(coalesce(portal_url, '')) = 'https://portal.sa.net'
            or lower(coalesce(portal_url, '')) like 'https://portal.sa.net/%'
          );

        update server_provider_access
        set sync_enabled = 1,
            updated_at = current_timestamp
        where connector_type in ('riven_cloud', 'orangevps')
          and coalesce(login_username, '') != ''
          and coalesce(password_encrypted, '') != ''
          and coalesce(service_reference, '') != ''
          and coalesce(external_server_id, '') != '';
        """,
    ),
]

SERVER_MIGRATIONS = {
    "ssh_host": "alter table servers add column ssh_host text",
    "ssh_port": "alter table servers add column ssh_port integer not null default 22",
    "ssh_key_path": "alter table servers add column ssh_key_path text",
    "ssh_local_key_path": "alter table servers add column ssh_local_key_path text",
    "ssh_windows_key_path": "alter table servers add column ssh_windows_key_path text",
    "ssh_options": "alter table servers add column ssh_options text",
    "panel_url": "alter table servers add column panel_url text",
    "panel_username": "alter table servers add column panel_username text",
    "panel_password_encrypted": "alter table servers add column panel_password_encrypted text",
    "is_starred": "alter table servers add column is_starred integer not null default 0",
    "is_retired": "alter table servers add column is_retired integer not null default 0",
    "heartbeat_enabled": "alter table servers add column heartbeat_enabled integer not null default 0",
    "heartbeat_port": "alter table servers add column heartbeat_port integer not null default 9108",
    "config_status": "alter table servers add column config_status text not null default 'unknown'",
    "config_summary": "alter table servers add column config_summary text",
    "config_report_json": "alter table servers add column config_report_json text not null default '{}'",
    "installed_apps_json": "alter table servers add column installed_apps_json text not null default '[]'",
    "services_json": "alter table servers add column services_json text not null default '[]'",
    "last_config_check_at": "alter table servers add column last_config_check_at text",
}


def connect(db_path: Union[str, Path]) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys = on")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    existing_columns = {
        row["name"] if isinstance(row, sqlite3.Row) else row[1]
        for row in conn.execute("pragma table_info(servers)").fetchall()
    }
    for column_name, statement in SERVER_MIGRATIONS.items():
        if column_name not in existing_columns:
            conn.execute(statement)
    applied = {
        int(row["version"] if isinstance(row, sqlite3.Row) else row[0])
        for row in conn.execute("select version from schema_migrations").fetchall()
    }
    for version, name, script in MIGRATIONS:
        if version in applied:
            continue
        conn.executescript(script)
        conn.execute(
            "insert into schema_migrations(version, name) values (?, ?)",
            (version, name),
        )
    conn.commit()


def row_to_server(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["tags"] = json.loads(data.pop("tags_json") or "[]")
    data["is_starred"] = bool(data.get("is_starred"))
    data["is_retired"] = bool(data.get("is_retired"))
    data["heartbeat_enabled"] = bool(data.get("heartbeat_enabled"))
    data["config_report"] = json.loads(data.pop("config_report_json") or "{}")
    data["installed_apps"] = json.loads(data.pop("installed_apps_json") or "[]")
    data["services"] = json.loads(data.pop("services_json") or "[]")
    data["has_panel_password"] = bool(data.get("panel_password_encrypted"))
    data.pop("panel_password_encrypted", None)
    data.pop("credential_encrypted", None)
    return data
