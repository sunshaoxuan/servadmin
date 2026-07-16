import json
import random
import sqlite3
import threading
import time
import urllib.error
import urllib.request

import pytest

from app.db import connect, init_db
from app.mesh import (
    HEARTBEAT_FRESH_SECONDS,
    HEARTBEAT_OFFLINE_SECONDS,
    SYNC_DELAY_SCORE,
    mesh_health_history,
    poll_mesh_once,
    _latest_payload,
)
from scripts import heartbeat_protocol as protocol
from scripts.deploy_heartbeat_mesh import (
    _activation_commands,
    _deployment_rows,
    _firewall_commands,
    _services,
)


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


def heartbeat(node_id, observed_at, seen_by=None, app_score=100.0, incarnation=0, sequence=0):
    return {
        **descriptor(node_id),
        "observed_at": observed_at,
        "app_score": app_score,
        "services": {},
        "seen_by": list(seen_by or [str(node_id)]),
        "incarnation": incarnation,
        "sequence": sequence,
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
    assert set(report["self"]["seen_by"]) == {"1", calls[0][0]}


def test_startup_registration_retries_random_peers_until_one_acknowledges(tmp_path):
    now = 1_500
    config = agent_config(tmp_path, 1, [descriptor(2), descriptor(3)])
    calls = []

    def sender(_config, target, payload):
        calls.append((target["node_id"], payload))
        if len(calls) == 1:
            raise TimeoutError("first peer unavailable")
        return {
            "ok": True,
            "registry": [descriptor(1), descriptor(2), descriptor(3)],
            "records": [heartbeat(target["node_id"], now)],
        }

    results = protocol.send_once(
        config,
        startup=True,
        now=now,
        sender=sender,
        rng=random.Random(11),
    )

    assert [item["ok"] for item in results] == [False, True]
    assert len(calls) == 2
    assert calls[0][0] != calls[1][0]
    assert all(
        {record["node_id"] for record in payload["records"]} == {"1"}
        for _target_id, payload in calls
    )
    report = protocol.build_report(config, now + 1)
    assert set(report["self"]["seen_by"]) == {"1", calls[1][0]}



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
        conn.execute("update outbound_status set succeeded_at = attempted_at")
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


def test_missing_visibility_lease_retries_without_waiting_for_cooldown(tmp_path):
    now = 2_500
    config = agent_config(tmp_path, 1, [descriptor(2), descriptor(3)])
    conn = protocol.connect_state(config["state_path"])
    try:
        protocol.initialize_state(conn, config, now)
        for peer_id in ("2", "3"):
            conn.execute(
                """
                insert into outbound_status(peer_id, peer_name, attempted_at, ok, latency_ms, error)
                values (?, ?, ?, 0, 4000, 'timeout')
                """,
                (peer_id, f"node-{peer_id}", now - 10),
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

    results = protocol.send_once(config, now=now, sender=sender, rng=random.Random(5))

    assert len(results) == 1
    assert results[0]["ok"] is True
    assert calls == [results[0]["peer_id"]]
    assert set(protocol.build_report(config, now + 1)["self"]["seen_by"]) == {"1", calls[0]}


def test_outbound_status_schema_migrates_successful_acknowledgements(tmp_path):
    state_path = tmp_path / "legacy-heartbeat.sqlite3"
    legacy = sqlite3.connect(state_path)
    try:
        legacy.executescript(
            """
            create table outbound_status (
              peer_id text primary key,
              peer_name text not null,
              attempted_at integer not null,
              ok integer not null,
              latency_ms integer,
              error text
            );
            insert into outbound_status values ('2', 'node-2', 1234, 1, 8, '');
            """
        )
        legacy.commit()
    finally:
        legacy.close()

    conn = protocol.connect_state(state_path)
    try:
        columns = {row["name"] for row in conn.execute("pragma table_info(outbound_status)")}
        registered_columns = {
            row["name"] for row in conn.execute("pragma table_info(registered_nodes)")
        }
        indexes = {
            row["name"] for row in conn.execute("pragma index_list(seen_reports)")
        }
        row = conn.execute("select succeeded_at from outbound_status where peer_id = '2'").fetchone()
    finally:
        conn.close()

    assert "succeeded_at" in columns
    assert {"failure_count", "next_attempt_at", "peer_versions_json", "peer_membership_digest"} <= columns
    assert {"membership_status", "membership_version"} <= registered_columns
    assert "seen_reports_received_idx" in indexes
    assert row["succeeded_at"] == 1234


def test_outbound_status_migration_is_serialized_between_agent_processes(tmp_path):
    state_path = tmp_path / "concurrent-legacy-heartbeat.sqlite3"
    legacy = sqlite3.connect(state_path)
    try:
        legacy.executescript(
            """
            pragma journal_mode = wal;
            create table outbound_status (
              peer_id text primary key,
              peer_name text not null,
              attempted_at integer not null,
              ok integer not null,
              latency_ms integer,
              error text
            );
            """
        )
        legacy.commit()
    finally:
        legacy.close()

    workers = 8
    barrier = threading.Barrier(workers + 1)
    errors = []

    def migrate():
        conn = None
        try:
            barrier.wait()
            conn = protocol.connect_state(state_path)
        except Exception as exc:
            errors.append(exc)
        finally:
            if conn is not None:
                conn.close()

    threads = [threading.Thread(target=migrate) for _index in range(workers)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=15)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    conn = protocol.connect_state(state_path)
    try:
        columns = {row["name"] for row in conn.execute("pragma table_info(outbound_status)")}
    finally:
        conn.close()
    assert "succeeded_at" in columns


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
        known = json.dumps({"3": [0, 0, now - 10]})
        for peer_id in ("2", "3"):
            conn.execute(
                """
                insert into outbound_status(
                  peer_id, peer_name, attempted_at, ok, latency_ms, error, peer_versions_json
                ) values (?, ?, ?, 1, 10, '', ?)
                """,
                (peer_id, f"node-{peer_id}", now - 400, known),
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


def test_v2_sends_stale_last_known_once_then_uses_peer_watermark(tmp_path):
    now = 5_000
    config = agent_config(tmp_path, 1, [descriptor(2), descriptor(3)])
    conn = protocol.connect_state(config["state_path"])
    try:
        protocol.initialize_state(conn, config, now - 500)
        stale = heartbeat(3, now - 400, ["3"])
        assert protocol.merge_heartbeat_record(conn, config, stale, now - 390)
        conn.commit()
    finally:
        conn.close()

    outbound = protocol.prepare_report(config, descriptor(2), now)
    assert {record["node_id"] for record in outbound["records"]} == {"1", "3"}
    assert outbound["protocol_version"] == 2

    conn = protocol.connect_state(config["state_path"])
    try:
        conn.execute(
            """
            insert into outbound_status(
              peer_id, peer_name, attempted_at, ok, latency_ms, error, peer_versions_json
            ) values ('2', 'node-2', ?, 1, 10, '', ?)
            """,
            (now, json.dumps({"3": [0, 0, now - 400]})),
        )
        conn.commit()
    finally:
        conn.close()
    repeated = protocol.prepare_report(config, descriptor(2), now + 1)
    assert {record["node_id"] for record in repeated["records"]} == {"1"}

    report = protocol.build_report(config, now)
    assert {record["node_id"] for record in report["records"]} == {"1"}
    latest = {record["node_id"]: record for record in report["latest_records"]}
    assert set(latest) == {"1", "3"}
    assert latest["3"]["observed_at"] == now - 400

    receiver_config = agent_config(tmp_path, 2, [descriptor(1), descriptor(3)])
    receiver_conn = protocol.connect_state(receiver_config["state_path"])
    try:
        merged = protocol.merge_sync_payload(
            receiver_conn,
            receiver_config,
            {
                "registry": [descriptor(1), descriptor(2), descriptor(3)],
                "latest_records": [stale],
                "records": [stale],
            },
            now,
        )
    finally:
        receiver_conn.close()
    receiver_report = protocol.build_report(receiver_config, now)
    assert merged == 1
    assert {record["node_id"] for record in receiver_report["records"]} == {"2"}
    assert {record["node_id"] for record in receiver_report["latest_records"]} == {"2", "3"}


def test_main_service_marks_one_missed_heartbeat_as_sync_delayed(tmp_path):
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

    def fetcher(server, _secret):
        source_id = str(server["id"])
        return {
            "node": {"node_id": source_id, "node_name": server["name"]},
            "registry": [descriptor(1), descriptor(2), descriptor(3)],
            "records": [heartbeat(1, now - 10, ["1", "2"], 100)],
            "latest_records": [
                heartbeat(2, now - HEARTBEAT_FRESH_SECONDS, ["2", "1"], 50),
                heartbeat(3, now - HEARTBEAT_OFFLINE_SECONDS, ["3", "1"], 90),
            ],
            "_latency_ms": 12,
        }

    recorded = poll_mesh_once(db_path, "mesh-secret", fetcher=fetcher, sampled_at=now)
    by_id = {item["server_id"]: item for item in recorded}

    assert by_id[1]["network_score"] == 100.0
    assert by_id[1]["direct_ok"] is True
    assert by_id[2]["network_score"] == SYNC_DELAY_SCORE
    assert by_id[2]["app_score"] == 50.0
    assert by_id[2]["direct_ok"] is True
    assert by_id[3]["network_score"] == 0.0
    assert by_id[3]["app_score"] is None
    assert by_id[3]["direct_ok"] is False

    conn = connect(db_path)
    try:
        history = mesh_health_history(conn, 3)
    finally:
        conn.close()
    current = {item["server_id"]: item["current"] for item in history["servers"]}
    assert current[2]["details"]["sync_delayed"] is True
    assert current[2]["details"]["heartbeat_age_seconds"] == HEARTBEAT_FRESH_SECONDS
    assert current[3]["details"]["sync_delayed"] is False


def test_main_service_does_not_mark_self_only_heartbeat_online(tmp_path):
    now = int(time.time())
    db_path = tmp_path / "ops-self-only.sqlite3"
    conn = connect(db_path)
    try:
        init_db(conn)
        for node_id in (1, 2):
            conn.execute(
                "insert into servers(name, hostname, login_user, heartbeat_enabled) values (?, ?, 'root', 1)",
                (f"node-{node_id}", f"node-{node_id}.example"),
            )
        conn.commit()
    finally:
        conn.close()

    def fetcher(server, _secret):
        source_id = str(server["id"])
        return {
            "node": {"node_id": source_id, "node_name": server["name"]},
            "registry": [descriptor(1), descriptor(2)],
            "records": [
                heartbeat(1, now - 10, ["1"], 100),
                heartbeat(2, now - 20, ["2", "1"], 50),
            ],
        }

    recorded = poll_mesh_once(
        db_path,
        "mesh-secret",
        fetcher=fetcher,
        sampled_at=now,
        rng=random.Random(3),
    )
    by_id = {item["server_id"]: item for item in recorded}
    assert by_id[1]["network_score"] == 0.0
    assert by_id[1]["app_score"] is None
    assert by_id[1]["direct_ok"] is False
    assert by_id[2]["direct_ok"] is True

    conn = connect(db_path)
    try:
        current = {item["server_id"]: item["current"] for item in mesh_health_history(conn, 3)["servers"]}
    finally:
        conn.close()
    assert current[1]["details"]["visibility_missing"] is True
    assert current[1]["details"]["external_visibility_confirmed"] is False


def test_main_service_merges_three_random_report_nodes(tmp_path):
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
                heartbeat(1, now - 10, ["1", "2"], 100),
                heartbeat(2, now - 20, ["2", "1"], 50),
                heartbeat(3, now - 30, ["3", "1"], 0),
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

    assert len(calls) == 3
    assert len(recorded) == 3
    assert all(item["direct_ok"] for item in recorded)
    assert {item["app_score"] for item in recorded} == {0.0, 50.0, 100.0}
    assert {item["source_report_server_id"] for item in recorded} <= {item[0] for item in calls}

    conn = connect(db_path)
    try:
        history = mesh_health_history(conn, 3)
    finally:
        conn.close()
    assert len(history["servers"]) == 3
    expected_sources = {item[0] for item in calls}
    assert all(
        set(item["current"]["details"]["source_report_server_ids"]) == expected_sources
        for item in history["servers"]
    )


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


def test_deployment_restarts_agent_and_runs_immediate_report():
    commands = _activation_commands(9108)

    assert "systemctl restart server-desk-heartbeat.service" in commands
    assert "systemctl restart server-desk-heartbeat-report.timer" in commands
    assert "systemctl start server-desk-heartbeat-report.service" in commands
    assert all("enable --now" not in command for command in commands)
    assert commands.index("systemctl restart server-desk-heartbeat.service") < commands.index(
        "systemctl start server-desk-heartbeat-report.service"
    )


def test_firewall_commands_only_allow_registered_node_addresses():
    commands = _firewall_commands(
        9108,
        ["203.24.89.50", "160.16.91.200", "203.24.89.50", "103.137.215.138"],
    )

    assert len(commands) == 3
    assert all("ufw allow proto tcp from" in command for command in commands)
    assert all("to any port 9108" in command for command in commands)
    assert all("ufw allow 9108/tcp" not in command for command in commands)
    assert any("from 103.137.215.138" in command for command in commands)
    assert any("from 160.16.91.200" in command for command in commands)
    assert any("from 203.24.89.50" in command for command in commands)


def test_firewall_commands_reject_invalid_addresses_and_ports():
    try:
        _firewall_commands(9108, ["node.example"])
    except ValueError as exc:
        assert "node.example" in str(exc)
    else:
        raise AssertionError("invalid firewall source was accepted")

    try:
        _firewall_commands(70000, ["203.24.89.50"])
    except ValueError as exc:
        assert "port" in str(exc)
    else:
        raise AssertionError("invalid heartbeat port was accepted")


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
        first_sent_at = int(time.time())
        results = protocol.send_once(config_a, startup=True, now=first_sent_at, rng=random.Random(1))
        assert len(results) == 1
        assert results[0]["ok"] is True

        report_a = protocol.build_report(config_a, first_sent_at + 1)
        assert report_a["self"]["seen_by"] == ["1", "2"]

        refreshed = protocol.send_once(
            config_a, now=first_sent_at + protocol.REPORT_INTERVAL_SECONDS + 1
        )
        assert len(refreshed) == 1
        assert refreshed[0]["ok"] is True
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


def test_regular_reporting_uses_two_stable_peers_and_one_random_peer(tmp_path):
    now = 10_000
    config = agent_config(tmp_path, 1, [descriptor(node_id) for node_id in range(2, 102)])
    conn = protocol.connect_state(config["state_path"])
    try:
        protocol.initialize_state(conn, config, now)
        for node_id in range(2, 102):
            conn.execute(
                """
                insert into outbound_status(
                  peer_id, peer_name, attempted_at, succeeded_at, ok, latency_ms, error
                ) values (?, ?, ?, ?, 1, 10, '')
                """,
                (str(node_id), f"node-{node_id}", now - 301, now - 10),
            )
        peers = protocol._known_peers(conn, config)
        stable_ids = protocol._stable_peer_ids(config, peers)
        targets = protocol.select_report_targets(
            conn, config, startup=False, now=now, rng=random.Random(9)
        )
    finally:
        conn.close()

    target_ids = {item["node_id"] for item in targets}
    assert len(targets) == 3
    assert stable_ids <= target_ids


def test_startup_registration_attempts_are_bounded_for_large_registry(tmp_path):
    config = agent_config(tmp_path, 1, [descriptor(node_id) for node_id in range(2, 102)])
    conn = protocol.connect_state(config["state_path"])
    try:
        targets = protocol.select_report_targets(
            conn, config, startup=True, now=10_000, rng=random.Random(7)
        )
    finally:
        conn.close()

    assert len(targets) == protocol.DEFAULT_REGISTRATION_ATTEMPTS
    assert len({item["node_id"] for item in targets}) == len(targets)


def test_failed_target_uses_exponential_backoff(tmp_path):
    config = agent_config(tmp_path, 1, [descriptor(2)])
    calls = []

    def unavailable(_config, target, _payload):
        calls.append(target["node_id"])
        raise TimeoutError("unreachable")

    first = protocol.send_once(config, startup=True, now=20_000, sender=unavailable)
    skipped = protocol.send_once(config, now=20_001, sender=unavailable)
    second = protocol.send_once(config, now=20_005, sender=unavailable)

    assert [item["ok"] for item in first] == [False]
    assert skipped == []
    assert [item["ok"] for item in second] == [False]
    assert calls == ["2", "2"]
    conn = protocol.connect_state(config["state_path"])
    try:
        status = conn.execute(
            "select failure_count, next_attempt_at from outbound_status where peer_id = '2'"
        ).fetchone()
    finally:
        conn.close()
    assert status["failure_count"] == 2
    assert status["next_attempt_at"] == 20_015


def test_membership_epoch_creates_tombstone_and_blocks_resurrection(tmp_path):
    now = 30_000
    config = agent_config(tmp_path, 1, [descriptor(2), descriptor(3)])
    config["membership_epoch"] = 10
    conn = protocol.connect_state(config["state_path"])
    try:
        protocol.initialize_state(conn, config, now)
        next_config = {**config, "peers": [descriptor(2)], "membership_epoch": 20}
        protocol.initialize_state(conn, next_config, now + 1)
        protocol.register_nodes(
            conn,
            [{**descriptor(3), "membership_status": "active", "membership_version": 10}],
            now + 2,
        )
        member = conn.execute(
            "select membership_status, membership_version from registered_nodes where node_id = '3'"
        ).fetchone()
        peers = protocol._known_peers(conn, next_config)
        accepted = protocol.merge_heartbeat_record(
            conn,
            next_config,
            {
                **heartbeat(3, now + 2, incarnation=3, sequence=1),
                "membership_version": 10,
            },
            now + 2,
        )
    finally:
        conn.close()

    assert dict(member) == {"membership_status": "retired", "membership_version": 20}
    assert "3" not in {item["node_id"] for item in peers}
    assert accepted is False


def test_incarnation_and_sequence_prevent_old_record_overwrite(tmp_path):
    config = agent_config(tmp_path, 1, [descriptor(2)])
    conn = protocol.connect_state(config["state_path"])
    try:
        protocol.initialize_state(conn, config, 40_000)
        assert protocol.merge_heartbeat_record(
            conn, config, heartbeat(2, 40_000, incarnation=10, sequence=5), 40_000
        )
        assert not protocol.merge_heartbeat_record(
            conn, config, heartbeat(2, 40_050, incarnation=10, sequence=4), 40_050
        )
        assert protocol.merge_heartbeat_record(
            conn, config, heartbeat(2, 39_990, incarnation=11, sequence=1), 40_050
        )
        stored = {
            item["node_id"]: item for item in protocol.latest_records(conn)
        }["2"]
    finally:
        conn.close()

    assert (stored["incarnation"], stored["sequence"], stored["observed_at"]) == (11, 1, 39_990)


def test_witness_list_is_bounded_while_seen_count_is_preserved(tmp_path):
    config = agent_config(tmp_path, 1, [descriptor(2)])
    conn = protocol.connect_state(config["state_path"])
    try:
        protocol.initialize_state(conn, config, 50_000)
        record = heartbeat(
            2,
            50_000,
            seen_by=[str(node_id) for node_id in range(1, 31)],
            incarnation=2,
            sequence=1,
        )
        assert protocol.merge_heartbeat_record(conn, config, record, 50_000)
        stored = {item["node_id"]: item for item in protocol.latest_records(conn)}["2"]
    finally:
        conn.close()

    assert len(stored["seen_by"]) == protocol.MAX_WITNESSES
    assert {"1", "2"} <= set(stored["seen_by"])
    assert stored["seen_count"] == 30


def test_compact_report_stays_bounded_with_150_nodes(tmp_path):
    now = 60_000
    config = agent_config(tmp_path, 1, [descriptor(node_id) for node_id in range(2, 151)])
    conn = protocol.connect_state(config["state_path"])
    try:
        protocol.initialize_state(conn, config, now)
        protocol.store_self_heartbeat(conn, config, now, force=True)
        for node_id in range(2, 151):
            record = heartbeat(
                node_id,
                now - node_id,
                seen_by=[str(node_id), "1"],
                incarnation=1_000 + node_id,
                sequence=1,
            )
            record["services"] = {
                f"app-{index}.service": "active" for index in range(7)
            }
            assert protocol.merge_heartbeat_record(conn, config, record, now)
        conn.commit()
    finally:
        conn.close()

    report = protocol.build_compact_report(config, now, include_all=True)
    encoded = json.dumps(report, separators=(",", ":")).encode("utf-8")
    assert len(report["records"]) == 150
    assert len(encoded) < protocol.MAX_BODY_BYTES
    assert {"self", "latest_records", "received", "outbound"}.isdisjoint(report)

    outbound = protocol.prepare_report(config, descriptor(2), now)
    outbound_size = len(json.dumps(outbound, separators=(",", ":")).encode("utf-8"))
    assert len(outbound["records"]) <= protocol.MAX_SYNC_RECORDS
    assert outbound_size < protocol.MAX_BODY_BYTES


def test_stale_sender_heartbeat_is_not_acknowledged(tmp_path):
    config_b = agent_config(tmp_path, 2, [])
    server = protocol.ThreadingHTTPServer(("127.0.0.1", 0), protocol.HeartbeatHandler)
    server.daemon_threads = True
    config_b["advertise_host"] = "127.0.0.1"
    config_b["port"] = server.server_address[1]
    server.config = config_b
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    target = {
        "node_id": "2",
        "node_name": "node-2",
        "host": "127.0.0.1",
        "port": server.server_address[1],
    }
    config_a = agent_config(tmp_path, 1, [target])
    try:
        stale_time = int(time.time()) - protocol.HEARTBEAT_FRESH_SECONDS - 1
        payload = protocol.prepare_report(config_a, target, stale_time)
        with pytest.raises(urllib.error.HTTPError) as caught:
            protocol.post_report(config_a, target, payload)
        assert caught.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_local_status_collection_is_cached_between_reports(tmp_path, monkeypatch):
    config = agent_config(tmp_path, 1, [])
    calls = []

    def collect(_config, now):
        calls.append(now)
        return heartbeat(1, now, incarnation=0, sequence=0)

    monkeypatch.setattr(protocol, "collect_local_status", collect)
    conn = protocol.connect_state(config["state_path"])
    try:
        protocol.initialize_state(conn, config, 70_000)
        first = protocol.store_self_heartbeat(conn, config, 70_000)
        cached = protocol.store_self_heartbeat(conn, config, 70_001)
        forced = protocol.store_self_heartbeat(conn, config, 70_002, force=True)
    finally:
        conn.close()

    assert calls == [70_000, 70_002]
    assert cached["sequence"] == first["sequence"]
    assert forced["sequence"] == first["sequence"] + 1


def test_main_merge_prefers_new_incarnation_after_clock_rollback():
    older_run = heartbeat(2, 80_100, incarnation=10, sequence=20)
    restarted = heartbeat(2, 80_000, incarnation=11, sequence=1)

    source_id, payload = _latest_payload(
        "2",
        {
            "1": {"records": [older_run]},
            "3": {"records": [restarted]},
        },
    )

    assert source_id == "3"
    assert payload is restarted


def test_http_get_supports_v1_and_compact_v2_during_rolling_upgrade(tmp_path):
    config = agent_config(tmp_path, 2, [])
    server = protocol.ThreadingHTTPServer(("127.0.0.1", 0), protocol.HeartbeatHandler)
    server.daemon_threads = True
    config["advertise_host"] = "127.0.0.1"
    config["port"] = server.server_address[1]
    server.config = config
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    path = "/v1/report"
    url = f"http://127.0.0.1:{server.server_address[1]}{path}"
    try:
        legacy_request = urllib.request.Request(
            url,
            method="GET",
            headers=protocol.signed_headers(
                config["shared_secret"], "server-desk-main", "GET", path
            ),
        )
        with urllib.request.urlopen(legacy_request, timeout=2) as response:
            legacy = json.loads(response.read().decode("utf-8"))

        compact_headers = protocol.signed_headers(
            config["shared_secret"], "server-desk-main", "GET", path
        )
        compact_headers["X-Heartbeat-Protocol"] = "2"
        compact_request = urllib.request.Request(
            url,
            method="GET",
            headers=compact_headers,
        )
        with urllib.request.urlopen(compact_request, timeout=2) as response:
            compact = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

    assert {"self", "records", "latest_records", "received", "outbound"} <= legacy.keys()
    assert compact["protocol_version"] == 2
    assert {"self", "latest_records", "received", "outbound"}.isdisjoint(compact)
