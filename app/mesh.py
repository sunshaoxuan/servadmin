from __future__ import annotations

import hashlib
import hmac
import json
import random
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Iterable

from .db import connect, init_db


DEFAULT_HEARTBEAT_PORT = 9108
DEFAULT_INTERVAL_SECONDS = 60
HEARTBEAT_FRESH_SECONDS = 300
HEARTBEAT_OFFLINE_SECONDS = 2 * HEARTBEAT_FRESH_SECONDS + DEFAULT_INTERVAL_SECONDS
SYNC_DELAY_SCORE = 70.0
MAX_CLOCK_SKEW_SECONDS = 650
SAMPLE_RETENTION_SECONDS = 7 * 24 * 60 * 60
MAX_REPORT_BYTES = 512 * 1024
REPORT_SAMPLE_SIZE = 3
REPORT_MAX_ATTEMPTS = 6


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


def fetch_peer_report(server: dict[str, Any], secret: str, timeout: float = 3.0) -> dict[str, Any]:
    host = server.get("ipv4") or server.get("hostname")
    port = int(server.get("heartbeat_port") or DEFAULT_HEARTBEAT_PORT)
    path = "/v1/report"
    headers = signed_headers(secret, "server-desk-main", "GET", path)
    headers["X-Heartbeat-Protocol"] = "2"
    request = urllib.request.Request(
        f"http://{host}:{port}{path}",
        method="GET",
        headers=headers,
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(MAX_REPORT_BYTES + 1)
            if len(body) > MAX_REPORT_BYTES:
                raise RuntimeError("heartbeat report exceeds size limit")
            response_node = response.headers.get("X-Heartbeat-Node", "")
            timestamp = response.headers.get("X-Heartbeat-Timestamp", "")
            signature = response.headers.get("X-Heartbeat-Signature", "")
        if response_node != str(server["id"]):
            raise RuntimeError("response node id mismatch")
        if abs(int(time.time()) - int(timestamp)) > MAX_CLOCK_SKEW_SECONDS:
            raise RuntimeError("stale response")
        expected = request_signature(secret, "RESPONSE", path, timestamp, body)
        if not hmac.compare_digest(signature, expected):
            raise RuntimeError("invalid response signature")
        payload = json.loads(body.decode("utf-8"))
        payload["_latency_ms"] = int((time.perf_counter() - started) * 1000)
        return payload
    except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(str(exc)[:240] or exc.__class__.__name__) from exc


def _report_payloads(report: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    if isinstance(report.get("self"), dict):
        candidates.append(report["self"])
    for key in ("records", "latest_records"):
        for record in report.get(key) or []:
            if isinstance(record, dict):
                candidates.append(record)
    for received in report.get("received") or []:
        if isinstance(received, dict) and isinstance(received.get("payload"), dict):
            candidates.append(received["payload"])
    latest: dict[str, dict[str, Any]] = {}
    for payload in candidates:
        node_id = str(payload.get("node_id") or "")
        if not node_id:
            continue
        current = latest.get(node_id)
        if current is None or int(payload.get("observed_at") or 0) >= int(current.get("observed_at") or 0):
            latest[node_id] = payload
    return list(latest.values())


def _latest_payload(
    target_id: str,
    reports: dict[str, dict[str, Any]],
) -> tuple[str, dict[str, Any] | None]:
    candidates = [
        (source_id, payload)
        for source_id, report in reports.items()
        for payload in _report_payloads(report)
        if str(payload.get("node_id")) == target_id
    ]
    if not candidates:
        return "", None
    def version_key(item: tuple[str, dict[str, Any]]) -> tuple[int, int, int, int]:
        payload = item[1]
        incarnation = int(payload.get("incarnation") or 0)
        observed_at = int(payload.get("observed_at") or 0)
        if incarnation > 0:
            return (1, incarnation, int(payload.get("sequence") or 0), observed_at)
        return (0, observed_at, 0, observed_at)

    return max(
        candidates,
        key=version_key,
    )


def record_mesh_cycle(
    conn,
    servers: Iterable[dict[str, Any]],
    reports: dict[str, dict[str, Any]],
    errors: dict[str, str] | None = None,
    sampled_at: int | None = None,
) -> list[dict[str, Any]]:
    now = int(sampled_at if sampled_at is not None else time.time())
    bucket = now - (now % DEFAULT_INTERVAL_SECONDS)
    server_rows = list(servers)
    expected_peers = max(0, len(server_rows) - 1)
    failures = errors or {}
    registry_ids = {
        str(item.get("node_id"))
        for report in reports.values()
        for item in report.get("registry") or []
        if isinstance(item, dict)
        and item.get("node_id") is not None
        and item.get("membership_status", "active") == "active"
    }
    report_source_ids = [int(item) for item in reports if item.isdigit()]
    recorded: list[dict[str, Any]] = []

    for server in server_rows:
        server_id = int(server["id"])
        target_id = str(server_id)
        source_id, payload = _latest_payload(target_id, reports)
        source_report = reports.get(source_id, {})
        observed_at = int(payload.get("observed_at") or 0) if payload else 0
        age = now - observed_at if observed_at else None
        timestamp_fresh = bool(
            payload and age is not None and -MAX_CLOCK_SKEW_SECONDS <= age < HEARTBEAT_FRESH_SECONDS
        )
        timestamp_active = bool(
            payload and age is not None and -MAX_CLOCK_SKEW_SECONDS <= age < HEARTBEAT_OFFLINE_SECONDS
        )
        seen_by = {str(item) for item in (payload or {}).get("seen_by", []) if item}
        seen_count = max(len(seen_by), int((payload or {}).get("seen_count") or 0))
        visible = min(expected_peers, max(len(seen_by - {target_id}), seen_count - 1))
        visibility_confirmed = expected_peers == 0 or visible > 0
        fresh = timestamp_fresh and visibility_confirmed
        active = timestamp_active and visibility_confirmed
        visibility_missing = timestamp_active and not visibility_confirmed
        sync_delayed = active and not fresh
        app_score = payload.get("app_score") if active and payload else None
        if app_score is not None:
            app_score = max(0.0, min(100.0, float(app_score)))
        network_score = 100.0 if fresh else SYNC_DELAY_SCORE if sync_delayed else 0.0
        details = {
            "source_report_server_id": int(source_id) if source_id.isdigit() else None,
            "source_report_server_ids": report_source_ids,
            "source_report_name": (source_report.get("node") or {}).get("node_name", ""),
            "observed_at": observed_at or None,
            "heartbeat_age_seconds": age,
            "heartbeat_fresh": fresh,
            "heartbeat_timestamp_fresh": timestamp_fresh,
            "external_visibility_confirmed": visibility_confirmed,
            "visibility_missing": visibility_missing,
            "sync_delayed": sync_delayed,
            "offline_after_seconds": HEARTBEAT_OFFLINE_SECONDS,
            "seen_by": sorted(seen_by),
            "registered": target_id in registry_ids,
            "report_errors": failures,
            "self": payload or {},
        }
        direct_latency = int(source_report.get("_latency_ms") or 0) if source_id == target_id else None
        values = (
            server_id,
            bucket,
            network_score,
            app_score,
            1 if active else 0,
            direct_latency,
            visible,
            expected_peers,
            json.dumps(details, ensure_ascii=False),
        )
        conn.execute(
            """
            insert into mesh_health_samples(
              server_id, sampled_at, network_score, app_score, direct_ok,
              direct_latency_ms, peer_visible, peer_expected, details_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(server_id, sampled_at) do update set
              network_score=excluded.network_score,
              app_score=excluded.app_score,
              direct_ok=excluded.direct_ok,
              direct_latency_ms=excluded.direct_latency_ms,
              peer_visible=excluded.peer_visible,
              peer_expected=excluded.peer_expected,
              details_json=excluded.details_json
            """,
            values,
        )
        recorded.append(
            {
                "server_id": server_id,
                "sampled_at": bucket,
                "network_score": network_score,
                "app_score": app_score,
                "direct_ok": active,
                "peer_visible": visible,
                "peer_expected": expected_peers,
                "source_report_server_id": details["source_report_server_id"],
            }
        )
    conn.execute("delete from mesh_health_samples where sampled_at < ?", (now - SAMPLE_RETENTION_SECONDS,))
    conn.commit()
    return recorded


def poll_mesh_once(
    db_path,
    secret: str,
    fetcher: Callable[[dict[str, Any], str], dict[str, Any]] = fetch_peer_report,
    sampled_at: int | None = None,
    rng: random.Random | None = None,
    sample_size: int = REPORT_SAMPLE_SIZE,
) -> list[dict[str, Any]]:
    conn = connect(db_path)
    try:
        init_db(conn)
        rows = conn.execute(
            "select id, name, hostname, ipv4, heartbeat_port from servers where heartbeat_enabled = 1 and is_retired = 0 order by id"
        ).fetchall()
        servers = [dict(row) for row in rows]
        candidates = list(servers)
        (rng or random.SystemRandom()).shuffle(candidates)
        reports: dict[str, dict[str, Any]] = {}
        errors: dict[str, str] = {}
        success_goal = min(len(candidates), max(1, int(sample_size)))
        attempt_limit = min(len(candidates), max(success_goal, REPORT_MAX_ATTEMPTS))
        for server in candidates[:attempt_limit]:
            key = str(server["id"])
            try:
                reports[key] = fetcher(server, secret)
                if len(reports) >= success_goal:
                    break
            except Exception as exc:
                errors[key] = str(exc)[:240] or exc.__class__.__name__
        return record_mesh_cycle(conn, servers, reports, errors, sampled_at)
    finally:
        conn.close()


def mesh_health_history(conn, hours: int = 3) -> dict[str, Any]:
    bounded_hours = max(1, min(24, int(hours)))
    cutoff = int(time.time()) - bounded_hours * 60 * 60
    servers = conn.execute(
        "select id, name from servers where heartbeat_enabled = 1 and is_retired = 0 order by is_starred desc, id"
    ).fetchall()
    result = []
    for server in servers:
        samples = []
        for row in conn.execute(
            """
            select sampled_at, network_score, app_score, direct_ok, direct_latency_ms,
                   peer_visible, peer_expected, details_json
            from mesh_health_samples
            where server_id = ? and sampled_at >= ?
            order by sampled_at
            """,
            (server["id"], cutoff),
        ).fetchall():
            sample = dict(row)
            sample["direct_ok"] = bool(sample["direct_ok"])
            sample["details"] = json.loads(sample.pop("details_json") or "{}")
            samples.append(sample)
        result.append(
            {
                "server_id": server["id"],
                "name": server["name"],
                "current": samples[-1] if samples else None,
                "samples": samples,
            }
        )
    return {
        "generated_at": int(time.time()),
        "window_hours": bounded_hours,
        "interval_seconds": DEFAULT_INTERVAL_SECONDS,
        "freshness_seconds": HEARTBEAT_FRESH_SECONDS,
        "offline_after_seconds": HEARTBEAT_OFFLINE_SECONDS,
        "servers": result,
    }
