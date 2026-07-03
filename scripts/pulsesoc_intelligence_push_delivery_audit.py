#!/usr/bin/env python3
"""Audit locked-screen push delivery for PulseSoc Intelligence alerts."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, label: str, failures: list[str]) -> None:
    if not condition:
        failures.append(label)


def seed_push_preferences(user_id: int) -> None:
    from services import db as db_service
    from services import pulsesoc_notification_system as notifications

    conn = db_service.connect()
    try:
        notifications.ensure_schema(conn)
        now = notifications.now_iso()
        cur = conn.cursor()
        for category in ("intelligence", "system"):
            cur.execute(
                """
                INSERT INTO notification_preferences
                (user_id, category, in_app, push, email, sms, sound, vibration, lock_screen_preview,
                 enable_push_notifications, enable_notification_sound, enable_notification_vibration, updated_at)
                VALUES (?, ?, 1, 1, 0, 0, 1, 1, 1, 1, 1, 1, ?)
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
                (int(user_id), category, now),
            )
        conn.commit()
    finally:
        conn.close()


def runtime_check(failures: list[str]) -> None:
    with tempfile.TemporaryDirectory(prefix="pulsesoc-intel-push-") as tmpdir:
        db_path = Path(tmpdir) / "audit.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
        os.environ["PULSESOC_NOTIFICATION_DELIVERY_AUTOPROCESS_ENABLED"] = "0"

        from services import db as db_service
        from services import pulsesoc_intelligence_engine as engine

        target_user_id = 991777
        engine.ensure_schema()
        engine.ensure_user_pack(target_user_id)
        seed_push_preferences(target_user_id)
        stream_update = engine.update_stream(
            target_user_id,
            "pulsesoc_discoveries",
            {"enabled": True, "push_enabled": True, "breaking_push_only": False, "quiet_hours_enabled": False},
        )
        require(bool(stream_update.get("ok")), "runtime stream push preferences update", failures)

        result = engine.send_test_alert(
            target_user_id,
            target_user_id=target_user_id,
            stream_key="pulsesoc_discoveries",
        )
        require(bool(result.get("ok")), "admin locked-screen Intelligence test sends", failures)
        queue_jobs = ((result.get("queue") or {}).get("jobs") or [])
        require(any("push" in (job.get("channels") or []) for job in queue_jobs), "intelligence delivery job includes push channel", failures)

        diagnostics = engine.delivery_diagnostics(limit=50)
        notification_jobs = diagnostics.get("notification_delivery_jobs") or []
        require(any(job.get("channel") == "push" for job in notification_jobs), "central notification push delivery job exists", failures)
        require(any((job.get("status") or "") in {"queued", "ready", "sent", "scheduled"} for job in notification_jobs if job.get("channel") == "push"), "push job has deliverable status", failures)

        conn = db_service.connect()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT n.notification_type, n.category, n.priority, n.deep_link, n.sound_key, n.vibration_json, j.payload_json
                FROM notifications n
                JOIN notification_delivery_jobs j ON j.notification_id=n.id
                WHERE n.user_id=? AND j.channel='push'
                ORDER BY n.id DESC LIMIT 1
                """,
                (target_user_id,),
            )
            row = cur.fetchone()
        finally:
            conn.close()
        require(bool(row), "push notification row can be inspected", failures)
        if row:
            values = [row[index] for index in range(len(row))]
            require(values[0] == "intelligence_pulse", "notification type is intelligence_pulse", failures)
            require(values[1] == "intelligence", "notification category is intelligence", failures)
            require(values[3].startswith("/pulse/alerts"), "deep link opens Pulse Alerts", failures)
            require(values[4] == "pulse_signal", "sound key is pulse_signal for non-urgent Intelligence", failures)
            require("160" in str(values[5]) or "standard" in str(values[6]), "vibration metadata exists", failures)
            require("show_on_lock_screen" in str(values[6]), "push payload records lock-screen eligibility", failures)


def main() -> int:
    failures: list[str] = []
    engine = read("services/pulsesoc_intelligence_engine.py")
    notifications = read("services/pulsesoc_notification_system.py")
    service_worker = read("static/service-worker.js")
    admin_template = read("templates/admin_galaxy_intelligence_center.html")
    report_path = ROOT / "reports" / "pulsesoc_intelligence_push_delivery.md"

    require("intelligence_pulse" in notifications, "central notification event type exists", failures)
    require('"intelligence"' in notifications and "LOCKED_DEVICE_PUSH_DEFAULT_CATEGORIES" in notifications, "intelligence category is lock-screen eligible", failures)
    require("pulse_signal" in notifications and "alert" in notifications, "intelligence sound keys are mapped", failures)
    require("show_on_lock_screen" in notifications and "badge_count" in notifications, "push payload has lock-screen fields", failures)
    require("_channels_for_subscription(subscription, event, resolved_delivery_type)" in engine, "delivery queue passes delivery type into channel selection", failures)
    require("push_due_now" in engine and "feature_discovery" in engine, "instant/forecast/feature delivery can create push channel", failures)
    require('"push_user_set"' in engine, "user push preference overrides are preserved", failures)
    require('"show_on_lock_screen": True' in engine and '"sound_key": sound_key' in engine, "intelligence notification metadata includes push sound/lock-screen data", failures)
    require("notification_delivery_jobs" in engine and "delivery_diagnostics" in engine, "diagnostics expose central delivery jobs", failures)
    require("isIntelligence" in service_worker and "/pulse/alerts" in service_worker, "service worker handles Intelligence fallback route", failures)
    require("pulsesoc-intelligence" in service_worker and "badgeAsset" in service_worker, "service worker tags Intelligence push and uses safe badge asset", failures)
    require("Send locked-screen test Intelligence Pulse" in admin_template, "admin locked-screen test button exists", failures)
    require(report_path.exists(), "push delivery report exists", failures)

    runtime_check(failures)

    if failures:
        print("PulseSoc Intelligence push delivery audit failed:")
        for failure in failures:
            print(f" - {failure}")
        return 1
    print("PulseSoc Intelligence push delivery audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
