#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import random
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable


MAX_BODY_BYTES = 512 * 1024
MAX_CLOCK_SKEW_SECONDS = 650
HEARTBEAT_FRESH_SECONDS = 5 * 60
REPORT_INTERVAL_SECONDS = 5 * 60
SEEN_REPORT_RETENTION_SECONDS = 30 * 60
PROTOCOL_VERSION = 2
MAX_SYNC_RECORDS = 64
MAX_WITNESSES = 8
DEFAULT_FANOUT = 3
DEFAULT_STABLE_FANOUT = 2
DEFAULT_REGISTRATION_ATTEMPTS = 5
DEFAULT_REPORT_TIME_BUDGET_SECONDS = 20
DEFAULT_LOCAL_STATUS_INTERVAL_SECONDS = 60
DEFAULT_MAX_WORKERS = 32


def request_signature(secret: str, method: str, path: str, timestamp: str, body: bytes = b"") -> str:
    digest = hashlib.sha256(body).hexdigest()
    message = "\n".join((method.upper(), path, timestamp, digest)).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def signed_headers(
    secret: str,
    node_id: str,
    method: str,
    path: str,
    body: bytes = b"",
    now: int | None = None,
) -> dict[str, str]:
    timestamp = str(int(now if now is not None else time.time()))
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Heartbeat-Node": node_id,
        "X-Heartbeat-Timestamp": timestamp,
        "X-Heartbeat-Signature": request_signature(secret, method, path, timestamp, body),
    }


def load_config(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not data.get("advertise_host"):
        data["advertise_host"] = data.get("host", "")
    required = ("node_id", "node_name", "advertise_host", "shared_secret", "port", "state_path")
    missing = [key for key in required if not data.get(key)]
    if missing:
        raise ValueError(f"missing config fields: {', '.join(missing)}")
    data["node_id"] = str(data["node_id"])
    data["port"] = int(data["port"])
    data.setdefault("bind_host", "0.0.0.0")
    data.setdefault("peers", [])
    data.setdefault("services", [])
    data.setdefault("membership_epoch", 0)
    data.setdefault("fanout", DEFAULT_FANOUT)
    data.setdefault("stable_fanout", DEFAULT_STABLE_FANOUT)
    data.setdefault("registration_max_attempts", DEFAULT_REGISTRATION_ATTEMPTS)
    data.setdefault("report_time_budget_seconds", DEFAULT_REPORT_TIME_BUDGET_SECONDS)
    data.setdefault("local_status_interval_seconds", DEFAULT_LOCAL_STATUS_INTERVAL_SECONDS)
    data.setdefault("max_workers", DEFAULT_MAX_WORKERS)
    return data


def connect_state(path: str | Path) -> sqlite3.Connection:
    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(state_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma journal_mode = wal")
    conn.execute("pragma foreign_keys = on")
    conn.executescript(
        """
        create table if not exists registered_nodes (
          node_id text primary key,
          node_name text not null,
          host text not null,
          port integer not null,
          registered_at integer not null,
          updated_at integer not null,
          membership_status text not null default 'active',
          membership_version integer not null default 0
        );
        create table if not exists heartbeat_records (
          node_id text primary key references registered_nodes(node_id) on delete cascade,
          observed_at integer not null,
          received_at integer not null,
          payload_json text not null,
          seen_by_json text not null
        );
        create table if not exists outbound_status (
          peer_id text primary key,
          peer_name text not null,
          attempted_at integer not null,
          succeeded_at integer,
          ok integer not null,
          latency_ms integer,
          error text,
          failure_count integer not null default 0,
          next_attempt_at integer not null default 0,
          peer_versions_json text not null default '{}',
          peer_membership_digest text not null default ''
        );
        create table if not exists seen_reports (
          report_id text primary key,
          sender_id text not null,
          received_at integer not null
        );
        create table if not exists local_meta (
          key text primary key,
          value text not null
        );
        create index if not exists seen_reports_received_idx on seen_reports(received_at);
        """
    )
    conn.execute("begin immediate")
    try:
        outbound_columns = {
            str(row["name"]) for row in conn.execute("pragma table_info(outbound_status)").fetchall()
        }
        if "succeeded_at" not in outbound_columns:
            conn.execute("alter table outbound_status add column succeeded_at integer")
        outbound_additions = {
            "failure_count": "integer not null default 0",
            "next_attempt_at": "integer not null default 0",
            "peer_versions_json": "text not null default '{}'",
            "peer_membership_digest": "text not null default ''",
        }
        for name, definition in outbound_additions.items():
            if name not in outbound_columns:
                conn.execute(f"alter table outbound_status add column {name} {definition}")
        registered_columns = {
            str(row["name"]) for row in conn.execute("pragma table_info(registered_nodes)").fetchall()
        }
        if "membership_status" not in registered_columns:
            conn.execute(
                "alter table registered_nodes add column membership_status text not null default 'active'"
            )
        if "membership_version" not in registered_columns:
            conn.execute(
                "alter table registered_nodes add column membership_version integer not null default 0"
            )
        conn.execute(
            "update outbound_status set succeeded_at = attempted_at where succeeded_at is null and ok = 1"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise
    return conn


def _memory_percent() -> float | None:
    try:
        values = {}
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, value = line.split(":", 1)
            values[key] = int(value.strip().split()[0])
        total = values.get("MemTotal", 0)
        available = values.get("MemAvailable", 0)
        return round(100 * (total - available) / total, 1) if total else None
    except (OSError, ValueError):
        return None


def collect_local_status(config: dict[str, Any], now: int | None = None) -> dict[str, Any]:
    observed_at = int(now if now is not None else time.time())
    service_names = [str(item) for item in config.get("services", []) if item]
    states: dict[str, str] = {}
    if service_names:
        try:
            completed = subprocess.run(
                ["systemctl", "is-active", *service_names],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
            lines = completed.stdout.splitlines()
            states = {
                name: lines[index].strip() if index < len(lines) else "unknown"
                for index, name in enumerate(service_names)
            }
        except (OSError, subprocess.TimeoutExpired):
            states = {name: "unknown" for name in service_names}
    active = sum(1 for state in states.values() if state == "active")
    app_score = round(100 * active / len(states), 1) if states else None
    try:
        disk = shutil.disk_usage("/")
        disk_percent = round(100 * disk.used / disk.total, 1)
    except OSError:
        disk_percent = None
    try:
        load_average = [round(value, 2) for value in os.getloadavg()]
    except (AttributeError, OSError):
        load_average = []
    return {
        "node_id": str(config["node_id"]),
        "node_name": config["node_name"],
        "host": config["advertise_host"],
        "port": int(config["port"]),
        "observed_at": observed_at,
        "app_score": app_score,
        "services": states,
        "load_average": load_average,
        "memory_used_percent": _memory_percent(),
        "disk_used_percent": disk_percent,
        "agent_version": "2.0.0",
        "seen_by": [str(config["node_id"])],
    }


def node_descriptor(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "node_id": str(item.get("node_id") or item.get("id") or ""),
        "node_name": str(item.get("node_name") or item.get("name") or item.get("node_id") or ""),
        "host": str(item.get("host") or ""),
        "port": int(item.get("port") or 0),
        "membership_status": str(item.get("membership_status") or "active"),
        "membership_version": int(item.get("membership_version") or 0),
    }


def _meta_get(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("select value from local_meta where key = ?", (key,)).fetchone()
    return str(row["value"]) if row else default


def _meta_set(conn: sqlite3.Connection, key: str, value: str | int) -> None:
    conn.execute(
        """
        insert into local_meta(key, value) values (?, ?)
        on conflict(key) do update set value=excluded.value
        """,
        (key, str(value)),
    )


def _bounded_witnesses(values: set[str], required: tuple[str, ...]) -> list[str]:
    ordered: list[str] = []
    for item in (*required, *sorted(values)):
        value = str(item)
        if value and value not in ordered:
            ordered.append(value)
    return sorted(ordered[:MAX_WITNESSES])


def register_nodes(conn: sqlite3.Connection, nodes: list[dict[str, Any]], now: int) -> int:
    changed = 0
    for raw in nodes:
        item = node_descriptor(raw)
        if not item["node_id"] or not item["host"] or not item["port"]:
            continue
        if item["membership_status"] not in {"active", "retired"}:
            continue
        current = conn.execute(
            "select membership_status, membership_version from registered_nodes where node_id = ?",
            (item["node_id"],),
        ).fetchone()
        if current:
            current_version = int(current["membership_version"] or 0)
            if item["membership_version"] < current_version:
                continue
            if (
                item["membership_version"] == current_version
                and current["membership_status"] == "retired"
                and item["membership_status"] == "active"
            ):
                continue
        conn.execute(
            """
            insert into registered_nodes(
              node_id, node_name, host, port, registered_at, updated_at,
              membership_status, membership_version
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(node_id) do update set
              node_name=excluded.node_name,
              host=excluded.host,
              port=excluded.port,
              updated_at=excluded.updated_at,
              membership_status=excluded.membership_status,
              membership_version=excluded.membership_version
            """,
            (
                item["node_id"], item["node_name"], item["host"], item["port"], now, now,
                item["membership_status"], item["membership_version"],
            ),
        )
        changed += 1
    return changed


def initialize_state(conn: sqlite3.Connection, config: dict[str, Any], now: int) -> None:
    epoch = max(0, int(config.get("membership_epoch") or 0))
    self_node = {
        "node_id": str(config["node_id"]),
        "node_name": config["node_name"],
        "host": config["advertise_host"],
        "port": int(config["port"]),
        "membership_status": "active",
        "membership_version": epoch,
    }
    configured = [
        {
            **node_descriptor(item),
            "membership_status": "active",
            "membership_version": epoch,
        }
        for item in [self_node, *config.get("peers", [])]
    ]
    applied_epoch = int(_meta_get(conn, "membership_epoch", "0") or 0)
    if epoch > applied_epoch:
        active_ids = [item["node_id"] for item in configured if item["node_id"]]
        placeholders = ",".join("?" for _item in active_ids)
        if active_ids:
            conn.execute(
                f"""
                update registered_nodes
                set membership_status = 'retired', membership_version = ?, updated_at = ?
                where membership_status = 'active'
                  and membership_version <= ?
                  and node_id not in ({placeholders})
                """,
                (epoch, now, epoch, *active_ids),
            )
        register_nodes(conn, configured, now)
        _meta_set(conn, "membership_epoch", epoch)
    else:
        register_nodes(conn, configured, now)


def bump_incarnation(conn: sqlite3.Connection, now: int) -> int:
    previous = int(_meta_get(conn, "self_incarnation", "0") or 0)
    incarnation = max(int(now), previous + 1, 1)
    _meta_set(conn, "self_incarnation", incarnation)
    _meta_set(conn, "self_sequence", 0)
    return incarnation


def _next_self_version(conn: sqlite3.Connection, now: int) -> tuple[int, int]:
    incarnation = int(_meta_get(conn, "self_incarnation", "0") or 0)
    if incarnation <= 0:
        incarnation = max(int(now), 1)
        _meta_set(conn, "self_incarnation", incarnation)
    sequence = int(_meta_get(conn, "self_sequence", "0") or 0) + 1
    _meta_set(conn, "self_sequence", sequence)
    return incarnation, sequence


def _self_witnesses(
    conn: sqlite3.Connection,
    config: dict[str, Any],
    now: int,
    existing: set[str] | None = None,
) -> tuple[list[str], int]:
    self_id = str(config["node_id"])
    witnesses = set(existing or ())
    witnesses.add(self_id)
    witnesses.update(
        str(row["peer_id"])
        for row in conn.execute(
            "select peer_id from outbound_status where succeeded_at > ?",
            (now - HEARTBEAT_FRESH_SECONDS,),
        ).fetchall()
    )
    return _bounded_witnesses(witnesses, (self_id,)), len(witnesses)


def _write_self_heartbeat(
    conn: sqlite3.Connection,
    config: dict[str, Any],
    payload: dict[str, Any],
    now: int,
) -> dict[str, Any]:
    payload["node_id"] = str(config["node_id"])
    payload["node_name"] = config["node_name"]
    payload["host"] = config["advertise_host"]
    payload["port"] = int(config["port"])
    payload["membership_status"] = "active"
    payload["membership_version"] = int(config.get("membership_epoch") or 0)
    witnesses, seen_count = _self_witnesses(
        conn, config, now, {str(item) for item in payload.get("seen_by", []) if item}
    )
    payload["seen_by"] = witnesses
    payload["seen_count"] = max(int(payload.get("seen_count") or 0), seen_count)
    register_nodes(conn, [payload], now)
    conn.execute(
        """
        insert into heartbeat_records(node_id, observed_at, received_at, payload_json, seen_by_json)
        values (?, ?, ?, ?, ?)
        on conflict(node_id) do update set
          observed_at=excluded.observed_at,
          received_at=excluded.received_at,
          payload_json=excluded.payload_json,
          seen_by_json=excluded.seen_by_json
        """,
        (
            payload["node_id"],
            payload["observed_at"],
            now,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            json.dumps(payload["seen_by"]),
        ),
    )
    return payload


def store_self_heartbeat(
    conn: sqlite3.Connection,
    config: dict[str, Any],
    now: int,
    force: bool = False,
) -> dict[str, Any]:
    current = conn.execute(
        "select observed_at, payload_json from heartbeat_records where node_id = ?",
        (str(config["node_id"]),),
    ).fetchone()
    interval = max(
        1,
        int(config.get("local_status_interval_seconds") or DEFAULT_LOCAL_STATUS_INTERVAL_SECONDS),
    )
    incarnation = int(_meta_get(conn, "self_incarnation", "0") or 0)
    if current and not force and int(current["observed_at"]) > now - interval:
        cached = json.loads(current["payload_json"] or "{}")
        if int(cached.get("incarnation") or 0) == incarnation:
            return cached
    payload = collect_local_status(config, now)
    payload["incarnation"], payload["sequence"] = _next_self_version(conn, now)
    return _write_self_heartbeat(conn, config, payload, now)


def refresh_self_visibility(conn: sqlite3.Connection, config: dict[str, Any], now: int) -> None:
    row = conn.execute(
        "select payload_json from heartbeat_records where node_id = ?",
        (str(config["node_id"]),),
    ).fetchone()
    if not row:
        store_self_heartbeat(conn, config, now, force=True)
        return
    payload = json.loads(row["payload_json"] or "{}")
    previous = {str(item) for item in payload.get("seen_by", []) if item}
    witnesses, _seen_count = _self_witnesses(conn, config, now, previous)
    if witnesses == sorted(previous):
        return
    payload["observed_at"] = now
    payload["incarnation"], payload["sequence"] = _next_self_version(conn, now)
    _write_self_heartbeat(conn, config, payload, now)


def record_version(payload: dict[str, Any]) -> tuple[int, int, int]:
    return (
        int(payload.get("incarnation") or 0),
        int(payload.get("sequence") or 0),
        int(payload.get("observed_at") or 0),
    )


def _compare_record_versions(incoming: dict[str, Any], current: dict[str, Any]) -> int:
    incoming_version = record_version(incoming)
    current_version = record_version(current)
    if incoming_version[0] > 0 and current_version[0] > 0:
        left = incoming_version
        right = current_version
    else:
        left = (incoming_version[2],)
        right = (current_version[2],)
    return (left > right) - (left < right)


def _wire_record(payload: dict[str, Any], witnesses: set[str], required: tuple[str, ...]) -> dict[str, Any]:
    result = dict(payload)
    result["seen_count"] = max(int(result.get("seen_count") or 0), len(witnesses))
    result["seen_by"] = _bounded_witnesses(witnesses, required)
    return result


def merge_heartbeat_record(
    conn: sqlite3.Connection,
    config: dict[str, Any],
    raw: dict[str, Any],
    now: int,
    allow_stale: bool = False,
) -> bool:
    try:
        payload = dict(raw)
        node_id = str(payload["node_id"])
        observed_at = int(payload["observed_at"])
        descriptor = node_descriptor(payload)
    except (KeyError, TypeError, ValueError):
        return False
    if node_id == str(config["node_id"]):
        return False
    age = now - observed_at
    if age < -MAX_CLOCK_SKEW_SECONDS or (not allow_stale and age >= HEARTBEAT_FRESH_SECONDS):
        return False
    if not descriptor["host"] or not descriptor["port"]:
        return False
    seen_by = {str(item) for item in payload.get("seen_by", []) if item}
    seen_by.add(node_id)
    seen_by.add(str(config["node_id"]))
    register_nodes(conn, [descriptor], now)
    membership = conn.execute(
        "select membership_status from registered_nodes where node_id = ?",
        (node_id,),
    ).fetchone()
    if not membership or membership["membership_status"] != "active":
        return False
    current = conn.execute(
        "select observed_at, payload_json, seen_by_json from heartbeat_records where node_id = ?",
        (node_id,),
    ).fetchone()
    current_payload = json.loads(current["payload_json"] or "{}") if current else {}
    comparison = _compare_record_versions(payload, current_payload) if current else 1
    witness_changed = False
    if comparison < 0:
        return False
    if comparison == 0:
        current_seen = {str(item) for item in json.loads(current["seen_by_json"] or "[]")}
        witness_changed = not seen_by.issubset(current_seen)
        seen_by.update(current_seen)
        payload["seen_count"] = max(
            int(payload.get("seen_count") or 0),
            int(current_payload.get("seen_count") or 0),
        )
        if len(current_payload) > len(payload):
            payload = current_payload
    payload = _wire_record(payload, seen_by, (node_id, str(config["node_id"])))
    observed_at = int(payload["observed_at"])
    conn.execute(
        """
        insert into heartbeat_records(node_id, observed_at, received_at, payload_json, seen_by_json)
        values (?, ?, ?, ?, ?)
        on conflict(node_id) do update set
          observed_at=excluded.observed_at,
          received_at=excluded.received_at,
          payload_json=excluded.payload_json,
          seen_by_json=excluded.seen_by_json
        """,
        (
            node_id,
            observed_at,
            now,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            json.dumps(payload["seen_by"]),
        ),
    )
    return comparison > 0 or witness_changed


def registry_snapshot(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            select node_id, node_name, host, port, registered_at, updated_at,
                   membership_status, membership_version
            from registered_nodes
            order by node_id
            """
        ).fetchall()
    ]


def fresh_records(conn: sqlite3.Connection, now: int) -> list[dict[str, Any]]:
    records = []
    rows = conn.execute(
        """
        select h.payload_json, h.seen_by_json
        from heartbeat_records h
        join registered_nodes n on n.node_id = h.node_id
        where n.membership_status = 'active'
          and h.observed_at > ? and h.observed_at <= ?
        order by h.node_id
        """,
        (now - HEARTBEAT_FRESH_SECONDS, now + MAX_CLOCK_SKEW_SECONDS),
    ).fetchall()
    for row in rows:
        payload = json.loads(row["payload_json"] or "{}")
        payload["seen_by"] = sorted({str(item) for item in json.loads(row["seen_by_json"] or "[]") if item})
        records.append(payload)
    return records


def latest_records(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    records = []
    rows = conn.execute(
        """
        select h.payload_json, h.seen_by_json
        from heartbeat_records h
        join registered_nodes n on n.node_id = h.node_id
        where n.membership_status = 'active'
        order by h.node_id
        """
    ).fetchall()
    for row in rows:
        payload = json.loads(row["payload_json"] or "{}")
        payload["seen_by"] = sorted(
            {str(item) for item in json.loads(row["seen_by_json"] or "[]") if item}
        )
        records.append(payload)
    return records


def normalize_versions(raw: Any) -> dict[str, tuple[int, int, int]]:
    versions: dict[str, tuple[int, int, int]] = {}
    if not isinstance(raw, dict):
        return versions
    for node_id, value in raw.items():
        try:
            if isinstance(value, dict):
                version = (
                    int(value.get("incarnation") or 0),
                    int(value.get("sequence") or 0),
                    int(value.get("observed_at") or 0),
                )
            elif isinstance(value, (list, tuple)) and len(value) >= 3:
                version = (int(value[0]), int(value[1]), int(value[2]))
            else:
                continue
        except (TypeError, ValueError):
            continue
        versions[str(node_id)] = version
    return versions


def state_versions(conn: sqlite3.Connection) -> dict[str, list[int]]:
    return {record["node_id"]: list(record_version(record)) for record in latest_records(conn)}


def _record_is_newer(record: dict[str, Any], known: tuple[int, int, int] | None) -> bool:
    if known is None:
        return True
    version = record_version(record)
    if version[0] > 0 and known[0] > 0:
        return version > known
    return version[2] > known[2]


def delta_records(
    conn: sqlite3.Connection,
    known_versions: Any,
    self_id: str = "",
    limit: int = MAX_SYNC_RECORDS,
    include_all: bool = False,
) -> list[dict[str, Any]]:
    known = normalize_versions(known_versions)
    records = latest_records(conn)
    selected = records if include_all else [
        record for record in records if _record_is_newer(record, known.get(str(record["node_id"])))
    ]
    selected.sort(key=lambda item: (-int(item.get("observed_at") or 0), str(item.get("node_id"))))
    if self_id:
        own = next((item for item in records if str(item.get("node_id")) == self_id), None)
        selected = [item for item in selected if str(item.get("node_id")) != self_id]
        if own:
            selected.insert(0, own)
    return selected if include_all else selected[: max(1, int(limit))]


def membership_digest(conn: sqlite3.Connection) -> str:
    canonical = [
        {
            "node_id": item["node_id"],
            "node_name": item["node_name"],
            "host": item["host"],
            "port": item["port"],
            "membership_status": item["membership_status"],
            "membership_version": item["membership_version"],
        }
        for item in registry_snapshot(conn)
    ]
    body = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:24]


def merge_sync_payload(
    conn: sqlite3.Connection,
    config: dict[str, Any],
    payload: dict[str, Any],
    now: int,
) -> int:
    register_nodes(conn, list(payload.get("registry") or []), now)
    v2_records = int(payload.get("protocol_version") or 1) >= PROTOCOL_VERSION
    candidates = [(record, v2_records) for record in payload.get("records") or []]
    if isinstance(payload.get("self"), dict):
        candidates.append((payload["self"], False))
    for received in payload.get("received") or []:
        if isinstance(received, dict) and isinstance(received.get("payload"), dict):
            candidates.append((received["payload"], False))
    candidates.extend(
        (record, True)
        for record in payload.get("latest_records") or []
        if isinstance(record, dict)
    )
    merged = 0
    seen_ids: set[tuple[str, int, int, int]] = set()
    for record, allow_stale in candidates:
        if not isinstance(record, dict):
            continue
        try:
            key = (str(record["node_id"]), *record_version(record))
        except (KeyError, TypeError, ValueError):
            continue
        if key in seen_ids:
            continue
        if merge_heartbeat_record(conn, config, record, now, allow_stale=allow_stale):
            seen_ids.add(key)
            merged += 1
    conn.commit()
    return merged


def build_report(config: dict[str, Any], now: int | None = None) -> dict[str, Any]:
    current = int(now if now is not None else time.time())
    conn = connect_state(config["state_path"])
    try:
        initialize_state(conn, config, current)
        self_payload = store_self_heartbeat(conn, config, current)
        records = fresh_records(conn, current)
        latest = latest_records(conn)
        received = [
            {
                "node_id": record["node_id"],
                "node_name": record.get("node_name", record["node_id"]),
                "observed_at": record["observed_at"],
                "received_at": current,
                "payload": record,
            }
            for record in records
            if str(record.get("node_id")) != str(config["node_id"])
        ]
        outbound = [dict(row) for row in conn.execute("select * from outbound_status order by peer_id")]
        registry = registry_snapshot(conn)
        conn.commit()
    finally:
        conn.close()
    return {
        "node": node_descriptor(self_payload),
        "generated_at": current,
        "freshness_seconds": HEARTBEAT_FRESH_SECONDS,
        "self": self_payload,
        "registry": registry,
        "records": records,
        "latest_records": latest,
        "received": received,
        "outbound": outbound,
    }


def build_compact_report(
    config: dict[str, Any],
    now: int | None = None,
    known_versions: Any = None,
    known_membership_digest: str = "",
    include_all: bool = False,
) -> dict[str, Any]:
    current = int(now if now is not None else time.time())
    conn = connect_state(config["state_path"])
    try:
        initialize_state(conn, config, current)
        self_payload = store_self_heartbeat(conn, config, current)
        digest = membership_digest(conn)
        response = {
            "protocol_version": PROTOCOL_VERSION,
            "node": node_descriptor(self_payload),
            "generated_at": current,
            "freshness_seconds": HEARTBEAT_FRESH_SECONDS,
            "records": delta_records(
                conn,
                known_versions,
                self_id=str(config["node_id"]),
                include_all=include_all,
            ),
            "membership_digest": digest,
        }
        if not include_all:
            response["known_versions"] = state_versions(conn)
        if digest != str(known_membership_digest or ""):
            response["registry"] = registry_snapshot(conn)
        conn.commit()
        return response
    finally:
        conn.close()


def _known_peers(conn: sqlite3.Connection, config: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            select n.node_id, n.node_name, n.host, n.port,
                   o.attempted_at, o.succeeded_at, o.ok,
                   coalesce(o.failure_count, 0) as failure_count,
                   coalesce(o.next_attempt_at, 0) as next_attempt_at,
                   coalesce(o.peer_versions_json, '{}') as peer_versions_json,
                   coalesce(o.peer_membership_digest, '') as peer_membership_digest
            from registered_nodes n
            left join outbound_status o on o.peer_id = n.node_id
            where n.node_id != ? and n.membership_status = 'active'
            order by coalesce(o.attempted_at, 0), n.node_id
            """,
            (str(config["node_id"]),),
        ).fetchall()
    ]


def _stable_peer_ids(config: dict[str, Any], peers: list[dict[str, Any]]) -> set[str]:
    self_id = str(config["node_id"])
    count = min(
        len(peers),
        max(0, int(config.get("stable_fanout") or DEFAULT_STABLE_FANOUT)),
    )
    ranked = sorted(peers, key=lambda item: hashlib.sha256(
        f"{self_id}:{item['node_id']}".encode("utf-8")
    ).digest())
    return {str(item["node_id"]) for item in ranked[:count]}


def select_report_targets(
    conn: sqlite3.Connection,
    config: dict[str, Any],
    startup: bool,
    now: int,
    rng: random.Random | None = None,
) -> list[dict[str, Any]]:
    initialize_state(conn, config, now)
    peers = _known_peers(conn, config)
    picker = rng or random.SystemRandom()
    if not peers:
        return []
    registration_required = not any(
        peer.get("succeeded_at") is not None
        and int(peer["succeeded_at"]) > now - HEARTBEAT_FRESH_SECONDS
        for peer in peers
    )
    eligible = [peer for peer in peers if int(peer.get("next_attempt_at") or 0) <= now]
    if startup or registration_required:
        picker.shuffle(eligible)
        max_attempts = max(
            1,
            int(config.get("registration_max_attempts") or DEFAULT_REGISTRATION_ATTEMPTS),
        )
        return eligible[:max_attempts]
    due = [
        peer
        for peer in eligible
        if peer.get("attempted_at") is None or int(peer["attempted_at"]) <= now - REPORT_INTERVAL_SECONDS
    ]
    if not due:
        return []
    fanout = max(1, int(config.get("fanout") or DEFAULT_FANOUT))
    stable_ids = _stable_peer_ids(config, peers)
    stable_due = [peer for peer in due if str(peer["node_id"]) in stable_ids]
    stable_due.sort(key=lambda item: (int(item.get("attempted_at") or 0), str(item["node_id"])))
    selected = stable_due[: min(fanout, len(stable_ids))]
    remaining = [peer for peer in due if peer not in selected]
    picker.shuffle(remaining)
    random_slots = max(0, fanout - len(selected))
    selected.extend(remaining[:random_slots])
    return selected


def prepare_report(config: dict[str, Any], target: dict[str, Any], now: int) -> dict[str, Any]:
    conn = connect_state(config["state_path"])
    try:
        initialize_state(conn, config, now)
        store_self_heartbeat(conn, config, now)
        self_id = str(config["node_id"])
        target_id = str(target["node_id"])
        status = conn.execute(
            """
            select peer_versions_json, peer_membership_digest
            from outbound_status where peer_id = ?
            """,
            (target_id,),
        ).fetchone()
        peer_versions = json.loads(status["peer_versions_json"] or "{}") if status else {}
        peer_digest = str(status["peer_membership_digest"] or "") if status else ""
        digest = membership_digest(conn)
        payload = {
            "protocol_version": PROTOCOL_VERSION,
            "report_id": uuid.uuid4().hex,
            "sender_id": self_id,
            "sent_at": now,
            "records": delta_records(conn, peer_versions, self_id=self_id),
            "known_versions": state_versions(conn),
            "membership_digest": digest,
        }
        if digest != peer_digest:
            payload["registry"] = registry_snapshot(conn)
        conn.commit()
        return payload
    finally:
        conn.close()


def _verify_response(
    response,
    body: bytes,
    config: dict[str, Any],
    target: dict[str, Any],
    path: str,
) -> None:
    node_id = response.headers.get("X-Heartbeat-Node", "")
    timestamp = response.headers.get("X-Heartbeat-Timestamp", "")
    signature = response.headers.get("X-Heartbeat-Signature", "")
    if node_id != str(target["node_id"]):
        raise RuntimeError("response node id mismatch")
    try:
        if abs(int(time.time()) - int(timestamp)) > MAX_CLOCK_SKEW_SECONDS:
            raise RuntimeError("stale response")
    except ValueError as exc:
        raise RuntimeError("invalid response timestamp") from exc
    expected = request_signature(config["shared_secret"], "RESPONSE", path, timestamp, body)
    if not hmac.compare_digest(signature, expected):
        raise RuntimeError("invalid response signature")


def post_report(
    config: dict[str, Any],
    target: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    path = "/v1/heartbeat"
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_BODY_BYTES:
        raise ValueError("heartbeat request exceeds size limit")
    headers = signed_headers(
        config["shared_secret"], str(config["node_id"]), "POST", path, body
    )
    headers["X-Heartbeat-Protocol"] = str(PROTOCOL_VERSION)
    request = urllib.request.Request(
        f"http://{target['host']}:{int(target['port'])}{path}",
        data=body,
        method="POST",
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=float(config.get("request_timeout", 4))) as response:
        response_body = response.read(MAX_BODY_BYTES + 1)
        if len(response_body) > MAX_BODY_BYTES:
            raise RuntimeError("heartbeat response exceeds size limit")
        _verify_response(response, response_body, config, target, path)
        if response.status != 200:
            raise RuntimeError(f"heartbeat response status {response.status}")
    return json.loads(response_body.decode("utf-8"))


def send_once(
    config: dict[str, Any],
    startup: bool = False,
    now: int | None = None,
    sender: Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], dict[str, Any]] = post_report,
    rng: random.Random | None = None,
) -> list[dict[str, Any]]:
    current = int(now if now is not None else time.time())
    conn = connect_state(config["state_path"])
    try:
        initialize_state(conn, config, current)
        registration_required = startup or not conn.execute(
            """
            select 1
            from outbound_status o
            join registered_nodes n on n.node_id = o.peer_id
            where n.membership_status = 'active' and o.succeeded_at > ?
            limit 1
            """,
            (current - HEARTBEAT_FRESH_SECONDS,),
        ).fetchone()
        targets = select_report_targets(conn, config, startup, current, rng)
        if targets:
            store_self_heartbeat(conn, config, current, force=True)
        conn.commit()
    finally:
        conn.close()

    results = []
    run_started = time.perf_counter()
    time_budget = max(
        1.0,
        float(config.get("report_time_budget_seconds") or DEFAULT_REPORT_TIME_BUDGET_SECONDS),
    )
    for target in targets:
        if time.perf_counter() - run_started >= time_budget:
            break
        started = time.perf_counter()
        error = ""
        ok = False
        merged = 0
        response: dict[str, Any] = {}
        try:
            response = sender(config, target, prepare_report(config, target, current))
            conn = connect_state(config["state_path"])
            try:
                merged = merge_sync_payload(conn, config, response, current)
            finally:
                conn.close()
            ok = bool(response.get("ok", True))
        except (OSError, urllib.error.URLError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            error = str(exc)[:240] or exc.__class__.__name__
        latency_ms = int((time.perf_counter() - started) * 1000)
        conn = connect_state(config["state_path"])
        try:
            previous = conn.execute(
                """
                select failure_count, peer_versions_json, peer_membership_digest
                from outbound_status where peer_id = ?
                """,
                (str(target["node_id"]),),
            ).fetchone()
            previous_failures = int(previous["failure_count"] or 0) if previous else 0
            failure_count = 0 if ok else previous_failures + 1
            if ok:
                next_attempt_at = current + REPORT_INTERVAL_SECONDS
            else:
                backoff = min(REPORT_INTERVAL_SECONDS, 5 * (2 ** min(failure_count - 1, 6)))
                next_attempt_at = current + backoff
            response_versions = response.get("known_versions")
            peer_versions_json = (
                json.dumps(response_versions, separators=(",", ":"))
                if isinstance(response_versions, dict)
                else str(previous["peer_versions_json"] or "{}") if previous else "{}"
            )
            peer_digest = str(
                response.get("membership_digest")
                or (previous["peer_membership_digest"] if previous else "")
            )
            conn.execute(
                """
                insert into outbound_status(
                  peer_id, peer_name, attempted_at, succeeded_at, ok, latency_ms, error,
                  failure_count, next_attempt_at, peer_versions_json, peer_membership_digest
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(peer_id) do update set
                  peer_name=excluded.peer_name,
                  attempted_at=excluded.attempted_at,
                  succeeded_at=coalesce(excluded.succeeded_at, outbound_status.succeeded_at),
                  ok=excluded.ok,
                  latency_ms=excluded.latency_ms,
                  error=excluded.error,
                  failure_count=excluded.failure_count,
                  next_attempt_at=excluded.next_attempt_at,
                  peer_versions_json=excluded.peer_versions_json,
                  peer_membership_digest=excluded.peer_membership_digest
                """,
                (
                    str(target["node_id"]),
                    target["node_name"],
                    current,
                    current if ok else None,
                    1 if ok else 0,
                    latency_ms,
                    error,
                    failure_count,
                    next_attempt_at,
                    peer_versions_json,
                    peer_digest,
                ),
            )
            if ok:
                refresh_self_visibility(conn, config, current)
            conn.commit()
        finally:
            conn.close()
        results.append(
            {
                "peer_id": str(target["node_id"]),
                "ok": ok,
                "latency_ms": latency_ms,
                "merged_records": merged,
                "error": error,
            }
        )
        if registration_required and ok:
            break
    return results


class HeartbeatHandler(BaseHTTPRequestHandler):
    server_version = "ServerDeskHeartbeat/2.0"

    @property
    def config(self) -> dict[str, Any]:
        return self.server.config  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) > MAX_BODY_BYTES:
            status = 507
            body = b'{"ok":false,"error":"response exceeds size limit"}'
        timestamp = str(int(time.time()))
        signature = request_signature(self.config["shared_secret"], "RESPONSE", self.path, timestamp, body)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Heartbeat-Node", str(self.config["node_id"]))
        self.send_header("X-Heartbeat-Timestamp", timestamp)
        self.send_header("X-Heartbeat-Signature", signature)
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError("invalid body length")
        return self.rfile.read(length)

    def _authorized(self, body: bytes) -> tuple[bool, str]:
        node_id = self.headers.get("X-Heartbeat-Node", "")
        timestamp = self.headers.get("X-Heartbeat-Timestamp", "")
        signature = self.headers.get("X-Heartbeat-Signature", "")
        if not node_id:
            return False, node_id
        try:
            if abs(int(time.time()) - int(timestamp)) > MAX_CLOCK_SKEW_SECONDS:
                return False, node_id
        except ValueError:
            return False, node_id
        expected = request_signature(self.config["shared_secret"], self.command, self.path, timestamp, body)
        return hmac.compare_digest(signature, expected), node_id

    def do_POST(self) -> None:
        if self.path != "/v1/heartbeat":
            self._json(404, {"ok": False, "error": "not found"})
            return
        try:
            body = self._body()
        except ValueError as exc:
            self._json(400, {"ok": False, "error": str(exc)})
            return
        authorized, caller = self._authorized(body)
        if not authorized:
            self._json(401, {"ok": False, "error": "invalid signature"})
            return
        try:
            payload = json.loads(body.decode("utf-8"))
            protocol_version = int(payload.get("protocol_version") or 1)
            report_id = str(payload["report_id"])
            if str(payload.get("sender_id")) != caller:
                raise ValueError("sender id mismatch")
            if abs(int(time.time()) - int(payload["sent_at"])) > MAX_CLOCK_SKEW_SECONDS:
                raise ValueError("stale report")
            sender_records = [
                record
                for record in payload.get("records") or []
                if isinstance(record, dict) and str(record.get("node_id")) == caller
            ]
            if not sender_records:
                raise ValueError("sender heartbeat missing")
            sender_record = max(sender_records, key=record_version)
            sender_age = int(time.time()) - int(sender_record.get("observed_at") or 0)
            if sender_age < -MAX_CLOCK_SKEW_SECONDS or sender_age >= HEARTBEAT_FRESH_SECONDS:
                raise ValueError("sender heartbeat is not fresh")
            conn = connect_state(self.config["state_path"])
            try:
                duplicate = conn.execute(
                    "select 1 from seen_reports where report_id = ?",
                    (report_id,),
                ).fetchone()
                if not duplicate:
                    merge_sync_payload(conn, self.config, payload, int(time.time()))
                    conn.execute(
                        "insert into seen_reports(report_id, sender_id, received_at) values (?, ?, ?)",
                        (report_id, caller, int(time.time())),
                    )
                accepted = conn.execute(
                    """
                    select h.payload_json
                    from heartbeat_records h
                    join registered_nodes n on n.node_id = h.node_id
                    where h.node_id = ? and n.membership_status = 'active'
                    """,
                    (caller,),
                ).fetchone()
                if not accepted:
                    raise ValueError("sender heartbeat was not accepted")
                accepted_payload = json.loads(accepted["payload_json"] or "{}")
                if _compare_record_versions(accepted_payload, sender_record) < 0:
                    raise ValueError("sender heartbeat was not accepted")
                conn.execute(
                    "delete from seen_reports where received_at < ?",
                    (int(time.time()) - SEEN_REPORT_RETENTION_SECONDS,),
                )
                conn.commit()
            finally:
                conn.close()
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"ok": False, "error": str(exc)[:160]})
            return
        if protocol_version >= PROTOCOL_VERSION:
            response = build_compact_report(
                self.config,
                known_versions=payload.get("known_versions"),
                known_membership_digest=str(payload.get("membership_digest") or ""),
            )
        else:
            response = build_report(self.config)
        response["ok"] = True
        response["duplicate"] = bool(duplicate)
        self._json(200, response)

    def do_GET(self) -> None:
        if self.path != "/v1/report":
            self._json(404, {"ok": False, "error": "not found"})
            return
        authorized, _caller = self._authorized(b"")
        if not authorized:
            self._json(401, {"ok": False, "error": "invalid signature"})
            return
        try:
            protocol_version = int(self.headers.get("X-Heartbeat-Protocol") or 1)
        except ValueError:
            protocol_version = 1
        report = (
            build_compact_report(self.config, include_all=True)
            if protocol_version >= PROTOCOL_VERSION
            else build_report(self.config)
        )
        self._json(200, report)


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    request_queue_size = 128
    daemon_threads = True
    block_on_close = False

    def __init__(self, server_address, handler_class, max_workers: int = DEFAULT_MAX_WORKERS):
        self._worker_slots = threading.BoundedSemaphore(max(1, int(max_workers)))
        super().__init__(server_address, handler_class)

    def process_request(self, request, client_address) -> None:
        if not self._worker_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._worker_slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._worker_slots.release()


def serve(config: dict[str, Any]) -> None:
    conn = connect_state(config["state_path"])
    try:
        current = int(time.time())
        initialize_state(conn, config, current)
        bump_incarnation(conn, current)
        store_self_heartbeat(conn, config, current, force=True)
        conn.commit()
    finally:
        conn.close()
    server = BoundedThreadingHTTPServer(
        (config["bind_host"], int(config["port"])),
        HeartbeatHandler,
        max_workers=int(config.get("max_workers") or DEFAULT_MAX_WORKERS),
    )
    server.config = config  # type: ignore[attr-defined]
    server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description="Server Desk decentralized heartbeat agent")
    parser.add_argument("command", choices=("serve", "send-once", "report"))
    parser.add_argument("--config", default="/etc/server-desk-heartbeat/config.json")
    parser.add_argument("--startup", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config)
    if args.command == "serve":
        serve(config)
        return 0
    if args.command == "send-once":
        results = send_once(config, startup=args.startup)
        print(json.dumps(results, ensure_ascii=False))
        return 0
    print(json.dumps(build_report(config), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
