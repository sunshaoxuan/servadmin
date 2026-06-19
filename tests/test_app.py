import os
import tempfile

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient


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
                "service_code": "113801369753",
                "tags": ["tokyo", "prod"],
                "notes": "seeded test host",
                "credential": "secret-value",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert "credential_encrypted" not in body
        assert body["name"] == "Tokyo VPS"

        response = client.get(f"/api/servers/{body['id']}/credential")
        assert response.status_code == 200
        assert response.json()["credential"] == "secret-value"

        response = client.get("/api/audit")
        assert response.status_code == 200
        actions = [row["action"] for row in response.json()]
        assert "create" in actions
        assert "reveal_credential" in actions
    finally:
        os.unlink(db_path)


def test_requires_login_for_servers():
    client, db_path = make_client()
    try:
        response = client.get("/api/servers")
        assert response.status_code == 401
    finally:
        os.unlink(db_path)
