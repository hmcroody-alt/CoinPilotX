#!/usr/bin/env python3
"""Audit the PulseSoc native Intelligence + Alerts foundation."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def require_all(source: str, tokens: list[str], label: str, failures: list[str]) -> None:
    for token in tokens:
        require(token in source, f"{label} missing {token!r}", failures)


def main() -> int:
    failures: list[str] = []

    api = read("mobile-native/src/api/intelligence.ts")
    screen = read("mobile-native/src/screens/IntelligenceCenterScreen.tsx")
    app_nav = read("mobile-native/src/navigation/AppNavigator.tsx")
    types = read("mobile-native/src/navigation/types.ts")
    linking = read("mobile-native/src/navigation/linking.ts")
    routing = read("mobile-native/src/navigation/notificationRouting.ts")
    settings = read("mobile-native/src/screens/SettingsScreen.tsx")
    growth = read("mobile-native/src/screens/GrowthCenterScreen.tsx")
    premium = read("mobile-native/src/screens/PremiumScreen.tsx")
    report = read("reports/pulsesoc_native_intelligence_progress.md")
    progress = read("reports/pulsesoc_native_progress.md")
    bot = read("bot.py")
    alert_engine = read("services/alert_engine.py")
    intelligence_service = read("services/dashboard_intelligence_command_center.py")

    require_all(
        api,
        [
            'pulseApi<IntelligenceState>("/api/dashboard/intelligence/state")',
            'pulseApi<AlertListResponse>("/api/crypto/alerts")',
            "readJsonCache",
            "writeJsonCache",
            "openIntelligenceWebFallback",
            "normalizeIntelligenceState",
            "normalizeAlertList",
            "alertConditionLabel",
            "alertWebPath"
        ],
        "intelligence API",
        failures,
    )
    require_all(
        screen,
        [
            "IntelligenceCenterScreen",
            "getIntelligenceState",
            "listCryptoAlerts",
            "getNotificationBadgeCounts",
            "getPremiumStatus",
            "loadCachedIntelligenceState",
            "loadCachedAlertList",
            "Guidance score",
            "Streams and forecasts",
            "Alert overview",
            "Alert detail",
            "Advanced tools",
            "Alert evaluation, forecasts, provider routing, and delivery remain backend-controlled",
            'openIntelligenceWebFallback("/dashboard/intelligence")',
            'openIntelligenceWebFallback("/dashboard/crypto/alerts")',
            'openIntelligenceWebFallback("/dashboard/crypto/alerts/create")'
        ],
        "intelligence screen",
        failures,
    )
    require_all(
        app_nav + types + linking + routing + settings + growth + premium,
        [
            "IntelligenceCenter",
            "IntelligenceCenterScreen",
            'path: "dashboard/intelligence/:subsystem?"',
            'normalized.startsWith("/dashboard/intelligence")',
            'normalized.startsWith("/dashboard/crypto/alerts")',
            "Intelligence and alerts",
            "Open Intelligence"
        ],
        "navigation/routing",
        failures,
    )
    require_all(
        report,
        [
            "Do not duplicate backend business logic",
            "backend remains authoritative",
            "QA-Driven Development Rule",
            "Real QA browser/device behavior was not claimed",
            "Native Feature Parity + QA Readiness Report",
            "Native alert trigger evaluation",
            "Native buy/sell/hold or investment recommendations"
        ],
        "intelligence report",
        failures,
    )
    require_all(
        progress,
        [
            "Intelligence + Alerts Foundation",
            "reports/pulsesoc_native_intelligence_progress.md",
            "scripts/pulsesoc_native_intelligence_audit.py",
            "Native Feature Parity + QA Readiness Report"
        ],
        "master progress",
        failures,
    )
    require_all(
        bot + alert_engine + intelligence_service,
        [
            "/api/dashboard/intelligence/state",
            "/api/crypto/alerts",
            "def list_alert_rules",
            "def dispatch_alert_event",
            "build_intelligence_state"
        ],
        "production backend evidence",
        failures,
    )

    forbidden = [
        "dispatch_alert_event(",
        "evaluate_alert",
        "evaluate_all_active_alerts",
        "buy recommendation",
        "sell recommendation",
        "hold recommendation",
        "investment advice",
        "grant_entitlement",
        "provider_api_key",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "CLAUDE_API_KEY",
        "daily_budget_cents =",
        "triggered ="
    ]
    for path, source in {
        "intelligence API": api,
        "intelligence screen": screen,
    }.items():
        lower_source = source.lower()
        for token in forbidden:
            check = token if token.isupper() else token.lower()
            require(check not in lower_source, f"{path} duplicates sensitive backend-owned logic via {token!r}", failures)

    production_paths = [
        "templates/index.html",
        "templates/account.html",
        "static/js/pulse_home_core.js",
        "mobile/pulse-react-native/App.tsx",
    ]
    for path in production_paths:
        source = read(path)
        require("pulsesoc_native_intelligence" not in source, f"{path} unexpectedly references native Intelligence audit artifacts", failures)

    if failures:
        print("PulseSoc native Intelligence + Alerts audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc native Intelligence + Alerts audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
