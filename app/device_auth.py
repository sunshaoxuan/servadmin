"""Opt-in remembered browsers. No secrets are persisted or returned in JSON."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from calendar import monthrange
from datetime import datetime, timezone

from fastapi import HTTPException

SESSION_TTL = 12 * 3600
FRESH_TTL = 15 * 60
ROTATION_GRACE = 30


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def month_later(timestamp: int) -> int:
    """One UTC calendar month, clamping a missing day to that month's last day."""
    current = datetime.fromtimestamp(timestamp, timezone.utc)
    year, month = (current.year + 1, 1) if current.month == 12 else (current.year, current.month + 1)
    return int(current.replace(year=year, month=month,
                               day=min(current.day, monthrange(year, month)[1])).timestamp())


def select_token(conn, tokens):
    """Prefer the current generation even if an older HTTP response restored an old cookie."""
    previous = ""
    for token in tokens:
        parsed = split_token(token)
        if not parsed:
            continue
        row = conn.execute("select token_hash,previous_hash from remembered_devices where id=?", (parsed[0],)).fetchone()
        if row and hmac.compare_digest(parsed[1], row["token_hash"]):
            return token
        if row and row["previous_hash"] and hmac.compare_digest(parsed[1], row["previous_hash"]):
            previous = token
    return previous


def password_version(row) -> str:
    return digest(row["password_hash"] + ":" + row["password_salt"])


def split_token(token: str):
    if len(token) > 256 or token.count(".") != 1:
        return None
    selector, secret = token.split(".")
    if not selector or not secret:
        return None
    return selector, digest(secret)


def active_device(conn, device_id: str, now: int):
    row = conn.execute(
        """select d.*, u.username, u.password_hash, u.password_salt
           from remembered_devices d join users u on u.id=d.user_id where d.id=?""",
        (device_id,),
    ).fetchone()
    if (not row or row["revoked_at"] is not None or row["expires_at"] <= now
            or not hmac.compare_digest(row["password_version"], password_version(row))):
        return None
    return row


def new_session(conn, device_id: str, now: int, fresh_at: int = 0):
    token = secrets.token_urlsafe(32)
    conn.execute("delete from device_sessions where expires_at <= ?", (now,))
    conn.execute(
        "insert into device_sessions(token_hash, device_id, expires_at, fresh_at) values (?, ?, ?, ?)",
        (digest(token), device_id, now + SESSION_TTL, fresh_at),
    )
    return token


def enroll(conn, user, label: str, existing_token: str = ""):
    now = int(time.time())
    conn.execute("begin immediate")
    try:
        parsed = split_token(existing_token)
        existing = active_device(conn, parsed[0], now) if parsed else None
        if (existing and existing["user_id"] == user["id"]
                and hmac.compare_digest(parsed[1], existing["token_hash"])):
            secret = secrets.token_urlsafe(32)
            conn.execute(
                """update remembered_devices set label=?,previous_hash=token_hash,token_hash=?,
                   rotated_at=?,last_used_at=?,expires_at=max(expires_at,?) where id=?""",
                (label, digest(secret), now, now, month_later(now), existing["id"]),
            )
            conn.execute("delete from device_sessions where device_id=?", (existing["id"],))
            session = new_session(conn, existing["id"], now, now)
            expires = conn.execute("select expires_at from remembered_devices where id=?", (existing["id"],)).fetchone()[0]
            conn.commit()
            return f'{existing["id"]}.{secret}', session, expires
        count = conn.execute(
            "select count(*) from remembered_devices where user_id=? and revoked_at is null and expires_at>?",
            (user["id"], now),
        ).fetchone()[0]
        if count >= 10:
            raise HTTPException(409, "remembered browser limit reached; revoke an old browser")
        selector, secret = secrets.token_urlsafe(18), secrets.token_urlsafe(32)
        conn.execute(
            """insert into remembered_devices
               (id,user_id,label,token_hash,password_version,created_at,last_used_at,expires_at)
               values (?,?,?,?,?,?,?,?)""",
            (selector, user["id"], label, digest(secret), password_version(user), now, now, month_later(now)),
        )
        session = new_session(conn, selector, now, now)
        conn.commit()
        return f"{selector}.{secret}", session, month_later(now)
    except Exception:
        conn.rollback()
        raise


def authenticate(conn, token: str):
    now = int(time.time())
    row = conn.execute("select * from device_sessions where token_hash=?", (digest(token),)).fetchone()
    if not row or row["expires_at"] <= now:
        return None
    device = active_device(conn, row["device_id"], now)
    if not device:
        return None
    return {"id": device["user_id"], "username": device["username"],
            "device_id": device["id"], "fresh_at": row["fresh_at"]}


def renew_access(conn, session_token: str, remembered_token: str, *, extend: bool = True):
    """Validate both secrets and extend on every authenticated API access, without throttling."""
    now = int(time.time())
    parsed = split_token(remembered_token)
    if not parsed:
        return None
    conn.execute("begin immediate")
    try:
        user = authenticate(conn, session_token)
        device = active_device(conn, parsed[0], now)
        if (not user or not device or user["device_id"] != parsed[0]
                or not hmac.compare_digest(parsed[1], device["token_hash"])):
            conn.rollback()
            return None
        expires = max(device["expires_at"], month_later(now)) if extend else device["expires_at"]
        if extend:
            conn.execute(
                "update remembered_devices set expires_at=?,last_used_at=max(last_used_at,?) where id=?",
                (expires, now, device["id"]),
            )
        conn.commit()
        return user, expires
    except Exception:
        conn.rollback()
        raise


def refresh(conn, token: str):
    parsed = split_token(token)
    if not parsed:
        raise HTTPException(401, "not authenticated")
    selector, candidate = parsed
    now = int(time.time())
    # SQLite serializes the compare-and-rotate with revocation and other refreshes.
    conn.execute("begin immediate")
    try:
        row = active_device(conn, selector, now)
        if not row:
            raise HTTPException(401, "not authenticated")
        if not hmac.compare_digest(candidate, row["token_hash"]):
            if row["previous_hash"] and hmac.compare_digest(candidate, row["previous_hash"]):
                if now <= row["rotated_at"] + ROTATION_GRACE:
                    raise HTTPException(409, "another tab refreshed; reload this page")
                conn.execute("update remembered_devices set revoked_at=? where id=?", (now, selector))
                conn.execute("delete from device_sessions where device_id=?", (selector,))
                conn.commit()
            raise HTTPException(401, "not authenticated")
        secret = secrets.token_urlsafe(32)
        conn.execute(
            """update remembered_devices set previous_hash=token_hash,token_hash=?,rotated_at=?,last_used_at=?,expires_at=max(expires_at,?)
               where id=?""", (digest(secret), now, now, month_later(now), selector),
        )
        # A newly restored session has no recent password authentication.
        session = new_session(conn, selector, now)
        conn.commit()
        return f"{selector}.{secret}", session, max(row["expires_at"], month_later(now))
    except Exception:
        conn.rollback()
        raise


def revoke(conn, user_id: int, device_id: str):
    conn.execute("begin immediate")
    try:
        changed = conn.execute(
            "update remembered_devices set revoked_at=? where id=? and user_id=?",
            (int(time.time()), device_id, user_id),
        ).rowcount
        if changed:
            conn.execute("delete from device_sessions where device_id=?", (device_id,))
        conn.commit()
        return bool(changed)
    except Exception:
        conn.rollback()
        raise
