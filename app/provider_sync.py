from __future__ import annotations

import re
import sqlite3
from calendar import monthrange
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from .db import connect
from .security import CredentialCipher


RIVEN_PORTAL_ORIGIN = "https://portal.sa.net"
RIVEN_CLOUD_ORIGIN = "https://cloud.sa.net"
ORANGE_PORTAL_ORIGIN = "https://portal.orangevps.com"
SUPPORTED_CONNECTORS = {"riven_cloud", "orangevps"}


class ProviderSyncError(RuntimeError):
    pass


def _required_match(pattern: str, value: str, label: str) -> str:
    match = re.search(pattern, value, flags=re.IGNORECASE)
    if not match:
        raise ProviderSyncError(f"{label} missing from provider response")
    return match.group(1)


def _validate_riven_access(access: sqlite3.Row, password: str) -> None:
    if not password:
        raise ProviderSyncError("provider password is not configured")
    if not re.fullmatch(r"\d+", access["service_reference"] or ""):
        raise ProviderSyncError("Riven Cloud service id is invalid")
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", access["external_server_id"] or ""):
        raise ProviderSyncError("Riven Cloud server id is invalid")
    portal_host = urlparse(access["portal_url"] or RIVEN_PORTAL_ORIGIN).hostname
    if portal_host != "portal.sa.net":
        raise ProviderSyncError("Riven Cloud portal host is invalid")


def _riven_usage(
    access: sqlite3.Row,
    password: str,
    client_factory: Callable[..., httpx.Client] = httpx.Client,
) -> dict[str, Any]:
    _validate_riven_access(access, password)
    service_id = access["service_reference"]
    external_server_id = access["external_server_id"]
    with client_factory(
        follow_redirects=True,
        timeout=25,
        headers={"User-Agent": "ServerDeskProviderSync/1.0"},
    ) as client:
        login_page = client.get(f"{RIVEN_PORTAL_ORIGIN}/clientarea.php?action=products")
        login_page.raise_for_status()
        token = _required_match(r'name="token"[^>]*value="([^"]+)"', login_page.text, "login token")
        login = client.post(
            f"{RIVEN_PORTAL_ORIGIN}/login",
            data={
                "token": token,
                "username": access["login_username"],
                "password": password,
                "rememberme": "on",
            },
        )
        login.raise_for_status()
        if "Logged in as:" not in login.text:
            raise ProviderSyncError("Riven Cloud login failed")
        sso = client.get(
            f"{RIVEN_PORTAL_ORIGIN}/modules/servers/VirtFusionDirect/client.php",
            params={"serviceID": service_id, "action": "loginAsServerOwner"},
        )
        sso.raise_for_status()
        sso_payload = sso.json()
        token_url = sso_payload.get("token_url") if sso_payload.get("success") else ""
        if not token_url or urlparse(token_url).hostname != "cloud.sa.net":
            raise ProviderSyncError("Riven Cloud single sign-on failed")
        authorized = client.get(token_url)
        authorized.raise_for_status()
        server_page = client.get(f"{RIVEN_CLOUD_ORIGIN}/server/{external_server_id}")
        server_page.raise_for_status()
        internal_id = _required_match(
            r'<client-server-manage[^>]*:id="([0-9]+)"[^>]*[^>]*uuid="' + re.escape(external_server_id) + r'"',
            server_page.text,
            "Riven Cloud internal server id",
        )
        traffic = client.get(f"{RIVEN_CLOUD_ORIGIN}/server/{internal_id}/resource/traffic.json")
        traffic.raise_for_status()
        traffic_payload = traffic.json()
        rows = traffic_payload.get("data", {}).get("data", {}).get("monthlyRaw", [])
        if not traffic_payload.get("success") or not rows:
            raise ProviderSyncError("Riven Cloud monthly traffic is unavailable")
        current_date = datetime.now(timezone.utc).date().isoformat()
        row = next(
            (
                item
                for item in rows
                if str(item.get("month_start", ""))[:10] <= current_date <= str(item.get("month_end", ""))[:10]
            ),
            max(rows, key=lambda item: str(item.get("month_end", ""))),
        )
        return {
            "period_start": str(row["month_start"])[:10],
            "period_end": str(row["month_end"])[:10],
            "rx_bytes": int(row.get("rx") or 0),
            "tx_bytes": int(row.get("tx") or 0),
            "used_bytes": int(row.get("total") or 0),
            "source_url": f"{RIVEN_CLOUD_ORIGIN}/server/{external_server_id}",
            "source_label": "Riven Cloud 自动同步",
            "created_by": "provider-sync:riven-cloud",
        }


def _validate_orange_access(access: sqlite3.Row, password: str) -> None:
    if not password:
        raise ProviderSyncError("provider password is not configured")
    if not re.fullmatch(r"\d+", access["service_reference"] or ""):
        raise ProviderSyncError("OrangeVPS service id is invalid")
    portal_host = urlparse(access["portal_url"] or ORANGE_PORTAL_ORIGIN).hostname
    if portal_host != "portal.orangevps.com":
        raise ProviderSyncError("OrangeVPS portal host is invalid")


def _orange_usage(
    access: sqlite3.Row,
    password: str,
    client_factory: Callable[..., httpx.Client] = httpx.Client,
) -> dict[str, Any]:
    _validate_orange_access(access, password)
    service_id = access["service_reference"]
    with client_factory(
        follow_redirects=True,
        timeout=25,
        headers={"User-Agent": "ServerDeskProviderSync/1.0"},
    ) as client:
        login_page = client.get(f"{ORANGE_PORTAL_ORIGIN}/login")
        login_page.raise_for_status()
        token = _required_match(r'name="token"[^>]*value="([^"]+)"', login_page.text, "login token")
        login = client.post(
            f"{ORANGE_PORTAL_ORIGIN}/login",
            data={
                "token": token,
                "username": access["login_username"],
                "password": password,
                "rememberme": "on",
            },
        )
        login.raise_for_status()
        if not re.search(r"Logged in as:|登入為:", login.text, flags=re.IGNORECASE):
            raise ProviderSyncError("OrangeVPS login failed")
        source_url = f"{ORANGE_PORTAL_ORIGIN}/clientarea.php?action=productdetails&id={service_id}"
        detail = client.get(source_url)
        detail.raise_for_status()
        traffic = re.search(
            r"Total\s+traffic.*?([0-9.]+)\s*GiB.*?/\s*([0-9.]+)\s*GiB",
            detail.text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not traffic:
            raise ProviderSyncError("OrangeVPS monthly traffic is unavailable")
        try:
            used_gib = Decimal(traffic.group(1))
            quota_gib = Decimal(traffic.group(2))
        except InvalidOperation as exc:
            raise ProviderSyncError("OrangeVPS traffic values are invalid") from exc
        if used_gib < 0 or quota_gib <= 0:
            raise ProviderSyncError("OrangeVPS traffic values are invalid")
        gibibyte = Decimal(1024**3)
        today = datetime.now(timezone.utc).date()
        period_start = today.replace(day=1)
        period_end = today.replace(day=monthrange(today.year, today.month)[1])
        raw_usage = f"{used_gib} GiB / {quota_gib} GiB"
        return {
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "used_bytes": int(used_gib * gibibyte),
            "quota_bytes": int(quota_gib * gibibyte),
            "source_url": source_url,
            "source_label": f"OrangeVPS 自动同步 · 原始 {raw_usage}",
            "created_by": "provider-sync:orangevps",
            "raw_usage": raw_usage,
        }


def sync_provider_usage(
    conn: sqlite3.Connection,
    cipher: CredentialCipher,
    server_id: int,
    client_factory: Callable[..., httpx.Client] = httpx.Client,
) -> dict[str, Any]:
    access = conn.execute(
        "select * from server_provider_access where server_id = ? and sync_enabled = 1",
        (server_id,),
    ).fetchone()
    if not access:
        raise ProviderSyncError("provider access is not configured or sync is disabled")
    connector = access["connector_type"]
    if connector not in SUPPORTED_CONNECTORS:
        raise ProviderSyncError(f"provider connector is not supported: {connector}")
    password = cipher.decrypt(access["password_encrypted"])
    try:
        if connector == "riven_cloud":
            usage = _riven_usage(access, password, client_factory)
        elif connector == "orangevps":
            usage = _orange_usage(access, password, client_factory)
        else:
            raise ProviderSyncError(f"provider connector is not supported: {connector}")
        quota_bytes = int(usage.get("quota_bytes") or 0)
        if not quota_bytes:
            latest = conn.execute(
                """
                select quota_bytes from server_subscription_usage
                where server_id = ? order by collected_at desc, id desc limit 1
                """,
                (server_id,),
            ).fetchone()
            if not latest or not int(latest["quota_bytes"] or 0):
                raise ProviderSyncError("provider traffic quota is not configured")
            quota_bytes = int(latest["quota_bytes"])
        conn.execute(
            """
            insert into server_subscription_usage(
              server_id, period_start, period_end, used_bytes, quota_bytes,
              source_label, source_url, created_by
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(server_id, period_start, period_end) do update set
              used_bytes=excluded.used_bytes,
              quota_bytes=excluded.quota_bytes,
              source_label=excluded.source_label,
              source_url=excluded.source_url,
              collected_at=current_timestamp,
              created_by=excluded.created_by
            """,
            (
                server_id,
                usage["period_start"],
                usage["period_end"],
                usage["used_bytes"],
                quota_bytes,
                usage["source_label"],
                usage["source_url"],
                usage["created_by"],
            ),
        )
        conn.execute(
            """
            update server_provider_access
            set last_sync_status = 'ok', last_sync_message = ?,
                last_synced_at = current_timestamp, updated_at = current_timestamp
            where server_id = ?
            """,
            (usage.get("raw_usage") or f"{usage['used_bytes']} / {quota_bytes} bytes", server_id),
        )
        conn.commit()
        return {**usage, "quota_bytes": quota_bytes, "connector": connector, "status": "ok"}
    except Exception as exc:
        message = str(exc).strip()[:300] or exc.__class__.__name__
        conn.execute(
            """
            update server_provider_access
            set last_sync_status = 'failed', last_sync_message = ?, updated_at = current_timestamp
            where server_id = ?
            """,
            (message, server_id),
        )
        conn.commit()
        if isinstance(exc, ProviderSyncError):
            raise
        raise ProviderSyncError(message) from exc


def sync_all_provider_usage(db_path: str | Path, credential_key: str) -> list[dict[str, Any]]:
    conn = connect(db_path)
    cipher = CredentialCipher(credential_key)
    try:
        rows = conn.execute(
            """
            select server_id from server_provider_access
            where sync_enabled = 1 and connector_type in ('riven_cloud', 'orangevps')
            order by server_id
            """
        ).fetchall()
        results = []
        for row in rows:
            server_id = int(row["server_id"])
            try:
                result = sync_provider_usage(conn, cipher, server_id)
                results.append({"server_id": server_id, "status": "ok", "used_bytes": result["used_bytes"]})
            except ProviderSyncError as exc:
                results.append({"server_id": server_id, "status": "failed", "error": str(exc)})
        return results
    finally:
        conn.close()
