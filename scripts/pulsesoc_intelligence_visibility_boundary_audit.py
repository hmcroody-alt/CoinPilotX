#!/usr/bin/env python3
"""Audit the admin-only intelligence engine and user-facing signal surfaces."""

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
    engine = read("services/pulsesoc_intelligence_engine.py")
    routes = read("pulse_communications_v2/routes.py")
    registry = read("services/pulse_dashboard_mission_control.py")
    state_service = read("services/dashboard_intelligence_command_center.py")
    user_template = read("templates/pulsesoc_intelligence_center.html")
    admin_template = read("templates/admin_galaxy_intelligence_center.html")
    dashboard_template = read("templates/dashboard.html")
    public_home = read("templates/index.html")
    ai_knowledge = read("services/pulse_ai_knowledge.py")
    feature_map = read("data/pulse_ai/pulsesoc_feature_map.json")
    knowledge = read("data/pulse_ai/pulsesoc_knowledge.json")
    report_exists = (ROOT / "reports/pulsesoc_intelligence_visibility_boundary.md").exists()

    user_labels = (
        "Pulse Alerts",
        "Pulse Forecasts",
        "Watchlists",
        "Pulse Advisor",
        "Security Signals",
        "Crypto Signals",
        "Market Signals",
        "World Events",
        "Daily Briefing",
    )
    user_sources = registry + user_template + engine
    for label in user_labels:
        require(label in user_sources, f"user module exists: {label}", failures)

    require('PUBLIC_CENTER_NAME = "Pulse Signals"' in engine, "public engine name is Pulse Signals", failures)
    require('ADMIN_CENTER_NAME = "Galaxy Intelligence Center"' in engine, "admin engine name remains available", failures)
    require("USER_SURFACES" in engine and "ADMIN_COMMAND_SECTIONS" in engine, "user/admin surface registries exist", failures)
    require("Galaxy Intelligence Center" not in user_template and "LogiNexus" not in user_template, "user template hides internal/admin names", failures)
    require("Galaxy Intelligence Center" in admin_template and "Admin only" in admin_template, "admin template identifies protected command center", failures)
    require('"Galaxy Intelligence Center", "Admin / Moderator Only"' in registry and "admin_only=True" in registry, "admin dashboard module is role-gated", failures)
    require('"Intelligence"' in registry and '"Intelligence Center"' not in registry, "user dashboard category is friendly", failures)
    require("USER_INTELLIGENCE_MODULES" in state_service, "user module state handler exists", failures)

    for route in (
        "/pulse/intelligence",
        "/pulse/forecasts",
        "/pulse/briefing",
        "/pulse/signals/<string:signal_key>",
        "/pulse/settings/signals",
    ):
        require(route in routes, f"user route exists: {route}", failures)
    for route in ("/admin/intelligence", "/api/admin/intelligence/health", "/api/admin/intelligence/state", "/api/admin/intelligence/collect"):
        require(route in routes, f"admin route exists: {route}", failures)
    require(routes.count("admin = _current_admin()") >= 5, "admin intelligence and AI routes perform permission checks", failures)
    require("Admin access required." in routes, "admin API denial response exists", failures)

    require("data-dashboard-module-search" in dashboard_template and 'document.querySelectorAll(".module-card")' in dashboard_template, "Mission Control search indexes rendered role-allowed modules", failures)
    require('action="/pulse/search"' not in dashboard_template, "Mission Control search does not escape into global results", failures)
    require("Open Intelligence Center" not in public_home and "Explore Pulse Alerts" in public_home, "public homepage uses friendly alert language", failures)

    public_knowledge = ai_knowledge + feature_map + knowledge
    require("Galaxy Intelligence Center" not in public_knowledge and "Pulse Alerts" in public_knowledge, "Pulse AI knowledge uses public feature language", failures)
    require(report_exists, "completion report exists", failures)

    if failures:
        print("PulseSoc intelligence visibility boundary audit failed:")
        for failure in failures:
            print(f" - {failure}")
        return 1
    print("PulseSoc intelligence visibility boundary audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
