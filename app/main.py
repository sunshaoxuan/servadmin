from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import shlex
import socket
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import paramiko

from .db import connect, init_db, row_to_server
from .security import CredentialCipher, SessionCodec, hash_password, verify_password


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("OPS_DB_PATH", BASE_DIR.parent / "data" / "ops.sqlite3"))
APP_SECRET = os.environ.get("OPS_APP_SECRET", "dev-only-change-me")
CREDENTIAL_KEY = os.environ.get("OPS_CREDENTIAL_KEY", CredentialCipher.generate_key())
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
    is_starred: bool = False
    tags: List[str] = Field(default_factory=list)
    notes: Optional[str] = ""
    credential: Optional[str] = ""


def db():
    conn = connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


def cipher() -> CredentialCipher:
    return CredentialCipher(CREDENTIAL_KEY)


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
echo "__SECTION__hostname"
printf '%s\n' "$(hostname 2>/dev/null || true)"
printf '%s\n' "$(hostname -f 2>/dev/null || true)"
echo "__SECTION__os"
(cat /etc/os-release 2>/dev/null || true) | sed -n '1,12p'
echo "__SECTION__kernel"
uname -a 2>/dev/null || true
echo "__SECTION__cpu"
(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo "") | head -1
echo "__SECTION__memory"
(awk '/MemTotal/ {print $2 " kB"}' /proc/meminfo 2>/dev/null || sysctl -n hw.memsize 2>/dev/null || echo "") | head -1
echo "__SECTION__disk"
df -hP / 2>/dev/null | tail -1 || true
echo "__SECTION__apps"
if command -v dpkg-query >/dev/null 2>&1; then
  dpkg-query -W -f='${binary:Package}\t${Version}\n' 2>/dev/null | head -80
elif command -v rpm >/dev/null 2>&1; then
  rpm -qa --qf '%{NAME}\t%{VERSION}-%{RELEASE}\n' 2>/dev/null | head -80
elif command -v brew >/dev/null 2>&1; then
  brew list --versions 2>/dev/null | head -80
fi
echo "__SECTION__services"
if command -v systemctl >/dev/null 2>&1; then
  systemctl list-units --type=service --state=running --no-legend --no-pager 2>/dev/null | awk '{print $1 "\t" $4 "\t" $5}' | head -80
elif command -v service >/dev/null 2>&1; then
  service --status-all 2>/dev/null | head -80
fi
echo "__SECTION__ports"
if command -v ss >/dev/null 2>&1; then
  ss -lntup 2>/dev/null | tail -n +2 | head -120
elif command -v netstat >/dev/null 2>&1; then
  netstat -lntup 2>/dev/null | tail -n +3 | head -120
fi
"""


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
    report = {
        "hostname": hostname_lines[0] if hostname_lines else "",
        "hostname_fqdn": hostname_lines[1] if len(hostname_lines) > 1 else "",
        "os": sections.get("os", []),
        "kernel": "\n".join(sections.get("kernel", [])).strip(),
        "cpu_count": "\n".join(sections.get("cpu", [])).strip(),
        "memory": "\n".join(sections.get("memory", [])).strip(),
        "disk_root": "\n".join(sections.get("disk", [])).strip(),
    }
    status = "ok" if report["kernel"] else "warning"
    summary = f"{len(apps)} 个应用，{len(services)} 个服务"
    return status, summary, report, apps, services


def fallback_local_config_report() -> tuple[str, str, dict[str, Any], list[dict[str, str]], list[dict[str, Any]]]:
    report = {
        "hostname": socket.gethostname(),
        "hostname_fqdn": socket.getfqdn(),
        "os": [platform.platform()],
        "kernel": platform.platform(),
        "cpu_count": str(os.cpu_count() or ""),
        "memory": "",
        "disk_root": "",
    }
    return "warning", "0 个应用，0 个服务", report, [], []


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
        _stdin, stdout, stderr = client.exec_command(INSPECTION_SCRIPT, timeout=20)
        output = stdout.read().decode("utf-8", errors="replace")
        error = stderr.read().decode("utf-8", errors="replace")
        exit_code = stdout.channel.recv_exit_status()
    except Exception as exc:
        detail = str(exc)[:500]
        return "error", detail, {"error": detail}, [], []
    finally:
        client.close()
    if exit_code != 0:
        detail = (error or output or "配置检查失败").strip()[:500]
        return "error", detail, {"error": detail}, [], []
    return build_config_report(output)


def run_server_inspection(row, password: str = "") -> tuple[str, str, dict[str, Any], list[dict[str, str]], list[dict[str, Any]]]:
    target = ssh_target(row)
    is_local = target in {"localhost", "127.0.0.1", "::1"} or row["hostname"] in {"localhost", "127.0.0.1", "::1"}
    if not is_local and row["auth_type"] == "password":
        return run_paramiko_inspection(row, password)
    command = local_inspection_command(INSPECTION_SCRIPT) if is_local else ssh_command(row, INSPECTION_SCRIPT)
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=20)
    except FileNotFoundError as exc:
        if is_local:
            return fallback_local_config_report()
        detail = str(exc)[:500]
        return "error", detail, {"error": detail}, [], []
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "inspection failed").strip()[:500]
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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    bootstrap()
    yield


app = FastAPI(title="Server Admin App", version="0.1.1", lifespan=lifespan)
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
    return [row_to_server(row) for row in rows]


@app.post("/api/servers")
def create_server(payload: ServerPayload, user=Depends(current_user), conn=Depends(db), c=Depends(cipher)):
    cur = conn.execute(
        """
        insert into servers(name, hostname, ipv4, ipv6, provider, region, login_user, auth_type,
          ssh_host, ssh_port, ssh_key_path, ssh_local_key_path, ssh_windows_key_path, ssh_options, panel_url, panel_username,
          panel_password_encrypted, service_code, is_starred, tags_json, notes, credential_encrypted)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            json.dumps(payload.tags),
            payload.notes or "",
            c.encrypt(payload.credential),
        ),
    )
    conn.commit()
    server_id = cur.lastrowid
    audit(conn, user["username"], "create", "server", server_id, payload.hostname)
    row = conn.execute("select * from servers where id = ?", (server_id,)).fetchone()
    return row_to_server(row)


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
          service_code = ?, is_starred = ?, tags_json = ?, notes = ?,
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
            json.dumps(payload.tags),
            payload.notes or "",
            credential,
            server_id,
        ),
    )
    conn.commit()
    audit(conn, user["username"], "update", "server", server_id, payload.hostname)
    row = conn.execute("select * from servers where id = ?", (server_id,)).fetchone()
    return row_to_server(row)


@app.delete("/api/servers/{server_id}")
def delete_server(server_id: int, user=Depends(current_user), conn=Depends(db)):
    row = conn.execute("select hostname from servers where id = ?", (server_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="server not found")
    conn.execute("delete from servers where id = ?", (server_id,))
    conn.commit()
    audit(conn, user["username"], "delete", "server", server_id, row["hostname"])
    return {"ok": True}


@app.post("/api/servers/{server_id}/check")
def check_server(server_id: int, user=Depends(current_user), conn=Depends(db)):
    row = conn.execute("select hostname, ipv4, ssh_host, ssh_port from servers where id = ?", (server_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="server not found")
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


@app.post("/api/servers/{server_id}/inspect")
def inspect_server(server_id: int, user=Depends(current_user), conn=Depends(db), c=Depends(cipher)):
    row = conn.execute("select * from servers where id = ?", (server_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="server not found")
    password = c.decrypt(row["credential_encrypted"]) if row["auth_type"] == "password" else ""
    status, summary, report, apps, services = run_server_inspection(row, password)
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
    audit(conn, user["username"], "inspect", "server", server_id, summary)
    row = conn.execute("select * from servers where id = ?", (server_id,)).fetchone()
    return row_to_server(row)


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
    audit(conn, user["username"], "reveal_connection_secret", "server", server_id)
    return {
        "credential": c.decrypt(row["credential_encrypted"]),
        "panel_password": c.decrypt(row["panel_password_encrypted"]),
    }


@app.get("/api/audit")
def audit_events(user=Depends(current_user), conn=Depends(db)):
    rows = conn.execute(
        "select actor, action, target_type, target_id, detail, created_at from audit_events order by id desc limit 50"
    ).fetchall()
    return [dict(row) for row in rows]
