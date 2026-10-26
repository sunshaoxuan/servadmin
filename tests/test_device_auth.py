"""All credentials in this suite are synthetic and local-only."""
import itertools
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import device_auth
from app.db import connect, init_db


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_ADMIN_PASSWORD", "test-only-password")
    monkeypatch.setenv("OPS_DEVICE_AUTH_ENABLED", "1")
    monkeypatch.setenv("OPS_PROVIDER_SYNC_ENABLED", "0")
    monkeypatch.setenv("OPS_MESH_ENABLED", "0")
    monkeypatch.setenv("OPS_COOKIE_SECURE", "0")
    monkeypatch.setenv("OPS_DEVICE_COOKIE_PATH", "/")
    from app import main
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "fake.sqlite3")
    monkeypatch.setattr(main, "APP_SECRET", "test-only-signing-secret")
    monkeypatch.setattr(main, "CREDENTIAL_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    sequence = itertools.count()
    monkeypatch.setattr(device_auth.secrets, "token_urlsafe", lambda n: f"fake-only-{n}-{next(sequence)}")
    main.bootstrap()
    return main, TestClient(main.app)


def login(client, remember=True, label="测试浏览器"):
    return client.post("/api/login", json={"username": "admin", "password": "test-only-password",
                                          "remember_device": remember, "device_label": label})


def remembered(client):
    return next(cookie.value for cookie in client.cookies.jar if cookie.name.startswith("ops_remember_"))


def test_opt_in_disabled_and_legacy_compatibility(setup, monkeypatch):
    main, client = setup
    monkeypatch.setenv("OPS_DEVICE_AUTH_ENABLED", "0")
    assert login(client).status_code == 409
    assert login(client, False).status_code == 200
    assert main.DEVICE_COOKIE not in client.cookies
    assert main.SESSION_COOKIE in client.cookies
    assert client.get("/api/me").json()["authenticated"]
    assert client.post("/api/auth/refresh").status_code == 404


def test_hashes_only_cookie_flags_and_metadata(setup, monkeypatch):
    main, client = setup
    monkeypatch.setenv("OPS_COOKIE_SECURE", "1")
    client.base_url = "https://testserver"
    response = login(client)
    assert response.status_code == 200
    cookies = response.headers.get_list("set-cookie")
    assert all("HttpOnly" in c and "Secure" in c and "SameSite=strict" in c for c in cookies[:2])
    raw = remembered(client)
    conn = connect(main.DB_PATH)
    row = conn.execute("select * from remembered_devices").fetchone()
    assert f'Max-Age={device_auth.month_later(row["created_at"]) - row["created_at"]}' in cookies[0]
    assert row["token_hash"] == device_auth.split_token(raw)[1]
    assert raw.split(".")[1] not in str(dict(row))
    conn.close()
    metadata = client.get("/api/auth/devices").json()
    assert metadata[0]["current"]
    assert "hash" not in str(metadata) and raw not in str(metadata)
    assert raw not in client.get("/api/audit").text


def test_restore_rotates_and_requires_password_for_sensitive_operations(setup):
    main, client = setup
    assert login(client).status_code == 200
    old = remembered(client)
    client.cookies.delete(main.DEVICE_SESSION_COOKIE)
    assert not client.get("/api/me").json()["authenticated"]
    assert client.post("/api/auth/refresh").status_code == 200
    assert old != remembered(client)
    assert client.get("/api/servers").status_code == 200
    assert client.get("/api/servers/1/credential").status_code == 403
    assert client.get("/api/servers/1/connection-secret").status_code == 403
    assert client.post("/api/servers/1/inspect").status_code == 403
    # Ordinary non-sensitive operations do not require the password again.
    assert client.post("/api/servers/1/check").status_code == 404
    assert client.post("/api/servers/1/provider-sync").status_code == 502  # missing fixture server; no connector runs
    old_session = client.cookies.get(main.DEVICE_SESSION_COOKIE)
    assert client.post("/api/auth/reauthenticate", json={"password": "wrong"}).status_code == 401
    assert client.post("/api/auth/reauthenticate", json={"password": "test-only-password"}).status_code == 200
    assert client.cookies.get(main.DEVICE_SESSION_COOKIE) != old_session
    assert client.get("/api/servers/1/credential").status_code == 404


def test_month_and_short_session_expiration(setup, monkeypatch):
    main, client = setup
    login(client)
    conn = connect(main.DB_PATH)
    row = conn.execute("select * from remembered_devices").fetchone()
    conn.execute("update device_sessions set expires_at=0")
    conn.commit()
    assert client.get("/api/servers").status_code == 401
    token = remembered(client)
    monkeypatch.setattr(device_auth.time, "time", lambda: row["expires_at"])
    with pytest.raises(HTTPException) as error:
        device_auth.refresh(conn, token)
    assert error.value.status_code == 401
    monkeypatch.setattr(device_auth.time, "time", lambda: row["expires_at"])
    assert device_auth.active_device(conn, row["id"], row["expires_at"]) is None
    conn.close()


def test_concurrent_refresh_only_one_winner_and_replay_revokes(setup, monkeypatch):
    main, client = setup
    login(client)
    token = remembered(client)
    def attempt():
        conn = connect(main.DB_PATH)
        try:
            return device_auth.refresh(conn, token)
        except HTTPException as error:
            return error.status_code
        finally:
            conn.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sum(isinstance(result, tuple) for result in results) == 1
    assert 409 in results
    current = next(result for result in results if isinstance(result, tuple))
    conn = connect(main.DB_PATH)
    rotated = conn.execute("select rotated_at from remembered_devices").fetchone()[0]
    monkeypatch.setattr(device_auth.time, "time", lambda: rotated + device_auth.ROTATION_GRACE + 1)
    with pytest.raises(HTTPException):
        device_auth.refresh(conn, token)
    assert device_auth.authenticate(conn, current[1]) is None
    conn.close()


def test_device_independent_revoke_and_no_legacy_fallback(setup):
    main, first = setup
    second = TestClient(main.app)
    login(first, label="A")
    login(second, label="B")
    first_id = next(row["id"] for row in first.get("/api/auth/devices").json() if row["current"])
    assert second.delete(f"/api/auth/devices/{first_id}").status_code == 200
    # Even a valid old legacy cookie must not bypass remembered-device revocation.
    first.cookies.set(main.SESSION_COOKIE, main.session_codec().sign(1))
    assert first.get("/api/servers").status_code == 401
    assert first.post("/api/auth/refresh").status_code == 401
    assert second.get("/api/servers").status_code == 200


def test_logout_after_short_session_expiry_forgets_device(setup):
    main, client = setup
    login(client)
    token = remembered(client)
    client.cookies.delete(main.DEVICE_SESSION_COOKIE)
    assert client.post("/api/logout").status_code == 200
    assert not any(c.name.startswith(main.DEVICE_COOKIE) for c in client.cookies.jar)
    conn = connect(main.DB_PATH)
    with pytest.raises(HTTPException):
        device_auth.refresh(conn, token)
    conn.close()
    assert client.post("/api/logout").status_code == 200


def test_password_change_and_feature_kill_switch(setup, monkeypatch):
    main, client = setup
    login(client)
    monkeypatch.setenv("OPS_DEVICE_AUTH_ENABLED", "0")
    assert client.get("/api/servers").status_code == 401
    monkeypatch.setenv("OPS_DEVICE_AUTH_ENABLED", "1")
    conn = connect(main.DB_PATH)
    conn.execute("update users set password_hash='changed-fake-hash'")
    conn.commit()
    conn.close()
    assert client.get("/api/servers").status_code == 401
    assert client.post("/api/auth/refresh").status_code == 401


def test_independent_browser_context_does_not_inherit_credentials(setup):
    main, client = setup
    login(client)
    isolated = TestClient(main.app)
    assert not isolated.get("/api/me").json()["authenticated"]
    assert isolated.post("/api/auth/refresh").status_code == 401


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_browser_csrf_and_sibling_subdomain_rejected(setup, site):
    _, client = setup
    assert login(client).status_code == 200
    assert client.post("/api/logout", headers={"sec-fetch-site": site}).status_code == 403
    assert client.get("/api/me").json()["authenticated"]


def test_device_limit_invalid_tokens_and_additive_migration(setup):
    main, client = setup
    for _ in range(10):
        assert login(TestClient(main.app)).status_code == 200
    assert login(TestClient(main.app)).status_code == 409
    conn = connect(main.DB_PATH)
    init_db(conn)
    assert conn.execute("select count(*) from schema_migrations where version=9").fetchone()[0] == 1
    assert conn.execute("select count(*) from users").fetchone()[0] == 1
    for token in ("", "x", "x.y.z", "x.wrong", "x." + "a" * 300):
        with pytest.raises(HTTPException):
            device_auth.refresh(conn, token)
    conn.close()


def test_refresh_revoke_race_cannot_resurrect_browser(setup):
    main, client = setup
    login(client)
    token = remembered(client)
    selector = device_auth.split_token(token)[0]
    def work(revoking):
        conn = connect(main.DB_PATH)
        try:
            return device_auth.revoke(conn, 1, selector) if revoking else device_auth.refresh(conn, token)
        except HTTPException as error:
            return error.status_code
        finally:
            conn.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(work, [False, True]))
    conn = connect(main.DB_PATH)
    assert device_auth.authenticate(conn, client.cookies.get(main.DEVICE_SESSION_COOKIE)) is None
    for result in results:
        if isinstance(result, tuple):
            assert device_auth.authenticate(conn, result[1]) is None
    conn.close()


def test_fresh_auth_window_and_revoke_ownership(setup, monkeypatch):
    main, client = setup
    login(client)
    conn = connect(main.DB_PATH)
    row = conn.execute("select * from remembered_devices").fetchone()
    assert not device_auth.revoke(conn, 999, row["id"])
    assert client.get("/api/servers/1/credential").status_code == 404
    monkeypatch.setattr(device_auth.time, "time", lambda: row["created_at"] + device_auth.FRESH_TTL)
    assert client.get("/api/servers/1/credential").status_code == 403
    assert client.delete(f'/api/auth/devices/{row["id"]}').status_code == 403
    conn.close()


def test_production_cookie_path_clear_matches_creation(setup, monkeypatch):
    main, client = setup
    monkeypatch.setenv("OPS_DEVICE_COOKIE_PATH", "/server-desk/")
    response = login(client)
    assert all("Path=/server-desk/" in c for c in response.headers.get_list("set-cookie")[:2])
    response = client.post("/api/logout")
    managed = [c for c in response.headers.get_list("set-cookie") if c.startswith((main.DEVICE_COOKIE, main.DEVICE_SESSION_COOKIE))]
    assert len(managed) == 2 and all("Path=/server-desk/" in c and "Max-Age=0" in c for c in managed)


def stamp(iso):
    return int(datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp())


@pytest.mark.parametrize("start,expected", [
    ("2026-01-31T12:34:56", "2026-02-28T12:34:56"),
    ("2028-01-31T12:34:56", "2028-02-29T12:34:56"),
    ("2028-02-29T23:59:59", "2028-03-29T23:59:59"),
    ("2026-12-31T00:00:00", "2027-01-31T00:00:00"),
    ("2026-03-31T01:02:03", "2026-04-30T01:02:03"),
])
def test_utc_calendar_month_boundaries(start, expected):
    assert device_auth.month_later(stamp(start)) == stamp(expected)


def test_every_authenticated_access_renews_server_and_browser_not_static(setup, monkeypatch):
    main, client = setup
    login(client)
    conn = connect(main.DB_PATH)
    initial = conn.execute("select * from remembered_devices").fetchone()
    for seconds in (1, 2, 3):
        now = initial["created_at"] + seconds
        monkeypatch.setattr(device_auth.time, "time", lambda: now)
        response = client.get("/api/servers")
        row = conn.execute("select * from remembered_devices").fetchone()
        assert response.status_code == 200
        assert row["expires_at"] == device_auth.month_later(now)
        assert row["last_used_at"] == now
        cookie = next(c for c in response.headers.get_list("set-cookie")
                      if c.startswith("ops_remember_") and "Max-Age=0" not in c)
        assert f'Max-Age={row["expires_at"] - now}' in cookie
        assert "HttpOnly" in cookie and "SameSite=strict" in cookie
    monkeypatch.setattr(device_auth.time, "time", lambda: initial["created_at"] + 60)
    for path in ("/", "/static/app.js", "/api/health", "/not-an-api"):
        assert "set-cookie" not in client.get(path).headers
    assert conn.execute("select expires_at from remembered_devices").fetchone()[0] == row["expires_at"]
    conn.close()


def test_active_browser_survives_many_months_without_absolute_or_seven_day_cap(setup, monkeypatch):
    main, client = setup
    clock = [stamp("2026-01-15T10:00:00")]
    monkeypatch.setattr(device_auth.time, "time", lambda: clock[0])
    assert login(client).status_code == 200
    conn = connect(main.DB_PATH)
    device_id = conn.execute("select id from remembered_devices").fetchone()[0]
    for date in ("2026-01-25T10:00:00", "2026-02-20T10:00:00", "2026-03-15T10:00:00",
                 "2026-04-10T10:00:00", "2026-05-01T10:00:00", "2026-05-30T10:00:00"):
        clock[0] = stamp(date)
        assert client.post("/api/auth/refresh").status_code == 200
        assert client.get("/api/me").json()["authenticated"]
        assert conn.execute("select id,expires_at from remembered_devices").fetchone()["id"] == device_id
        assert conn.execute("select expires_at from remembered_devices").fetchone()[0] == device_auth.month_later(clock[0])
    conn.close()


def test_successful_password_login_renews_same_browser_and_failure_does_not(setup, monkeypatch):
    main, client = setup
    login(client)
    conn = connect(main.DB_PATH)
    initial = conn.execute("select * from remembered_devices").fetchone()
    monkeypatch.setattr(device_auth.time, "time", lambda: initial["created_at"] + 3600)
    assert client.post("/api/login", json={"username": "admin", "password": "wrong",
                                         "remember_device": True}).status_code == 401
    assert conn.execute("select expires_at from remembered_devices").fetchone()[0] == initial["expires_at"]
    assert login(client).status_code == 200
    row = conn.execute("select * from remembered_devices").fetchone()
    assert conn.execute("select count(*) from remembered_devices").fetchone()[0] == 1
    assert row["id"] == initial["id"]
    assert row["expires_at"] == device_auth.month_later(initial["created_at"] + 3600)
    assert row["token_hash"] != initial["token_hash"]
    conn.close()


def test_invalid_revoked_expired_previous_token_access_never_renews(setup, monkeypatch):
    main, client = setup
    login(client)
    old = remembered(client)
    session = client.cookies.get(main.DEVICE_SESSION_COOKIE)
    conn = connect(main.DB_PATH)
    initial = conn.execute("select * from remembered_devices").fetchone()
    monkeypatch.setattr(device_auth.time, "time", lambda: initial["created_at"] + 60)
    assert device_auth.renew_access(conn, session, "invalid") is None
    assert device_auth.renew_access(conn, "invalid", old) is None
    assert conn.execute("select expires_at from remembered_devices").fetchone()[0] == initial["expires_at"]
    current, session, _ = device_auth.refresh(conn, old)
    expiry = conn.execute("select expires_at from remembered_devices").fetchone()[0]
    monkeypatch.setattr(device_auth.time, "time", lambda: initial["created_at"] + 61)
    assert device_auth.renew_access(conn, session, old) is None
    assert conn.execute("select expires_at from remembered_devices").fetchone()[0] == expiry
    device_auth.revoke(conn, 1, initial["id"])
    assert device_auth.renew_access(conn, session, current) is None
    assert conn.execute("select expires_at from remembered_devices").fetchone()[0] == expiry
    conn.close()


def test_delayed_old_response_cannot_replace_rotated_cookie(setup):
    main, client = setup
    login(client)
    stale = client.get("/api/servers")
    old = remembered(client)
    assert client.post("/api/auth/refresh").status_code == 200
    new = remembered(client)
    assert new != old
    client.cookies.extract_cookies(stale)  # deliver an earlier fake response after the rotation
    assert len([c for c in client.cookies.jar if c.name.startswith("ops_remember_")]) == 2
    assert client.get("/api/servers").status_code == 200
    assert remembered(client) == new
    assert len([c for c in client.cookies.jar if c.name.startswith("ops_remember_")]) == 1


def test_parallel_access_and_clock_order_do_not_shorten_expiry(setup, monkeypatch):
    main, client = setup
    login(client)
    token, session = remembered(client), client.cookies.get(main.DEVICE_SESSION_COOKIE)
    conn = connect(main.DB_PATH)
    created = conn.execute("select created_at from remembered_devices").fetchone()[0]
    monkeypatch.setattr(device_auth.time, "time", lambda: created + 120)
    def access(_):
        own = connect(main.DB_PATH)
        try:
            return device_auth.renew_access(own, session, token)
        finally:
            own.close()
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert all(pool.map(access, range(4)))
    expected = device_auth.month_later(created + 120)
    assert conn.execute("select expires_at from remembered_devices").fetchone()[0] == expected
    monkeypatch.setattr(device_auth.time, "time", lambda: created + 60)
    assert device_auth.renew_access(conn, session, token)
    assert conn.execute("select expires_at from remembered_devices").fetchone()[0] == expected
    conn.close()


def test_delayed_same_secret_response_cannot_shorten_cross_month_cookie(setup, monkeypatch):
    _, client = setup
    clock = [stamp("2026-02-28T23:59:59")]
    monkeypatch.setattr(device_auth.time, "time", lambda: clock[0])
    login(client)
    stale = client.get("/api/servers")
    clock[0] = stamp("2026-03-01T00:00:01")
    assert client.get("/api/servers").status_code == 200
    expected = device_auth.month_later(clock[0])
    client.cookies.extract_cookies(stale)
    cookies = [c for c in client.cookies.jar if c.name.startswith("ops_remember_")]
    assert max(c.expires for c in cookies) >= expected
    assert len(cookies) == 2
    assert client.get("/api/servers").status_code == 200
    cookies = [c for c in client.cookies.jar if c.name.startswith("ops_remember_")]
    assert len(cookies) == 1 and cookies[0].expires >= expected


def test_metadata_edits_without_step_up_but_security_edits_require_it(setup):
    main, client = setup
    login(client)
    response = client.post("/api/servers", json={"name": "fake", "hostname": "fake.invalid", "login_user": "test"})
    assert response.status_code == 200
    saved = response.json()
    server_id = saved["id"]
    client.cookies.delete(main.DEVICE_SESSION_COOKIE)
    assert client.post("/api/auth/refresh").status_code == 200
    saved["notes"] = "ordinary metadata edit"
    assert client.put(f"/api/servers/{server_id}", json=saved).status_code == 200
    saved["credential"] = "fake-only-replacement"
    assert client.put(f"/api/servers/{server_id}", json=saved).status_code == 403
    saved.pop("credential")
    saved["ssh_host"] = "different.invalid"
    assert client.put(f"/api/servers/{server_id}", json=saved).status_code == 403
    assert client.delete(f"/api/servers/{server_id}").status_code == 403
