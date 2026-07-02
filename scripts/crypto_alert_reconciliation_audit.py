#!/usr/bin/env python3
"""Audit PulseSoc crypto alert source-of-truth reconciliation."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TMP_DIR = Path(tempfile.mkdtemp(prefix="crypto-alert-reconcile-"))
TMP_DB = TMP_DIR / "audit.sqlite3"
os.environ["DATABASE_URL"] = f"sqlite:///{TMP_DB}"
os.environ.setdefault("PULSE_NOTIFICATIONS_DISABLE_EXTERNAL_DELIVERY", "1")
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import alert_engine, dashboard_crypto_command_center  # noqa: E402


RESULTS: dict[str, object] = {}


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def conn():
    connection = bot.db()
    connection.row_factory = getattr(__import__("sqlite3"), "Row")
    return connection


def row_count(connection, table: str, where: str = "1=1", params: tuple = ()) -> int:
    cur = connection.cursor()
    cur.execute(f"SELECT COUNT(*) AS total FROM {table} WHERE {where}", params)
    row = cur.fetchone()
    return int(row["total"] if hasattr(row, "keys") else row[0])


def seed_legacy_crypto_alert(connection, user_id: int) -> None:
    dashboard_crypto_command_center.ensure_tables(connection)
    cur = connection.cursor()
    cur.execute(
        """
        INSERT INTO crypto_alerts
        (user_id, asset_symbol, condition_type, target_value, status,
         notify_push, notify_email, notify_sms, notify_in_app, note, created_at, updated_at)
        VALUES (?, 'SOL', 'below', 84.59, 'active', 1, 0, 0, 1, 'legacy dashboard row', '2026-07-02T00:00:00Z', '2026-07-02T00:00:00Z')
        """,
        (user_id,),
    )
    connection.commit()


def run_static_audit() -> None:
    alert_engine_source = read("services/alert_engine.py")
    dashboard_source = read("services/dashboard_crypto_command_center.py")
    bot_source = read("bot.py")
    notification_source = read("services/pulsesoc_notification_system.py")
    notification_js = read("static/notifications.js")
    require("source_of_truth" in alert_engine_source and "alert_rules" in alert_engine_source, "alert engine declares alert_rules as source of truth")
    require("def reconcile_legacy_alerts" in alert_engine_source, "legacy reconciliation helper exists")
    require("SELECT * FROM alert_rules" in alert_engine_source, "worker/dashboard list reads alert_rules")
    require("SELECT * FROM crypto_alerts" in alert_engine_source, "legacy crypto_alerts import path exists")
    require("evaluate_all_active_alerts" in alert_engine_source and "reconcile_legacy_alerts()" in alert_engine_source, "worker reconciles before evaluating")
    require("dashboard_crypto_command_center.list_alerts" in bot_source, "Manage Alerts route uses dashboard crypto service")
    require("crypto-alert-card" in bot_source and "crypto-alert-grid" in bot_source, "mobile card alert UI exists")
    require("crypto-command-table'><thead><tr><th>Asset</th><th>Condition" not in bot_source, "old broken alert table removed")
    require("/api/crypto/alerts/<int:alert_id>/duplicate" in bot_source, "duplicate endpoint exists")
    require("/api/crypto/alerts/<int:alert_id>/history" in bot_source, "history endpoint exists")
    require("alert_engine.create_alert_rule" in dashboard_source, "Create Alert writes canonical alert_rules")
    require("alert_engine.list_alert_rules" in dashboard_source, "Manage Alerts reads canonical alert_rules")
    require("alert_engine.delete_alert" in dashboard_source, "Delete is canonical soft delete")
    require("duplicate_alert_rule" in dashboard_source, "Duplicate action is wired")
    require("alert_history" in dashboard_source, "History action is wired")
    require("/dashboard/crypto/alerts?alert_id=" in notification_source, "crypto notification helper deep-links to Manage Alerts")
    require("/dashboard/crypto/alerts?alert_id=" in notification_js, "frontend notification fallback deep-links to Manage Alerts")


def run_runtime_audit() -> None:
    bot.init_db()
    user_id = 777001
    with conn() as connection:
        seed_legacy_crypto_alert(connection, user_id)
        before = row_count(connection, "alert_rules", "user_id=?", (user_id,))
        alerts = dashboard_crypto_command_center.list_alerts(connection, user_id)
        after = row_count(connection, "alert_rules", "user_id=?", (user_id,))
        legacy_visible = [alert for alert in alerts if alert.get("source_ref") == "crypto_alerts:1"]
        require(after == before + 1, "legacy crypto_alerts row imported exactly once")
        require(len(legacy_visible) == 1, "legacy firing alert is visible in Manage My Alerts")
        imported_alert_id = int(legacy_visible[0]["id"])
        dashboard_crypto_command_center.update_alert(connection, user_id, imported_alert_id, {"status": "paused"})
        paused = alert_engine.get_alert_rule(imported_alert_id, user_id)
        require(paused and paused.get("status") == "paused", "pause updates canonical alert")
        dashboard_crypto_command_center.update_alert(connection, user_id, imported_alert_id, {"status": "active"})
        resumed = alert_engine.get_alert_rule(imported_alert_id, user_id)
        require(resumed and resumed.get("status") == "active", "resume updates canonical alert")
        created = dashboard_crypto_command_center.create_alert(
            connection,
            user_id,
            {"assetSymbol": "BTC", "condition": "above", "targetValue": 1111, "notifyPush": True, "notifyInApp": True},
        )
        created_id = int(created["alert_id"])
        require(alert_engine.get_alert_rule(created_id, user_id), "dashboard-created alert exists in alert_rules")
        duplicated = dashboard_crypto_command_center.duplicate_alert(connection, user_id, created_id)
        require(int(duplicated.get("alert_id") or 0) != created_id, "duplicate creates a separate manageable alert")
        rule = alert_engine.get_alert_rule(created_id, user_id)
        event = alert_engine._create_event(rule, 1112.0, "triggered", "BTC crossed above $1,111. Live observed value: $1,112.")
        alert_engine._update_event_delivery(event.get("id"), 12345, {"channels": {"in_app": "created", "push": "queued"}})
        history = dashboard_crypto_command_center.alert_history(connection, user_id, created_id)
        require(history.get("events"), "history endpoint returns trigger events")
        require("push:queued" in (history["events"][0].get("delivery_status") or ""), "history includes delivery status")
        dashboard_crypto_command_center.delete_alert(connection, user_id, created_id)
        deleted = alert_engine.get_alert_rule(created_id, user_id)
        require(deleted and deleted.get("status") == "deleted", "delete soft-deletes canonical alert")
        active_count = row_count(connection, "alert_rules", "user_id=? AND COALESCE(status, 'active')='active' AND deleted_at IS NULL", (user_id,))
        require(active_count >= 2, "active source-of-truth alerts remain visible after reconciliation")
        second_pass = dashboard_crypto_command_center.list_alerts(connection, user_id)
        require(row_count(connection, "alert_rules", "source_ref='crypto_alerts:1'") == 1, "reconciliation is idempotent")
    RESULTS["runtime"] = {
        "imported_alert_id": imported_alert_id,
        "created_alert_id": created_id,
        "visible_alerts_after_reconcile": len(second_pass),
    }


def main() -> int:
    try:
        run_static_audit()
        run_runtime_audit()
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc), "results": RESULTS}, indent=2))
        return 1
    print(json.dumps({"ok": True, "results": RESULTS}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
