#!/usr/bin/env python3
"""Audit the three-hour PulseSoc Intelligence alert cadence."""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, label: str, failures: list[str]) -> None:
    if not condition:
        failures.append(label)


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)


def seed_push_preferences(user_id: int) -> None:
    from services import db as db_service
    from services import pulsesoc_notification_system as notifications

    conn = db_service.connect()
    try:
        notifications.ensure_schema(conn)
        now = notifications.now_iso()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO notification_preferences
            (user_id, category, in_app, push, email, sms, sound, vibration, lock_screen_preview,
             enable_push_notifications, enable_notification_sound, enable_notification_vibration, updated_at)
            VALUES (?, 'intelligence', 1, 1, 0, 0, 1, 1, 1, 1, 1, 1, ?)
            ON CONFLICT(user_id, category) DO UPDATE SET
                in_app=1,
                push=1,
                sound=1,
                vibration=1,
                lock_screen_preview=1,
                enable_push_notifications=1,
                enable_notification_sound=1,
                enable_notification_vibration=1,
                updated_at=excluded.updated_at
            """,
            (int(user_id), now),
        )
        conn.commit()
    finally:
        conn.close()


def runtime_check(failures: list[str]) -> None:
    with tempfile.TemporaryDirectory(prefix="pulsesoc-alert-cadence-") as tmpdir:
        db_path = Path(tmpdir) / "audit.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
        os.environ["PULSESOC_NOTIFICATION_DELIVERY_AUTOPROCESS_ENABLED"] = "0"

        from services import db as db_service
        from services import pulsesoc_intelligence_engine as engine

        target_user_id = 992303
        engine.ensure_schema()
        engine.ensure_user_pack(target_user_id)
        seed_push_preferences(target_user_id)
        engine.update_stream(
            target_user_id,
            "pulsesoc_discoveries",
            {"enabled": True, "push_enabled": True, "breaking_push_only": False, "quiet_hours_enabled": False},
        )

        initial = engine.cadence_status()
        require(initial.get("due_now") is True, "first cadence run is due immediately", failures)

        result = engine.run_alert_cadence(target_user_id=target_user_id, limit=1)
        require(bool(result.get("ok")), "cadence run succeeds", failures)
        require(bool(result.get("event_id")), "cadence run selects or creates one event", failures)
        require(result.get("source") in {"accepted_signal", "pulsesoc_discovery_fallback"}, "cadence source is real or safe fallback", failures)
        queue = result.get("queue") or {}
        require((queue.get("queued") or 0) <= 1, "cadence queues at most one alert job", failures)

        cadence = result.get("cadence") or engine.cadence_status()
        next_run_at = cadence.get("next_run_at") or ""
        last_run_at = cadence.get("last_run_at") or ""
        require(bool(next_run_at and last_run_at), "cadence records last and next run timestamps", failures)
        if next_run_at and last_run_at:
            delta = (parse_iso(next_run_at) - parse_iso(last_run_at)).total_seconds()
            require(10740 <= delta <= 10860, "next cadence run is three hours later", failures)

        second = engine.run_alert_cadence(target_user_id=target_user_id, limit=1)
        require(second.get("status") == "not_due", "second non-forced run does not spam", failures)

        diagnostics = engine.delivery_diagnostics(limit=50)
        require(any(job.get("channel") == "push" for job in diagnostics.get("notification_delivery_jobs") or []), "cadence alert uses central push delivery job", failures)

        conn = db_service.connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) AS count FROM intelligence_alert_cadence")
            count = (cur.fetchone() or [0])[0]
            require(int(count or 0) == 1, "single cadence state row exists", failures)
        finally:
            conn.close()


def main() -> int:
    failures: list[str] = []
    engine = read("services/pulsesoc_intelligence_engine.py")
    routes = read("pulse_communications_v2/routes.py")
    worker = read("scripts/pulsesoc_intelligence_worker.py")
    template = read("templates/admin_galaxy_intelligence_center.html")
    js = read("static/js/pulsesoc_intelligence_center.js")
    migration = read("migrations/pulsesoc_intelligence_engine.sql")
    report_path = ROOT / "reports" / "pulsesoc_alert_cadence.md"

    require("ALERT_CADENCE_SECONDS = 3 * 60 * 60" in engine, "three-hour cadence constant exists", failures)
    require("ALERT_CADENCE_PRIORITY_STREAMS" in engine and "security_pulse" in engine and "market_pulse" in engine, "priority order exists", failures)
    require("run_alert_cadence" in engine and "_select_cadence_event" in engine, "cadence runner and selector exist", failures)
    require("_cadence_fallback_signal" in engine and "pulsesoc_discoveries" in engine, "safe PulseSoc fallback exists", failures)
    require("queue_event_delivery(" in engine and "process_delivery_queue(" in engine, "cadence uses existing delivery queue", failures)
    require("intelligence_alert_cadence" in migration and "10800" in migration, "PostgreSQL migration stores cadence state", failures)
    require("/api/admin/intelligence/cadence/status" in routes, "admin cadence status API exists", failures)
    require("/api/admin/intelligence/cadence/send-now" in routes and "_admin_can_mass_send" in routes, "admin force send route is protected", failures)
    require("--cadence" in worker and "run_alert_cadence" in worker, "worker can run cadence job", failures)
    require("Send next alert now" in template and "dashboard.cadence.next_run_at" in template, "admin cadence UI shows next run and force button", failures)
    require("data-admin-intel-cadence" in js and "/api/admin/intelligence/cadence/send-now" in js, "admin cadence UI is wired", failures)
    require(report_path.exists(), "cadence report exists", failures)

    runtime_check(failures)

    if failures:
        print("PulseSoc alert cadence audit failed:")
        for failure in failures:
            print(f" - {failure}")
        return 1
    print("PulseSoc alert cadence audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
