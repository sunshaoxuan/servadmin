from __future__ import annotations

import json
import os
from pathlib import Path

from app.db import connect, init_db
from app.security import CredentialCipher


def main() -> None:
    db_path = Path(os.environ["OPS_DB_PATH"])
    key = os.environ["OPS_CREDENTIAL_KEY"]
    credential = os.environ.get("INITIAL_CREDENTIAL", "")
    payload = {
        "name": os.environ.get("INITIAL_NAME", "Ubuntu 24.04 amd64 Tokyo 2G"),
        "hostname": os.environ["INITIAL_HOSTNAME"],
        "ipv4": os.environ.get("INITIAL_IPV4", ""),
        "ipv6": os.environ.get("INITIAL_IPV6", ""),
        "provider": os.environ.get("INITIAL_PROVIDER", "Sakura VPS"),
        "region": os.environ.get("INITIAL_REGION", "Tokyo 2"),
        "login_user": os.environ.get("INITIAL_LOGIN_USER", "ubuntu"),
        "auth_type": os.environ.get("INITIAL_AUTH_TYPE", "password"),
        "ssh_host": os.environ.get("INITIAL_SSH_HOST", ""),
        "ssh_port": int(os.environ.get("INITIAL_SSH_PORT", "22")),
        "ssh_key_path": os.environ.get("INITIAL_SSH_KEY_PATH", ""),
        "ssh_options": os.environ.get("INITIAL_SSH_OPTIONS", ""),
        "service_code": os.environ.get("INITIAL_SERVICE_CODE", ""),
        "tags_json": json.dumps([x.strip() for x in os.environ.get("INITIAL_TAGS", "prod,tokyo").split(",") if x.strip()]),
        "notes": os.environ.get("INITIAL_NOTES", ""),
        "credential_encrypted": CredentialCipher(key).encrypt(credential),
    }
    conn = connect(db_path)
    try:
        init_db(conn)
        existing = conn.execute("select id from servers where hostname = ?", (payload["hostname"],)).fetchone()
        if existing:
            conn.execute(
                """
                update servers set name = ?, ipv4 = ?, ipv6 = ?, provider = ?, region = ?, login_user = ?,
                  auth_type = ?, ssh_host = ?, ssh_port = ?, ssh_key_path = ?, ssh_options = ?,
                  service_code = ?, tags_json = ?, notes = ?, credential_encrypted = ?,
                  updated_at = current_timestamp
                where hostname = ?
                """,
                (
                    payload["name"],
                    payload["ipv4"],
                    payload["ipv6"],
                    payload["provider"],
                    payload["region"],
                    payload["login_user"],
                    payload["auth_type"],
                    payload["ssh_host"],
                    payload["ssh_port"],
                    payload["ssh_key_path"],
                    payload["ssh_options"],
                    payload["service_code"],
                    payload["tags_json"],
                    payload["notes"],
                    payload["credential_encrypted"],
                    payload["hostname"],
                ),
            )
        else:
            conn.execute(
                """
                insert into servers(name, hostname, ipv4, ipv6, provider, region, login_user, auth_type,
                  ssh_host, ssh_port, ssh_key_path, ssh_options, service_code, tags_json, notes, credential_encrypted)
                values (:name, :hostname, :ipv4, :ipv6, :provider, :region, :login_user, :auth_type,
                  :ssh_host, :ssh_port, :ssh_key_path, :ssh_options, :service_code, :tags_json, :notes, :credential_encrypted)
                """,
                payload,
            )
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
