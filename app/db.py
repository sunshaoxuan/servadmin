from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


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
  service_code text,
  tags_json text not null default '[]',
  notes text,
  credential_encrypted text,
  last_status text not null default 'unknown',
  last_latency_ms integer,
  last_checked_at text,
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


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys = on")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def row_to_server(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["tags"] = json.loads(data.pop("tags_json") or "[]")
    data.pop("credential_encrypted", None)
    return data

