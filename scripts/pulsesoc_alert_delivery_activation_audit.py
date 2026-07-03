#!/usr/bin/env python3
"""Audit PulseSoc Intelligence alert delivery activation."""

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


def runtime_check(failures: list[str]) -> None:
    with tempfile.TemporaryDirectory(prefix="pulsesoc-alert-delivery-") as tmpdir:
        db_path = Path(tmpdir) / "audit.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
        os.environ["PULSESOC_NOTIFICATION_DELIVERY_AUTOPROCESS_ENABLED"] = "0"
        from services import pulsesoc_intelligence_engine as engine

        engine.ensure_schema()
        target_user_id = 991001
        payload = {
            "stream_key": "security_pulse",
            "event_type": "security_update",
            "event_key": "audit-security-delivery",
            "headline": "Security Signal delivery audit",
            "summary": "A high-confidence defensive security signal should create a manageable Pulse Alert.",
            "why_it_matters": "This verifies Intelligence signals enter the central notification delivery system.",
            "expected_impact": "One notification record and delivery job are created for the eligible test user.",
            "source_keys": ["cisa", "nist"],
            "importance_score": 90,
            "freshness_score": 90,
            "global_impact": 70,
            "duplicate_confidence": 60,
            "priority": "high",
            "metadata": {"deep_link": "/pulse/alerts"},
        }
        result = engine.ingest_signal(payload, deliver=True, target_user_id=target_user_id)
        require(bool(result.get("ok")), "runtime signal ingest succeeds", failures)
        require(bool((result.get("delivery") or {}).get("processing", {}).get("sent", 0) >= 1), "runtime delivery processor sends queued alert", failures)

        diagnostics = engine.delivery_diagnostics(limit=20)
        require(bool(diagnostics.get("jobs")), "runtime delivery jobs are visible", failures)
        require(bool(diagnostics.get("logs")), "runtime delivery logs are visible", failures)
        require(any(log.get("delivery_status") == "sent" for log in diagnostics.get("logs") or []), "runtime delivery log records sent status", failures)

        duplicate = engine.ingest_signal(payload, deliver=True, target_user_id=target_user_id)
        require(bool(duplicate.get("deduped")), "duplicate signal is deduped", failures)

        engine.update_stream(target_user_id, "security_pulse", {"enabled": False})
        blocked = engine.ingest_signal({**payload, "event_key": "audit-security-disabled"}, deliver=True, target_user_id=target_user_id)
        queue = (blocked.get("delivery") or {}).get("queue") or {}
        require(queue.get("skipped", 0) >= 1 and queue.get("queued", 0) == 0, "disabled stream skips delivery", failures)

        test = engine.send_test_alert(target_user_id, target_user_id=target_user_id, stream_key="pulsesoc_discoveries")
        require(bool(test.get("ok")), "admin test alert helper works", failures)


def main() -> int:
    failures: list[str] = []
    service = read("services/pulsesoc_intelligence_engine.py")
    routes = read("pulse_communications_v2/routes.py")
    migration = read("migrations/pulsesoc_intelligence_engine.sql")
    admin_template = read("templates/admin_galaxy_intelligence_center.html")
    user_template = read("templates/pulsesoc_intelligence_center.html")
    js = read("static/js/pulsesoc_intelligence_center.js")
    notification = read("services/pulsesoc_notification_system.py")
    report_path = ROOT / "reports" / "pulsesoc_alert_delivery_activation.md"

    for token in (
        "intelligence_delivery_jobs",
        "queue_event_delivery",
        "process_delivery_queue",
        "process_digest_jobs",
        "generate_digest_jobs",
        "send_test_alert",
        "admin_send_event",
        "cancel_delivery_job",
        "delivery_diagnostics",
    ):
        require(token in service, f"engine contains {token}", failures)

    for route in (
        "/pulse/alerts",
        "/api/admin/intelligence/delivery/test",
        "/api/admin/intelligence/delivery/send",
        "/api/admin/intelligence/delivery/process",
        "/api/admin/intelligence/delivery/digests",
        "/api/admin/intelligence/delivery/cancel",
        "/api/admin/intelligence/delivery/logs",
    ):
        require(route in routes, f"route exists {route}", failures)

    require("intelligence_delivery_jobs" in migration and "idx_intel_delivery_jobs_status" in migration, "production migration includes delivery queue", failures)
    require("data-admin-intel-test-alert" in admin_template and "data-admin-intel-process-delivery" in admin_template, "admin delivery controls exist", failures)
    require("data-admin-intel-delivery-result" in admin_template and "Queue and Logs" in admin_template, "admin delivery diagnostics UI exists", failures)
    require("Not helpful" in js and "Wrong" in js and "Outdated" in js, "user feedback buttons exist", failures)
    require("navigator.share" in js and "clipboard.writeText" in js, "CTA share/copy behavior exists", failures)
    require("https://apps.apple.com/us/app/pulsesoc/id6777591572" not in user_template, "raw App Store URL not shown in user template", failures)
    require("notification_delivery_jobs" in notification and "intelligence_pulse" in notification, "central notification delivery path exists", failures)
    require("technology_pulse" in service and '"default_enabled": False' in service, "suggested streams default off in new-user pack", failures)
    require(report_path.exists(), "activation report exists", failures)

    runtime_check(failures)

    if failures:
        print("PulseSoc alert delivery activation audit failed:")
        for failure in failures:
            print(f" - {failure}")
        return 1
    print("PulseSoc alert delivery activation audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
