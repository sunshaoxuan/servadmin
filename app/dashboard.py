from __future__ import annotations

import json
import time
from typing import Any

from .mesh import HEARTBEAT_FRESH_SECONDS, HEARTBEAT_OFFLINE_SECONDS


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _rate(current: dict[str, Any], previous: dict[str, Any], field: str) -> float | None:
    current_value = _number(current.get(field))
    previous_value = _number(previous.get(field))
    elapsed = _number(current.get("observed_at"))
    previous_at = _number(previous.get("observed_at"))
    if current_value is None or previous_value is None or elapsed is None or previous_at is None:
        return None
    seconds = elapsed - previous_at
    delta = current_value - previous_value
    if seconds <= 0 or delta < 0:
        return None
    return round(delta / seconds, 1)


def _cpu_percent(current: dict[str, Any], previous: dict[str, Any]) -> float | None:
    total = _number(current.get("cpu_total_jiffies"))
    previous_total = _number(previous.get("cpu_total_jiffies"))
    idle = _number(current.get("cpu_idle_jiffies"))
    previous_idle = _number(previous.get("cpu_idle_jiffies"))
    if None in (total, previous_total, idle, previous_idle):
        return None
    total_delta = total - previous_total
    idle_delta = idle - previous_idle
    if total_delta <= 0 or idle_delta < 0:
        return None
    return round(max(0.0, min(100.0, 100.0 * (1.0 - idle_delta / total_delta))), 1)


def _latest_samples(conn, server_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        select sampled_at, network_score, app_score, direct_ok, peer_visible,
               peer_expected, details_json
        from mesh_health_samples
        where server_id = ?
        order by sampled_at desc
        limit 12
        """,
        (server_id,),
    ).fetchall()
    newest_distinct = []
    seen_observations = set()
    for row in rows:
        sample = dict(row)
        sample["details"] = json.loads(sample.pop("details_json") or "{}")
        sample["self"] = sample["details"].get("self") or {}
        observed_at = sample["self"].get("observed_at")
        observation_key = ("observed", observed_at) if observed_at is not None else ("sampled", sample["sampled_at"])
        if observation_key in seen_observations:
            continue
        seen_observations.add(observation_key)
        newest_distinct.append(sample)
        if len(newest_distinct) == 2:
            break
    return list(reversed(newest_distinct))


def _latest_subscription(conn, server_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        select period_start, period_end, used_bytes, quota_bytes, source_label,
               source_url, collected_at
        from server_subscription_usage
        where server_id = ?
        order by collected_at desc, id desc
        limit 1
        """,
        (server_id,),
    ).fetchone()
    if not row:
        return None
    data = dict(row)
    quota = int(data["quota_bytes"] or 0)
    used = int(data["used_bytes"] or 0)
    data["used_percent"] = round(100.0 * used / quota, 1) if quota > 0 else None
    return data


def _server_card(conn, server: dict[str, Any], generated_at: int) -> dict[str, Any]:
    samples = _latest_samples(conn, int(server["id"]))
    previous = samples[-2] if len(samples) > 1 else None
    current = samples[-1] if samples else None
    current_self = current["self"] if current else {}
    previous_self = previous["self"] if previous else {}
    sampled_at = int(current["sampled_at"]) if current else None
    sample_age = generated_at - sampled_at if sampled_at else None
    heartbeat_enabled = bool(server.get("heartbeat_enabled"))

    if not heartbeat_enabled:
        state = server.get("last_status") or "unknown"
        state_detail = "未部署心跳 Agent"
    elif current is None:
        state = "pending"
        state_detail = "等待首个心跳样本"
    elif sample_age is not None and sample_age >= HEARTBEAT_OFFLINE_SECONDS:
        state = "offline"
        state_detail = f"样本已中断 {sample_age} 秒"
    elif sample_age is not None and sample_age >= HEARTBEAT_FRESH_SECONDS:
        state = "delayed"
        state_detail = f"样本延迟 {sample_age} 秒"
    elif current.get("direct_ok"):
        state = "online"
        state_detail = f"{current['peer_visible']}/{current['peer_expected']} 个同伴可见"
    else:
        state = "offline"
        state_detail = "心跳未被同伴确认"

    telemetry = {
        "sampled_at": sampled_at,
        "sample_age_seconds": sample_age,
        "load_1m": (current_self.get("load_average") or [None])[0],
        "cpu_used_percent": _cpu_percent(current_self, previous_self) if previous else None,
        "memory_used_percent": _number(current_self.get("memory_used_percent")),
        "disk_used_percent": _number(current_self.get("disk_used_percent")),
        "disk_total_bytes": current_self.get("disk_total_bytes"),
        "disk_free_bytes": current_self.get("disk_free_bytes"),
        "network_rx_bytes_per_second": _rate(current_self, previous_self, "network_rx_bytes") if previous else None,
        "network_tx_bytes_per_second": _rate(current_self, previous_self, "network_tx_bytes") if previous else None,
        "disk_read_bytes_per_second": _rate(current_self, previous_self, "disk_read_bytes") if previous else None,
        "disk_write_bytes_per_second": _rate(current_self, previous_self, "disk_write_bytes") if previous else None,
    }
    return {
        "id": server["id"],
        "name": server["name"],
        "hostname": server["hostname"],
        "ipv4": server.get("ipv4") or "",
        "provider": server.get("provider") or "未设置",
        "region": server.get("region") or "未设置",
        "is_starred": bool(server.get("is_starred")),
        "state": state,
        "state_detail": state_detail,
        "telemetry": telemetry,
        "subscription": _latest_subscription(conn, int(server["id"])),
    }


def dashboard_snapshot(conn) -> dict[str, Any]:
    generated_at = int(time.time())
    rows = conn.execute(
        """
        select id, name, hostname, ipv4, provider, region, is_starred,
               heartbeat_enabled, last_status
        from servers
        where is_retired = 0
        order by is_starred desc, name collate nocase
        """
    ).fetchall()
    cards = [_server_card(conn, dict(row), generated_at) for row in rows]
    online = sum(1 for item in cards if item["state"] == "online")
    attention = sum(1 for item in cards if item["state"] in {"offline", "delayed", "pending"})
    traffic_ready = sum(1 for item in cards if item["subscription"] is not None)
    return {
        "generated_at": generated_at,
        "summary": {
            "total": len(cards),
            "online": online,
            "attention": attention,
            "subscription_ready": traffic_ready,
        },
        "servers": cards,
    }
