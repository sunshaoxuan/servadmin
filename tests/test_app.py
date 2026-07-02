import os
import socket
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
        assert "panel_password_encrypted" not in body
        assert body["is_starred"] is True
        assert body["is_retired"] is False

        response = client.get(f"/api/servers/{body['id']}/credential")
        assert response.status_code == 200
        assert response.json()["credential"] == "secret-value"

        response = client.get(f"/api/servers/{body['id']}/connection-secret")
        assert response.status_code == 200
        assert response.json()["panel_password"] == "panel-secret"

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
        assert "static/styles.css?v=20260702-detailenv1" in response.text
        assert 'id="detailCredential"' in response.text
        assert 'id="settingsView"' in response.text
        assert 'id="showRetiredToggle"' in response.text
        assert 'id="environmentDetailReport"' in response.text
        assert 'data-detail-tab="environment"' in response.text
        assert 'id="is_retired"' in response.text
        assert 'id="environmentView"' not in response.text
        assert 'id="runAllEnvironmentBtn"' not in response.text

        response = client.get("/static/styles.css")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache, no-store, must-revalidate"
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
    assert apps[0]["category"] == "custom"
    assert any(service["external"] for service in services)


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

    def fake_run(command, capture_output, text, timeout):
        calls.append(
            {
                "command": command,
                "capture_output": capture_output,
                "text": text,
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
