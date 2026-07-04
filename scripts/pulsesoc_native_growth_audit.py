#!/usr/bin/env python3
"""Audit the PulseSoc native Growth Center foundation."""

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

    api = read("mobile-native/src/api/growth.ts")
    screen = read("mobile-native/src/screens/GrowthCenterScreen.tsx")
    app_nav = read("mobile-native/src/navigation/AppNavigator.tsx")
    types = read("mobile-native/src/navigation/types.ts")
    linking = read("mobile-native/src/navigation/linking.ts")
    routing = read("mobile-native/src/navigation/notificationRouting.ts")
    settings = read("mobile-native/src/screens/SettingsScreen.tsx")
    post_card = read("mobile-native/src/components/PostCard.tsx")
    home = read("mobile-native/src/screens/HomeScreen.tsx")
    post_detail = read("mobile-native/src/screens/PostDetailScreen.tsx")
    reel_card = read("mobile-native/src/components/ReelPlayerCard.tsx")
    reels = read("mobile-native/src/screens/ReelsScreen.tsx")
    profile_header = read("mobile-native/src/components/ProfileHeader.tsx")
    profile = read("mobile-native/src/screens/ProfileScreen.tsx")
    report = read("reports/pulsesoc_native_growth_progress.md")
    progress = read("reports/pulsesoc_native_progress.md")

    require_all(
        api,
        [
            'pulseApi<GrowthState>("/api/pulse/growth")',
            "readJsonCache",
            "writeJsonCache",
            "openGrowthWebFallback",
            "promotionWebPath",
            "normalizeGrowthState",
            "growthMoney"
        ],
        "growth API",
        failures,
    )
    require_all(
        screen,
        [
            "GrowthCenterScreen",
            "getGrowthState",
            "getPremiumStatus",
            "loadCachedGrowthState",
            "Promote content",
            "Audience preview",
            "Campaign overview",
            "Analytics snapshot",
            "Advanced tools",
            "Growth eligibility, promotion readiness, targeting, and billing remain backend-controlled",
            'openGrowthWebFallback("/pulse/promote")',
            'openGrowthWebFallback("/pulse/growth#wallet")',
            'openGrowthWebFallback("/pulse/growth#billing")'
        ],
        "growth screen",
        failures,
    )
    require_all(
        app_nav + types + linking + routing + settings,
        [
            "GrowthCenter",
            "GrowthCenterScreen",
            'path: "pulse/growth"',
            'normalized.startsWith("/pulse/growth")',
            'normalized.startsWith("/pulse/promote")',
            "Growth Center"
        ],
        "navigation/routing",
        failures,
    )
    require_all(
        post_card + home + post_detail + reel_card + reels + profile_header + profile,
        [
            "onPromote",
            'contentType: "post"',
            'contentType: "reel"',
            'contentType: "profile"',
            "onGrowth"
        ],
        "promotion shortcuts",
        failures,
    )
    require_all(
        report,
        [
            "server authoritative",
            "Real-device QA was not claimed",
            "Native Intelligence + Alerts Foundation",
            "Do not duplicate backend business logic",
            "Local growth score calculation",
            "Native ad billing"
        ],
        "growth report",
        failures,
    )
    require_all(
        progress,
        [
            "Growth Center Foundation",
            "scripts/pulsesoc_native_growth_audit.py",
            "Native Intelligence + Alerts Foundation"
        ],
        "master progress",
        failures,
    )

    forbidden = [
        "launchCampaign",
        "createPromotion",
        "create_promotion",
        "grant_entitlement",
        "wallet_balance =",
        "daily_budget_cents =",
        "stripe.",
        "targeting =",
        "growth_score ="
    ]
    for path, source in {
        "growth API": api,
        "growth screen": screen,
    }.items():
        for token in forbidden:
            require(token not in source, f"{path} duplicates backend-owned logic via {token!r}", failures)

    production_paths = [
        "templates/index.html",
        "templates/account.html",
        "static/js/pulse_home_core.js",
        "mobile/pulse-react-native/App.tsx",
    ]
    for path in production_paths:
        source = read(path)
        require("pulsesoc_native_growth" not in source, f"{path} unexpectedly references native Growth audit artifacts", failures)

    if failures:
        print("PulseSoc native Growth Center audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc native Growth Center audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
