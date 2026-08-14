from __future__ import annotations

import calendar
from datetime import datetime, timezone
from typing import Any


def _as_counter(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _period_for(conn, server_id: int, observed_at: int) -> tuple[str, str, dict[str, Any] | None]:
    observed_date = datetime.fromtimestamp(observed_at, timezone.utc).date()
    iso_date = observed_date.isoformat()
    provider_row = conn.execute(
        """
        select period_start, period_end, used_bytes, quota_bytes, source_label,
               source_url, collected_at
        from server_subscription_usage
        where server_id = ? and period_start <= ? and period_end >= ?
        order by collected_at desc, id desc
        limit 1
        """,
        (server_id, iso_date, iso_date),
    ).fetchone()
    if provider_row:
        provider = dict(provider_row)
        return provider["period_start"], provider["period_end"], provider
    last_day = calendar.monthrange(observed_date.year, observed_date.month)[1]
    return (
        observed_date.replace(day=1).isoformat(),
        observed_date.replace(day=last_day).isoformat(),
        None,
    )


def _counter_delta(current: int, previous: int | None) -> int:
    if previous is None:
        return 0
    return current - previous if current >= previous else current


def update_traffic_meter(conn, server_id: int, payload: dict[str, Any]) -> bool:
    observed_at = _as_counter(payload.get("observed_at"))
    rx_counter = _as_counter(payload.get("network_rx_bytes"))
    tx_counter = _as_counter(payload.get("network_tx_bytes"))
    if observed_at is None or rx_counter is None or tx_counter is None:
        return False
    period_start, period_end, provider = _period_for(conn, server_id, observed_at)
    row = conn.execute(
        """
        select id, last_observed_at, last_rx_counter, last_tx_counter
        from server_traffic_meter
        where server_id = ? and period_start = ? and period_end = ?
        """,
        (server_id, period_start, period_end),
    ).fetchone()
    if not row:
        conn.execute(
            """
            insert into server_traffic_meter(
              server_id, period_start, period_end, base_used_bytes, quota_bytes,
              measured_rx_bytes, measured_tx_bytes, last_rx_counter,
              last_tx_counter, last_observed_at, source_label, baseline_collected_at,
              count_mode, is_partial
            ) values (?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, 'both', ?)
            """,
            (
                server_id,
                period_start,
                period_end,
                int(provider["used_bytes"]) if provider else 0,
                int(provider["quota_bytes"]) if provider else None,
                rx_counter,
                tx_counter,
                observed_at,
                provider["source_label"] if provider else "Agent 自动计量",
                provider["collected_at"] if provider else None,
                0 if provider else 1,
            ),
        )
        return True
    if observed_at <= int(row["last_observed_at"] or 0):
        return False
    rx_delta = _counter_delta(rx_counter, _as_counter(row["last_rx_counter"]))
    tx_delta = _counter_delta(tx_counter, _as_counter(row["last_tx_counter"]))
    conn.execute(
        """
        update server_traffic_meter
        set measured_rx_bytes = measured_rx_bytes + ?,
            measured_tx_bytes = measured_tx_bytes + ?,
            last_rx_counter = ?, last_tx_counter = ?, last_observed_at = ?,
            updated_at = current_timestamp
        where id = ?
        """,
        (rx_delta, tx_delta, rx_counter, tx_counter, observed_at, row["id"]),
    )
    return True


def reset_traffic_meter_from_provider(
    conn,
    server_id: int,
    period_start: str,
    period_end: str,
    used_bytes: int,
    quota_bytes: int,
    source_label: str,
    count_mode: str = "both",
) -> None:
    conn.execute(
        """
        insert into server_traffic_meter(
          server_id, period_start, period_end, base_used_bytes, quota_bytes,
          measured_rx_bytes, measured_tx_bytes, last_rx_counter, last_tx_counter,
          last_observed_at, source_label, count_mode, baseline_collected_at, is_partial
        ) values (?, ?, ?, ?, ?, 0, 0, null, null, null, ?, ?, current_timestamp, 0)
        on conflict(server_id, period_start, period_end) do update set
          base_used_bytes=excluded.base_used_bytes,
          quota_bytes=excluded.quota_bytes,
          measured_rx_bytes=0,
          measured_tx_bytes=0,
          last_rx_counter=null,
          last_tx_counter=null,
          last_observed_at=null,
          source_label=excluded.source_label,
          count_mode=excluded.count_mode,
          baseline_collected_at=current_timestamp,
          is_partial=0,
          updated_at=current_timestamp
        """,
        (server_id, period_start, period_end, used_bytes, quota_bytes, source_label, count_mode),
    )
