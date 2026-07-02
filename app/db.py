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
"""

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
    conn.commit()


def row_to_server(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["tags"] = json.loads(data.pop("tags_json") or "[]")
    data["is_starred"] = bool(data.get("is_starred"))
    data["is_retired"] = bool(data.get("is_retired"))
    data["config_report"] = json.loads(data.pop("config_report_json") or "{}")
    data["installed_apps"] = json.loads(data.pop("installed_apps_json") or "[]")
    data["services"] = json.loads(data.pop("services_json") or "[]")
    data["has_panel_password"] = bool(data.get("panel_password_encrypted"))
    data.pop("panel_password_encrypted", None)
    data.pop("credential_encrypted", None)
    return data
