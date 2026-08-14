from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import shlex
import socket
import subprocess
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AnyHttpUrl, BaseModel, Field
import paramiko

from .db import connect, init_db, row_to_server
from .dashboard import dashboard_snapshot
from .mesh import DEFAULT_INTERVAL_SECONDS, fetch_peer_report, mesh_health_history, poll_mesh_once
from .provider_sync import ProviderSyncError, sync_all_provider_usage, sync_provider_usage
from .security import CredentialCipher, SessionCodec, hash_password, verify_password


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("OPS_DB_PATH", BASE_DIR.parent / "data" / "ops.sqlite3"))
APP_SECRET = os.environ.get("OPS_APP_SECRET", "dev-only-change-me")
CREDENTIAL_KEY = os.environ.get("OPS_CREDENTIAL_KEY", CredentialCipher.generate_key())
MESH_SECRET = os.environ.get("OPS_MESH_SECRET", "").strip()
MESH_ENABLED = os.environ.get("OPS_MESH_ENABLED", "0") == "1" and len(MESH_SECRET) >= 32
MESH_INTERVAL_SECONDS = max(60, int(os.environ.get("OPS_MESH_INTERVAL_SECONDS", str(DEFAULT_INTERVAL_SECONDS))))
PROVIDER_SYNC_ENABLED = os.environ.get("OPS_PROVIDER_SYNC_ENABLED", "1") == "1"
PROVIDER_SYNC_INTERVAL_SECONDS = max(900, int(os.environ.get("OPS_PROVIDER_SYNC_INTERVAL_SECONDS", "21600")))
SESSION_COOKIE = "ops_session"
SYSTEMD_CHECKS = [
    {"id": "server-desk", "name": "Server Desk", "unit": "server-desk.service", "category": "system"},
    {"id": "nginx", "name": "Nginx", "unit": "nginx.service", "category": "system"},
    {"id": "frps", "name": "FRP Server", "unit": "frps.service", "category": "network"},
    {"id": "xray", "name": "Xray", "unit": "xray.service", "category": "network"},
]
NGINX_PROXY_CONFIGS = [
    Path("/etc/nginx/conf.d/frp_services.conf"),
    Path("/etc/nginx/conf.d/xray.conf"),
]


class LoginRequest(BaseModel):
    username: str
    password: str


class ServerPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    hostname: str = Field(min_length=1, max_length=255)
    ipv4: Optional[str] = ""
    ipv6: Optional[str] = ""
    provider: Optional[str] = ""
    region: Optional[str] = ""
    login_user: str = Field(min_length=1, max_length=80)
    auth_type: str = "password"
    ssh_host: Optional[str] = ""
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_key_path: Optional[str] = ""
    ssh_local_key_path: Optional[str] = ""
    ssh_windows_key_path: Optional[str] = ""
    ssh_options: Optional[str] = ""
    panel_url: Optional[str] = ""
    panel_username: Optional[str] = ""
    panel_password: Optional[str] = ""
    service_code: Optional[str] = ""
    provider_portal_url: Optional[str] = None
    provider_username: Optional[str] = None
    provider_password: Optional[str] = None
    provider_service_id: Optional[str] = None
    provider_server_id: Optional[str] = None
    provider_connector: Optional[str] = None
    provider_sync_enabled: Optional[bool] = None
    is_starred: bool = False
    is_retired: bool = False
    heartbeat_enabled: bool = False
    heartbeat_port: int = Field(default=9108, ge=1, le=65535)
    tags: List[str] = Field(default_factory=list)
    notes: Optional[str] = ""
    credential: Optional[str] = ""


class SubscriptionUsagePayload(BaseModel):
    period_start: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    period_end: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    used_gb: float = Field(ge=0)
    quota_gb: float = Field(gt=0)
    source_label: str = Field(min_length=1, max_length=120)
    source_url: Optional[AnyHttpUrl] = None


def db():
    conn = connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


def cipher() -> CredentialCipher:
    return CredentialCipher(CREDENTIAL_KEY)


def server_response(conn, row) -> dict[str, Any]:
    server = row_to_server(row)
    access = conn.execute(
        """
        select portal_url, login_username, password_encrypted, service_reference,
               external_server_id, connector_type, sync_enabled, last_sync_status,
               last_sync_message, last_synced_at
        from server_provider_access where server_id = ?
        """,
        (server["id"],),
    ).fetchone()
    if access:
        provider_access = dict(access)
        provider_access["provider_portal_url"] = provider_access.pop("portal_url") or ""
        provider_access["provider_username"] = provider_access.pop("login_username") or ""
        provider_access["provider_service_id"] = provider_access.pop("service_reference") or ""
        provider_access["provider_server_id"] = provider_access.pop("external_server_id") or ""
        provider_access["provider_connector"] = provider_access.pop("connector_type") or "browser"
        provider_access["provider_sync_enabled"] = bool(provider_access.pop("sync_enabled"))
        provider_access["has_provider_password"] = bool(provider_access.pop("password_encrypted"))
        server.update(provider_access)
    else:
        server.update(
            {
                "provider_portal_url": "",
                "provider_username": "",
                "provider_service_id": "",
                "provider_server_id": "",
                "provider_connector": "browser",
                "provider_sync_enabled": False,
                "has_provider_password": False,
                "last_sync_status": "unconfigured",
                "last_sync_message": "",
                "last_synced_at": None,
            }
        )
    return server


def save_provider_access(conn, server_id: int, payload: ServerPayload, c: CredentialCipher) -> None:
    existing = conn.execute(
        "select * from server_provider_access where server_id = ?",
        (server_id,),
    ).fetchone()
    supplied = any(
        value not in (None, "")
        for value in (
            payload.provider_portal_url,
            payload.provider_username,
            payload.provider_password,
            payload.provider_service_id,
            payload.provider_server_id,
        )
    )
    if not existing and not supplied:
        return
    old = dict(existing) if existing else {}
    password_encrypted = old.get("password_encrypted", "")
    if payload.provider_password:
        password_encrypted = c.encrypt(payload.provider_password)
    values = {
        "portal_url": payload.provider_portal_url if payload.provider_portal_url is not None else old.get("portal_url", ""),
        "login_username": payload.provider_username if payload.provider_username is not None else old.get("login_username", ""),
        "service_reference": payload.provider_service_id if payload.provider_service_id is not None else old.get("service_reference", ""),
        "external_server_id": payload.provider_server_id if payload.provider_server_id is not None else old.get("external_server_id", ""),
        "connector_type": payload.provider_connector if payload.provider_connector is not None else old.get("connector_type", "browser"),
        "sync_enabled": payload.provider_sync_enabled if payload.provider_sync_enabled is not None else bool(old.get("sync_enabled", 1)),
    }
    conn.execute(
        """
        insert into server_provider_access(
          server_id, portal_url, login_username, password_encrypted, service_reference,
          external_server_id, connector_type, sync_enabled
        ) values (?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(server_id) do update set
          portal_url=excluded.portal_url,
          login_username=excluded.login_username,
          password_encrypted=excluded.password_encrypted,
          service_reference=excluded.service_reference,
          external_server_id=excluded.external_server_id,
          connector_type=excluded.connector_type,
          sync_enabled=excluded.sync_enabled,
          updated_at=current_timestamp
        """,
        (
            server_id,
            values["portal_url"] or "",
            values["login_username"] or "",
            password_encrypted,
            values["service_reference"] or "",
            values["external_server_id"] or "",
            values["connector_type"] or "browser",
            1 if values["sync_enabled"] else 0,
        ),
    )


def session_codec() -> SessionCodec:
    return SessionCodec(APP_SECRET)


def _status_from_bool(ok: bool) -> str:
    return "online" if ok else "offline"


def _parse_systemctl_show(output: str) -> dict[str, str]:
    data: dict[str, str] = {}
    for line in output.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            data[key] = value
    return data


def _check_systemd_service(item: dict[str, str]) -> dict[str, Any]:
    started = time.perf_counter()
    base = {
        "id": item["id"],
        "name": item["name"],
        "category": item["category"],
        "kind": "systemd",
        "target": item["unit"],
        "can_check": True,
    }
    try:
        result = subprocess.run(
            [
                "systemctl",
                "show",
                item["unit"],
                "--property=Id,LoadState,ActiveState,SubState,UnitFileState,Description",
                "--no-pager",
            ],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
    except FileNotFoundError:
        return {**base, "status": "unknown", "detail": "systemctl unavailable", "latency_ms": 0}
    except subprocess.TimeoutExpired:
        return {**base, "status": "unknown", "detail": "systemctl timeout", "latency_ms": 1500}
    latency = int((time.perf_counter() - started) * 1000)
    if result.returncode != 0:
        return {**base, "status": "unknown", "detail": (result.stderr or result.stdout).strip()[:180], "latency_ms": latency}
    data = _parse_systemctl_show(result.stdout)
    active = data.get("ActiveState", "unknown")
    sub = data.get("SubState", "unknown")
    load_state = data.get("LoadState", "unknown")
    unit_file_state = data.get("UnitFileState", "unknown")
    status = "online" if active == "active" else "offline" if load_state == "loaded" else "unknown"
    return {
        **base,
        "status": status,
        "detail": f"{active} / {sub}",
        "latency_ms": latency,
        "unit_file_state": unit_file_state,
        "description": data.get("Description", item["name"]),
    }


def _check_tcp_target(host: str, port: int) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=1.2):
            pass
        ok = True
        detail = "tcp reachable"
    except OSError as exc:
        ok = False
        detail = str(exc)
    return {
        "status": _status_from_bool(ok),
        "detail": detail[:180],
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "service"


def _discover_nginx_proxy_apps() -> list[dict[str, Any]]:
    apps: dict[str, dict[str, Any]] = {}
    for config_path in NGINX_PROXY_CONFIGS:
        if not config_path.exists():
            continue
        label = ""
        server_names: list[str] = []
        for raw_line in config_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            comment = re.match(r"#\s*---\s*\d+\.\s*(.*?)\s*---", line)
            if comment:
                label = comment.group(1).strip()
                server_names = []
                continue
            name_match = re.match(r"server_name\s+(.+?);", line)
            if name_match:
                server_names = [name for name in name_match.group(1).split() if name and "*" not in name]
                continue
            proxy_match = re.match(r"proxy_pass\s+https?://([^:/;]+):(\d+)", line)
            if not proxy_match or not server_names:
                continue
            host, port_raw = proxy_match.groups()
            port = int(port_raw)
            domain = server_names[0]
            key = f"{domain}:{host}:{port}"
            apps[key] = {
                "id": _slug(domain),
                "name": label or domain,
                "category": "application",
                "kind": "tcp",
                "target": f"{host}:{port}",
                "public_url": f"https://{domain}",
                "can_check": True,
                "source": str(config_path),
            }
    return list(apps.values())


def _check_proxy_app(item: dict[str, Any]) -> dict[str, Any]:
    host, port_raw = item["target"].rsplit(":", 1)
    result = _check_tcp_target(host, int(port_raw))
    return {**item, **result}


def current_user(request: Request, conn=Depends(db)) -> dict[str, Any]:
    token = request.cookies.get(SESSION_COOKIE, "")
    user_id = session_codec().verify(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="not authenticated")
    row = conn.execute("select id, username from users where id = ?", (user_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="not authenticated")
    return dict(row)


def audit(conn, actor: str, action: str, target_type: str, target_id: int | None = None, detail: str = "") -> None:
    conn.execute(
        "insert into audit_events(actor, action, target_type, target_id, detail) values (?, ?, ?, ?, ?)",
        (actor, action, target_type, target_id, detail[:500]),
    )
    conn.commit()


def ensure_active_server(row) -> None:
    if row["is_retired"]:
        raise HTTPException(status_code=409, detail="server is retired")


def ssh_target(row) -> str:
    return row["ssh_host"] or row["ipv4"] or row["hostname"]


def ssh_command(row, remote_command: str) -> list[str]:
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=8",
        "-p",
        str(row["ssh_port"] or 22),
    ]
    if row["ssh_key_path"]:
        command.extend(["-i", row["ssh_key_path"]])
    if row["ssh_options"]:
        command.extend(shlex.split(row["ssh_options"]))
    command.append(f"{row['login_user']}@{ssh_target(row)}")
    command.append(remote_command)
    return command


def local_inspection_command(remote_command: str) -> list[str]:
    return ["sh", "-lc", remote_command]


INSPECTION_SCRIPT = r"""
set -u
run_timeout() {
  seconds="$1"
  shift
  if command -v timeout >/dev/null 2>&1; then
    timeout -k 1s "${seconds}s" "$@"
  else
    "$@"
  fi
}
echo "__SECTION__hostname"
printf '%s\n' "$(hostname 2>/dev/null || true)"
printf '%s\n' "$(hostname -f 2>/dev/null || true)"
echo "__SECTION__os"
(cat /etc/os-release 2>/dev/null || true) | sed -n '1,12p'
echo "__SECTION__kernel"
uname -a 2>/dev/null || true
echo "__SECTION__board"
echo "__SECTION__runtime"
printf 'virtualization=%s\n' "$(run_timeout 3 systemd-detect-virt 2>/dev/null || true)"
printf 'uptime=%s\n' "$(run_timeout 3 uptime -p 2>/dev/null || true)"
printf 'load_average=%s\n' "$(run_timeout 3 cut -d ' ' -f 1-3 /proc/loadavg 2>/dev/null || run_timeout 3 uptime 2>/dev/null | sed 's/.*load average: //')"
printf 'processes=%s\n' "$(run_timeout 3 sh -c 'ps -e --no-headers 2>/dev/null | wc -l | tr -d " "' || true)"
printf 'logged_users=%s\n' "$(run_timeout 3 sh -c 'who 2>/dev/null | wc -l | tr -d " "' || true)"
printf 'active_services=%s\n' "$(run_timeout 5 sh -c 'systemctl list-units --type=service --state=running --no-legend --no-pager 2>/dev/null | wc -l | tr -d " "' || true)"
printf 'locale=%s\n' "${LANG:-}"
printf 'timezone=%s\n' "$(date '+%Z %z' 2>/dev/null || true)"
echo "__SECTION__cpu"
printf 'count=%s\n' "$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo "")"
printf 'architecture=%s\n' "$(uname -m 2>/dev/null || true)"
if command -v lscpu >/dev/null 2>&1; then
  lscpu 2>/dev/null | awk -F: '
    /^Model name:/ {gsub(/^[ \t]+/, "", $2); print "model=" $2}
    /^Vendor ID:/ {gsub(/^[ \t]+/, "", $2); print "vendor=" $2}
    /^Socket\(s\):/ {gsub(/^[ \t]+/, "", $2); print "sockets=" $2}
    /^Core\(s\) per socket:/ {gsub(/^[ \t]+/, "", $2); print "cores_per_socket=" $2}
    /^Thread\(s\) per core:/ {gsub(/^[ \t]+/, "", $2); print "threads_per_core=" $2}
  ' | head -8
fi
echo "__SECTION__gpu"
if command -v lspci >/dev/null 2>&1; then
  lspci 2>/dev/null | grep -Ei 'vga|3d|display' | head -12 || true
fi
echo "__SECTION__memory"
if command -v free >/dev/null 2>&1; then
  free -h 2>/dev/null | awk '/^Mem:/ {print "memory_total=" $2; print "memory_used=" $3; print "memory_available=" $7}'
fi
awk '/MemTotal/ {print "mem_total_kb=" $2 " kB"} /MemAvailable/ {print "mem_available_kb=" $2 " kB"}' /proc/meminfo 2>/dev/null || true
echo "__SECTION__disk"
df -hP / 2>/dev/null | tail -1 || true
df -hP -x tmpfs -x devtmpfs 2>/dev/null | head -8 || true
echo "__SECTION__block_devices"
if command -v lsblk >/dev/null 2>&1; then
  lsblk -o NAME,TYPE,SIZE,MODEL,MOUNTPOINT -e 7,11 -n 2>/dev/null | head -40
fi
echo "__SECTION__network"
printf 'addresses=%s\n' "$(hostname -I 2>/dev/null | sed 's/[[:space:]]*$//')"
if command -v ip >/dev/null 2>&1; then
  ip -brief address show 2>/dev/null | head -20
  ip route show default 2>/dev/null | head -5
fi
awk '/^nameserver/ {print "dns=" $2}' /etc/resolv.conf 2>/dev/null | head -4 || true
echo "__SECTION__public_ip"
if command -v curl >/dev/null 2>&1; then
  printf 'ipv4=%s\n' "$(curl -4 -fsS --max-time 5 https://api.ipify.org 2>/dev/null || true)"
  printf 'ipv6=%s\n' "$(curl -6 -fsS --max-time 5 https://api64.ipify.org 2>/dev/null || true)"
fi
echo "__SECTION__tcp"
printf 'congestion_control=%s\n' "$(sysctl -n net.ipv4.tcp_congestion_control 2>/dev/null || true)"
printf 'qdisc=%s\n' "$(sysctl -n net.core.default_qdisc 2>/dev/null || true)"
printf 'tcp_rmem=%s\n' "$(cat /proc/sys/net/ipv4/tcp_rmem 2>/dev/null || true)"
printf 'tcp_wmem=%s\n' "$(cat /proc/sys/net/ipv4/tcp_wmem 2>/dev/null || true)"
echo "__SECTION__network_quality"
if command -v curl >/dev/null 2>&1; then
  for target in "cloudflare=https://www.cloudflare.com/cdn-cgi/trace" "google=https://www.google.com/generate_204" "microsoft=https://www.microsoft.com"; do
    name="${target%%=*}"
    url="${target#*=}"
    curl -o /dev/null -fsS --max-time 6 -w "${name} http=%{http_code} dns=%{time_namelookup} connect=%{time_connect} tls=%{time_appconnect} total=%{time_total} ip=%{remote_ip}\n" "$url" 2>/dev/null || printf '%s error=unreachable\n' "$name"
  done
fi
if command -v ping >/dev/null 2>&1; then
  ping -c 3 -W 2 1.1.1.1 2>/dev/null | tail -2 | sed 's/^/ping_1_1_1_1=/' || true
  ping -c 3 -W 2 8.8.8.8 2>/dev/null | tail -2 | sed 's/^/ping_8_8_8_8=/' || true
fi
echo "__SECTION__apps"
if [ -r /var/lib/dpkg/status ]; then
  run_timeout 5 awk '/^Package:/{p=$2}/^Version:/{print p "\t" $2; c++; if(c>=40) exit}' /var/lib/dpkg/status 2>/dev/null
elif command -v dpkg-query >/dev/null 2>&1; then
  run_timeout 5 dpkg-query -W -f='${binary:Package}\t${Version}\n' 2>/dev/null | head -40
elif command -v rpm >/dev/null 2>&1; then
  run_timeout 10 rpm -qa --qf '%{NAME}\t%{VERSION}-%{RELEASE}\n' 2>/dev/null | head -80
elif command -v brew >/dev/null 2>&1; then
  run_timeout 10 brew list --versions 2>/dev/null | head -80
fi
echo "__SECTION__services"
if command -v systemctl >/dev/null 2>&1; then
  run_timeout 10 systemctl list-units --type=service --state=running --no-legend --no-pager 2>/dev/null | awk '{print $1 "\t" $4 "\t" $5}' | head -80
elif command -v service >/dev/null 2>&1; then
  run_timeout 10 service --status-all 2>/dev/null | head -80
fi
echo "__SECTION__ports"
if command -v ss >/dev/null 2>&1; then
  run_timeout 10 ss -lntup 2>/dev/null | tail -n +2 | head -120
elif command -v netstat >/dev/null 2>&1; then
  run_timeout 10 netstat -lntup 2>/dev/null | tail -n +3 | head -120
fi
echo "__SECTION__quality_diagnostics"
printf 'root_disk_used_percent=%s\n' "$(df -P / 2>/dev/null | awk 'NR==2 {gsub(/%/, "", $5); print $5}')"
printf 'root_inode_used_percent=%s\n' "$(df -Pi / 2>/dev/null | awk 'NR==2 {gsub(/%/, "", $5); print $5}')"
printf 'failed_service_count=%s\n' "$(run_timeout 5 sh -c 'systemctl --failed --type=service --no-legend --no-pager 2>/dev/null | sed "/^[[:space:]]*$/d" | wc -l | tr -d " "' || true)"
printf 'ntp_synchronized=%s\n' "$(run_timeout 5 timedatectl show -p NTPSynchronized --value 2>/dev/null || true)"
printf 'heartbeat_service=%s\n' "$(run_timeout 5 systemctl is-active server-desk-heartbeat.service 2>/dev/null || true)"
printf 'heartbeat_timer=%s\n' "$(run_timeout 5 systemctl is-active server-desk-heartbeat-report.timer 2>/dev/null || true)"
printf 'heartbeat_timer_substate=%s\n' "$(run_timeout 5 systemctl show server-desk-heartbeat-report.timer -p SubState --value 2>/dev/null || true)"
printf 'heartbeat_timer_last_trigger=%s\n' "$(run_timeout 5 systemctl show server-desk-heartbeat-report.timer -p LastTriggerUSec --value 2>/dev/null || true)"
printf 'heartbeat_timer_next_trigger=%s\n' "$(run_timeout 5 systemctl show server-desk-heartbeat-report.timer -p NextElapseUSecRealtime --value 2>/dev/null || true)"
if command -v sshd >/dev/null 2>&1; then
  run_timeout 5 sshd -T 2>/dev/null | awk '/^(permitrootlogin|passwordauthentication|pubkeyauthentication) / {print $1 "=" $2}'
fi
if command -v ufw >/dev/null 2>&1; then
  printf 'firewall=%s\n' "$(run_timeout 5 ufw status 2>/dev/null | sed -n '1s/^Status: //p')"
elif command -v firewall-cmd >/dev/null 2>&1; then
  printf 'firewall=%s\n' "$(run_timeout 5 firewall-cmd --state 2>/dev/null || true)"
elif command -v nft >/dev/null 2>&1; then
  printf 'firewall=%s\n' "$(run_timeout 5 sh -c 'nft list ruleset 2>/dev/null | grep -q . && echo active' || true)"
else
  printf 'firewall=unavailable\n'
fi
echo "__SECTION__failed_services"
run_timeout 5 systemctl --failed --type=service --no-legend --no-pager 2>/dev/null | head -20 || true
"""


PYTHON_INSPECTION_SCRIPT = r'''
from __future__ import annotations

import glob
import os
import platform
import re
import shutil
import shlex
import socket
import subprocess
from pathlib import Path


def section(name: str) -> None:
    print(f"__SECTION__{name}", flush=True)


def run(command: str, timeout: float = 5) -> str:
    try:
        completed = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception:
        return ""
    return completed.stdout.strip()


def print_lines(text: str, limit: int = 24) -> None:
    for line in text.splitlines()[:limit]:
        clean = line.rstrip()
        if clean:
            print(clean)


def read_lines(path: str, limit: int = 24) -> list[str]:
    try:
        return Path(path).read_text(errors="replace").splitlines()[:limit]
    except Exception:
        return []


section("hostname")
print(socket.gethostname())
print(run("hostname -f 2>/dev/null", 3))

section("os")
for line in read_lines("/etc/os-release", 12):
    print(line)

section("kernel")
print(run("uname -a 2>/dev/null", 3) or platform.platform())

section("board")

section("runtime")
uptime_raw = ""
try:
    uptime_raw = Path("/proc/uptime").read_text(errors="replace").split()[0]
except Exception:
    pass
print("virtualization=")
print(f"uptime=up {uptime_raw} seconds" if uptime_raw else "uptime=")
load_average = run("cut -d ' ' -f 1-3 /proc/loadavg 2>/dev/null", 3)
print(f"load_average={load_average}")
process_count = len(glob.glob("/proc/[0-9]*"))
print(f"processes={process_count or ''}")
logged_users = run("who 2>/dev/null | wc -l | tr -d ' '", 3)
active_services = run("systemctl list-units --type=service --state=running --no-legend --no-pager 2>/dev/null | wc -l | tr -d ' '", 5)
print(f"logged_users={logged_users}")
print(f"active_services={active_services}")
print(f"locale={os.environ.get('LANG', '')}")
timezone = run("date '+%Z %z' 2>/dev/null", 3)
print(f"timezone={timezone}")

section("cpu")
print(f"count={os.cpu_count() or ''}")
print(f"architecture={platform.machine()}")
cpuinfo = "\n".join(read_lines("/proc/cpuinfo", 400))
model = re.search(r"^model name\s*:\s*(.+)$", cpuinfo, re.MULTILINE)
vendor = re.search(r"^vendor_id\s*:\s*(.+)$", cpuinfo, re.MULTILINE)
cpu_cores = re.search(r"^cpu cores\s*:\s*(.+)$", cpuinfo, re.MULTILINE)
siblings = re.search(r"^siblings\s*:\s*(.+)$", cpuinfo, re.MULTILINE)
if model:
    print(f"model={model.group(1)}")
if vendor:
    print(f"vendor={vendor.group(1)}")
if cpu_cores:
    print(f"cores_per_socket={cpu_cores.group(1)}")
if siblings and cpu_cores:
    try:
        threads = max(1, int(siblings.group(1)) // max(1, int(cpu_cores.group(1))))
        print(f"threads_per_core={threads}")
    except Exception:
        pass
print("sockets=1")

section("gpu")
if shutil.which("lspci"):
    print_lines(run("lspci 2>/dev/null | grep -Ei 'vga|3d|display' | head -12", 5), 12)

section("memory")
meminfo = {}
for line in read_lines("/proc/meminfo", 80):
    if ":" in line:
        key, value = line.split(":", 1)
        meminfo[key] = value.strip()
if meminfo.get("MemTotal"):
    print(f"mem_total_kb={meminfo['MemTotal']}")
if meminfo.get("MemAvailable"):
    print(f"mem_available_kb={meminfo['MemAvailable']}")
print_lines(run("free -h 2>/dev/null | awk '/^Mem:/ {print \"memory_total=\" $2; print \"memory_used=\" $3; print \"memory_available=\" $7}'", 5), 3)

section("disk")
print_lines(run("df -hP / 2>/dev/null | tail -1", 5), 1)
print_lines(run("df -hP -x tmpfs -x devtmpfs 2>/dev/null | head -8", 5), 8)

section("block_devices")
if shutil.which("lsblk"):
    print_lines(run("lsblk -o NAME,TYPE,SIZE,MODEL,MOUNTPOINT -e 7,11 -n 2>/dev/null | head -40", 5), 40)

section("network")
addresses = run("hostname -I 2>/dev/null | sed 's/[[:space:]]*$//'", 3)
print(f"addresses={addresses}")
if shutil.which("ip"):
    print_lines(run("ip -brief address show 2>/dev/null | head -20", 5), 20)
    print_lines(run("ip route show default 2>/dev/null | head -5", 5), 5)
for line in read_lines("/etc/resolv.conf", 20):
    if line.startswith("nameserver"):
        parts = line.split()
        if len(parts) > 1:
            print(f"dns={parts[1]}")

section("public_ip")
if shutil.which("curl"):
    print(f"ipv4={run('curl -4 -fsS --max-time 5 https://api.ipify.org 2>/dev/null', 7)}")
    print(f"ipv6={run('curl -6 -fsS --max-time 5 https://api64.ipify.org 2>/dev/null', 7)}")

section("tcp")
print(f"congestion_control={run('sysctl -n net.ipv4.tcp_congestion_control 2>/dev/null', 3)}")
print(f"qdisc={run('sysctl -n net.core.default_qdisc 2>/dev/null', 3)}")
for name in ("tcp_rmem", "tcp_wmem"):
    try:
        print(f"{name}={Path('/proc/sys/net/ipv4/' + name).read_text(errors='replace').strip()}")
    except Exception:
        print(f"{name}=")

section("network_quality")
if shutil.which("curl"):
    targets = (
        ("cloudflare", "https://www.cloudflare.com/cdn-cgi/trace"),
        ("google", "https://www.google.com/generate_204"),
        ("microsoft", "https://www.microsoft.com"),
    )
    for name, url in targets:
        line = run(
            "curl -o /dev/null -fsS --max-time 6 "
            f"-w '{name} http=%{{http_code}} dns=%{{time_namelookup}} connect=%{{time_connect}} tls=%{{time_appconnect}} total=%{{time_total}} ip=%{{remote_ip}}\\n' "
            + shlex.quote(url)
            + " 2>/dev/null",
            8,
        )
        print(line or f"{name} error=unreachable")
if shutil.which("ping"):
    print_lines(run("ping -c 3 -W 2 1.1.1.1 2>/dev/null | tail -2 | sed 's/^/ping_1_1_1_1=/'", 8), 2)
    print_lines(run("ping -c 3 -W 2 8.8.8.8 2>/dev/null | tail -2 | sed 's/^/ping_8_8_8_8=/'", 8), 2)

section("apps")
status_path = Path("/var/lib/dpkg/status")
if status_path.exists():
    package = ""
    count = 0
    for line in status_path.read_text(errors="replace").splitlines():
        if line.startswith("Package:"):
            package = line.split(":", 1)[1].strip()
        elif line.startswith("Version:") and package:
            print(f"{package}\t{line.split(':', 1)[1].strip()}")
            count += 1
            if count >= 40:
                break
elif shutil.which("rpm"):
    print_lines(run("rpm -qa --qf '%{NAME}\\t%{VERSION}-%{RELEASE}\\n' 2>/dev/null | head -40", 8), 40)
elif shutil.which("brew"):
    print_lines(run("brew list --versions 2>/dev/null | head -40", 8), 40)

section("services")
print_lines(run("systemctl list-units --type=service --state=running --no-legend --no-pager 2>/dev/null | awk '{print $1 \"\\t\" $4 \"\\t\" $5}' | head -80", 10), 80)

section("ports")
if shutil.which("ss"):
    print_lines(run("ss -lntup 2>/dev/null | tail -n +2 | head -120", 10), 120)
elif shutil.which("netstat"):
    print_lines(run("netstat -lntup 2>/dev/null | tail -n +3 | head -120", 10), 120)

section("quality_diagnostics")
print("root_disk_used_percent=" + run("df -P / 2>/dev/null | awk 'NR==2 {gsub(/%/, \"\", $5); print $5}'", 5))
print("root_inode_used_percent=" + run("df -Pi / 2>/dev/null | awk 'NR==2 {gsub(/%/, \"\", $5); print $5}'", 5))
failed_count = run("systemctl --failed --type=service --no-legend --no-pager 2>/dev/null | sed '/^[[:space:]]*$/d' | wc -l | tr -d ' '", 5)
print(f"failed_service_count={failed_count}")
print(f"ntp_synchronized={run('timedatectl show -p NTPSynchronized --value 2>/dev/null', 5)}")
print(f"heartbeat_service={run('systemctl is-active server-desk-heartbeat.service 2>/dev/null', 5)}")
print(f"heartbeat_timer={run('systemctl is-active server-desk-heartbeat-report.timer 2>/dev/null', 5)}")
print(f"heartbeat_timer_substate={run('systemctl show server-desk-heartbeat-report.timer -p SubState --value 2>/dev/null', 5)}")
print(f"heartbeat_timer_last_trigger={run('systemctl show server-desk-heartbeat-report.timer -p LastTriggerUSec --value 2>/dev/null', 5)}")
print(f"heartbeat_timer_next_trigger={run('systemctl show server-desk-heartbeat-report.timer -p NextElapseUSecRealtime --value 2>/dev/null', 5)}")
if shutil.which("sshd"):
    print_lines(run("sshd -T 2>/dev/null | awk '/^(permitrootlogin|passwordauthentication|pubkeyauthentication) / {print $1 \"=\" $2}'", 5), 3)
if shutil.which("ufw"):
    print("firewall=" + run("ufw status 2>/dev/null | sed -n '1s/^Status: //p'", 5))
elif shutil.which("firewall-cmd"):
    print(f"firewall={run('firewall-cmd --state 2>/dev/null', 5)}")
elif shutil.which("nft"):
    print("firewall=" + run("nft list ruleset 2>/dev/null | grep -q . && echo active", 5))
else:
    print("firewall=unavailable")

section("failed_services")
print_lines(run("systemctl --failed --type=service --no-legend --no-pager 2>/dev/null | head -20", 5), 20)
'''


def split_sections(output: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = ""
    for line in output.splitlines():
        if line.startswith("__SECTION__"):
            current = line.replace("__SECTION__", "", 1)
            sections[current] = []
        elif current:
            sections[current].append(line)
    return sections


def first_nonempty(lines: list[str]) -> str:
    return next((line.strip() for line in lines if line.strip()), "")


def parse_key_values(lines: list[str]) -> dict[str, str]:
    data: dict[str, str] = {}
    for line in lines:
        clean = line.strip()
        if not clean or "=" not in clean:
            continue
        key, value = clean.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def parse_os_name(lines: list[str]) -> str:
    data = parse_key_values(lines)
    return data.get("PRETTY_NAME", "").strip('"') or first_nonempty(lines)


def report_lines(lines: list[str], limit: int = 24) -> list[str]:
    return [line.rstrip() for line in lines if line.strip()][:limit]


def parse_apps(lines: list[str]) -> list[dict[str, str]]:
    apps = []
    for line in lines:
        clean = line.strip()
        if not clean:
            continue
        if "\t" in clean:
            name, version = clean.split("\t", 1)
        else:
            parts = clean.split(maxsplit=1)
            name = parts[0]
            version = parts[1] if len(parts) > 1 else ""
        apps.append({"name": name, "version": version, "category": app_category(name)})
    return apps


def app_category(name: str) -> str:
    custom_prefixes = (
        "1panel",
        "apache",
        "caddy",
        "certbot",
        "docker",
        "frp",
        "frpc",
        "frps",
        "grafana",
        "jenkins",
        "mongodb",
        "mysql",
        "nginx",
        "nodejs",
        "php",
        "postgres",
        "redis",
        "tomcat",
        "xray",
    )
    custom_names = {
        "containerd.io",
        "docker-ce",
        "docker-ce-cli",
        "docker-compose-plugin",
        "fail2ban",
        "frp",
        "frpc",
        "frps",
        "gitlab-ce",
        "goaccess",
        "mariadb-server",
        "npm",
        "pm2",
        "supervisor",
        "tailscale",
        "xray",
    }
    return "custom" if name in custom_names or name.startswith(custom_prefixes) else "system"


def parse_services(service_lines: list[str], port_lines: list[str]) -> list[dict[str, Any]]:
    services: dict[str, dict[str, Any]] = {}
    for line in service_lines:
        clean = line.strip()
        if not clean:
            continue
        name = clean.split()[0]
        services[name] = {
            "name": name,
            "state": "running",
            "ports": [],
            "external": False,
            "category": service_category(name),
        }
    for line in port_lines:
        clean = line.strip()
        if not clean:
            continue
        external = any(marker in clean for marker in ("0.0.0.0:", "[::]:", ":::"))
        key = service_name_from_port_line(clean)
        entry = services.setdefault(
            key,
            {
                "name": key,
                "state": "listening",
                "ports": [],
                "external": False,
                "category": service_category(key),
            },
        )
        entry["ports"].append(clean)
        entry["external"] = entry["external"] or external
    return list(services.values())[:120]


def service_name_from_port_line(line: str) -> str:
    match = re.search(r'users:\(\("([^"]+)"', line)
    if match:
        return match.group(1)
    parts = line.split()
    if not parts:
        return "unknown-listener"
    return f"listener:{parts[4] if len(parts) > 4 else parts[-1]}"


def service_category(name: str) -> str:
    custom_prefixes = (
        "1panel",
        "bobstudio",
        "docker",
        "dovecot",
        "frp",
        "frpc",
        "frps",
        "gunicorn",
        "mailinabox",
        "master",
        "munin",
        "named",
        "nginx",
        "node",
        "opendkim",
        "opendmarc",
        "php",
        "postfix",
        "postgrey",
        "shadowsocks",
        "spampd",
        "ss-",
        "xfrd",
        "xray",
    )
    custom_names = {
        "containerd",
        "containerd.service",
        "docker.service",
        "nginx.service",
        "server-desk.service",
    }
    system_prefixes = (
        "accounts-",
        "acpid",
        "apparmor",
        "apt-",
        "chrony",
        "chronyd",
        "cron",
        "dbus",
        "fwupd",
        "getty@",
        "irqbalance",
        "listener:",
        "keyboard-",
        "kmod",
        "logrotate",
        "lvm",
        "ModemManager",
        "multipathd",
        "networkd-",
        "polkit",
        "rsyslog",
        "serial-getty@",
        "snap.",
        "snapd",
        "ssh.service",
        "sshd",
        "systemd-",
        "tailscaled",
        "udisks2",
        "unattended-",
        "upower",
        "user@",
    )
    system_names = {
        "atd.service",
        "packagekit.service",
        "tuned.service",
    }
    if name in custom_names or name.startswith(custom_prefixes):
        return "custom"
    if name in system_names or name.startswith(system_prefixes):
        return "system"
    return "system"


def build_config_report(output: str) -> tuple[str, str, dict[str, Any], list[dict[str, str]], list[dict[str, Any]]]:
    sections = split_sections(output)
    apps = parse_apps(sections.get("apps", []))
    services = parse_services(sections.get("services", []), sections.get("ports", []))
    hostname_lines = [line.strip() for line in sections.get("hostname", []) if line.strip()]
    board = parse_key_values(sections.get("board", []))
    runtime = parse_key_values(sections.get("runtime", []))
    cpu = parse_key_values(sections.get("cpu", []))
    gpu = report_lines(sections.get("gpu", []), 12)
    memory = parse_key_values(sections.get("memory", []))
    network_lines = report_lines(sections.get("network", []), 30)
    public_ip = parse_key_values(sections.get("public_ip", []))
    network_quality = report_lines(sections.get("network_quality", []), 40)
    tcp = parse_key_values(sections.get("tcp", []))
    quality_diagnostics = parse_key_values(sections.get("quality_diagnostics", []))
    failed_services = report_lines(sections.get("failed_services", []), 20)
    disk_lines = report_lines(sections.get("disk", []), 12)
    block_devices = report_lines(sections.get("block_devices", []), 40)
    external_services = [service for service in services if service.get("external")]
    health_score = 100
    if not first_nonempty(sections.get("kernel", [])):
        health_score -= 30
    if not cpu.get("count"):
        health_score -= 10
    if not memory:
        health_score -= 10
    if not disk_lines:
        health_score -= 10
    if not network_lines:
        health_score -= 10
    health_score = max(0, health_score)
    report = {
        "hostname": hostname_lines[0] if hostname_lines else "",
        "hostname_fqdn": hostname_lines[1] if len(hostname_lines) > 1 else "",
        "os": sections.get("os", []),
        "os_name": parse_os_name(sections.get("os", [])),
        "kernel": "\n".join(sections.get("kernel", [])).strip(),
        "board": board,
        "runtime": runtime,
        "cpu": cpu,
        "cpu_count": cpu.get("count", first_nonempty(sections.get("cpu", []))),
        "gpu": gpu,
        "memory": memory.get("memory_total") or memory.get("mem_total_kb") or first_nonempty(sections.get("memory", [])),
        "memory_detail": memory,
        "disk_root": disk_lines[0] if disk_lines else "",
        "disks": disk_lines,
        "block_devices": block_devices,
        "network": {
            "lines": network_lines,
            "addresses": network_lines[0].replace("addresses=", "").split() if network_lines and network_lines[0].startswith("addresses=") else [],
            "public_ip": public_ip,
            "quality": network_quality,
            "tcp": tcp,
        },
        "external_service_count": len(external_services),
        "quality_diagnostics": quality_diagnostics,
        "failed_services": failed_services,
        "health_score": health_score,
        "report_sections": {
            "runtime": report_lines(sections.get("runtime", [])),
            "board": report_lines(sections.get("board", [])),
            "cpu": report_lines(sections.get("cpu", [])),
            "gpu": gpu,
            "memory": report_lines(sections.get("memory", [])),
            "disk": disk_lines,
            "block_devices": block_devices,
            "network": network_lines,
            "public_ip": report_lines(sections.get("public_ip", [])),
            "network_quality": network_quality,
            "tcp": report_lines(sections.get("tcp", [])),
            "ports": report_lines(sections.get("ports", []), 30),
            "quality_diagnostics": report_lines(sections.get("quality_diagnostics", [])),
            "failed_services": failed_services,
        },
    }
    status = "ok" if report["kernel"] else "warning"
    summary_parts = [f"{len(apps)} 个应用", f"{len(services)} 个服务"]
    if report["cpu_count"]:
        summary_parts.append(f"CPU {report['cpu_count']} 核")
    if report["memory"]:
        summary_parts.append(f"内存 {report['memory']}")
    summary = "，".join(summary_parts)
    return status, summary, report, apps, services


def _number(value: Any) -> float | None:
    match = re.search(r"-?\d+(?:\.\d+)?", str(value or ""))
    return float(match.group()) if match else None


def _quality_check(
    check_id: str,
    label: str,
    status: str,
    points: int,
    max_points: int,
    evidence: str,
    recommendation: str = "",
) -> dict[str, Any]:
    return {
        "id": check_id,
        "label": label,
        "status": status,
        "points": points,
        "max_points": max_points,
        "evidence": evidence[:500],
        "recommendation": recommendation[:500],
    }


def _threshold_check(
    check_id: str,
    label: str,
    value: float | None,
    warning_at: float,
    failure_at: float,
    max_points: int,
    unit: str = "%",
) -> dict[str, Any]:
    if value is None:
        return _quality_check(check_id, label, "info", max_points // 2, max_points, "目标机未返回该指标", "确认目标机具备标准 Linux 诊断命令。")
    evidence = f"{value:g}{unit}"
    if value >= failure_at:
        return _quality_check(check_id, label, "fail", 0, max_points, evidence, f"将该指标降至 {failure_at:g}{unit} 以下并复查。")
    if value >= warning_at:
        return _quality_check(check_id, label, "warn", max_points // 2, max_points, evidence, f"持续观察并将该指标控制在 {warning_at:g}{unit} 以下。")
    return _quality_check(check_id, label, "pass", max_points, max_points, evidence)


def mesh_quality_evidence(conn, server_id: int, limit: int = 30) -> dict[str, Any]:
    rows = conn.execute(
        """
        select sampled_at, network_score, app_score, direct_ok, peer_visible, peer_expected, details_json
        from mesh_health_samples where server_id = ? order by sampled_at desc limit ?
        """,
        (server_id, limit),
    ).fetchall()
    samples = []
    for row in rows:
        item = dict(row)
        try:
            item["details"] = json.loads(item.pop("details_json") or "{}")
        except json.JSONDecodeError:
            item["details"] = {}
        samples.append(item)
    confirmed = sum(1 for item in samples if item["details"].get("external_visibility_confirmed"))
    return {
        "sample_count": len(samples),
        "confirmed_count": confirmed,
        "confirmed_rate": round(confirmed / len(samples), 3) if samples else None,
        "latest": samples[0] if samples else None,
    }


def probe_heartbeat_agent(row) -> dict[str, Any]:
    if not row["heartbeat_enabled"]:
        return {"status": "disabled", "ok": True, "detail": "未启用分布式心跳"}
    if not MESH_ENABLED:
        return {"status": "unavailable", "ok": None, "detail": "主服务未启用心跳签名探测"}
    try:
        report = fetch_peer_report(dict(row), MESH_SECRET, timeout=3.0)
        return {
            "status": "ok",
            "ok": True,
            "detail": "签名报告读取成功",
            "latency_ms": int(report.get("_latency_ms") or 0),
        }
    except Exception as exc:
        detail = str(exc).strip()[:240] or exc.__class__.__name__
        return {"status": "error", "ok": False, "detail": detail}


def build_quality_report(
    row,
    inspection_status: str,
    report: dict[str, Any],
    services: list[dict[str, Any]],
    mesh_evidence: dict[str, Any],
    heartbeat_probe: dict[str, Any],
) -> dict[str, Any]:
    diagnostics = report.get("quality_diagnostics") or {}
    network_lines = report.get("network", {}).get("lines") or []
    quality_lines = report.get("network", {}).get("quality") or []
    dimensions: list[dict[str, Any]] = []

    access_checks = [
        _quality_check(
            "ssh_inspection",
            "SSH 登录与远程采集",
            "pass" if inspection_status != "error" else "fail",
            10 if inspection_status != "error" else 0,
            10,
            "远程采集成功" if inspection_status != "error" else report.get("error", "远程采集失败"),
            "核对 SSH 地址、端口、凭据和目标机访问控制。",
        )
    ]
    auth_ready = bool(row["ssh_key_path"] or row["credential_encrypted"])
    access_checks.append(
        _quality_check("auth_material", "登录资料完整性", "pass" if auth_ready else "warn", 5 if auth_ready else 2, 5, "已保存认证资料" if auth_ready else "未发现密钥路径或加密凭据", "补齐可用的登录资料并复查。")
    )
    dimensions.append({"id": "access", "name": "访问链路", "checks": access_checks})

    disk_used = _number(diagnostics.get("root_disk_used_percent"))
    inode_used = _number(diagnostics.get("root_inode_used_percent"))
    total_kb = _number((report.get("memory_detail") or {}).get("mem_total_kb"))
    available_kb = _number((report.get("memory_detail") or {}).get("mem_available_kb"))
    memory_used = 100 * (1 - available_kb / total_kb) if total_kb and available_kb is not None else None
    load_one = _number((report.get("runtime") or {}).get("load_average"))
    cpu_count = _number(report.get("cpu_count"))
    normalized_load = 100 * load_one / cpu_count if load_one is not None and cpu_count else None
    ntp = str(diagnostics.get("ntp_synchronized") or "").lower()
    system_checks = [
        _threshold_check("root_disk", "根分区使用率", disk_used, 80, 90, 7),
        _threshold_check("root_inode", "根分区 inode 使用率", inode_used, 80, 90, 4),
        _threshold_check("memory", "内存使用率", memory_used, 85, 95, 5),
        _threshold_check("load", "单核归一化负载", normalized_load, 100, 150, 5),
        _quality_check("clock", "系统时间同步", "pass" if ntp == "yes" else "warn" if ntp == "no" else "info", 4 if ntp == "yes" else 1 if ntp == "no" else 2, 4, ntp or "未返回同步状态", "启用可靠的 NTP 时间同步并确认偏差。"),
    ]
    dimensions.append({"id": "system", "name": "系统资源", "checks": system_checks})

    addresses = report.get("network", {}).get("addresses") or []
    has_route = any("default" in line for line in network_lines)
    has_dns = any(line.startswith("dns=") for line in network_lines)
    endpoint_lines = [line for line in quality_lines if re.match(r"^(cloudflare|google|microsoft) ", line)]
    endpoints_ok = sum(1 for line in endpoint_lines if re.search(r"http=[234]\d\d", line))
    outbound_status = "pass" if endpoints_ok == 3 else "warn" if endpoints_ok else "fail"
    outbound_points = 12 if endpoints_ok == 3 else 8 if endpoints_ok == 2 else 4 if endpoints_ok == 1 else 0
    network_checks = [
        _quality_check("addresses", "本机地址", "pass" if addresses else "fail", 3 if addresses else 0, 3, " ".join(addresses) or "未发现地址"),
        _quality_check("default_route", "默认路由", "pass" if has_route else "fail", 2 if has_route else 0, 2, "已发现默认路由" if has_route else "未发现默认路由"),
        _quality_check("dns", "DNS 配置", "pass" if has_dns else "warn", 3 if has_dns else 1, 3, "已发现 DNS 服务器" if has_dns else "未发现 DNS 服务器"),
        _quality_check("outbound_https", "外网 HTTPS 探测", outbound_status, outbound_points, 12, f"{endpoints_ok}/3 个目标成功", "检查 DNS、默认路由、出站策略和 TLS 时间。"),
    ]
    dimensions.append({"id": "network", "name": "网络质量", "checks": network_checks})

    failed_count = int(_number(diagnostics.get("failed_service_count")) or len(report.get("failed_services") or []))
    service_checks = [
        _quality_check("failed_services", "失败的 systemd 服务", "pass" if failed_count == 0 else "fail", 10 if failed_count == 0 else 0, 10, f"{failed_count} 个失败服务", "查看失败服务日志，修复后重新体检。"),
        _quality_check("running_services", "运行服务清单", "pass" if services else "info", 5 if services else 2, 5, f"采集到 {len(services)} 个运行或监听服务"),
    ]
    dimensions.append({"id": "services", "name": "服务状态", "checks": service_checks})

    root_policy = str(diagnostics.get("permitrootlogin") or "unknown").lower()
    password_policy = str(diagnostics.get("passwordauthentication") or "unknown").lower()
    firewall = str(diagnostics.get("firewall") or "unknown").lower()
    root_points = 5 if root_policy == "no" else 4 if root_policy in {"prohibit-password", "without-password"} else 2 if root_policy == "yes" else 3
    password_points = 5 if password_policy == "no" else 2 if password_policy == "yes" else 3
    firewall_active = firewall in {"active", "running"}
    security_checks = [
        _quality_check("root_login", "Root SSH 策略", "pass" if root_points >= 4 else "warn" if root_policy != "unknown" else "info", root_points, 5, root_policy, "确认来源限制、强凭据和防爆破措施符合当前运维策略。"),
        _quality_check("password_auth", "密码登录策略", "pass" if password_policy == "no" else "warn" if password_policy == "yes" else "info", password_points, 5, password_policy, "允许密码登录时应限制来源并配置防爆破措施。"),
        _quality_check("firewall", "主机防火墙", "pass" if firewall_active else "warn" if firewall not in {"unknown", "unavailable", ""} else "info", 5 if firewall_active else 2 if firewall not in {"unknown", "unavailable", ""} else 3, 5, firewall, "核对云防火墙与主机防火墙的入站范围。"),
    ]
    dimensions.append({"id": "security", "name": "访问安全", "checks": security_checks})

    if row["heartbeat_enabled"]:
        latest = mesh_evidence.get("latest") or {}
        latest_details = latest.get("details") or {}
        latest_available = bool(latest)
        direct_ok = heartbeat_probe.get("ok")
        externally_confirmed = bool(latest_details.get("external_visibility_confirmed"))
        rate = mesh_evidence.get("confirmed_rate")
        timer_state = str(diagnostics.get("heartbeat_timer") or "").lower()
        timer_substate = str(diagnostics.get("heartbeat_timer_substate") or "").lower()
        timer_ok = timer_state == "active" and timer_substate == "waiting"
        timer_known = bool(timer_state or timer_substate)
        timer_evidence = f"{timer_state or 'unknown'} / {timer_substate or 'unknown'}"
        if diagnostics.get("heartbeat_timer_last_trigger"):
            timer_evidence += f"，最近触发 {diagnostics['heartbeat_timer_last_trigger']}"
        probe_evidence = str(heartbeat_probe.get("detail") or "无结果")
        if heartbeat_probe.get("latency_ms") is not None:
            probe_evidence += f"，{heartbeat_probe['latency_ms']} ms"
        heartbeat_checks = [
            _quality_check("heartbeat_direct", "心跳 Agent 直连", "pass" if direct_ok is True else "fail" if direct_ok is False else "info", 3 if direct_ok is True else 0 if direct_ok is False else 1, 3, probe_evidence, "检查 Agent 服务、端口监听、防火墙和签名配置。"),
            _quality_check("heartbeat_latest", "最新邻居确认", "pass" if externally_confirmed else "fail" if latest_available else "info", 2 if externally_confirmed else 0 if latest_available else 1, 2, f"传播 {latest.get('peer_visible', 0)}/{latest.get('peer_expected', 0)}" if latest_available else "暂无样本", "检查节点到邻居的 9108/tcp 双向连通和报告定时器。"),
            _quality_check("heartbeat_window", "近期邻居确认率", "pass" if rate is not None and rate >= 0.8 else "warn" if rate is not None and rate >= 0.5 else "fail" if rate is not None else "info", 3 if rate is not None and rate >= 0.8 else 2 if rate is not None and rate >= 0.5 else 0 if rate is not None else 1, 3, f"{mesh_evidence.get('confirmed_count', 0)}/{mesh_evidence.get('sample_count', 0)} 个样本，{round(rate * 100)}%" if rate is not None else "暂无样本", "结合失败报告源和邻居可见列表排查间歇性传播。"),
            _quality_check("heartbeat_timer_schedule", "主动上报定时器", "pass" if timer_ok else "fail" if timer_known else "info", 2 if timer_ok else 0 if timer_known else 1, 2, timer_evidence, "定时器应为 active / waiting；若为 elapsed，应恢复调度并核对重启后的首次触发。"),
        ]
    else:
        heartbeat_checks = [_quality_check("heartbeat_disabled", "分布式心跳", "info", 10, 10, "该节点未启用分布式心跳")]
    dimensions.append({"id": "heartbeat", "name": "心跳传播", "checks": heartbeat_checks})

    for dimension in dimensions:
        dimension["score"] = sum(item["points"] for item in dimension["checks"])
        dimension["max_score"] = sum(item["max_points"] for item in dimension["checks"])
    score = sum(item["score"] for item in dimensions)
    maximum = sum(item["max_score"] for item in dimensions)
    normalized_score = round(100 * score / maximum) if maximum else 0
    findings = [check for dimension in dimensions for check in dimension["checks"] if check["status"] in {"warn", "fail"}]
    grade = "A" if normalized_score >= 90 else "B" if normalized_score >= 75 else "C" if normalized_score >= 60 else "D"
    status = "ok" if normalized_score >= 90 and not any(item["status"] == "fail" for item in findings) else "warning" if normalized_score >= 60 else "error"
    return {
        "version": 2,
        "title": "环境质量体检报告",
        "score": normalized_score,
        "grade": grade,
        "status": status,
        "dimensions": dimensions,
        "findings": findings,
        "mesh_evidence": mesh_evidence,
        "heartbeat_probe": heartbeat_probe,
        "summary": f"{normalized_score} 分，{len(findings)} 项需要关注",
    }


def fallback_local_config_report() -> tuple[str, str, dict[str, Any], list[dict[str, str]], list[dict[str, Any]]]:
    report = {
        "hostname": socket.gethostname(),
        "hostname_fqdn": socket.getfqdn(),
        "os": [platform.platform()],
        "os_name": platform.platform(),
        "kernel": platform.platform(),
        "board": {},
        "runtime": {},
        "cpu": {"count": str(os.cpu_count() or ""), "architecture": platform.machine()},
        "cpu_count": str(os.cpu_count() or ""),
        "gpu": [],
        "memory": "",
        "memory_detail": {},
        "disk_root": "",
        "disks": [],
        "block_devices": [],
        "network": {"lines": [], "addresses": [], "public_ip": {}, "quality": [], "tcp": {}},
        "external_service_count": 0,
        "health_score": 60,
        "report_sections": {
            "runtime": [],
            "board": [],
            "cpu": [],
            "gpu": [],
            "memory": [],
            "disk": [],
            "block_devices": [],
            "network": [],
            "public_ip": [],
            "network_quality": [],
            "tcp": [],
            "ports": [],
        },
    }
    return "warning", "0 个应用，0 个服务", report, [], []


def read_paramiko_exec(client: paramiko.SSHClient, command: str, timeout: int = 75) -> tuple[int, str, str]:
    _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    output = stdout.read().decode("utf-8", errors="replace")
    error = stderr.read().decode("utf-8", errors="replace")
    return stdout.channel.recv_exit_status(), output, error


def paramiko_has_python3(client: paramiko.SSHClient) -> bool:
    try:
        exit_code, output, _error = read_paramiko_exec(client, "command -v python3", timeout=8)
    except Exception:
        return False
    return exit_code == 0 and bool(output.strip())


def run_paramiko_python_script(client: paramiko.SSHClient) -> tuple[int, str, str]:
    remote_path = f"/tmp/server-desk-inspect-{uuid.uuid4().hex}.py"
    sftp = client.open_sftp()
    try:
        with sftp.file(remote_path, "w") as remote_file:
            remote_file.write(PYTHON_INSPECTION_SCRIPT)
        sftp.chmod(remote_path, 0o700)
    finally:
        sftp.close()
    try:
        return read_paramiko_exec(client, f"python3 {shlex.quote(remote_path)}", timeout=75)
    finally:
        try:
            read_paramiko_exec(client, f"rm -f {shlex.quote(remote_path)}", timeout=5)
        except Exception:
            pass


def run_paramiko_inspection(row, password: str) -> tuple[str, str, dict[str, Any], list[dict[str, str]], list[dict[str, Any]]]:
    if not password:
        return "error", "密码认证需要先保存登录凭据。", {"error": "密码认证需要先保存登录凭据。"}, [], []
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=ssh_target(row),
            port=int(row["ssh_port"] or 22),
            username=row["login_user"],
            password=password,
            timeout=10,
            banner_timeout=10,
            auth_timeout=10,
            look_for_keys=False,
            allow_agent=False,
        )
        if paramiko_has_python3(client):
            exit_code, output, error = run_paramiko_python_script(client)
        else:
            exit_code, output, error = read_paramiko_exec(client, INSPECTION_SCRIPT, timeout=45)
    except Exception as exc:
        detail = str(exc).strip()[:500] or exc.__class__.__name__
        return "error", detail, {"error": detail}, [], []
    finally:
        client.close()
    if exit_code != 0:
        detail = (error or output or "").strip()[:500] or "配置检查失败"
        return "error", detail, {"error": detail}, [], []
    return build_config_report(output)


def run_server_inspection(row, password: str = "") -> tuple[str, str, dict[str, Any], list[dict[str, str]], list[dict[str, Any]]]:
    target = ssh_target(row)
    is_local = target in {"localhost", "127.0.0.1", "::1"} or row["hostname"] in {"localhost", "127.0.0.1", "::1"}
    if not is_local and row["auth_type"] == "password":
        return run_paramiko_inspection(row, password)
    command = local_inspection_command(INSPECTION_SCRIPT) if is_local else ssh_command(row, INSPECTION_SCRIPT)
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
        )
    except FileNotFoundError as exc:
        if is_local:
            return fallback_local_config_report()
        detail = str(exc).strip()[:500] or exc.__class__.__name__
        return "error", detail, {"error": detail}, [], []
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[:500] or "inspection failed"
        return "error", detail, {"error": detail}, [], []
    return build_config_report(completed.stdout)


def bootstrap() -> None:
    conn = connect(DB_PATH)
    try:
        init_db(conn)
        admin_password = os.environ.get("OPS_ADMIN_PASSWORD", "").strip()
        existing = conn.execute("select count(*) as c from users").fetchone()["c"]
        if existing == 0 and admin_password:
            password_hash, salt = hash_password(admin_password)
            conn.execute(
                "insert into users(username, password_hash, password_salt) values (?, ?, ?)",
                ("admin", password_hash, salt),
            )
            conn.commit()
    finally:
        conn.close()


async def mesh_monitor_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(poll_mesh_once, DB_PATH, MESH_SECRET)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"mesh poll failed: {exc}", file=sys.stderr, flush=True)
        await asyncio.sleep(MESH_INTERVAL_SECONDS)


async def provider_sync_loop() -> None:
    await asyncio.sleep(30)
    while True:
        try:
            await asyncio.to_thread(sync_all_provider_usage, DB_PATH, CREDENTIAL_KEY)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"provider sync failed: {exc}", file=sys.stderr, flush=True)
        await asyncio.sleep(PROVIDER_SYNC_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    bootstrap()
    monitor_task = asyncio.create_task(mesh_monitor_loop()) if MESH_ENABLED else None
    provider_task = asyncio.create_task(provider_sync_loop()) if PROVIDER_SYNC_ENABLED else None
    try:
        yield
    finally:
        if monitor_task:
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass
        if provider_task:
            provider_task.cancel()
            try:
                await provider_task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="Server Admin App", version="0.2.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.middleware("http")
async def add_cache_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.get("/")
def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/api/health")
def health(conn=Depends(db)):
    conn.execute("select 1").fetchone()
    return {"ok": True, "version": app.version}


@app.get("/api/services/status")
async def services_status(user=Depends(current_user)):
    system_tasks = [asyncio.to_thread(_check_systemd_service, item) for item in SYSTEMD_CHECKS]
    proxy_items = await asyncio.to_thread(_discover_nginx_proxy_apps)
    proxy_tasks = [asyncio.to_thread(_check_proxy_app, item) for item in proxy_items]
    systems, applications = await asyncio.gather(
        asyncio.gather(*system_tasks),
        asyncio.gather(*proxy_tasks) if proxy_tasks else asyncio.sleep(0, result=[]),
    )
    return {
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "services": sorted(systems, key=lambda item: (item["category"], item["name"])),
        "applications": sorted(applications, key=lambda item: item["name"]),
    }


@app.get("/api/mesh/health")
def mesh_health(hours: int = 3, user=Depends(current_user), conn=Depends(db)):
    return mesh_health_history(conn, hours)


@app.get("/api/dashboard")
def dashboard(user=Depends(current_user), conn=Depends(db)):
    return dashboard_snapshot(conn)


@app.get("/api/me")
def me(request: Request, conn=Depends(db)):
    token = request.cookies.get(SESSION_COOKIE, "")
    user_id = session_codec().verify(token)
    if not user_id:
        return {"authenticated": False}
    row = conn.execute("select id, username from users where id = ?", (user_id,)).fetchone()
    if not row:
        return {"authenticated": False}
    return {**dict(row), "authenticated": True}


@app.post("/api/login")
def login(payload: LoginRequest, response: Response, conn=Depends(db)):
    row = conn.execute(
        "select id, username, password_hash, password_salt from users where username = ?",
        (payload.username,),
    ).fetchone()
    if not row or not verify_password(payload.password, row["password_hash"], row["password_salt"]):
        raise HTTPException(status_code=401, detail="invalid credentials")
    response.set_cookie(
        SESSION_COOKIE,
        session_codec().sign(row["id"]),
        httponly=True,
        secure=os.environ.get("OPS_COOKIE_SECURE", "0") == "1",
        samesite="lax",
        max_age=60 * 60 * 12,
    )
    audit(conn, row["username"], "login", "user", row["id"])
    return {"ok": True, "username": row["username"]}


@app.post("/api/logout")
def logout(response: Response, user=Depends(current_user)):
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@app.get("/api/servers")
def list_servers(user=Depends(current_user), conn=Depends(db)):
    rows = conn.execute("select * from servers order by is_starred desc, updated_at desc, id desc").fetchall()
    return [server_response(conn, row) for row in rows]


@app.post("/api/servers")
def create_server(payload: ServerPayload, user=Depends(current_user), conn=Depends(db), c=Depends(cipher)):
    cur = conn.execute(
        """
        insert into servers(name, hostname, ipv4, ipv6, provider, region, login_user, auth_type,
          ssh_host, ssh_port, ssh_key_path, ssh_local_key_path, ssh_windows_key_path, ssh_options, panel_url, panel_username,
          panel_password_encrypted, service_code, is_starred, is_retired, heartbeat_enabled, heartbeat_port,
          tags_json, notes, credential_encrypted)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload.name,
            payload.hostname,
            payload.ipv4 or "",
            payload.ipv6 or "",
            payload.provider or "",
            payload.region or "",
            payload.login_user,
            payload.auth_type,
            payload.ssh_host or "",
            payload.ssh_port,
            payload.ssh_key_path or "",
            payload.ssh_local_key_path or "",
            payload.ssh_windows_key_path or "",
            payload.ssh_options or "",
            payload.panel_url or "",
            payload.panel_username or "",
            c.encrypt(payload.panel_password),
            payload.service_code or "",
            1 if payload.is_starred else 0,
            1 if payload.is_retired else 0,
            1 if payload.heartbeat_enabled else 0,
            payload.heartbeat_port,
            json.dumps(payload.tags),
            payload.notes or "",
            c.encrypt(payload.credential),
        ),
    )
    server_id = cur.lastrowid
    save_provider_access(conn, server_id, payload, c)
    conn.commit()
    audit(conn, user["username"], "create", "server", server_id, payload.hostname)
    row = conn.execute("select * from servers where id = ?", (server_id,)).fetchone()
    return server_response(conn, row)


@app.put("/api/servers/{server_id}")
def update_server(server_id: int, payload: ServerPayload, user=Depends(current_user), conn=Depends(db), c=Depends(cipher)):
    row = conn.execute("select * from servers where id = ?", (server_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="server not found")
    credential = row["credential_encrypted"] if payload.credential == "" else c.encrypt(payload.credential)
    panel_password = row["panel_password_encrypted"] if payload.panel_password == "" else c.encrypt(payload.panel_password)
    conn.execute(
        """
        update servers set name = ?, hostname = ?, ipv4 = ?, ipv6 = ?, provider = ?, region = ?,
          login_user = ?, auth_type = ?, ssh_host = ?, ssh_port = ?, ssh_key_path = ?, ssh_local_key_path = ?,
          ssh_windows_key_path = ?, ssh_options = ?, panel_url = ?, panel_username = ?, panel_password_encrypted = ?,
          service_code = ?, is_starred = ?, is_retired = ?, heartbeat_enabled = ?, heartbeat_port = ?, tags_json = ?, notes = ?,
          credential_encrypted = ?, updated_at = current_timestamp
        where id = ?
        """,
        (
            payload.name,
            payload.hostname,
            payload.ipv4 or "",
            payload.ipv6 or "",
            payload.provider or "",
            payload.region or "",
            payload.login_user,
            payload.auth_type,
            payload.ssh_host or "",
            payload.ssh_port,
            payload.ssh_key_path or "",
            payload.ssh_local_key_path or "",
            payload.ssh_windows_key_path or "",
            payload.ssh_options or "",
            payload.panel_url or "",
            payload.panel_username or "",
            panel_password,
            payload.service_code or "",
            1 if payload.is_starred else 0,
            1 if payload.is_retired else 0,
            1 if payload.heartbeat_enabled else 0,
            payload.heartbeat_port,
            json.dumps(payload.tags),
            payload.notes or "",
            credential,
            server_id,
        ),
    )
    save_provider_access(conn, server_id, payload, c)
    conn.commit()
    audit(conn, user["username"], "update", "server", server_id, payload.hostname)
    row = conn.execute("select * from servers where id = ?", (server_id,)).fetchone()
    return server_response(conn, row)


@app.delete("/api/servers/{server_id}")
def delete_server(server_id: int, user=Depends(current_user), conn=Depends(db)):
    row = conn.execute("select hostname from servers where id = ?", (server_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="server not found")
    conn.execute("delete from servers where id = ?", (server_id,))
    conn.commit()
    audit(conn, user["username"], "delete", "server", server_id, row["hostname"])
    return {"ok": True}


@app.put("/api/servers/{server_id}/subscription-usage")
def save_subscription_usage(
    server_id: int,
    payload: SubscriptionUsagePayload,
    user=Depends(current_user),
    conn=Depends(db),
):
    if payload.period_end < payload.period_start:
        raise HTTPException(status_code=422, detail="period_end must not precede period_start")
    row = conn.execute("select id from servers where id = ? and is_retired = 0", (server_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="active server not found")
    decimal_gb = 1_000_000_000
    used_bytes = round(payload.used_gb * decimal_gb)
    quota_bytes = round(payload.quota_gb * decimal_gb)
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
            payload.period_start,
            payload.period_end,
            used_bytes,
            quota_bytes,
            payload.source_label,
            str(payload.source_url) if payload.source_url else "",
            user["username"],
        ),
    )
    conn.commit()
    audit(
        conn,
        user["username"],
        "update_subscription_usage",
        "server",
        server_id,
        f"{payload.used_gb:g}/{payload.quota_gb:g} GB via {payload.source_label}",
    )
    return {"ok": True, "dashboard": dashboard_snapshot(conn)}


@app.post("/api/servers/{server_id}/provider-sync")
def sync_server_provider_usage(
    server_id: int,
    user=Depends(current_user),
    conn=Depends(db),
    c=Depends(cipher),
):
    try:
        result = sync_provider_usage(conn, c, server_id)
    except ProviderSyncError as exc:
        audit(conn, user["username"], "provider_sync_failed", "server", server_id, str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    audit(
        conn,
        user["username"],
        "provider_sync",
        "server",
        server_id,
        f"{result['used_bytes']}/{result['quota_bytes']} bytes via {result['connector']}",
    )
    row = conn.execute("select * from servers where id = ?", (server_id,)).fetchone()
    return {
        "ok": True,
        "usage": result,
        "server": server_response(conn, row),
        "dashboard": dashboard_snapshot(conn),
    }


@app.post("/api/servers/{server_id}/check")
def check_server(server_id: int, user=Depends(current_user), conn=Depends(db)):
    row = conn.execute("select hostname, ipv4, ssh_host, ssh_port, is_retired from servers where id = ?", (server_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="server not found")
    ensure_active_server(row)
    target = ssh_target(row)
    port = int(row["ssh_port"] or 22)
    started = time.perf_counter()
    status = "offline"
    error = ""
    try:
        with socket.create_connection((target, port), timeout=3):
            status = "online"
    except OSError as exc:
        error = str(exc)
    latency = int((time.perf_counter() - started) * 1000)
    conn.execute(
        "update servers set last_status = ?, last_latency_ms = ?, last_checked_at = current_timestamp where id = ?",
        (status, latency, server_id),
    )
    conn.commit()
    audit(conn, user["username"], "check", "server", server_id, f"{status} {latency}ms")
    return {"status": status, "latency_ms": latency, "error": error}


def run_and_store_inspection(server_id: int, user, conn, c, include_quality: bool = False):
    row = conn.execute("select * from servers where id = ?", (server_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="server not found")
    ensure_active_server(row)
    password = c.decrypt(row["credential_encrypted"]) if row["auth_type"] == "password" else ""
    status, summary, report, apps, services = run_server_inspection(row, password)
    audit_action = "inspect"
    if include_quality:
        mesh_evidence = mesh_quality_evidence(conn, server_id)
        heartbeat_probe = probe_heartbeat_agent(row)
        quality_report = build_quality_report(row, status, report, services, mesh_evidence, heartbeat_probe)
        report["quality_report"] = quality_report
        report["health_score"] = quality_report["score"]
        if report.get("error"):
            report["collection_error"] = report.pop("error")
        status = quality_report["status"]
        summary = quality_report["summary"]
        audit_action = "quality_check"
    actual_hostname = report.get("hostname") or row["hostname"]
    conn.execute(
        """
        update servers set hostname = ?, config_status = ?, config_summary = ?, config_report_json = ?,
          installed_apps_json = ?, services_json = ?, last_config_check_at = current_timestamp,
          updated_at = current_timestamp
        where id = ?
        """,
        (actual_hostname, status, summary, json.dumps(report), json.dumps(apps), json.dumps(services), server_id),
    )
    conn.commit()
    audit(conn, user["username"], audit_action, "server", server_id, summary)
    row = conn.execute("select * from servers where id = ?", (server_id,)).fetchone()
    return server_response(conn, row)


@app.post("/api/servers/{server_id}/inspect")
def inspect_server(server_id: int, user=Depends(current_user), conn=Depends(db), c=Depends(cipher)):
    return run_and_store_inspection(server_id, user, conn, c)


@app.post("/api/servers/{server_id}/quality-check")
def quality_check_server(server_id: int, user=Depends(current_user), conn=Depends(db), c=Depends(cipher)):
    return run_and_store_inspection(server_id, user, conn, c, include_quality=True)


@app.get("/api/servers/{server_id}/credential")
def reveal_credential(server_id: int, user=Depends(current_user), conn=Depends(db), c=Depends(cipher)):
    row = conn.execute("select credential_encrypted from servers where id = ?", (server_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="server not found")
    audit(conn, user["username"], "reveal_credential", "server", server_id)
    return {"credential": c.decrypt(row["credential_encrypted"])}


@app.get("/api/servers/{server_id}/connection-secret")
def reveal_connection_secret(server_id: int, user=Depends(current_user), conn=Depends(db), c=Depends(cipher)):
    row = conn.execute(
        "select credential_encrypted, panel_password_encrypted from servers where id = ?",
        (server_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="server not found")
    provider_row = conn.execute(
        "select password_encrypted from server_provider_access where server_id = ?",
        (server_id,),
    ).fetchone()
    audit(conn, user["username"], "reveal_connection_secret", "server", server_id)
    return {
        "credential": c.decrypt(row["credential_encrypted"]),
        "panel_password": c.decrypt(row["panel_password_encrypted"]),
        "provider_password": c.decrypt(provider_row["password_encrypted"]) if provider_row else "",
    }


@app.get("/api/audit")
def audit_events(user=Depends(current_user), conn=Depends(db)):
    rows = conn.execute(
        "select actor, action, target_type, target_id, detail, created_at from audit_events order by id desc limit 50"
    ).fetchall()
    return [dict(row) for row in rows]
