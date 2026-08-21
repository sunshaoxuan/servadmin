import os
import asyncio
import json
import socket
import tempfile
import time
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.db import connect, init_db


def make_client():
    db_file = tempfile.NamedTemporaryFile(delete=False)
    db_file.close()
    os.environ["OPS_DB_PATH"] = db_file.name
    os.environ["OPS_APP_SECRET"] = "test-secret"
    os.environ["OPS_CREDENTIAL_KEY"] = Fernet.generate_key().decode("ascii")
    os.environ["OPS_ADMIN_PASSWORD"] = "admin-pass"
    from app import main

    main.DB_PATH = os.environ["OPS_DB_PATH"]
    main.APP_SECRET = os.environ["OPS_APP_SECRET"]
    main.CREDENTIAL_KEY = os.environ["OPS_CREDENTIAL_KEY"]
    main.bootstrap()
    return TestClient(main.app), db_file.name


def test_version_3_clears_existing_agent_billing_meter_for_safe_rollback(tmp_path):
    conn = connect(tmp_path / "migration-v2.sqlite3")
    try:
        conn.execute(
            "create table schema_migrations(version integer primary key, name text not null, applied_at text default current_timestamp)"
        )
        conn.execute("insert into schema_migrations(version, name) values (1, 'server subscription usage')")
        conn.execute("insert into schema_migrations(version, name) values (2, 'automatic monthly traffic meter')")
        conn.execute("create table server_traffic_meter(id integer primary key, measured_rx_bytes integer)")
        conn.execute("create table server_subscription_usage(id integer primary key)")
        conn.execute("create index idx_traffic_meter_server_period on server_traffic_meter(id)")
        conn.execute("insert into server_traffic_meter(measured_rx_bytes) values (123456)")
        conn.commit()

        init_db(conn)

        assert conn.execute("select count(*) from schema_migrations where version = 3").fetchone()[0] == 1
        assert conn.execute("select count(*) from schema_migrations where version = 4").fetchone()[0] == 1
        assert conn.execute("select count(*) from schema_migrations where version = 5").fetchone()[0] == 1
        assert conn.execute("select count(*) from schema_migrations where version = 6").fetchone()[0] == 1
        assert conn.execute("select count(*) from server_traffic_meter").fetchone()[0] == 0
    finally:
        conn.close()


def test_version_6_enables_complete_provider_authentication(tmp_path):
    conn = connect(tmp_path / "migration-v5.sqlite3")
    try:
        init_db(conn)
        conn.execute(
            """
            insert into servers(name, hostname, provider, login_user)
            values ('orange', 'host.orangevps', 'OrangeVPS', 'root')
            """
        )
        server_id = conn.execute("select id from servers").fetchone()[0]
        conn.execute(
            """
            insert into server_provider_access(
              server_id, portal_url, login_username, password_encrypted,
              service_reference, external_server_id, connector_type, sync_enabled
            ) values (?, 'https://portal.orangevps.com/clientarea.php', 'operator',
                      'encrypted', '10807', 'host.orangevps', 'browser', 0)
            """,
            (server_id,),
        )
        conn.execute("delete from schema_migrations where version = 6")
        conn.commit()

        init_db(conn)

        access = conn.execute(
            "select connector_type, sync_enabled from server_provider_access where server_id = ?",
            (server_id,),
        ).fetchone()
        assert access["connector_type"] == "orangevps"
        assert access["sync_enabled"] == 1
        assert conn.execute("select count(*) from schema_migrations where version = 6").fetchone()[0] == 1
        init_db(conn)
        assert conn.execute("select count(*) from schema_migrations where version = 6").fetchone()[0] == 1
    finally:
        conn.close()


def test_git_sync_waits_for_application_health():
    script = (Path(__file__).parents[1] / "scripts" / "server_desk_git_sync.sh").read_text(encoding="utf-8")

    assert "for _attempt in $(seq 1 30)" in script
    assert "health endpoint did not become ready within 30 seconds" in script
    assert "sleep 2" not in script


def test_provider_sync_loop_runs_before_waiting(monkeypatch):
    from app import main

    calls = []

    async def fake_to_thread(function, *args):
        calls.append((function, args))
        return []

    async def stop_after_first_cycle(seconds):
        assert seconds == main.PROVIDER_SYNC_INTERVAL_SECONDS
        raise asyncio.CancelledError

    monkeypatch.setattr(main.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(main.asyncio, "sleep", stop_after_first_cycle)

    try:
        asyncio.run(main.provider_sync_loop())
    except asyncio.CancelledError:
        pass

    assert calls == [(main.sync_all_provider_usage, (main.DB_PATH, main.CREDENTIAL_KEY))]


def test_orange_provider_authentication_selects_and_enables_sync():
    client, db_path = make_client()
    try:
        assert client.post("/api/login", json={"username": "admin", "password": "admin-pass"}).status_code == 200
        response = client.post(
            "/api/servers",
            json={
                "name": "Orange VPS",
                "hostname": "host1782378673.orangevps",
                "provider": "OrangeVPS",
                "login_user": "root",
                "provider_portal_url": "https://portal.orangevps.com/clientarea.php?action=productdetails&id=10807",
                "provider_username": "operator@example.com",
                "provider_password": "provider-secret",
                "provider_service_id": "10807",
                "provider_server_id": "host1782378673.orangevps",
                "provider_connector": "browser",
                "provider_sync_enabled": False,
            },
        )
        assert response.status_code == 200
        assert response.json()["provider_connector"] == "orangevps"
        assert response.json()["provider_sync_enabled"] is True
    finally:
        os.unlink(db_path)


def test_login_create_reveal_and_audit():
    client, db_path = make_client()
    try:
        response = client.post("/api/login", json={"username": "admin", "password": "admin-pass"})
        assert response.status_code == 200
        response = client.post(
            "/api/servers",
            json={
                "name": "Tokyo VPS",
                "hostname": "tk2-221-20446.vs.sakura.ne.jp",
                "ipv4": "160.16.91.200",
                "ipv6": "2001:e42:102:1521:160:16:91:200",
                "provider": "Sakura VPS",
                "region": "Tokyo 2",
                "login_user": "ubuntu",
                "auth_type": "password",
                "ssh_host": "127.0.0.1",
                "ssh_port": 2222,
                "ssh_key_path": "/home/ops/.ssh/id_ed25519",
                "ssh_local_key_path": "/Users/shou/.ssh/id_ed25519",
                "ssh_windows_key_path": "C:\\Users\\shou\\.ssh\\id_ed25519",
                "ssh_options": "-o UserKnownHostsFile=/tmp/known_hosts",
                "panel_url": "http://127.0.0.1:8091/entrance",
                "panel_username": "panel-admin",
                "panel_password": "panel-secret",
                "service_code": "113801369753",
                "provider_portal_url": "https://provider.example/clientarea",
                "provider_username": "ops@example.com",
                "provider_password": "provider-secret",
                "provider_service_id": "service-23492",
                "provider_server_id": "server-baf47198",
                "provider_connector": "browser",
                "provider_sync_enabled": True,
                "is_starred": True,
                "tags": ["tokyo", "prod"],
                "notes": "seeded test host",
                "credential": "secret-value",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert "credential_encrypted" not in body
        assert body["name"] == "Tokyo VPS"
        assert body["ssh_host"] == "127.0.0.1"
        assert body["ssh_port"] == 2222
        assert body["ssh_key_path"] == "/home/ops/.ssh/id_ed25519"
        assert body["ssh_local_key_path"] == "/Users/shou/.ssh/id_ed25519"
        assert body["ssh_windows_key_path"] == "C:\\Users\\shou\\.ssh\\id_ed25519"
        assert body["panel_url"] == "http://127.0.0.1:8091/entrance"
        assert body["panel_username"] == "panel-admin"
        assert body["has_panel_password"] is True
        assert body["provider_portal_url"] == "https://provider.example/clientarea"
        assert body["provider_username"] == "ops@example.com"
        assert body["provider_service_id"] == "service-23492"
        assert body["provider_server_id"] == "server-baf47198"
        assert body["provider_sync_enabled"] is True
        assert body["has_provider_password"] is True
        assert "provider_password" not in body
        assert "password_encrypted" not in body
        assert "panel_password_encrypted" not in body
        assert body["is_starred"] is True
        assert body["is_retired"] is False
        assert body["heartbeat_enabled"] is False
        assert body["heartbeat_port"] == 9108

        response = client.get(f"/api/servers/{body['id']}/credential")
        assert response.status_code == 200
        assert response.json()["credential"] == "secret-value"

        response = client.get(f"/api/servers/{body['id']}/connection-secret")
        assert response.status_code == 200
        assert response.json()["panel_password"] == "panel-secret"
        assert response.json()["provider_password"] == "provider-secret"

        updated = client.put(
            f"/api/servers/{body['id']}",
            json={**body, "credential": "", "panel_password": "", "provider_password": ""},
        )
        assert updated.status_code == 200
        response = client.get(f"/api/servers/{body['id']}/connection-secret")
        assert response.json()["panel_password"] == "panel-secret"
        assert response.json()["provider_password"] == "provider-secret"

        response = client.get("/api/audit")
        assert response.status_code == 200
        actions = [row["action"] for row in response.json()]
        assert "create" in actions
        assert "reveal_credential" in actions
        assert "reveal_connection_secret" in actions
    finally:
        os.unlink(db_path)


def test_starred_servers_are_listed_first():
    client, db_path = make_client()
    try:
        response = client.post("/api/login", json={"username": "admin", "password": "admin-pass"})
        assert response.status_code == 200
        base_payload = {
            "hostname": "host.local",
            "ipv4": "192.0.2.20",
            "ipv6": "",
            "provider": "Test",
            "region": "Test",
            "login_user": "root",
            "auth_type": "key",
            "ssh_host": "192.0.2.20",
            "ssh_port": 22,
            "ssh_key_path": "",
            "ssh_options": "",
            "service_code": "",
            "tags": [],
            "notes": "",
            "credential": "",
        }
        response = client.post("/api/servers", json={**base_payload, "name": "Normal", "is_starred": False})
        assert response.status_code == 200
        response = client.post("/api/servers", json={**base_payload, "name": "Starred", "is_starred": True})
        assert response.status_code == 200

        response = client.get("/api/servers")
        assert response.status_code == 200
        rows = response.json()
        assert rows[0]["name"] == "Starred"
        assert rows[0]["is_starred"] is True
    finally:
        os.unlink(db_path)


def test_requires_login_for_servers():
    client, db_path = make_client()
    try:
        response = client.get("/api/me")
        assert response.status_code == 200
        assert response.json() == {"authenticated": False}

        response = client.get("/api/servers")
        assert response.status_code == 401
    finally:
        os.unlink(db_path)


def test_dashboard_combines_heartbeat_io_space_and_subscription_usage():
    client, db_path = make_client()
    try:
        assert client.post("/api/login", json={"username": "admin", "password": "admin-pass"}).status_code == 200
        created = client.post(
            "/api/servers",
            json={
                "name": "Dashboard Node",
                "hostname": "dashboard-node.local",
                "ipv4": "192.0.2.88",
                "provider": "Riven Cloud",
                "region": "Tokyo",
                "login_user": "root",
                "auth_type": "key",
                "heartbeat_enabled": True,
                "tags": ["prod"],
            },
        ).json()
        now = int(time.time())
        conn = connect(db_path)
        try:
            init_db(conn)
            for sampled_at, self_data in [
                (
                    now - 60,
                    {
                        "observed_at": now - 60,
                        "cpu_total_jiffies": 1000,
                        "cpu_idle_jiffies": 700,
                        "network_rx_bytes": 1_000_000,
                        "network_tx_bytes": 2_000_000,
                        "disk_read_bytes": 3_000_000,
                        "disk_write_bytes": 4_000_000,
                    },
                ),
                (
                    now,
                    {
                        "observed_at": now,
                        "cpu_total_jiffies": 1600,
                        "cpu_idle_jiffies": 1000,
                        "network_rx_bytes": 1_060_000,
                        "network_tx_bytes": 2_120_000,
                        "disk_read_bytes": 3_180_000,
                        "disk_write_bytes": 4_240_000,
                        "load_average": [0.42, 0.3, 0.2],
                        "memory_used_percent": 47.5,
                        "disk_used_percent": 38.2,
                        "disk_total_bytes": 100_000_000_000,
                        "disk_free_bytes": 61_800_000_000,
                    },
                ),
            ]:
                conn.execute(
                    """
                    insert into mesh_health_samples(
                      server_id, sampled_at, network_score, app_score, direct_ok,
                      peer_visible, peer_expected, details_json
                    ) values (?, ?, 100, 100, 1, 2, 2, ?)
                    """,
                    (created["id"], sampled_at, json.dumps({"self": self_data})),
                )
            conn.execute(
                """
                insert into mesh_health_samples(
                  server_id, sampled_at, network_score, app_score, direct_ok,
                  peer_visible, peer_expected, details_json
                ) values (?, ?, 100, 100, 1, 2, 2, ?)
                """,
                (created["id"], now + 1, json.dumps({"self": self_data})),
            )
            conn.commit()
        finally:
            conn.close()

        dashboard = client.get("/api/dashboard")
        assert dashboard.status_code == 200
        node = dashboard.json()["servers"][0]
        assert node["state"] == "online"
        assert node["telemetry"]["cpu_used_percent"] == 50.0
        assert node["telemetry"]["network_rx_bytes_per_second"] == 1000.0
        assert node["telemetry"]["network_tx_bytes_per_second"] == 2000.0
        assert node["telemetry"]["disk_read_bytes_per_second"] == 3000.0
        assert node["telemetry"]["disk_write_bytes_per_second"] == 4000.0
        assert node["subscription"] is None
        assert node["provider_sync"]["status"] == "unconfigured"

        saved = client.put(
            f"/api/servers/{created['id']}/subscription-usage",
            json={
                "period_start": "2026-08-01",
                "period_end": "2026-08-31",
                "used_gb": 128.5,
                "quota_gb": 1024,
                "source_label": "Riven Cloud 管理画面",
                "source_url": "https://example.invalid/server/usage",
                "next_reset_at": "2026-08-24T08:03:17",
                "reset_timezone": "America/Toronto",
            },
        )
        assert saved.status_code == 200
        subscription = saved.json()["dashboard"]["servers"][0]["subscription"]
        assert subscription["used_bytes"] == 128_500_000_000
        assert subscription["quota_bytes"] == 1_024_000_000_000
        assert subscription["used_percent"] == 12.5
        assert subscription["authority"] == "provider"
        assert subscription["next_reset_at"] == "2026-08-24T08:03:17-04:00"
        assert subscription["reset_timezone"] == "America/Toronto"
        assert saved.json()["dashboard"]["summary"]["subscription_ready"] == 1

        precise_saved = client.put(
            f"/api/servers/{created['id']}/subscription-usage",
            json={
                "period_start": "2026-08-01",
                "period_end": "2026-08-31",
                "used_gb": 29.260123456,
                "quota_gb": 8589.934592,
                "source_label": "OrangeVPS 管理画面",
                "source_url": "",
            },
        )
        assert precise_saved.status_code == 200
        precise_subscription = precise_saved.json()["dashboard"]["servers"][0]["subscription"]
        assert precise_subscription["used_bytes"] == 29_260_123_456
        assert precise_subscription["quota_bytes"] == 8_589_934_592_000

        invalid_reset = client.put(
            f"/api/servers/{created['id']}/subscription-usage",
            json={
                "period_start": "2026-08-01",
                "period_end": "2026-08-31",
                "used_gb": 128.5,
                "quota_gb": 1024,
                "source_label": "Riven Cloud 管理画面",
                "next_reset_at": "2026-09-01T00:00:00",
            },
        )
        assert invalid_reset.status_code == 422

        conn = connect(db_path)
        try:
            assert conn.execute("select count(*) from schema_migrations where version = 1").fetchone()[0] == 1
            assert conn.execute("select count(*) from schema_migrations where version = 2").fetchone()[0] == 1
            assert conn.execute("select count(*) from schema_migrations where version = 3").fetchone()[0] == 1
            assert conn.execute("select count(*) from schema_migrations where version = 4").fetchone()[0] == 1
            assert conn.execute("select count(*) from schema_migrations where version = 5").fetchone()[0] == 1
            assert conn.execute("select count(*) from schema_migrations where version = 6").fetchone()[0] == 1
            assert conn.execute("select count(*) from server_traffic_meter").fetchone()[0] == 0
            init_db(conn)
            assert conn.execute("select count(*) from schema_migrations where version = 1").fetchone()[0] == 1
            assert conn.execute("select count(*) from schema_migrations where version = 2").fetchone()[0] == 1
            assert conn.execute("select count(*) from schema_migrations where version = 3").fetchone()[0] == 1
            assert conn.execute("select count(*) from schema_migrations where version = 4").fetchone()[0] == 1
            assert conn.execute("select count(*) from schema_migrations where version = 5").fetchone()[0] == 1
            assert conn.execute("select count(*) from schema_migrations where version = 6").fetchone()[0] == 1
        finally:
            conn.close()
    finally:
        os.unlink(db_path)


def test_dashboard_reports_provider_sync_freshness():
    client, db_path = make_client()
    try:
        assert client.post("/api/login", json={"username": "admin", "password": "admin-pass"}).status_code == 200
        created = client.post(
            "/api/servers",
            json={
                "name": "Provider Sync Node",
                "hostname": "provider-sync.local",
                "provider": "OrangeVPS",
                "login_user": "root",
                "provider_portal_url": "https://portal.orangevps.com/clientarea.php",
                "provider_username": "operator",
                "provider_password": "provider-secret",
                "provider_service_id": "10807",
                "provider_server_id": "provider-sync.local",
            },
        ).json()
        conn = connect(db_path)
        try:
            init_db(conn)
            conn.execute(
                """
                update server_provider_access
                set last_sync_status = 'ok', last_synced_at = current_timestamp
                where server_id = ?
                """,
                (created["id"],),
            )
            conn.commit()
        finally:
            conn.close()

        fresh = client.get("/api/dashboard").json()["servers"][0]["provider_sync"]
        assert fresh["status"] == "fresh"
        assert fresh["fresh"] is True
        assert fresh["age_seconds"] is not None
        assert fresh["age_seconds"] <= 1

        conn = connect(db_path)
        try:
            init_db(conn)
            conn.execute(
                """
                update server_provider_access
                set last_sync_status = 'ok', last_synced_at = datetime('now', '-16 minutes')
                where server_id = ?
                """,
                (created["id"],),
            )
            conn.commit()
        finally:
            conn.close()
        stale = client.get("/api/dashboard").json()["servers"][0]["provider_sync"]
        assert stale["status"] == "stale"
        assert stale["fresh"] is False
        assert stale["age_seconds"] >= 960

        conn = connect(db_path)
        try:
            init_db(conn)
            conn.execute(
                "update server_provider_access set last_sync_status = 'failed' where server_id = ?",
                (created["id"],),
            )
            conn.commit()
        finally:
            conn.close()
        failed = client.get("/api/dashboard").json()["servers"][0]["provider_sync"]
        assert failed["status"] == "failed"
        assert failed["fresh"] is False
    finally:
        os.unlink(db_path)


def test_check_uses_configured_ssh_port(monkeypatch):
    client, db_path = make_client()
    calls = []

    class DummySocket:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_create_connection(address, timeout):
        calls.append((address, timeout))
        return DummySocket()

    monkeypatch.setattr(socket, "create_connection", fake_create_connection)
    try:
        response = client.post("/api/login", json={"username": "admin", "password": "admin-pass"})
        assert response.status_code == 200
        response = client.post(
            "/api/servers",
            json={
                "name": "Custom Port",
                "hostname": "custom-port.local",
                "ipv4": "192.0.2.10",
                "ipv6": "",
                "provider": "Test",
                "region": "Test",
                "login_user": "root",
                "auth_type": "key",
                "ssh_host": "192.0.2.10",
                "ssh_port": 3022,
                "ssh_key_path": "",
                "ssh_options": "",
                "service_code": "",
                "tags": [],
                "notes": "",
                "credential": "",
            },
        )
        assert response.status_code == 200
        server_id = response.json()["id"]

        response = client.post(f"/api/servers/{server_id}/check")
        assert response.status_code == 200
        assert response.json()["status"] == "online"
        assert calls == [(("192.0.2.10", 3022), 3)]
    finally:
        os.unlink(db_path)


def test_static_and_index_are_not_cached():
    client, db_path = make_client()
    try:
        response = client.get("/")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache, no-store, must-revalidate"
        assert "static/styles.css?v=20260821-provider-freshness2" in response.text
        assert "static/app.js?v=20260821-provider-freshness1" in response.text
        assert 'id="trafficUsedGb" type="number" min="0" step="any"' in response.text
        assert 'id="trafficQuotaGb" type="number" min="0.000000001" step="any"' in response.text
        assert 'id="trafficNextResetAt"' in response.text
        assert "15 分钟内已成功读取" in (Path(__file__).parents[1] / "app/static/app.js").read_text(encoding="utf-8")
        assert 'id="trafficResetTimezone"' in response.text
        assert 'id="detailCredential"' in response.text
        assert 'id="settingsView"' in response.text
        assert 'id="showRetiredToggle"' in response.text
        assert 'id="environmentDetailReport"' in response.text
        assert 'data-detail-tab="environment"' in response.text
        assert 'id="is_retired"' in response.text
        assert 'id="heartbeat_enabled"' in response.text
        assert 'id="qualityCheckBtn"' in response.text
        assert 'id="detailMeshNetwork"' in response.text
        assert 'id="environmentView"' not in response.text
        assert 'id="runAllEnvironmentBtn"' not in response.text

        response = client.get("/static/styles.css")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache, no-store, must-revalidate"
        assert "action-spinner" in response.text
        assert "mesh-sparkline" in response.text
        assert "mesh-network-line" in response.text
        assert "mesh-collection-failure" in response.text
        assert "mesh-health.unconfirmed" in response.text
        assert "quality-dimensions" in response.text

        response = client.get("/static/app.js")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache, no-store, must-revalidate"
        assert "runningActions" in response.text
        assert "ti-loader-2 action-spinner" in response.text
        assert "meshHealthHtml" in response.text
        assert "sparklineSegments" in response.text
        assert "source_report_name" in response.text
        assert "syncDelayed" in response.text
        assert "同步延迟" in response.text
        assert "visibilityMissing" in response.text
        assert "未被邻居确认" in response.text
        assert "采集异常" in response.text
        assert "qualityReportHtml" in response.text
        assert 'runServerAction(s.id, "quality-check")' in response.text
        assert 'value === null || value === undefined' in response.text
    finally:
        os.unlink(db_path)


def test_retired_servers_cannot_run_checks():
    client, db_path = make_client()
    try:
        response = client.post("/api/login", json={"username": "admin", "password": "admin-pass"})
        assert response.status_code == 200
        response = client.post(
            "/api/servers",
            json={
                "name": "Retired Host",
                "hostname": "retired-host.local",
                "ipv4": "192.0.2.44",
                "ipv6": "",
                "provider": "Test",
                "region": "Test",
                "login_user": "root",
                "auth_type": "key",
                "ssh_host": "192.0.2.44",
                "ssh_port": 22,
                "ssh_key_path": "",
                "ssh_options": "",
                "service_code": "",
                "is_retired": True,
                "tags": [],
                "notes": "",
                "credential": "",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["is_retired"] is True

        response = client.post(f"/api/servers/{body['id']}/check")
        assert response.status_code == 409
        assert response.json()["detail"] == "server is retired"

        response = client.post(f"/api/servers/{body['id']}/inspect")
        assert response.status_code == 409
        assert response.json()["detail"] == "server is retired"

        response = client.post(f"/api/servers/{body['id']}/quality-check")
        assert response.status_code == 409
        assert response.json()["detail"] == "server is retired"
    finally:
        os.unlink(db_path)


def test_inspect_localhost_records_config_and_services():
    client, db_path = make_client()
    try:
        response = client.post("/api/login", json={"username": "admin", "password": "admin-pass"})
        assert response.status_code == 200
        response = client.post(
            "/api/servers",
            json={
                "name": "Localhost",
                "hostname": "localhost",
                "ipv4": "127.0.0.1",
                "ipv6": "",
                "provider": "Local",
                "region": "Dev",
                "login_user": "shou",
                "auth_type": "key",
                "ssh_host": "127.0.0.1",
                "ssh_port": 22,
                "ssh_key_path": "",
                "ssh_options": "",
                "service_code": "",
                "tags": ["local"],
                "notes": "",
                "credential": "",
            },
        )
        assert response.status_code == 200
        server_id = response.json()["id"]

        response = client.post(f"/api/servers/{server_id}/inspect")
        assert response.status_code == 200
        body = response.json()
        assert body["config_status"] in {"ok", "warning"}
        assert "个应用" in body["config_summary"]
        assert isinstance(body["config_report"], dict)
        assert "hostname" in body["config_report"]
        assert "health_score" in body["config_report"]
        assert "report_sections" in body["config_report"]
        assert "network" in body["config_report"]
        assert isinstance(body["installed_apps"], list)
        assert isinstance(body["services"], list)
        if body["installed_apps"]:
            assert "category" in body["installed_apps"][0]
        if body["services"]:
            assert "category" in body["services"][0]

        response = client.get("/api/audit")
        assert response.status_code == 200
        assert "inspect" in [row["action"] for row in response.json()]
    finally:
        os.unlink(db_path)


def test_build_config_report_extracts_environment_sections():
    from app.main import build_config_report

    output = """
__SECTION__hostname
demo-node
demo-node.example
__SECTION__os
PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"
__SECTION__kernel
Linux demo 6.1.0 x86_64 GNU/Linux
__SECTION__board
system_vendor=Example Vendor
product_name=Example Product
bios_version=1.2.3
__SECTION__runtime
virtualization=kvm
uptime=up 2 days
load_average=0.01 0.02 0.03
processes=101
active_services=18
locale=C.UTF-8
timezone=UTC +0000
__SECTION__cpu
count=4
architecture=x86_64
model=Example CPU
__SECTION__gpu
00:02.0 VGA compatible controller: Example GPU
__SECTION__memory
memory_total=8.0Gi
memory_used=2.0Gi
memory_available=5.0Gi
__SECTION__disk
/dev/vda1 40G 10G 30G 25% /
__SECTION__block_devices
vda disk 40G ExampleDisk
__SECTION__network
addresses=192.0.2.10 2001:db8::10
eth0 UP 192.0.2.10/24
default via 192.0.2.1 dev eth0
dns=1.1.1.1
__SECTION__public_ip
ipv4=198.51.100.10
ipv6=2001:db8::20
__SECTION__tcp
congestion_control=bbr
qdisc=fq
tcp_rmem=4096 87380 6291456
tcp_wmem=4096 16384 4194304
__SECTION__network_quality
cloudflare http=200 dns=0.001 connect=0.010 tls=0.020 total=0.050 ip=104.16.1.1
ping_1_1_1_1=rtt min/avg/max/mdev = 10.1/10.2/10.3/0.1 ms
__SECTION__apps
nginx\t1.24.0
python3\t3.11
__SECTION__services
nginx.service\trunning\tNginx
__SECTION__ports
LISTEN 0 511 0.0.0.0:80 0.0.0.0:* users:(("nginx",pid=1,fd=6))
__SECTION__quality_diagnostics
root_disk_used_percent=25
root_inode_used_percent=4
failed_service_count=0
ntp_synchronized=yes
heartbeat_timer=active
heartbeat_timer_substate=waiting
heartbeat_timer_last_trigger=Thu 2026-07-30 03:05:28 UTC
permitrootlogin=no
passwordauthentication=no
firewall=active
__SECTION__failed_services
"""

    status, summary, report, apps, services = build_config_report(output)

    assert status == "ok"
    assert "2 个应用" in summary
    assert "CPU 4 核" in summary
    assert report["os_name"] == "Debian GNU/Linux 12 (bookworm)"
    assert report["runtime"]["virtualization"] == "kvm"
    assert report["board"]["system_vendor"] == "Example Vendor"
    assert report["cpu"]["model"] == "Example CPU"
    assert report["gpu"] == ["00:02.0 VGA compatible controller: Example GPU"]
    assert report["memory_detail"]["memory_total"] == "8.0Gi"
    assert report["block_devices"] == ["vda disk 40G ExampleDisk"]
    assert report["network"]["addresses"] == ["192.0.2.10", "2001:db8::10"]
    assert report["network"]["public_ip"]["ipv4"] == "198.51.100.10"
    assert report["network"]["quality"][0].startswith("cloudflare http=200")
    assert report["network"]["tcp"]["congestion_control"] == "bbr"
    assert report["external_service_count"] == 1
    assert report["health_score"] == 100
    assert report["quality_diagnostics"]["root_disk_used_percent"] == "25"
    assert report["failed_services"] == []
    assert apps[0]["category"] == "custom"
    assert any(service["external"] for service in services)


def test_inspection_script_bounds_slow_inventory_commands():
    from app.main import INSPECTION_SCRIPT, PYTHON_INSPECTION_SCRIPT

    assert "run_timeout()" in INSPECTION_SCRIPT
    assert 'timeout -k 1s "${seconds}s"' in INSPECTION_SCRIPT
    assert "run_timeout 3 uptime -p" in INSPECTION_SCRIPT
    assert "run_timeout 5 sh -c 'systemctl list-units" in INSPECTION_SCRIPT
    assert "dmidecode" not in INSPECTION_SCRIPT
    assert "[ -r /var/lib/dpkg/status ]" in INSPECTION_SCRIPT
    assert "run_timeout 5 awk" in INSPECTION_SCRIPT
    assert "c>=40" in INSPECTION_SCRIPT
    assert "run_timeout 5 dpkg-query" in INSPECTION_SCRIPT
    assert "run_timeout 10 systemctl list-units" in INSPECTION_SCRIPT
    assert "run_timeout 10 ss -lntup" in INSPECTION_SCRIPT
    assert 'section("network_quality")' in PYTHON_INSPECTION_SCRIPT
    assert 'section("quality_diagnostics")' in PYTHON_INSPECTION_SCRIPT
    assert 'section("failed_services")' in PYTHON_INSPECTION_SCRIPT
    assert '__SECTION__quality_diagnostics' in INSPECTION_SCRIPT
    assert "/var/lib/dpkg/status" in PYTHON_INSPECTION_SCRIPT
    compile(PYTHON_INSPECTION_SCRIPT, "<remote-inspection>", "exec")


def test_quality_report_scores_dimensions_and_mesh_evidence():
    from app.main import build_quality_report

    row = {
        "ssh_key_path": "/etc/server-desk/ssh/key.pem",
        "credential_encrypted": "",
        "heartbeat_enabled": 1,
    }
    report = {
        "cpu_count": "4",
        "runtime": {"load_average": "0.40 0.30 0.20"},
        "memory_detail": {"mem_total_kb": "8000000 kB", "mem_available_kb": "6000000 kB"},
        "network": {
            "addresses": ["192.0.2.10"],
            "lines": ["addresses=192.0.2.10", "default via 192.0.2.1", "dns=1.1.1.1"],
            "quality": [
                "cloudflare http=200 total=0.05",
                "google http=204 total=0.06",
                "microsoft http=200 total=0.08",
            ],
        },
        "quality_diagnostics": {
            "root_disk_used_percent": "20",
            "root_inode_used_percent": "5",
            "failed_service_count": "0",
            "ntp_synchronized": "yes",
            "permitrootlogin": "no",
            "passwordauthentication": "no",
            "firewall": "active",
            "heartbeat_timer": "active",
            "heartbeat_timer_substate": "waiting",
            "heartbeat_timer_last_trigger": "Thu 2026-07-30 03:05:28 UTC",
        },
        "failed_services": [],
    }
    mesh_evidence = {
        "sample_count": 10,
        "confirmed_count": 9,
        "confirmed_rate": 0.9,
        "latest": {"peer_visible": 3, "peer_expected": 7, "details": {"external_visibility_confirmed": True}},
    }
    quality = build_quality_report(
        row,
        "ok",
        report,
        [{"name": "nginx.service"}],
        mesh_evidence,
        {"ok": True, "status": "ok", "detail": "签名报告读取成功", "latency_ms": 5},
    )

    assert quality["version"] == 2
    assert quality["score"] == 100
    assert quality["grade"] == "A"
    assert quality["status"] == "ok"
    assert quality["findings"] == []
    assert {item["id"] for item in quality["dimensions"]} == {"access", "system", "network", "services", "security", "heartbeat"}


def test_quality_report_detects_elapsed_heartbeat_timer():
    from app.main import build_quality_report

    row = {"ssh_key_path": "/key.pem", "credential_encrypted": "", "heartbeat_enabled": 1}
    report = {
        "cpu_count": "2",
        "runtime": {"load_average": "0.1 0.1 0.1"},
        "memory_detail": {"mem_total_kb": "1000 kB", "mem_available_kb": "900 kB"},
        "network": {
            "addresses": ["192.0.2.2"],
            "lines": ["default via 192.0.2.1", "dns=1.1.1.1"],
            "quality": ["cloudflare http=200", "google http=204", "microsoft http=200"],
        },
        "quality_diagnostics": {
            "root_disk_used_percent": "10",
            "root_inode_used_percent": "2",
            "failed_service_count": "0",
            "ntp_synchronized": "yes",
            "permitrootlogin": "no",
            "passwordauthentication": "no",
            "firewall": "active",
            "heartbeat_timer": "active",
            "heartbeat_timer_substate": "elapsed",
            "heartbeat_timer_last_trigger": "Mon 2026-07-27 19:23:42 UTC",
        },
    }
    mesh = {
        "sample_count": 30,
        "confirmed_count": 30,
        "confirmed_rate": 1.0,
        "latest": {"peer_visible": 3, "peer_expected": 7, "details": {"external_visibility_confirmed": True}},
    }

    quality = build_quality_report(row, "ok", report, [{"name": "ssh.service"}], mesh, {"ok": True, "detail": "ok"})

    heartbeat = next(item for item in quality["dimensions"] if item["id"] == "heartbeat")
    timer_check = next(item for item in heartbeat["checks"] if item["id"] == "heartbeat_timer_schedule")
    assert timer_check["status"] == "fail"
    assert timer_check["points"] == 0
    assert "active / elapsed" in timer_check["evidence"]


def test_quality_check_endpoint_records_report_and_audit(monkeypatch):
    from app import main

    client, db_path = make_client()
    monkeypatch.setattr(
        main,
        "run_server_inspection",
        lambda _row, _password="": (
            "ok",
            "采集完成",
            {
                "hostname": "quality-node",
                "kernel": "Linux 6.1",
                "cpu_count": "2",
                "runtime": {"load_average": "0.1 0.1 0.1"},
                "memory_detail": {"mem_total_kb": "1000 kB", "mem_available_kb": "800 kB"},
                "network": {"addresses": ["127.0.0.1"], "lines": ["default via 127.0.0.1", "dns=1.1.1.1"], "quality": []},
                "quality_diagnostics": {"root_disk_used_percent": "10", "root_inode_used_percent": "10", "failed_service_count": "0", "ntp_synchronized": "yes"},
                "failed_services": [],
            },
            [],
            [{"name": "ssh.service"}],
        ),
    )
    try:
        assert client.post("/api/login", json={"username": "admin", "password": "admin-pass"}).status_code == 200
        response = client.post(
            "/api/servers",
            json={
                "name": "Quality Node",
                "hostname": "quality-node",
                "ipv4": "127.0.0.1",
                "provider": "Test",
                "region": "Local",
                "login_user": "root",
                "auth_type": "key",
                "ssh_host": "127.0.0.1",
                "ssh_key_path": "/tmp/test-key",
                "tags": [],
            },
        )
        server_id = response.json()["id"]
        response = client.post(f"/api/servers/{server_id}/quality-check")
        assert response.status_code == 200
        body = response.json()
        assert body["config_report"]["quality_report"]["version"] == 2
        assert body["config_summary"].endswith("项需要关注")
        audits = client.get("/api/audit").json()
        assert "quality_check" in [item["action"] for item in audits]

        monkeypatch.setattr(
            main,
            "run_server_inspection",
            lambda _row, _password="": ("error", "SSH timeout", {"error": "SSH timeout"}, [], []),
        )
        response = client.post(f"/api/servers/{server_id}/quality-check")
        assert response.status_code == 200
        failed_report = response.json()["config_report"]
        assert failed_report["collection_error"] == "SSH timeout"
        assert "error" not in failed_report
        assert failed_report["quality_report"]["dimensions"][0]["checks"][0]["status"] == "fail"
    finally:
        os.unlink(db_path)


def test_paramiko_inspection_reports_blank_timeout(monkeypatch):
    from app import main

    class DummyClient:
        def set_missing_host_key_policy(self, _policy):
            pass

        def connect(self, **_kwargs):
            raise TimeoutError()

        def close(self):
            pass

    monkeypatch.setattr(main.paramiko, "SSHClient", lambda: DummyClient())

    row = {
        "hostname": "timeout.example",
        "ipv4": "192.0.2.60",
        "ssh_host": "192.0.2.60",
        "ssh_port": 22,
        "login_user": "root",
    }

    status, summary, report, apps, services = main.run_paramiko_inspection(row, "secret")

    assert status == "error"
    assert summary == "TimeoutError"
    assert report == {"error": "TimeoutError"}
    assert apps == []
    assert services == []


def test_remote_key_inspection_runs_environment_script_on_selected_host(monkeypatch):
    from app import main

    calls = []

    class DummyCompleted:
        returncode = 0
        stdout = """
__SECTION__hostname
remote-node
__SECTION__kernel
Linux remote-node 6.1.0 x86_64 GNU/Linux
"""
        stderr = ""

    def fake_run(command, capture_output, text, encoding, errors, timeout):
        calls.append(
            {
                "command": command,
                "capture_output": capture_output,
                "text": text,
                "encoding": encoding,
                "errors": errors,
                "timeout": timeout,
            }
        )
        return DummyCompleted()

    row = {
        "hostname": "remote-node.example",
        "ipv4": "198.51.100.20",
        "ssh_host": "192.0.2.55",
        "ssh_port": 3022,
        "auth_type": "key",
        "login_user": "ops",
        "ssh_key_path": "/etc/server-desk/ssh/id_ed25519",
        "ssh_options": "-o ProxyJump=bastion.example",
    }

    monkeypatch.setattr(main.subprocess, "run", fake_run)

    status, _summary, report, _apps, _services = main.run_server_inspection(row)

    assert status == "ok"
    assert report["hostname"] == "remote-node"
    assert len(calls) == 1
    command = calls[0]["command"]
    assert command[0] == "ssh"
    assert "-p" in command
    assert "3022" in command
    assert "ops@192.0.2.55" in command
    assert command[-1] == main.INSPECTION_SCRIPT
    assert calls[0]["timeout"] == 45
    assert calls[0]["encoding"] == "utf-8"
    assert calls[0]["errors"] == "replace"


def test_services_status_requires_login_and_returns_shape():
    client, db_path = make_client()
    try:
        response = client.get("/api/services/status")
        assert response.status_code == 401

        response = client.post("/api/login", json={"username": "admin", "password": "admin-pass"})
        assert response.status_code == 200

        response = client.get("/api/services/status")
        assert response.status_code == 200
        body = response.json()
        assert "checked_at" in body
        assert "services" in body
        assert "applications" in body
        service_ids = {item["id"] for item in body["services"]}
        assert {"server-desk", "nginx", "frps", "xray"}.issubset(service_ids)
    finally:
        os.unlink(db_path)


def test_mesh_health_requires_login_and_returns_protocol_window():
    client, db_path = make_client()
    try:
        response = client.get("/api/mesh/health")
        assert response.status_code == 401

        response = client.post("/api/login", json={"username": "admin", "password": "admin-pass"})
        assert response.status_code == 200

        response = client.get("/api/mesh/health?hours=3")
        assert response.status_code == 200
        body = response.json()
        assert body["window_hours"] == 3
        assert body["interval_seconds"] == 60
        assert body["freshness_seconds"] == 300
        assert body["offline_after_seconds"] == 660
        assert "window_started_at" in body
        assert body["poll_cycles"] == []
        assert body["servers"] == []
    finally:
        os.unlink(db_path)
