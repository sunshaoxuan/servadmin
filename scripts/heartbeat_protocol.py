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
SEEN_REPORT_RETENTION_SECONDS = 24 * 60 * 60


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
          updated_at integer not null
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
          ok integer not null,
          latency_ms integer,
          error text
        );
        create table if not exists seen_reports (
          report_id text primary key,
          sender_id text not null,
          received_at integer not null
        );
        """
    )
    conn.commit()
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
        "agent_version": "1.0.0",
        "seen_by": [str(config["node_id"])],
    }


def node_descriptor(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "node_id": str(item.get("node_id") or item.get("id") or ""),
        "node_name": str(item.get("node_name") or item.get("name") or item.get("node_id") or ""),
        "host": str(item.get("host") or ""),
        "port": int(item.get("port") or 0),
    }


def register_nodes(conn: sqlite3.Connection, nodes: list[dict[str, Any]], now: int) -> None:
    for raw in nodes:
        item = node_descriptor(raw)
        if not item["node_id"] or not item["host"] or not item["port"]:
            continue
        conn.execute(
            """
            insert into registered_nodes(node_id, node_name, host, port, registered_at, updated_at)
            values (?, ?, ?, ?, ?, ?)
            on conflict(node_id) do update set
              node_name=excluded.node_name,
              host=excluded.host,
              port=excluded.port,
              updated_at=excluded.updated_at
            """,
            (item["node_id"], item["node_name"], item["host"], item["port"], now, now),
        )


def initialize_state(conn: sqlite3.Connection, config: dict[str, Any], now: int) -> None:
    self_node = {
        "node_id": str(config["node_id"]),
        "node_name": config["node_name"],
        "host": config["advertise_host"],
        "port": int(config["port"]),
    }
    register_nodes(conn, [self_node, *config.get("peers", [])], now)


def store_self_heartbeat(conn: sqlite3.Connection, config: dict[str, Any], now: int) -> dict[str, Any]:
    payload = collect_local_status(config, now)
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


def merge_heartbeat_record(
    conn: sqlite3.Connection,
    config: dict[str, Any],
    raw: dict[str, Any],
    now: int,
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
    if age < -MAX_CLOCK_SKEW_SECONDS or age >= HEARTBEAT_FRESH_SECONDS:
        return False
    if not descriptor["host"] or not descriptor["port"]:
        return False
    seen_by = {str(item) for item in payload.get("seen_by", []) if item}
    seen_by.add(node_id)
    seen_by.add(str(config["node_id"]))
    payload["seen_by"] = sorted(seen_by)
    register_nodes(conn, [descriptor], now)
    current = conn.execute(
        "select observed_at, payload_json, seen_by_json from heartbeat_records where node_id = ?",
        (node_id,),
    ).fetchone()
    if current and int(current["observed_at"]) > observed_at:
        return False
    if current and int(current["observed_at"]) == observed_at:
        current_seen = {str(item) for item in json.loads(current["seen_by_json"] or "[]")}
        seen_by.update(current_seen)
        current_payload = json.loads(current["payload_json"] or "{}")
        if len(current_payload) > len(payload):
            payload = current_payload
        payload["seen_by"] = sorted(seen_by)
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
            json.dumps(sorted(seen_by)),
        ),
    )
    return True


def registry_snapshot(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            "select node_id, node_name, host, port, registered_at, updated_at from registered_nodes order by node_id"
        ).fetchall()
    ]


def fresh_records(conn: sqlite3.Connection, now: int) -> list[dict[str, Any]]:
    records = []
    rows = conn.execute(
        """
        select payload_json, seen_by_json
        from heartbeat_records
        where observed_at > ? and observed_at <= ?
        order by node_id
        """,
        (now - HEARTBEAT_FRESH_SECONDS, now + MAX_CLOCK_SKEW_SECONDS),
    ).fetchall()
    for row in rows:
        payload = json.loads(row["payload_json"] or "{}")
        payload["seen_by"] = sorted({str(item) for item in json.loads(row["seen_by_json"] or "[]") if item})
        records.append(payload)
    return records


def merge_sync_payload(
    conn: sqlite3.Connection,
    config: dict[str, Any],
    payload: dict[str, Any],
    now: int,
) -> int:
    register_nodes(conn, list(payload.get("registry") or []), now)
    candidates = list(payload.get("records") or [])
    if isinstance(payload.get("self"), dict):
        candidates.append(payload["self"])
    for received in payload.get("received") or []:
        if isinstance(received, dict) and isinstance(received.get("payload"), dict):
            candidates.append(received["payload"])
    merged = 0
    seen_ids: set[tuple[str, int]] = set()
    for record in candidates:
        if not isinstance(record, dict):
            continue
        try:
            key = (str(record["node_id"]), int(record["observed_at"]))
        except (KeyError, TypeError, ValueError):
            continue
        if key in seen_ids:
            continue
        seen_ids.add(key)
        merged += 1 if merge_heartbeat_record(conn, config, record, now) else 0
    conn.commit()
    return merged


def build_report(config: dict[str, Any], now: int | None = None) -> dict[str, Any]:
    current = int(now if now is not None else time.time())
    conn = connect_state(config["state_path"])
    try:
        initialize_state(conn, config, current)
        self_payload = store_self_heartbeat(conn, config, current)
        records = fresh_records(conn, current)
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
        "received": received,
        "outbound": outbound,
    }


def _known_peers(conn: sqlite3.Connection, config: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            select n.node_id, n.node_name, n.host, n.port, o.attempted_at, o.ok
            from registered_nodes n
            left join outbound_status o on o.peer_id = n.node_id
            where n.node_id != ?
            order by coalesce(o.attempted_at, 0), n.node_id
            """,
            (str(config["node_id"]),),
        ).fetchall()
    ]


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
    if startup:
        return [picker.choice(peers)] if peers else []
    due = [
        peer
        for peer in peers
        if peer.get("attempted_at") is None or int(peer["attempted_at"]) <= now - REPORT_INTERVAL_SECONDS
    ]
    picker.shuffle(due)
    return due


def prepare_report(config: dict[str, Any], target: dict[str, Any], now: int) -> dict[str, Any]:
    conn = connect_state(config["state_path"])
    try:
        initialize_state(conn, config, now)
        store_self_heartbeat(conn, config, now)
        target_id = str(target["node_id"])
        records = [record for record in fresh_records(conn, now) if target_id not in set(record.get("seen_by", []))]
        payload = {
            "protocol_version": 1,
            "report_id": uuid.uuid4().hex,
            "sender_id": str(config["node_id"]),
            "sent_at": now,
            "registry": registry_snapshot(conn),
            "records": records,
        }
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
    request = urllib.request.Request(
        f"http://{target['host']}:{int(target['port'])}{path}",
        data=body,
        method="POST",
        headers=signed_headers(config["shared_secret"], str(config["node_id"]), "POST", path, body),
    )
    with urllib.request.urlopen(request, timeout=float(config.get("request_timeout", 4))) as response:
        response_body = response.read(MAX_BODY_BYTES)
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
        targets = select_report_targets(conn, config, startup, current, rng)
        conn.commit()
    finally:
        conn.close()

    results = []
    for target in targets:
        started = time.perf_counter()
        error = ""
        ok = False
        merged = 0
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
            conn.execute(
                """
                insert into outbound_status(peer_id, peer_name, attempted_at, ok, latency_ms, error)
                values (?, ?, ?, ?, ?, ?)
                on conflict(peer_id) do update set
                  peer_name=excluded.peer_name,
                  attempted_at=excluded.attempted_at,
                  ok=excluded.ok,
                  latency_ms=excluded.latency_ms,
                  error=excluded.error
                """,
                (
                    str(target["node_id"]),
                    target["node_name"],
                    current,
                    1 if ok else 0,
                    latency_ms,
                    error,
                ),
            )
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
    return results


class HeartbeatHandler(BaseHTTPRequestHandler):
    server_version = "ServerDeskHeartbeat/1.0"

    @property
    def config(self) -> dict[str, Any]:
        return self.server.config  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
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
        self._json(200, build_report(self.config))


def serve(config: dict[str, Any]) -> None:
    conn = connect_state(config["state_path"])
    try:
        initialize_state(conn, config, int(time.time()))
        conn.commit()
    finally:
        conn.close()
    server = ThreadingHTTPServer((config["bind_host"], int(config["port"])), HeartbeatHandler)
    server.daemon_threads = True
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
