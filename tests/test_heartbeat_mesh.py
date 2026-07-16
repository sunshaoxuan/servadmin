import random
import threading
import time

from app.db import connect, init_db
from app.mesh import mesh_health_history, poll_mesh_once
from scripts import heartbeat_protocol as protocol
from scripts.deploy_heartbeat_mesh import _deployment_rows, _services


def agent_config(tmp_path, node_id, peers):
    return {
        "node_id": str(node_id),
        "node_name": f"node-{node_id}",
        "advertise_host": f"192.0.2.{node_id}",
        "shared_secret": "test-secret-with-at-least-32-characters",
        "bind_host": "127.0.0.1",
        "port": 9108,
        "state_path": str(tmp_path / f"heartbeat-{node_id}.sqlite3"),
        "request_timeout": 1,
        "peers": peers,
        "services": [],
    }


def descriptor(node_id):
    return {
        "node_id": str(node_id),
        "node_name": f"node-{node_id}",
        "host": f"192.0.2.{node_id}",
        "port": 9108,
    }


def heartbeat(node_id, observed_at, seen_by=None, app_score=100.0):
    return {
        **descriptor(node_id),
        "observed_at": observed_at,
        "app_score": app_score,
        "services": {},
        "seen_by": list(seen_by or [str(node_id)]),
    }


def test_startup_registration_uses_one_random_peer_and_syncs_registry(tmp_path):
    now = 1_000
    config = agent_config(tmp_path, 1, [descriptor(2), descriptor(3)])
    calls = []

    def sender(_config, target, payload):
        calls.append((target["node_id"], payload))
        return {
            "ok": True,
            "registry": [descriptor(1), descriptor(2), descriptor(3), descriptor(4)],
            "records": [heartbeat(target["node_id"], now, [target["node_id"]])],
        }

    results = protocol.send_once(
        config,
        startup=True,
        now=now,
        sender=sender,
        rng=random.Random(7),
    )

    assert len(results) == 1
    assert len(calls) == 1
    assert calls[0][0] in {"2", "3"}
    assert {record["node_id"] for record in calls[0][1]["records"]} == {"1"}
    assert calls[0][1]["records"][0]["app_score"] is None

    report = protocol.build_report(config, now + 1)
    assert {item["node_id"] for item in report["registry"]} == {"1", "2", "3", "4"}
    assert calls[0][0] in {record["node_id"] for record in report["records"]}


def test_regular_reports_only_to_nodes_due_after_five_minutes(tmp_path):
    now = 2_000
    config = agent_config(tmp_path, 1, [descriptor(2), descriptor(3)])
    conn = protocol.connect_state(config["state_path"])
    try:
        protocol.initialize_state(conn, config, now)
        conn.execute(
            """
            insert into outbound_status(peer_id, peer_name, attempted_at, ok, latency_ms, error)
            values ('2', 'node-2', ?, 1, 10, '')
            """,
            (now - 100,),
        )
        conn.execute(
            """
            insert into outbound_status(peer_id, peer_name, attempted_at, ok, latency_ms, error)
            values ('3', 'node-3', ?, 1, 10, '')
            """,
            (now - protocol.REPORT_INTERVAL_SECONDS - 1,),
        )
        conn.commit()
    finally:
        conn.close()

    calls = []

    def sender(_config, target, _payload):
        calls.append(target["node_id"])
        return {
            "ok": True,
            "registry": [descriptor(1), descriptor(2), descriptor(3)],
            "records": [heartbeat(target["node_id"], now)],
        }

    results = protocol.send_once(config, now=now, sender=sender, rng=random.Random(2))

    assert [item["peer_id"] for item in results] == ["3"]
    assert calls == ["3"]
    assert protocol.send_once(config, now=now + 10, sender=sender, rng=random.Random(2)) == []


def test_forwarded_heartbeats_expire_and_do_not_cycle(tmp_path):
    now = 3_000
    config = agent_config(tmp_path, 1, [descriptor(2), descriptor(3)])
    conn = protocol.connect_state(config["state_path"])
    try:
        protocol.initialize_state(conn, config, now)
        assert protocol.merge_heartbeat_record(conn, config, heartbeat(3, now - 10, ["3"]), now)
        assert protocol.merge_heartbeat_record(conn, config, heartbeat(3, now - 10, ["3", "2"]), now)
        assert not protocol.merge_heartbeat_record(
            conn,
            config,
            heartbeat(4, now - protocol.HEARTBEAT_FRESH_SECONDS, ["4"]),
            now,
        )
        conn.commit()
    finally:
        conn.close()

    report_to_two = protocol.prepare_report(config, descriptor(2), now)
    forwarded_to_two = {record["node_id"]: record for record in report_to_two["records"]}
    assert set(forwarded_to_two) == {"1"}
    assert "2" in protocol.build_report(config, now)["records"][1]["seen_by"]

    report_to_three = protocol.prepare_report(config, descriptor(3), now)
    assert {record["node_id"] for record in report_to_three["records"]} == {"1"}
    assert "4" not in {item["node_id"] for item in report_to_three["registry"]}


def test_report_to_unseen_target_carries_fresh_relay(tmp_path):
    now = 4_000
    config = agent_config(tmp_path, 1, [descriptor(2), descriptor(3), descriptor(4)])
    conn = protocol.connect_state(config["state_path"])
    try:
        protocol.initialize_state(conn, config, now)
        assert protocol.merge_heartbeat_record(conn, config, heartbeat(3, now - 20, ["3", "2"]), now)
        conn.commit()
    finally:
        conn.close()

    report = protocol.prepare_report(config, descriptor(4), now)
    records = {record["node_id"]: record for record in report["records"]}

    assert set(records) == {"1", "3"}
    assert records["3"]["seen_by"] == ["1", "2", "3"]


def test_main_service_reads_all_nodes_from_one_random_report_node(tmp_path):
    now = int(time.time())
    db_path = tmp_path / "ops.sqlite3"
    conn = connect(db_path)
    try:
        init_db(conn)
        for node_id in (1, 2, 3):
            conn.execute(
                "insert into servers(name, hostname, login_user, heartbeat_enabled) values (?, ?, 'root', 1)",
                (f"node-{node_id}", f"node-{node_id}.example"),
            )
        conn.commit()
    finally:
        conn.close()

    calls = []

    def fetcher(server, secret):
        calls.append((server["id"], secret))
        source_id = str(server["id"])
        return {
            "node": {"node_id": source_id, "node_name": server["name"]},
            "registry": [descriptor(1), descriptor(2), descriptor(3)],
            "records": [
                heartbeat(1, now - 10, ["1", source_id], 100),
                heartbeat(2, now - 20, ["2", source_id], 50),
                heartbeat(3, now - 30, ["3", source_id], 0),
            ],
            "_latency_ms": 18,
        }

    recorded = poll_mesh_once(
        db_path,
        "mesh-secret",
        fetcher=fetcher,
        sampled_at=now,
        rng=random.Random(4),
    )

    assert len(calls) == 1
    assert len(recorded) == 3
    assert all(item["direct_ok"] for item in recorded)
    assert {item["app_score"] for item in recorded} == {0.0, 50.0, 100.0}
    assert {item["source_report_server_id"] for item in recorded} == {calls[0][0]}

    conn = connect(db_path)
    try:
        history = mesh_health_history(conn, 3)
    finally:
        conn.close()
    assert len(history["servers"]) == 3
    assert all(item["current"]["details"]["source_report_server_id"] == calls[0][0] for item in history["servers"])


def test_deployment_monitors_custom_services_even_when_currently_stopped():
    row = {
        "services_json": """
        [
          {"name": "app-a.service", "state": "running", "category": "custom"},
          {"name": "app-b.service", "state": "failed", "category": "custom"},
          {"name": "ssh.service", "state": "running", "category": "system"}
        ]
        """
    }

    assert _services(row) == ["app-a.service", "app-b.service"]


def test_signed_http_registration_round_trip(tmp_path):
    config_b = agent_config(tmp_path, 2, [])
    server = protocol.ThreadingHTTPServer(("127.0.0.1", 0), protocol.HeartbeatHandler)
    server.daemon_threads = True
    config_b["advertise_host"] = "127.0.0.1"
    config_b["port"] = server.server_address[1]
    server.config = config_b
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    config_a = agent_config(
        tmp_path,
        1,
        [
            {
                "node_id": "2",
                "node_name": "node-2",
                "host": "127.0.0.1",
                "port": server.server_address[1],
            }
        ],
    )
    try:
        results = protocol.send_once(config_a, startup=True, rng=random.Random(1))
        assert len(results) == 1
        assert results[0]["ok"] is True

        report_b = protocol.build_report(config_b)
        records = {record["node_id"]: record for record in report_b["records"]}
        assert set(records) == {"1", "2"}
        assert records["1"]["seen_by"] == ["1", "2"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_new_node_deployment_keeps_existing_mesh_nodes_as_seeds():
    rows = [
        {
            "id": 1,
            "heartbeat_enabled": 1,
            "config_report_json": '{"os": ["ID=ubuntu"]}',
        },
        {
            "id": 2,
            "heartbeat_enabled": 0,
            "config_report_json": '{"os": ["ID=ubuntu"]}',
        },
    ]

    selected, mesh_rows = _deployment_rows(rows, False, [2])

    assert [row["id"] for row in selected] == [2]
    assert {row["id"] for row in mesh_rows} == {1, 2}
