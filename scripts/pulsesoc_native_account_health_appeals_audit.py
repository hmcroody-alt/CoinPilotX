#!/usr/bin/env python3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    target = ROOT / path
    if not target.exists():
        raise AssertionError(f"Missing required file: {path}")
    return target.read_text(encoding="utf-8")


def require(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise AssertionError(f"Missing {label}: {needle}")


def main() -> None:
    api = read("mobile-native/src/api/accountHealth.ts")
    screen = read("mobile-native/src/screens/AccountHealthAppealsScreen.tsx")
    types = read("mobile-native/src/navigation/types.ts")
    linking = read("mobile-native/src/navigation/linking.ts")
    navigator = read("mobile-native/src/navigation/AppNavigator.tsx")
    routing = read("mobile-native/src/navigation/notificationRouting.ts")
    settings = read("mobile-native/src/screens/SettingsScreen.tsx")
    settings_registry = read("mobile-native/src/settings/registry.ts")
    settings_catalog = read("mobile-native/src/i18n/catalogs/en/extended.json")
    trust = read("mobile-native/src/screens/TrustSafetyScreen.tsx")
    report = read("reports/pulsesoc_native_account_health_appeals_progress.md")
    progress = read("reports/pulsesoc_native_progress.md")

    for endpoint in (
        "/api/dashboard/account/state",
        "/dashboard/account/health",
    ):
        require(api + report, endpoint, f"account health backend reuse {endpoint}")

    for symbol in (
        "loadAccountHealthState",
        "loadCachedAccountHealthState",
        "submitAccountHealthVerificationAppeal",
        "openAccountHealthWebFallback",
    ):
        require(api, symbol, f"account health API symbol {symbol}")

    for text in (
        "Account Health",
        "Enforcement history",
        "Appeals",
        "Linked reports and cases",
        "Recent security signals",
        "Open protected health details",
        "Submit supported appeal",
        "This appeal path needs the protected Account Health or Verification Center flow.",
    ):
        require(screen, text, f"native account health UI {text}")

    for route_file, route_text in (
        (types, "AccountHealth"),
        (types, "AccountHealthWeb"),
        (linking, "pulse/account-health"),
        (linking, "dashboard/account/health"),
        (navigator, "AccountHealthAppealsScreen"),
        (routing, "accountHealthTarget"),
        (read("mobile-native/src/navigation/nativeRouteActions.ts"), "/dashboard/account/health"),
    ):
        require(route_file, route_text, f"route wiring {route_text}")

    require(settings + settings_registry, 'id: "account-health"', "Settings entry point")
    require(settings_catalog, "Account Health and Appeals", "localized Settings entry point")
    require(trust, "Account Health", "Trust/Safety entry point")
    require(report, "Native Blocks, Mutes, and Report Management Foundation", "next recommendation")
    require(progress, "Native Account Health + Appeals Center Foundation", "progress update")

    forbidden_user_copy = "LogiNexus"
    if forbidden_user_copy in api or forbidden_user_copy in screen:
        raise AssertionError("Internal LogiNexus naming must not appear in native Account Health user-facing code.")

    print("PulseSoc native account health appeals audit passed.")


if __name__ == "__main__":
    main()
