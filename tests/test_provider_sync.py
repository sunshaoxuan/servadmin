import sqlite3

import httpx

from app.db import connect, init_db
from app.provider_sync import ProviderSyncError, sync_provider_usage
from app.security import CredentialCipher


KEY = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="


def seeded_provider(conn: sqlite3.Connection, connector: str = "riven_cloud") -> int:
    server_id = conn.execute(
        "insert into servers(name, hostname, provider, login_user) values ('riven', 'riven', 'Riven Cloud', 'root')"
    ).lastrowid
    conn.execute(
        """
        insert into server_provider_access(
          server_id, portal_url, login_username, password_encrypted,
          service_reference, external_server_id, connector_type, sync_enabled
        ) values (?, 'https://portal.sa.net/clientarea.php', 'operator@example.com', ?,
                  '23492', 'baf47198-98cc-49d3-bf96-6f88029d1e92', ?, 1)
        """,
        (server_id, CredentialCipher(KEY).encrypt("provider-secret"), connector),
    )
    conn.execute(
        """
        insert into server_subscription_usage(
          server_id, period_start, period_end, used_bytes, quota_bytes,
          source_label, source_url, created_by
        ) values (?, '2026-07-24', '2026-08-23', 1, 1024000000000,
                  'provider baseline', '', 'test')
        """,
        (server_id,),
    )
    conn.commit()
    return server_id


def test_riven_connector_logs_in_through_sso_and_persists_monthly_usage(tmp_path):
    db_path = tmp_path / "provider-sync.sqlite3"
    conn = connect(db_path)
    init_db(conn)
    server_id = seeded_provider(conn)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "portal.sa.net" and request.url.path == "/clientarea.php":
            return httpx.Response(200, text='<input name="token" value="csrf-token">')
        if request.url.host == "portal.sa.net" and request.url.path == "/login":
            assert b"username=operator%40example.com" in request.content
            assert b"password=provider-secret" in request.content
            return httpx.Response(200, text="Logged in as:")
        if request.url.host == "portal.sa.net" and request.url.path.endswith("/client.php"):
            assert request.url.params["serviceID"] == "23492"
            return httpx.Response(
                200,
                json={"success": True, "token_url": "https://cloud.sa.net/sso/test-token"},
            )
        if request.url.host == "cloud.sa.net" and request.url.path == "/sso/test-token":
            return httpx.Response(200, text="authorized")
        if request.url.host == "cloud.sa.net" and request.url.path.startswith("/server/baf47198"):
            return httpx.Response(
                200,
                text='<client-server-manage :id="133" uuid="baf47198-98cc-49d3-bf96-6f88029d1e92">',
            )
        if request.url.host == "cloud.sa.net" and request.url.path == "/server/133/resource/traffic.json":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "data": {
                            "monthlyRaw": [
                                {
                                    "month_start": "2026-07-24 00:00:00",
                                    "month_end": "2026-08-23 23:59:59",
                                    "rx": 72064455970,
                                    "tx": 283537774780,
                                    "total": 355602230750,
                                }
                            ]
                        }
                    },
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    transport = httpx.MockTransport(handler)

    def client_factory(**kwargs):
        return httpx.Client(transport=transport, **kwargs)

    result = sync_provider_usage(conn, CredentialCipher(KEY), server_id, client_factory)
    snapshot = conn.execute(
        "select * from server_subscription_usage where server_id = ?",
        (server_id,),
    ).fetchone()
    access = conn.execute(
        "select last_sync_status, last_synced_at from server_provider_access where server_id = ?",
        (server_id,),
    ).fetchone()
    conn.close()

    assert result["used_bytes"] == 355602230750
    assert result["quota_bytes"] == 1024000000000
    assert snapshot["period_start"] == "2026-07-24"
    assert snapshot["period_end"] == "2026-08-23"
    assert snapshot["used_bytes"] == 355602230750
    assert snapshot["source_label"] == "Riven Cloud 自动同步"
    assert snapshot["created_by"] == "provider-sync:riven-cloud"
    assert access["last_sync_status"] == "ok"
    assert access["last_synced_at"] is not None


def test_unsupported_provider_connector_is_rejected(tmp_path):
    conn = connect(tmp_path / "unsupported-provider.sqlite3")
    init_db(conn)
    server_id = seeded_provider(conn, connector="browser")
    try:
        sync_provider_usage(conn, CredentialCipher(KEY), server_id)
    except ProviderSyncError as exc:
        assert "not supported" in str(exc)
    else:
        raise AssertionError("unsupported connector should fail")
    finally:
        conn.close()
