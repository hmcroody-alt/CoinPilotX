#!/usr/bin/env python3
"""Audit crypto alert creation for bounded, failure-isolated web requests."""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TMP_DB = Path(tempfile.mkdtemp(prefix="crypto-alert-request-safety-")) / "audit.sqlite3"
os.environ["COINPILOTX_DISABLE_LOCAL_ENV"] = "1"
os.environ["DATABASE_URL"] = f"sqlite:///{TMP_DB}"
os.environ["SECRET_KEY"] = "crypto-alert-request-safety"
os.environ["SESSION_SECRET"] = "crypto-alert-request-safety"
os.environ["PULSE_NOTIFICATIONS_DISABLE_EXTERNAL_DELIVERY"] = "1"
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import alert_engine, dashboard_crypto_command_center  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def static_checks() -> None:
    bot_source = (ROOT / "bot.py").read_text(encoding="utf-8")
    alert_source = (ROOT / "services/alert_engine.py").read_text(encoding="utf-8")
    dashboard_source = (ROOT / "services/dashboard_crypto_command_center.py").read_text(encoding="utf-8")
    crypto_routes = bot_source[bot_source.index('@webhook_app.route("/api/dashboard/crypto/state"'):bot_source.index('@webhook_app.route("/api/dashboard/ads/state"')]
    require("init_db()" not in crypto_routes, "crypto API routes do not run full application migrations")
    require("CRYPTO_API_REQUEST_FAILED" in crypto_routes and "correlation_id" in crypto_routes, "crypto API failures are isolated and traceable")
    require("conn.rollback()" in crypto_routes and "conn.close()" in crypto_routes, "crypto API closes and rolls back failed transactions")
    require("connection=conn" in dashboard_source and "schema_ready=True" in dashboard_source, "alert creation reuses the request transaction")
    require("reconcile_legacy_alerts(user_id=int(user_id))" not in dashboard_source, "legacy reconciliation is not run during alert creation")
    require("_TABLES_READY" in dashboard_source and "_TABLES_LOCK" in dashboard_source, "focused dashboard schema readiness is cached")
    require("_ALERT_SCHEMA_READY" in alert_source and "_ALERT_SCHEMA_LOCK" in alert_source, "canonical alert schema readiness is cached")
    require("information_schema.columns" in alert_source and "PRAGMA table_info" in alert_source, "schema upgrades inspect columns before ALTER")


def runtime_checks() -> dict[str, int]:
    bot.init_db()
    user_id = 990201
    conn = bot.db()
    conn.row_factory = getattr(__import__("sqlite3"), "Row")
    try:
        dashboard_crypto_command_center.ensure_tables(conn)
        started = time.perf_counter()
        created_ids: list[int] = []
        for index in range(20):
            result = dashboard_crypto_command_center.create_alert(
                conn,
                user_id,
                {
                    "assetSymbol": "BTC" if index % 2 == 0 else "ETH",
                    "condition": "above",
                    "targetValue": 1000 + index,
                    "notifyPush": True,
                    "notifyInApp": True,
                },
            )
            require(result.get("ok"), f"alert {index + 1} was created")
            created_ids.append(int(result.get("alert_id") or 0))
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        require(len(set(created_ids)) == 20 and all(created_ids), "repeated alerts receive unique canonical ids")
        require(elapsed_ms < 10000, "20 alert writes complete without request-path migration stalls")
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS total FROM alert_rules WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        total = int(row["total"] if hasattr(row, "keys") else row[0])
        require(total == 20, "all repeated alert writes persist in the canonical table")
        return {"created": total, "elapsed_ms": elapsed_ms}
    finally:
        conn.close()


def main() -> int:
    try:
        static_checks()
        result = runtime_checks()
    except Exception as exc:
        print(f"FAIL: {exc}")
        return 1
    print(f"PASS: crypto alert request safety audit passed ({result['created']} alerts in {result['elapsed_ms']}ms)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
