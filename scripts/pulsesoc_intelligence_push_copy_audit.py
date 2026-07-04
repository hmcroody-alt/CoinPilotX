#!/usr/bin/env python3
"""Audit Intelligence push copy normalization for crypto alert delivery."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def audit_static(failures: list[str]) -> None:
    alert_engine = read("services/alert_engine.py")
    service_worker = read("static/service-worker.js")
    notification_system = read("services/pulsesoc_notification_system.py")

    require("_crypto_intelligence_push_copy" in alert_engine, "crypto alert push copy helper exists", failures)
    require("normalize_intelligence_alert_copy(signal)" in alert_engine, "crypto alert path invokes Intelligence copy normalizer", failures)
    require('title = alert_copy["lock_title"]' in alert_engine, "old crypto title is replaced before notification creation", failures)
    require("push_body = alert_copy[\"lock_body\"]" in alert_engine, "normalized body is passed before notification creation", failures)
    require('"notification_type": "intelligence_pulse"' in alert_engine, "crypto push metadata marks intelligence_pulse", failures)
    require('"category": "intelligence"' in alert_engine, "crypto push metadata marks intelligence category", failures)
    require('"headline": alert_copy["lock_headline"]' in alert_engine, "crypto push metadata includes normalized headline", failures)
    require('metadata["sound_key"] = "alert" if priority == "urgent" else "pulse_signal"' in alert_engine, "crypto push metadata sets Intelligence sound key", failures)
    require("PulseSoc Alert:" not in alert_engine, "old PulseSoc Alert prefix is not emitted by alert engine", failures)

    require('isIntelligence ? "PULSESOC ALERT"' in service_worker, "service worker renders Intelligence title", failures)
    require("intelligenceHeadline" in service_worker, "service worker renders normalized headline in body", failures)
    require('"subtitle": str(payload.get("headline")' in notification_system, "APNs payload receives normalized headline as subtitle", failures)


def audit_runtime(failures: list[str]) -> None:
    from services import alert_engine

    body = "BTC crossed above $61,000. Live observed value: $62,558."
    rule = {
        "id": 61000,
        "user_id": 7,
        "symbol": "BTC",
        "alert_type": "coin_price",
        "condition": "above",
        "threshold_value": 61000,
        "channels": {"in_app": True, "push": True},
    }
    event = {
        "id": 9001,
        "alert_rule_id": 61000,
        "user_id": 7,
        "symbol": "BTC",
        "observed_value": 62558,
        "message": body,
        "trigger_bucket": "audit-bucket",
    }
    copy = alert_engine._crypto_intelligence_push_copy(event, rule, body)
    joined = " ".join(str(copy.get(key, "")) for key in ("lock_title", "lock_headline", "lock_body"))
    require(copy.get("lock_title") == "PULSESOC ALERT", "lock title uses official label", failures)
    require(copy.get("lock_headline") == "BTC BREAKOUT DETECTED", "BTC headline is normalized", failures)
    require(copy.get("lock_body") == body, "BTC body is short normalized observed value text", failures)
    require("PulseSoc Alert:" not in joined, "runtime copy has no old PulseSoc Alert prefix", failures)

    captured: dict[str, object] = {}

    def fake_notify_crypto_alert(recipient_user_id, alert_id, title, body_arg, coin_symbol, **kwargs):
        captured.update(
            {
                "recipient_user_id": recipient_user_id,
                "alert_id": alert_id,
                "title": title,
                "body": body_arg,
                "coin_symbol": coin_symbol,
                **kwargs,
            }
        )
        return {
            "ok": True,
            "notification_id": 123,
            "delivery_jobs": [{"channel": "push", "status": "queued"}, {"channel": "in_app", "status": "ready"}],
        }

    original_notify = alert_engine.pulsesoc_notification_system.notify_crypto_alert
    original_schema = alert_engine.ensure_alert_schema
    original_user = alert_engine._user_record
    original_log = alert_engine._log_delivery
    original_update = alert_engine._update_event_delivery
    original_readiness = alert_engine.channel_readiness
    try:
        alert_engine.pulsesoc_notification_system.notify_crypto_alert = fake_notify_crypto_alert
        alert_engine.ensure_alert_schema = lambda *args, **kwargs: None
        alert_engine._user_record = lambda user_id: {"user_id": user_id}
        alert_engine._log_delivery = lambda *args, **kwargs: {"ok": True}
        alert_engine._update_event_delivery = lambda *args, **kwargs: {"ok": True}
        alert_engine.channel_readiness = lambda user_id: {"telegram": {"ready": False, "message": "not linked"}}
        alert_engine.dispatch_alert_event(event, rule)
    finally:
        alert_engine.pulsesoc_notification_system.notify_crypto_alert = original_notify
        alert_engine.ensure_alert_schema = original_schema
        alert_engine._user_record = original_user
        alert_engine._log_delivery = original_log
        alert_engine._update_event_delivery = original_update
        alert_engine.channel_readiness = original_readiness

    metadata = captured.get("metadata") if isinstance(captured.get("metadata"), dict) else {}
    require(captured.get("title") == "PULSESOC ALERT", "central notification title is normalized before creation", failures)
    require(captured.get("body") == body, "central notification body is normalized before creation", failures)
    require(metadata.get("headline") == "BTC BREAKOUT DETECTED", "central metadata carries normalized headline", failures)
    require(metadata.get("category") == "intelligence", "central metadata marks push as Intelligence", failures)
    require(metadata.get("notification_type") == "intelligence_pulse", "central metadata marks service worker type", failures)
    require(metadata.get("sound_key") == "pulse_signal", "central metadata uses Intelligence sound", failures)
    require("PulseSoc Alert:" not in str(captured), "captured central payload has no old prefix", failures)


def main() -> int:
    failures: list[str] = []
    audit_static(failures)
    audit_runtime(failures)
    report_path = ROOT / "reports" / "pulsesoc_intelligence_push_copy_fix.md"
    require(report_path.exists(), "report exists", failures)
    if failures:
        print("PulseSoc Intelligence push copy audit failed:")
        for failure in failures:
            print(f" - {failure}")
        return 1
    print("PulseSoc Intelligence push copy audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
