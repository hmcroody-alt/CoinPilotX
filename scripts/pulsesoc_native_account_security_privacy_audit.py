#!/usr/bin/env python3
"""Audit the PulseSoc native Account, Security, and Privacy foundation."""

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


def forbid(text: str, needle: str, label: str) -> None:
    if needle in text:
        raise AssertionError(f"Forbidden {label}: {needle}")


def main() -> int:
    account_api = read("mobile-native/src/api/account.ts")
    account_screen = read("mobile-native/src/screens/AccountCenterScreen.tsx")
    settings_screen = read("mobile-native/src/screens/SettingsScreen.tsx")
    types = read("mobile-native/src/navigation/types.ts")
    navigator = read("mobile-native/src/navigation/AppNavigator.tsx")
    linking = read("mobile-native/src/navigation/linking.ts")
    notification_routing = read("mobile-native/src/navigation/notificationRouting.ts")
    report = read("reports/pulsesoc_native_account_security_privacy_progress.md")
    qa_report = read("reports/pulsesoc_native_account_security_privacy_qa.md")
    progress = read("reports/pulsesoc_native_progress.md")

    for endpoint in [
        "/api/account/status",
        "/api/dashboard/account/settings",
        "/api/account/security",
        "/api/account/verify-email",
        "/api/account/verify-phone",
        "/api/account/2fa/enable",
        "/api/account/2fa/disable",
        "/api/account/recovery-codes/generate",
        "/api/account/security-events",
        "/api/account/trusted-devices",
        "/api/account/reauthenticate",
        "/api/account/sessions/revoke-all",
    ]:
        require(account_api, endpoint, "server-authoritative account API wrapper")

    for section in [
        "Account Center",
        "Security Center",
        "Privacy Center",
        "Sessions and Devices",
        "Verify email",
        "Verify phone",
        "Enable 2FA",
        "Recovery codes",
        "Trusted devices",
        "Security history",
        "Open Privacy Center",
    ]:
        require(account_screen, section, "native account center surface")

    for fallback in ["/account/settings", "/account/delete", "/privacy-center", "/dashboard/account/security"]:
        require(account_screen, fallback, "safe protected web fallback")

    for settings_entry in ["Account Center", "Security Center", "Privacy Center", "Sessions and devices"]:
        require(settings_screen, settings_entry, "Settings navigation entry")

    require(types, "AccountCenter", "navigation type")
    for route_name in ["AccountSettings", "AccountSecurity", "AccountWebSettings", "AccountWebSecurity", "AccountPrivacy", "AccountDevices"]:
        require(types, route_name, "account route alias type")
        require(navigator, route_name, "account route alias stack screen")
    require(navigator, "AccountCenterScreen", "stack route component")
    require(linking, 'path: "pulse/settings/:section"', "Pulse settings deep link")
    for path in ["dashboard/account/settings", "dashboard/account/security", "account/settings", "account/security", "privacy-center"]:
        require(linking, path, "direct account/privacy link alias")

    for route in [
        "/pulse/settings/security",
        "/dashboard/account/security",
        "/account/security",
        "/pulse/settings/privacy",
        "/privacy-center",
        "/pulse/settings/devices",
        "/dashboard/account/settings",
        "/account/settings",
    ]:
        require(notification_routing, route, "account notification/deep-link routing")

    for phrase in [
        "server-authoritative",
        "Sensitive flows stay on existing protected web routes",
        "Physical-device verification is not claimed",
        "Run a short practical QA sweep",
    ]:
        require(report, phrase, "account/security/privacy progress report")

    for phrase in [
        "No critical, security, production-breaking, or data-loss issues were found",
        "Direct account/privacy aliases fell back to Home",
        "All aliases listed above rendered the correct native Account, Security, or Privacy Center",
        "2FA enable action through `/api/account/2fa/enable`",
    ]:
        require(qa_report, phrase, "account/security/privacy QA report")

    require(progress, "Native Account, Security & Privacy", "progress report completed feature")
    forbid(account_screen, "LogiNexus", "user-facing internal design language in AccountCenterScreen")
    forbid(settings_screen, "LogiNexus", "user-facing internal design language in SettingsScreen")

    print("PulseSoc native account/security/privacy audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
