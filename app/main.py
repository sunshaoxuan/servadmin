from __future__ import annotations

import json
import os
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

from .db import connect, init_db, row_to_server
from .security import CredentialCipher, SessionCodec, hash_password, verify_password


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("OPS_DB_PATH", BASE_DIR.parent / "data" / "ops.sqlite3"))
APP_SECRET = os.environ.get("OPS_APP_SECRET", "dev-only-change-me")
CREDENTIAL_KEY = os.environ.get("OPS_CREDENTIAL_KEY", CredentialCipher.generate_key())
SESSION_COOKIE = "ops_session"


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
    ssh_options: Optional[str] = ""
    service_code: Optional[str] = ""
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
        apps.append({"name": name, "version": version})
    return apps


def parse_services(service_lines: list[str], port_lines: list[str]) -> list[dict[str, Any]]:
    services: dict[str, dict[str, Any]] = {}
    for line in service_lines:
        clean = line.strip()
        if not clean:
            continue
        name = clean.split()[0]
        services[name] = {"name": name, "state": "running", "ports": [], "external": False}
    for line in port_lines:
        clean = line.strip()
        if not clean:
            continue
        external = any(marker in clean for marker in ("0.0.0.0:", "[::]:", ":::"))
        key = clean.split()[-1] if clean.split() else clean
        if key == "*":
            key = clean
        entry = services.setdefault(key, {"name": key, "state": "listening", "ports": [], "external": False})
        entry["ports"].append(clean)
        entry["external"] = entry["external"] or external
    return list(services.values())[:120]


def build_config_report(output: str) -> tuple[str, str, dict[str, Any], list[dict[str, str]], list[dict[str, Any]]]:
    sections = split_sections(output)
    apps = parse_apps(sections.get("apps", []))
    services = parse_services(sections.get("services", []), sections.get("ports", []))
    report = {
        "os": sections.get("os", []),
        "kernel": "\n".join(sections.get("kernel", [])).strip(),
        "cpu_count": "\n".join(sections.get("cpu", [])).strip(),
        "memory": "\n".join(sections.get("memory", [])).strip(),
        "disk_root": "\n".join(sections.get("disk", [])).strip(),
    }
    status = "ok" if report["kernel"] else "warning"
    summary = f"{len(apps)} apps, {len(services)} services"
    return status, summary, report, apps, services


def run_server_inspection(row) -> tuple[str, str, dict[str, Any], list[dict[str, str]], list[dict[str, Any]]]:
    target = ssh_target(row)
    is_local = target in {"localhost", "127.0.0.1", "::1"} or row["hostname"] in {"localhost", "127.0.0.1", "::1"}
    command = local_inspection_command(INSPECTION_SCRIPT) if is_local else ssh_command(row, INSPECTION_SCRIPT)
    completed = subprocess.run(command, capture_output=True, text=True, timeout=20)
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
    rows = conn.execute("select * from servers order by updated_at desc, id desc").fetchall()
    return [row_to_server(row) for row in rows]


@app.post("/api/servers")
def create_server(payload: ServerPayload, user=Depends(current_user), conn=Depends(db), c=Depends(cipher)):
    cur = conn.execute(
        """
        insert into servers(name, hostname, ipv4, ipv6, provider, region, login_user, auth_type,
          ssh_host, ssh_port, ssh_key_path, ssh_options, service_code, tags_json, notes, credential_encrypted)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            payload.ssh_options or "",
            payload.service_code or "",
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
    conn.execute(
        """
        update servers set name = ?, hostname = ?, ipv4 = ?, ipv6 = ?, provider = ?, region = ?,
          login_user = ?, auth_type = ?, ssh_host = ?, ssh_port = ?, ssh_key_path = ?, ssh_options = ?,
          service_code = ?, tags_json = ?, notes = ?,
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
            payload.ssh_options or "",
            payload.service_code or "",
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
    started = time.perf_counter()
    status = "offline"
    error = ""
    try:
        with socket.create_connection((target, 22), timeout=3):
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
def inspect_server(server_id: int, user=Depends(current_user), conn=Depends(db)):
    row = conn.execute("select * from servers where id = ?", (server_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="server not found")
    status, summary, report, apps, services = run_server_inspection(row)
    conn.execute(
        """
        update servers set config_status = ?, config_summary = ?, config_report_json = ?,
          installed_apps_json = ?, services_json = ?, last_config_check_at = current_timestamp,
          updated_at = current_timestamp
        where id = ?
        """,
        (status, summary, json.dumps(report), json.dumps(apps), json.dumps(services), server_id),
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


@app.get("/api/audit")
def audit_events(user=Depends(current_user), conn=Depends(db)):
    rows = conn.execute(
        "select actor, action, target_type, target_id, detail, created_at from audit_events order by id desc limit 50"
    ).fetchall()
    return [dict(row) for row in rows]
