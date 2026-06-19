from __future__ import annotations

import json
import os
import socket
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

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
    ipv4: str | None = ""
    ipv6: str | None = ""
    provider: str | None = ""
    region: str | None = ""
    login_user: str = Field(min_length=1, max_length=80)
    auth_type: str = "password"
    service_code: str | None = ""
    tags: list[str] = Field(default_factory=list)
    notes: str | None = ""
    credential: str | None = ""


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
          service_code, tags_json, notes, credential_encrypted)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
          login_user = ?, auth_type = ?, service_code = ?, tags_json = ?, notes = ?,
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
    row = conn.execute("select hostname, ipv4 from servers where id = ?", (server_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="server not found")
    target = row["ipv4"] or row["hostname"]
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
