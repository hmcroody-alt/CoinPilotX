#!/usr/bin/env python3
"""Audit the PulseSoc Galaxy Intelligence Engine foundation."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, label: str, failures: list[str]) -> None:
    if not condition:
        failures.append(label)


def main() -> int:
    failures: list[str] = []
    service = read("services/pulsesoc_intelligence_engine.py")
    routes = read("pulse_communications_v2/routes.py")
    migration = read("migrations/pulsesoc_intelligence_engine.sql")
    user_template = read("templates/pulsesoc_intelligence_center.html")
    admin_template = read("templates/admin_galaxy_intelligence_center.html")
    js = read("static/js/pulsesoc_intelligence_center.js")
    css = read("static/css/pulsesoc_intelligence_center.css")
    notification = read("services/pulsesoc_notification_system.py")
    ai_knowledge = read("services/pulse_ai_knowledge.py")
    worker = read("scripts/pulsesoc_intelligence_worker.py")
    report = read("reports/loginexus_intelligence_engine_foundation.md") if (ROOT / "reports/loginexus_intelligence_engine_foundation.md").exists() else ""

    for stream in (
        "pulsesoc_discoveries",
        "crypto_pulse",
        "market_pulse",
        "world_pulse",
        "security_pulse",
        "technology_pulse",
        "pulsesoc_pulse",
        "creator_pulse",
        "music_pulse",
    ):
        require(stream in service and stream in migration, f"{stream} stream exists", failures)

    for table in (
        "intelligence_streams",
        "user_intelligence_streams",
        "intelligence_sources",
        "intelligence_events",
        "intelligence_forecasts",
        "intelligence_feedback",
        "intelligence_collector_runs",
        "intelligence_digest_jobs",
        "intelligence_delivery_log",
    ):
        require(table in service and table in migration, f"{table} schema exists", failures)

    require("ingest_signal" in service and "_score_signal" in service, "multi-stage scoring pipeline exists", failures)
    require("deliver_event" in service and "pulsesoc_notification_system.intake_event" in service, "central notification delivery integration exists", failures)
    require("private_conversations_used" in service and "private_messages_used_by_collectors" in service, "privacy-safe learning guard exists", failures)
    require("run_internal_collector" in service and "collector_runs" in service, "background collector foundation exists", failures)
    require("fetch(" not in service and "requests." not in service, "no synchronous external fetch in service", failures)
    require(
        all(route in routes for route in ("/pulse/intelligence", "/pulse/forecasts", "/pulse/briefing", "/pulse/signals/<string:signal_key>", "/api/pulse/intelligence/state")),
        "user Pulse Signals routes exist",
        failures,
    )
    require("/admin/intelligence" in routes and "/api/admin/intelligence/collect" in routes, "admin Intelligence Center routes exist", failures)
    require("surface.title" in user_template and "Signal Preferences" in user_template and "data-stream-list" in user_template and '"Pulse Alerts"' in service, "user Pulse Signals UI exists", failures)
    require("Galaxy Intelligence Center" not in user_template and "LogiNexus" not in user_template, "admin engine names stay out of user UI", failures)
    require("Galaxy Intelligence Center" in admin_template and "Source Readiness" in admin_template and "data-admin-intel-collect" in admin_template, "admin UI exists", failures)
    require("fetch(url" in js and "data-stream-toggle" in js and "data-feedback" in js, "frontend stream controls exist", failures)
    require("prefers-reduced-motion" in css and "overflow-x: hidden" in css, "performance/mobile CSS guard exists", failures)
    require("intelligence_pulse" in notification and '"intelligence"' in notification, "notification category/event exists", failures)
    require("Pulse Signals" in ai_knowledge and "Pulse Alerts" in ai_knowledge and "Galaxy Intelligence Center" not in ai_knowledge, "Pulse AI knowledge uses user-facing terms", failures)
    require("run_internal_collector" in worker, "worker entry point exists", failures)
    require("Private" in report and "Performance" in report and "Phase" in report, "completion report exists", failures)

    public_ui = user_template + js
    require("LogiNexus" not in public_ui and "L.I.E." not in public_ui and "Galaxy Intelligence Center" not in public_ui, "internal names not exposed in user UI", failures)

    if failures:
        print("PulseSoc Intelligence Engine audit failed:")
        for failure in failures:
            print(f" - {failure}")
        return 1
    print("PulseSoc Intelligence Engine audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
