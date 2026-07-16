#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import paramiko


ROOT = Path(__file__).resolve().parents[1]
AGENT_SOURCES = (
    ROOT / "scripts" / "heartbeat_agent.py",
    ROOT / "scripts" / "heartbeat_protocol.py",
)
UNIT_FILES = (
    ROOT / "deploy" / "server-desk-heartbeat.service",
    ROOT / "deploy" / "server-desk-heartbeat-report.service",
    ROOT / "deploy" / "server-desk-heartbeat-report.timer",
)


def _load_env_file(path: str) -> None:
    env_path = Path(path)
    if not env_path.exists():
        raise SystemExit(f"environment file not found: {env_path}")
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _ubuntu_report(row) -> bool:
    try:
        report = json.loads(row["config_report_json"] or "{}")
    except json.JSONDecodeError:
        return False
    os_info = report.get("os") or []
    text = "\n".join(os_info) if isinstance(os_info, list) else json.dumps(os_info)
    return "ID=ubuntu" in text or 'NAME="Ubuntu"' in text


def _services(row) -> list[str]:
    try:
        services = json.loads(row["services_json"] or "[]")
    except json.JSONDecodeError:
        return []
    selected = {
        item["name"]
        for item in services
        if item.get("category") == "custom"
        and str(item.get("name", "")).endswith(".service")
        and not str(item["name"]).startswith("user@")
    }
    return sorted(selected)


def _deployment_rows(rows, all_ubuntu: bool, server_ids: list[int]):
    selected = [
        row
        for row in rows
        if (all_ubuntu and _ubuntu_report(row)) or row["id"] in server_ids
    ]
    mesh_rows = {row["id"]: row for row in rows if row["heartbeat_enabled"]}
    mesh_rows.update({row["id"]: row for row in selected})
    return selected, list(mesh_rows.values())


def _activation_commands(port: int) -> list[str]:
    return [
        "systemctl daemon-reload",
        "systemctl enable server-desk-heartbeat.service server-desk-heartbeat-report.timer",
        "systemctl restart server-desk-heartbeat.service",
        "systemctl restart server-desk-heartbeat-report.timer",
        "systemctl start server-desk-heartbeat-report.service",
        "sleep 1",
        "systemctl is-active --quiet server-desk-heartbeat.service",
        "systemctl is-active --quiet server-desk-heartbeat-report.timer",
        f"ss -ltn | grep -q ':{port} '",
    ]


def _connect(row, cipher) -> tuple[paramiko.SSHClient, str]:
    password = cipher.decrypt(row["credential_encrypted"]) if row["auth_type"] == "password" else ""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs: dict[str, Any] = {
        "hostname": row["ssh_host"] or row["ipv4"] or row["hostname"],
        "port": int(row["ssh_port"] or 22),
        "username": row["login_user"],
        "timeout": 12,
        "banner_timeout": 12,
        "auth_timeout": 12,
        "allow_agent": False,
        "look_for_keys": False,
    }
    if row["auth_type"] == "password":
        kwargs["password"] = password
    else:
        kwargs["key_filename"] = row["ssh_key_path"]
    client.connect(**kwargs)
    return client, password


def _run(client: paramiko.SSHClient, command: str, login_user: str, password: str = "", timeout: int = 45) -> str:
    actual = command if login_user == "root" else f"sudo -S -p '' bash -lc {shlex.quote(command)}"
    stdin, stdout, stderr = client.exec_command(actual, timeout=timeout)
    if login_user != "root":
        stdin.write(password + "\n")
        stdin.flush()
    output = stdout.read().decode("utf-8", "replace")
    error = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    if code != 0:
        raise RuntimeError((error or output or f"remote command failed with {code}")[-1200:])
    return output


def _upload(client: paramiko.SSHClient, content: bytes, suffix: str) -> str:
    remote = f"/tmp/server-desk-heartbeat-{uuid.uuid4().hex}-{suffix}"
    sftp = client.open_sftp()
    try:
        with sftp.file(remote, "wb") as handle:
            handle.write(content)
    finally:
        sftp.close()
    return remote


def deploy_node(row, peers: list[dict[str, Any]], secret: str, port: int, cipher, configure_firewall: bool) -> dict[str, Any]:
    client, password = _connect(row, cipher)
    uploaded: list[str] = []
    try:
        config = {
            "node_id": str(row["id"]),
            "node_name": row["name"],
            "advertise_host": row["ipv4"],
            "shared_secret": secret,
            "bind_host": "0.0.0.0",
            "port": port,
            "state_path": "/var/lib/server-desk-heartbeat/heartbeat.sqlite3",
            "request_timeout": 4,
            "peers": [peer for peer in peers if str(peer["node_id"]) != str(row["id"])],
            "services": _services(row),
        }
        files = [(source.read_bytes(), source.name) for source in AGENT_SOURCES]
        files.extend((unit.read_bytes(), unit.name) for unit in UNIT_FILES)
        files.append((json.dumps(config, ensure_ascii=False, indent=2).encode("utf-8"), "config.json"))
        remote_files = {}
        for content, name in files:
            remote_files[name] = _upload(client, content, name)
            uploaded.append(remote_files[name])

        install = [
            "id -u serverdesk-heartbeat >/dev/null 2>&1 || useradd --system --home /var/lib/server-desk-heartbeat --shell /usr/sbin/nologin serverdesk-heartbeat",
            "install -d -m 0755 /opt/server-desk-heartbeat /etc/server-desk-heartbeat",
            "install -d -o serverdesk-heartbeat -g serverdesk-heartbeat -m 0750 /var/lib/server-desk-heartbeat",
            f"install -o root -g root -m 0755 {shlex.quote(remote_files['heartbeat_agent.py'])} /opt/server-desk-heartbeat/heartbeat_agent.py",
            f"install -o root -g root -m 0644 {shlex.quote(remote_files['heartbeat_protocol.py'])} /opt/server-desk-heartbeat/heartbeat_protocol.py",
            f"install -o root -g serverdesk-heartbeat -m 0640 {shlex.quote(remote_files['config.json'])} /etc/server-desk-heartbeat/config.json",
        ]
        for unit in UNIT_FILES:
            install.append(
                f"install -o root -g root -m 0644 {shlex.quote(remote_files[unit.name])} /etc/systemd/system/{unit.name}"
            )
        if configure_firewall:
            install.append(
                "if command -v ufw >/dev/null 2>&1 && ufw status | grep -q '^Status: active'; then "
                f"ufw allow {port}/tcp comment 'server-desk-heartbeat'; fi"
            )
        install.extend(_activation_commands(port))
        _run(client, "set -e\n" + "\n".join(install), row["login_user"], password, timeout=90)
        return {
            "server_id": row["id"],
            "name": row["name"],
            "ok": True,
            "port": port,
            "services": config["services"],
        }
    finally:
        if uploaded:
            try:
                _run(client, "rm -f " + " ".join(shlex.quote(path) for path in uploaded), row["login_user"], password)
            except Exception:
                pass
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy the Server Desk heartbeat mesh to inventory nodes")
    parser.add_argument("--server-id", action="append", type=int, default=[])
    parser.add_argument("--all-ubuntu", action="store_true")
    parser.add_argument("--port", type=int, default=9108)
    parser.add_argument("--configure-firewall", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--env-file", default="")
    args = parser.parse_args()

    if args.env_file:
        _load_env_file(args.env_file)
    secret = os.environ.get("OPS_MESH_SECRET", "").strip()
    if len(secret) < 32:
        raise SystemExit("OPS_MESH_SECRET must contain at least 32 characters")

    sys.path.insert(0, str(ROOT))
    from app.db import connect, init_db
    from app.main import CREDENTIAL_KEY, DB_PATH
    from app.security import CredentialCipher

    conn = connect(DB_PATH)
    init_db(conn)
    rows = conn.execute("select * from servers where is_retired = 0 order by id").fetchall()
    selected, mesh_rows = _deployment_rows(rows, args.all_ubuntu, args.server_id)
    if not selected:
        raise SystemExit("no matching active Ubuntu servers")
    peers = [
        {"node_id": str(row["id"]), "node_name": row["name"], "host": row["ipv4"], "port": args.port}
        for row in mesh_rows
        if row["ipv4"]
    ]
    if len(peers) != len(mesh_rows):
        raise SystemExit("every mesh server must have an IPv4 address")
    if args.dry_run:
        print(
            json.dumps(
                {
                    "port": args.port,
                    "targets": [{"server_id": row["id"], "name": row["name"]} for row in selected],
                    "registry": peers,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        conn.close()
        return 0

    cipher = CredentialCipher(CREDENTIAL_KEY)
    results = []
    for row in selected:
        started = time.perf_counter()
        try:
            result = deploy_node(row, peers, secret, args.port, cipher, args.configure_firewall)
            conn.execute(
                "update servers set heartbeat_enabled = 1, heartbeat_port = ?, updated_at = current_timestamp where id = ?",
                (args.port, row["id"]),
            )
            conn.commit()
        except Exception as exc:
            result = {"server_id": row["id"], "name": row["name"], "ok": False, "error": str(exc)[:1200]}
        result["elapsed_seconds"] = round(time.perf_counter() - started, 2)
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    conn.close()
    failures = [item for item in results if not item["ok"]]
    print(json.dumps({"deployed": len(results) - len(failures), "failed": len(failures)}, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
